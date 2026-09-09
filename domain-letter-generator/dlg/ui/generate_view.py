"""Pohled „Generovat“ — vyplnění formuláře a uložení hotového dopisu.

Vlevo je dynamický formulář poskládaný podle ``TemplateMeta.ordered_fields()``,
vpravo živý náhled výsledného textu. Náhled se přepočítává se zpožděním
(:data:`PREVIEW_DELAY_MS`) po poslední změně, aby psaní neseklo.

Sestavení dopisu (`fill_docx` nad celým balíčkem) je u delší šablony práce na
stovky ms, proto běží ve vlákně (:func:`dlg.ui.widgets.run_in_thread`) — jak
při generování (po dobu běhu jsou tlačítka zakázaná), tak při přepočtu náhledu
po pauze v psaní. Do vlákna jde jen čistý snímek formuláře (:meth:`_snapshot`),
nikdy widgety. Vyplněné hodnoty se ukládají do historie, takže je příště
nabídne našeptávač — dosazují se ale nikdy samy, viz :meth:`default_value`.

Použití z :mod:`dlg.app`::

    view = GenerateView(
        container, store, history, settings_getter=self.settings, status=self.status
    )
    view.load(template_id)
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import tempfile
import tkinter as tk
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping
from tkinter import ttk

from .. import config, mapping, naming
from ..docx_engine import extract_text, fill_docx
from ..models import FieldSpec, FillReport, TemplateMeta
from . import theme, widgets

__all__ = [
    "GenerateView",
    "PREVIEW_DELAY_MS",
    "open_path",
]

#: Zpoždění přepočtu náhledu po poslední změně formuláře (ms).
PREVIEW_DELAY_MS = 300
#: Kolik znaků odstavce se ukáže u volitelného odstavce bez popisku.
PARAGRAPH_PREVIEW_CHARS = 120
#: Pojistka proti zvýrazňování v pathologicky dlouhém náhledu.
MAX_HIGHLIGHTS = 500
#: Kolik nevyplněných míst se vypíše jmenovitě.
MAX_LISTED_UNFILLED = 6
#: Tag, kterým se v náhledu zvýrazní dosazené hodnoty.
FILLED_TAG = "vyplneno"


# ---------------------------------------------------------------------------
# drobné pomůcky
# ---------------------------------------------------------------------------
def _invoke(callback: Callable[..., Any] | None, *args: Any) -> Any:
    """Zavolá callback; když nebere argumenty, zavolá ho bez nich."""

    if callback is None:
        return None
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):  # pragma: no cover - exotické volatelné objekty
        return callback(*args)
    try:
        signature.bind(*args)
    except TypeError:
        return callback()
    return callback(*args)


def _describe_error(exc: BaseException) -> str:
    """České vysvětlení chyby — hlášky operačního systému jsou anglicky."""

    if isinstance(exc, OSError):
        return config.describe_os_error(exc).capitalize() + "."
    return str(exc)


def _save_docx(directory: Path, stem: str, payload: bytes) -> Path:
    """Uloží dopis atomicky — buď je celý, nebo ve složce nezůstane nic.

    Zápis přímo do cílového souboru by při plném disku nebo odpojeném síťovém
    disku nechal ve složce nedopsaný ``.docx``, který Word neotevře, a další
    pokus by vedle něj vyrobil „… (2).docx“.
    """

    config.ensure_dir(directory)
    target = naming.unique_path(directory, stem, ".docx")
    handle, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".dlg-", suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:  # pragma: no cover - dočasný soubor už není
            pass
        raise config.ConfigError(
            f"Dopis „{target.name}“ se nepodařilo uložit: "
            f"{config.describe_os_error(exc)}."
        ) from exc
    return target


class _Status:
    """Obal nad stavovým řádkem — snese ``None``, funkci i ``StatusBar``."""

    def __init__(self, target: Any = None) -> None:
        self.target = target

    def _call(self, method: str, text: str) -> None:
        target = self.target
        if target is None:
            return
        handler = getattr(target, method, None)
        if callable(handler):
            handler(text)
            return
        if callable(target):
            target(text)

    def set(self, text: str) -> None:
        self._call("set", text)

    def error(self, text: str) -> None:
        self._call("set_error", text)

    def success(self, text: str) -> None:
        self._call("set_success", text)

    def busy(self, text: str) -> None:
        target = self.target
        starter = getattr(target, "start_progress", None)
        if callable(starter):
            starter(text)
            return
        self.set(text)

    def idle(self) -> None:
        stopper = getattr(self.target, "stop_progress", None)
        if callable(stopper):
            stopper()


def open_path(target: Path | str) -> bool:
    """Otevře soubor nebo složku v systému. Vrací ``True`` při úspěchu.

    Windows: ``os.startfile``. macOS: ``open``. Jinde: ``xdg-open``.
    Funkce nikdy nevyhodí výjimku — když to nejde, jen vrátí ``False``.
    """

    path = str(target)
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606 - jen na Windows
            return True
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen(  # noqa: S603 - pevně daný program, cesta je argument
            [opener, path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:  # noqa: BLE001 - neotevřený soubor není důvod ke spadnutí
        return False


# ---------------------------------------------------------------------------
# pohled
# ---------------------------------------------------------------------------
class GenerateView(ttk.Frame):
    """Formulář jedné šablony, živý náhled a generování dopisu.

    :param master: rodičovský widget
    :param store: :class:`dlg.store.TemplateStore`
    :param history: :class:`dlg.store.ValueHistory` (může být ``None``)
    :param settings_getter: funkce vracející aktuální :class:`dlg.config.Settings`
    :param status: stavový řádek, funkce ``(text) -> None``, nebo ``None``
    """

    def __init__(
        self,
        master: tk.Misc | None = None,
        store: Any = None,
        history: Any = None,
        settings_getter: Callable[[], Any] | None = None,
        status: Any = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("padding", theme.PAD_L)
        super().__init__(master, **kwargs)
        self.store = store
        self.history = history
        self.settings_getter = settings_getter or config.load_settings
        self.status = _Status(status)

        #: Zavolá se po úspěšném vygenerování; dostane cestu k souboru.
        self.on_generated: Callable[..., Any] | None = None
        #: Zavolá se, když pohled sám změní nastavení (volba PDF), ať se uloží.
        self.on_settings_changed: Callable[..., Any] | None = None

        self.meta: TemplateMeta | None = None
        self.scan: Any = None
        self.last_output_path: Path | None = None
        self.last_pdf_path: Path | None = None
        self.last_report: FillReport | None = None

        self._docx: bytes = b""
        self._templates: list[TemplateMeta] = []
        self._display_to_id: dict[str, str] = {}
        self._fields: dict[str, widgets.LabeledField] = {}
        self._specs: list[FieldSpec] = []
        self._paragraph_vars: list[tuple[str, tk.BooleanVar]] = []
        self._preview_job: str | None = None
        #: Varování, že uložené mapování už nemusí sedět na upravenou šablonu.
        self._mapping_warning = ""
        #: Pořadí náhledu — starší výsledek z vlákna nesmí přepsat novější.
        self._preview_seq = 0
        self._filename_touched = False
        self._filename_auto = ""
        #: Stav formuláře, ve kterém byl postavený — proti němu se pozná,
        #: co do něj vyplnil uživatel a co je jen předvyplněná výchozí hodnota.
        self._baseline: tuple[dict[str, str], dict[str, bool]] = ({}, {})
        self._busy = False
        self._loading = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_header()
        self._build_body()
        self._build_footer()
        self.refresh_templates()

    # ------------------------------------------------------------------
    # stavba rozhraní
    # ------------------------------------------------------------------
    def _build_header(self) -> None:
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_M))
        header.columnconfigure(1, weight=1)

        ttk.Label(header, style="Popisek.TLabel", text="Šablona:").grid(
            row=0, column=0, sticky="w", padx=(0, theme.PAD_S)
        )
        self.template_var = tk.StringVar(master=self)
        self.template_combo = ttk.Combobox(
            header, textvariable=self.template_var, state="readonly", values=()
        )
        self.template_combo.grid(row=0, column=1, sticky="ew")
        self.template_combo.bind("<<ComboboxSelected>>", self._on_template_selected, add="+")

        self.description_label = ttk.Label(
            header, style="Napoveda.TLabel", text="", wraplength=900, justify="left"
        )
        self.description_label.grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(theme.PAD_S, 0)
        )

    def _build_body(self) -> None:
        self.panes = ttk.PanedWindow(self, orient="horizontal")
        self.panes.grid(row=1, column=0, sticky="nsew")

        left = ttk.Frame(self.panes, padding=(0, 0, theme.PAD_M, 0))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.form_area = widgets.ScrollableFrame(left, padding=(0, 0, theme.PAD_S, 0))
        self.form_area.grid(row=0, column=0, sticky="nsew")
        self.panes.add(left, weight=3)

        right = ttk.Frame(self.panes, padding=(theme.PAD_M, 0, 0, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        ttk.Label(right, style="Podnadpis.TLabel", text="Náhled dopisu").grid(
            row=0, column=0, sticky="w", pady=(0, theme.PAD_S)
        )

        preview_holder = ttk.Frame(right)
        preview_holder.grid(row=1, column=0, sticky="nsew")
        preview_holder.columnconfigure(0, weight=1)
        preview_holder.rowconfigure(0, weight=1)

        self.preview = tk.Text(
            preview_holder,
            wrap="word",
            width=44,
            borderwidth=1,
            relief="solid",
            highlightthickness=0,
            background=theme.COLOR_SURFACE,
            foreground=theme.COLOR_TEXT,
            padx=theme.PAD_M,
            pady=theme.PAD_S,
            font=theme.font_name(theme.FONT_TEXT, self),
        )
        self.preview.grid(row=0, column=0, sticky="nsew")
        preview_scroll = ttk.Scrollbar(
            preview_holder, orient="vertical", command=self.preview.yview
        )
        preview_scroll.grid(row=0, column=1, sticky="ns")
        self.preview.configure(yscrollcommand=preview_scroll.set, state="disabled")
        self.preview.tag_configure(
            FILLED_TAG, background=theme.COLOR_ACCENT_SOFT, foreground=theme.COLOR_TEXT
        )

        self.unfilled_label = ttk.Label(
            right, style="Napoveda.TLabel", text="", wraplength=420, justify="left"
        )
        self.unfilled_label.grid(row=2, column=0, sticky="w", pady=(theme.PAD_S, 0))

        self.panes.add(right, weight=2)

    def _build_footer(self) -> None:
        footer = ttk.Frame(self)
        footer.grid(row=2, column=0, sticky="ew", pady=(theme.PAD_M, 0))
        footer.columnconfigure(1, weight=1)

        ttk.Label(footer, style="Popisek.TLabel", text="Název souboru:").grid(
            row=0, column=0, sticky="w", padx=(0, theme.PAD_S)
        )
        self.filename_var = tk.StringVar(master=self)
        self.filename_entry = ttk.Entry(footer, textvariable=self.filename_var)
        self.filename_entry.grid(row=0, column=1, sticky="ew")
        ttk.Label(footer, style="Napoveda.TLabel", text=".docx").grid(
            row=0, column=2, sticky="w", padx=(theme.PAD_S, 0)
        )
        for sequence in ("<KeyRelease>", "<<Paste>>", "<<Cut>>"):
            self.filename_entry.bind(sequence, self._on_filename_typed, add="+")

        self.output_label = ttk.Label(
            footer, style="Napoveda.TLabel", text="", wraplength=900, justify="left"
        )
        self.output_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))

        self._build_pdf_option(footer)

        self.toolbar = widgets.Toolbar(footer, padding=(0, theme.PAD_M, 0, 0))
        self.toolbar.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.toolbar.add_button("Generovat", self.generate, primary=True, key="generovat")
        self.toolbar.add_button(
            "Generovat a otevřít", lambda: self.generate(open_after=True), key="generovat_otevrit"
        )
        self.toolbar.add_button(
            "Otevřít složku s výstupy", self.open_output_dir, side="right", key="slozka"
        )

    def _build_pdf_option(self, footer: tk.Misc) -> None:
        """Zaškrtávátko „uložit i PDF“.

        PDF vyrábí Word (nebo LibreOffice), který je na počítači — aplikace sama
        žádnou knihovnu na PDF nemá, viz :mod:`dlg.pdf`. Když tu není čím
        převádět, volba se nenabízí vůbec; nabízet ji a pak selhat by bylo horší
        než ji neukázat.
        """

        from .. import pdf as pdf_module  # importuje se až tady, ne při startu

        self.pdf_var = tk.BooleanVar(
            master=self, value=bool(getattr(self.settings(), "export_pdf", False))
        )
        self._pdf_converter = pdf_module.converter_name()
        if not self._pdf_converter:
            self.pdf_check = None
            self.pdf_var.set(False)
            return

        self.pdf_check = ttk.Checkbutton(
            footer,
            text="Uložit vedle dopisu i PDF",
            variable=self.pdf_var,
            command=self._on_pdf_toggled,
        )
        self.pdf_check.grid(row=2, column=0, columnspan=3, sticky="w", pady=(theme.PAD_S, 0))

    def _on_pdf_toggled(self) -> None:
        """Volbu si aplikace pamatuje — příště bude zaškrtnutá stejně."""

        try:
            settings = self.settings()
            settings.export_pdf = bool(self.pdf_var.get())
            _invoke(self.on_settings_changed, settings)
        except Exception:  # noqa: BLE001 - nezapamatovaná volba není důvod spadnout
            pass

    def wants_pdf(self) -> bool:
        """Má se vedle dopisu uložit i PDF?"""

        variable = getattr(self, "pdf_var", None)
        if variable is None or getattr(self, "pdf_check", None) is None:
            return False
        try:
            return bool(variable.get())
        except Exception:  # noqa: BLE001 - widget už nemusí existovat
            return False

    # ------------------------------------------------------------------
    # nastavení a seznam šablon
    # ------------------------------------------------------------------
    def settings(self) -> Any:
        """Aktuální nastavení; při chybě spadne zpátky na výchozí hodnoty."""

        try:
            return self.settings_getter()
        except Exception:  # noqa: BLE001 - rozbité nastavení nesmí shodit pohled
            return config.Settings(output_dir=str(config.default_output_dir()))

    def output_dir(self) -> Path:
        return self.settings().resolved_output_dir()

    def refresh_templates(self, select: str = "") -> None:
        """Načte seznam šablon do comboboxu. ``select`` = id, které se má vybrat."""

        try:
            self._templates = list(self.store.list()) if self.store is not None else []
        except Exception as exc:  # noqa: BLE001
            self._templates = []
            self.status.error(str(exc))

        self._display_to_id = {}
        displays: list[str] = []
        names: dict[str, int] = {}
        for meta in self._templates:
            names[meta.name] = names.get(meta.name, 0) + 1
        for meta in self._templates:
            display = meta.name if names.get(meta.name, 0) == 1 else f"{meta.name} ({meta.id})"
            self._display_to_id[display] = meta.id
            displays.append(display)

        self.template_combo.configure(values=tuple(displays))
        wanted = select or self.template_id
        for display, tid in self._display_to_id.items():
            if tid == wanted:
                self.template_var.set(display)
                return
        if not displays:
            self.template_var.set("")
            self._clear_form("V knihovně zatím není žádná šablona. Nahrajte ji v části Šablony.")

    @property
    def template_id(self) -> str:
        return self.meta.id if self.meta is not None else ""

    def _on_template_selected(self, _event: Any = None) -> None:
        template_id = self._display_to_id.get(self.template_var.get(), "")
        if not template_id or template_id == self.template_id:
            return
        if not self.confirm_discard("Přepnutím na jinou šablonu o ně přijdete."):
            # v comboboxu musí zůstat šablona, která je opravdu načtená
            self.refresh_templates(select=self.template_id)
            return
        self.load(template_id)

    # ------------------------------------------------------------------
    # načtení šablony a stavba formuláře
    # ------------------------------------------------------------------
    def load(self, template_id: str = "") -> bool:
        """Načte šablonu a postaví formulář. Prázdné id = poslední/první šablona."""

        if self.store is None:  # pragma: no cover - pojistka
            return False

        if not template_id:
            if not self._templates:
                self.refresh_templates()
            # Kandidáty je nutné porovnat se skutečnou knihovnou: naposledy
            # použitá šablona mohla být smazaná (i mimo aplikaci) a pokus
            # o její načtení by při každém startu vyskočil s chybovým oknem.
            known = {m.id for m in self._templates}
            settings = self.settings()
            candidates = [settings.last_template_id] + [m.id for m in self._templates]
            template_id = next((c for c in candidates if c and c in known), "")
        if not template_id:
            self._clear_form("V knihovně zatím není žádná šablona. Nahrajte ji v části Šablony.")
            return False

        try:
            meta = self.store.get(template_id)
            scan = self.store.scan(template_id)
            docx = self.store.read_docx(template_id)
        except Exception as exc:  # noqa: BLE001
            self._clear_form("Šablonu se nepodařilo načíst.")
            self.status.error(str(exc))
            widgets.show_error(self, "Šablonu se nepodařilo načíst.", detail=str(exc))
            return False

        self.meta = meta
        self.scan = scan
        self._docx = docx
        self._filename_touched = False
        self._mapping_warning = self._check_mapping(meta)
        self.refresh_templates(select=meta.id)
        self.description_label.configure(
            text=meta.description or f"Zdrojový soubor: {meta.source_filename or '—'}"
        )
        self._build_form()
        self.update_filename()
        self.update_preview()
        if self._mapping_warning:
            self.status.error(self._mapping_warning)
        else:
            self.status.set(f"Šablona „{meta.name}“ je připravená k vyplnění.")
        return True

    def _check_mapping(self, meta: TemplateMeta) -> str:
        """Ověří, že uložená pole pořád míří tam, kam mají.

        Šablonu jde upravit i mimo aplikaci (tlačítko „Otevřít složku“).
        Protože ``Placeholder.id`` obsahuje pořadové číslo odstavce, může se
        po smazání odstavců stát, že staré id připadne jinému místu dokumentu
        — a hodnota by se tiše zapsala do špatné pasáže.
        """

        verify = getattr(self.store, "verify_mapping", None)
        if not callable(verify):
            return ""
        try:
            check = verify(meta)
        except Exception:  # noqa: BLE001 - kontrola je pojistka, ne překážka
            return ""
        return str(getattr(check, "message", "") or "")

    def has_unsaved_input(self) -> bool:
        """Je ve formuláři něco, co vyplnil uživatel a co ještě nebylo použito?"""

        if self.meta is None:
            return False
        base_values, base_paragraphs = self._baseline
        for key, value in self.values().items():
            if value.strip() and value != base_values.get(key, ""):
                return True
        return any(
            bool(var.get()) != base_paragraphs.get(pid, True)
            for pid, var in self._paragraph_vars
        )

    def confirm_discard(self, detail: str = "Opravdu je chcete zahodit?") -> bool:
        """Dotaz před zahozením rozepsaného formuláře (``True`` = smí se zahodit)."""

        if not self.has_unsaved_input():
            return True
        return widgets.ask_yes_no(
            self,
            "Ve formuláři máte rozepsané údaje.",
            detail=detail,
            title="Rozepsaný formulář",
        )

    def _clear_form(self, message: str) -> None:
        self.meta = None
        self._mapping_warning = ""
        self.scan = None
        self._docx = b""
        self._fields = {}
        self._specs = []
        self._paragraph_vars = []
        self.form_area.clear()
        widgets.wrap_to_width(
            ttk.Label(
                self.form_area.body, style="Napoveda.TLabel", text=message,
                wraplength=320, justify="left",
            )
        ).grid(row=0, column=0, sticky="w")
        self.description_label.configure(text="")
        self._set_preview_text("", ())
        self.unfilled_label.configure(text="")
        self.filename_var.set("")
        self._filename_auto = ""

    def _build_form(self) -> None:
        assert self.meta is not None
        self._loading = True
        try:
            self.form_area.clear()
            self._fields = {}
            self._paragraph_vars = []
            body = self.form_area.body
            body.columnconfigure(0, weight=1)

            settings = self.settings()
            self._specs = self.meta.ordered_fields()
            row = 0

            if not self._specs:
                widgets.wrap_to_width(
                    ttk.Label(
                        body,
                        style="Napoveda.TLabel",
                        text="Šablona nemá žádná pole. Doplňte je v části „Pole šablony“.",
                        wraplength=320,
                        justify="left",
                    )
                ).grid(row=row, column=0, sticky="w")
                row += 1

            for spec in self._specs:
                widget = self._make_field(body, spec, settings)
                widget.grid(row=row, column=0, sticky="ew", pady=(0, theme.PAD_M))
                self._fields[spec.key] = widget
                row += 1

            optional = list(self.meta.optional_paragraphs)
            if optional:
                ttk.Separator(body, orient="horizontal").grid(
                    row=row, column=0, sticky="ew", pady=(0, theme.PAD_M)
                )
                row += 1
                ttk.Label(body, style="Podnadpis.TLabel", text="Volitelné odstavce").grid(
                    row=row, column=0, sticky="w"
                )
                row += 1
                widgets.wrap_to_width(
                    ttk.Label(
                        body,
                        style="Napoveda.TLabel",
                        text="Odškrtnutý odstavec se do dopisu nedostane.",
                        wraplength=320,
                        justify="left",
                    )
                ).grid(row=row, column=0, sticky="w", pady=(2, theme.PAD_S))
                row += 1

                for op in optional:
                    variable = tk.BooleanVar(master=self, value=bool(op.included_by_default))
                    caption = op.label.strip() or self._paragraph_preview(op.paragraph_id)
                    check = ttk.Checkbutton(body, text=caption, variable=variable)
                    check.grid(row=row, column=0, sticky="w", pady=(0, theme.PAD_S))
                    variable.trace_add("write", lambda *_a: self._on_form_change())
                    self._paragraph_vars.append((op.paragraph_id, variable))
                    row += 1

            self._baseline = (
                self.values(),
                {pid: bool(var.get()) for pid, var in self._paragraph_vars},
            )
        finally:
            self._loading = False

    def _paragraph_preview(self, paragraph_id: str) -> str:
        if self.scan is not None:
            paragraph = self.scan.paragraph(paragraph_id)
            if paragraph is not None:
                return widgets.shorten(paragraph.text, PARAGRAPH_PREVIEW_CHARS) or paragraph_id
        return paragraph_id

    def _make_field(self, parent: tk.Misc, spec: FieldSpec, settings: Any) -> widgets.LabeledField:
        value = self.default_value(spec, settings)
        suggestions = self._suggestions(spec.key)
        widget: widgets.LabeledField

        if spec.type == "multiline":
            widget = widgets.LabeledText(
                parent, spec.label, help_text=spec.help, required=spec.required, height=4
            )
        elif spec.type == "choice":
            options = list(spec.options)
            if value and value not in options:
                options = options + [value]
            widget = widgets.LabeledCombobox(
                parent,
                spec.label,
                values=options,
                help_text=spec.help or "Můžete vybrat variantu, nebo napsat vlastní text.",
                required=spec.required,
                readonly=False,
            )
        elif spec.type == "date":
            widget = widgets.LabeledDate(
                parent,
                spec.label,
                help_text=spec.help or f"Datum ve tvaru {widgets.DATE_FORMAT_HINT}.",
                required=spec.required,
            )
        else:
            widget = widgets.LabeledCombobox(
                parent,
                spec.label,
                values=suggestions,
                help_text=spec.help,
                required=spec.required,
                readonly=False,
                autocomplete=True,
            )

        widget.set(value)
        widget.bind_change(lambda _value: self._on_form_change())
        return widget

    def _suggestions(self, key: str) -> list[str]:
        if self.history is None:
            return []
        try:
            return list(self.history.suggestions(key))
        except Exception:  # noqa: BLE001 - rozbitá historie nesmí shodit formulář
            return []

    def default_value(self, spec: FieldSpec, settings: Any = None) -> str:
        """Výchozí hodnota pole: profil > ``FieldSpec.default`` > u data dnešek.

        Historie se **nedosazuje**. Nabízí ji našeptávač, ale předvyplnit
        jméno a doménu z minulého dopisu by znamenalo, že projde i kontrola
        povinných polí a aplikace ohlásí „vše vyplněno“ — výzva by pak odešla
        na jméno předchozího klienta. Trvalé výchozí hodnoty patří do profilu
        v Nastavení, kde si je uživatel nastaví vědomě.
        """

        settings = self.settings() if settings is None else settings
        profile = str((getattr(settings, "profile", {}) or {}).get(spec.key, ""))
        if profile.strip():
            return profile
        if spec.default.strip():
            return spec.default
        if spec.type == "date":
            return widgets.format_date(date.today())
        return ""

    # ------------------------------------------------------------------
    # hodnoty formuláře
    # ------------------------------------------------------------------
    def values(self) -> dict[str, str]:
        """Vyplněné hodnoty podle klíče pole."""

        out: dict[str, str] = {}
        for key, widget in self._fields.items():
            try:
                out[key] = widget.get()
            except tk.TclError:  # pragma: no cover
                out[key] = ""
        return out

    def set_value(self, key: str, value: str) -> bool:
        """Nastaví hodnotu jednoho pole (pro app i pro testy)."""

        widget = self._fields.get(key)
        if widget is None:
            return False
        widget.set(value)
        self._on_form_change()
        return True

    def set_values(self, values: Mapping[str, str]) -> None:
        for key, value in (values or {}).items():
            self.set_value(key, value)

    def dropped_paragraph_ids(self) -> list[str]:
        """Odstavce, které se mají z dopisu vypustit (odškrtnuté volitelné)."""

        return [pid for pid, variable in self._paragraph_vars if not bool(variable.get())]

    def set_paragraph_included(self, paragraph_id: str, included: bool) -> bool:
        for pid, variable in self._paragraph_vars:
            if pid == paragraph_id:
                variable.set(bool(included))
                return True
        return False

    # ------------------------------------------------------------------
    # náhled
    # ------------------------------------------------------------------
    def _on_form_change(self) -> None:
        if self._loading:
            return
        self.update_filename()
        self.schedule_preview()

    def schedule_preview(self, delay: int = PREVIEW_DELAY_MS) -> None:
        """Naplánuje přepočet náhledu; opakované volání předchozí plán zruší."""

        self.cancel_preview()
        try:
            self._preview_job = self.after(max(1, int(delay)), self._run_preview)
        except tk.TclError:  # pragma: no cover - okno zaniklo
            self._preview_job = None

    def cancel_preview(self) -> None:
        if self._preview_job is None:
            return
        try:
            self.after_cancel(self._preview_job)
        except (tk.TclError, ValueError):  # pragma: no cover
            pass
        self._preview_job = None

    def _run_preview(self) -> None:
        """Přepočet po pauze v psaní — u delší šablony trvá stovky ms.

        Proto běží ve vlákně: hlavní vlákno musí zůstat volné, jinak by se
        okno při každé pauze v psaní zaseklo. Výsledek staršího běhu se
        zahodí, aby nepřepsal novější náhled.
        """

        self._preview_job = None
        if self.meta is None or not self._docx:
            self.update_preview()
            return
        try:
            snapshot = self._snapshot()
        except Exception as exc:  # noqa: BLE001 - náhled nesmí shodit aplikaci
            self._set_preview_text(f"Náhled se nepodařilo sestavit: {exc}", ())
            return
        self._preview_seq += 1
        generation = self._preview_seq

        def work() -> tuple[FillReport, str, list[str]]:
            filled, report, inserted = self._build(snapshot)
            return report, extract_text(filled), inserted

        def done(result: tuple[FillReport, str, list[str]]) -> None:
            if generation != self._preview_seq:
                return  # mezitím přišel novější náhled
            report, text, inserted = result
            self._set_preview_text(text, sorted(inserted, key=len, reverse=True))
            self._show_unfilled(report)

        def failed(exc: BaseException) -> None:
            if generation != self._preview_seq:
                return
            self._set_preview_text(f"Náhled se nepodařilo sestavit: {exc}", ())
            self.unfilled_label.configure(text="")

        widgets.run_in_thread(self, work, done, failed, name="dlg-preview")

    def update_preview(self) -> None:
        """Přepočítá náhled hned (bez čekání na zpoždění)."""

        self.cancel_preview()
        self._preview_seq += 1  # výsledek běžícího vlákna už je neplatný
        if self.meta is None or not self._docx:
            self._set_preview_text("", ())
            self.unfilled_label.configure(text="")
            return

        try:
            filled, report, inserted = self._fill()
            text = extract_text(filled)
        except Exception as exc:  # noqa: BLE001 - náhled nesmí shodit aplikaci
            self._set_preview_text(f"Náhled se nepodařilo sestavit: {exc}", ())
            self.unfilled_label.configure(text="")
            return

        # Delší hodnoty se zvýrazňují první, aby kratší nepřebila jejich část.
        self._set_preview_text(text, sorted(inserted, key=len, reverse=True))
        self._show_unfilled(report)

    def _snapshot(self) -> dict[str, Any]:
        """Posbírá vše potřebné z widgetů — jediná část, která sahá na Tk."""

        assert self.meta is not None
        settings = self.settings()
        return {
            "docx": self._docx,
            "values": mapping.apply_fields(self.meta.fields, self.values()),
            "drop": self.dropped_paragraph_ids(),
            "clear_highlight": bool(getattr(settings, "clear_highlight", True)),
            "keep_unfilled": bool(getattr(settings, "keep_unfilled", True)),
        }

    @staticmethod
    def _build(snapshot: Mapping[str, Any]) -> tuple[bytes, FillReport, list[str]]:
        """Sestaví dopis ze snímku formuláře. Nesahá na Tk — smí běžet ve vlákně."""

        values = dict(snapshot["values"])
        filled, report = fill_docx(
            snapshot["docx"],
            values,
            drop_paragraphs=snapshot["drop"],
            clear_highlight=snapshot["clear_highlight"],
            keep_unfilled=snapshot["keep_unfilled"],
        )
        inserted = sorted({v for v in values.values() if v.strip()})
        return filled, report, inserted

    def _fill(self) -> tuple[bytes, FillReport, list[str]]:
        """Sestaví dopis z aktuálního formuláře. Vrací i dosazené hodnoty."""

        return self._build(self._snapshot())

    def preview_text(self) -> str:
        """Text, který je právě v náhledu (kvůli testům a app.py)."""

        try:
            return self.preview.get("1.0", "end-1c")
        except tk.TclError:  # pragma: no cover
            return ""

    def _set_preview_text(self, text: str, highlights: "tuple[str, ...] | list[str]") -> None:
        try:
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            if text:
                self.preview.insert("1.0", text)
            self._highlight(highlights)
            self.preview.configure(state="disabled")
        except tk.TclError:  # pragma: no cover
            pass

    def _highlight(self, values: "tuple[str, ...] | list[str]") -> None:
        """Zvýrazní v náhledu místa, kam se dosadily hodnoty.

        Víceřádkové hodnoty se hledají po řádcích — ``Text.search`` přes
        zalomení řádku neumí.
        """

        self.preview.tag_remove(FILLED_TAG, "1.0", "end")
        marked = 0
        for value in self._highlight_needles(values):
            needle = value
            if len(needle) < 2:
                continue
            start = "1.0"
            while marked < MAX_HIGHLIGHTS:
                try:
                    found = self.preview.search(needle, start, stopindex="end", exact=True)
                except tk.TclError:  # pragma: no cover
                    break
                if not found:
                    break
                end = f"{found}+{len(needle)}c"
                self.preview.tag_add(FILLED_TAG, found, end)
                marked += 1
                start = end
            if marked >= MAX_HIGHLIGHTS:
                break

    @staticmethod
    def _highlight_needles(values: "tuple[str, ...] | list[str]") -> list[str]:
        """Hodnoty rozpadlé na jednotlivé řádky, nejdelší první."""

        needles: set[str] = set()
        for value in values:
            for line in str(value or "").splitlines():
                text = line.strip()
                if len(text) >= 2:
                    needles.add(text)
        return sorted(needles, key=len, reverse=True)

    def unfilled_placeholders(self, report: FillReport | None = None) -> list[str]:
        """Popisky míst, která by v dokumentu zůstala nevyplněná."""

        if report is None:
            try:
                _bytes, report, _values = self._fill()
            except Exception:  # noqa: BLE001
                return []
        out: list[str] = []
        for pid in report.unfilled:
            placeholder = self.scan.placeholder(pid) if self.scan is not None else None
            out.append(widgets.shorten(placeholder.raw, 60) if placeholder is not None else pid)
        return out

    def _show_unfilled(self, report: FillReport) -> None:
        names = self.unfilled_placeholders(report)
        if not names:
            self.unfilled_label.configure(
                text="Všechna místa v dokumentu jsou vyplněná.", style="Uspech.TLabel"
            )
            return
        listed = ", ".join(names[:MAX_LISTED_UNFILLED])
        if len(names) > MAX_LISTED_UNFILLED:
            listed += f" a další ({len(names) - MAX_LISTED_UNFILLED})"
        self.unfilled_label.configure(
            text=f"Nevyplněných míst: {len(names)} — {listed}", style="Napoveda.TLabel"
        )

    # ------------------------------------------------------------------
    # název souboru
    # ------------------------------------------------------------------
    def _on_filename_typed(self, _event: Any = None) -> None:
        """Za ruční zásah se počítá jen skutečná změna textu.

        ``<KeyRelease>`` chodí i pro šipky, Home/End a modifikátory a
        ``<<Paste>>``/``<<Cut>>`` se doručí ještě před vložením textu — proto
        se obsah pole porovnává až po zpracování události.
        """

        try:
            self.after_idle(self._check_filename_edited)
        except tk.TclError:  # pragma: no cover - okno zaniklo
            self._check_filename_edited()

    def _check_filename_edited(self) -> None:
        try:
            current = self.filename_var.get()
        except tk.TclError:  # pragma: no cover - okno zaniklo
            return
        if current != self._filename_auto:
            self._filename_touched = True

    def update_output_hint(self) -> None:
        """Osvěží popisek s cílovou složkou (nezávisle na názvu souboru).

        Vlastní metoda schválně: při ručně zadaném názvu se ``update_filename``
        vrací dřív, takže by se popisek po změně výstupní složky v Nastavení
        už nikdy neopravil.
        """

        try:
            self.output_label.configure(text=f"Uloží se do složky: {self.output_dir()}")
        except tk.TclError:  # pragma: no cover
            pass

    def update_filename(self, *, force: bool = False) -> str:
        """Přepočítá název souboru ze vzoru — dokud do pole uživatel nesáhl."""

        self.update_output_hint()
        if self.meta is None:
            return ""
        if self._filename_touched and not force:
            return self.filename_var.get()
        stem = naming.render_pattern(
            self.meta.output_pattern,
            self.values(),
            template_name=self.meta.name,
            today=date.today(),
        )
        self.filename_var.set(stem)
        self._filename_auto = stem
        if force:
            self._filename_touched = False
        return stem

    def filename_stem(self) -> str:
        """Očištěný název souboru bez přípony."""

        return naming.sanitize_filename(self.filename_var.get())

    # ------------------------------------------------------------------
    # kontrola
    # ------------------------------------------------------------------
    def validate_form(self) -> list[str]:
        """Zkontroluje povinná pole a data; vrací seznam českých hlášek."""

        problems: list[str] = []
        first_bad: widgets.LabeledField | None = None
        for spec in self._specs:
            widget = self._fields.get(spec.key)
            if widget is None:  # pragma: no cover
                continue
            message = widget.validate()
            if message:
                problems.append(f"{spec.label or spec.key}: {message}")
                if first_bad is None:
                    first_bad = widget
        if first_bad is not None:
            try:
                first_bad.focus_set()
            except tk.TclError:  # pragma: no cover
                pass
        return problems

    # ------------------------------------------------------------------
    # generování
    # ------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        """Běží právě generování?"""

        return self._busy

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        for key in ("generovat", "generovat_otevrit", "slozka"):
            self.toolbar.set_enabled(key, not self._busy)

    def generate(self, open_after: bool | None = None) -> bool:
        """Vyplní šablonu a uloží dopis. Vrací ``True``, když se práce rozběhla."""

        if self._busy:
            return False
        if self.meta is None or not self._docx:
            widgets.show_warning(self, "Nejdřív vyberte šablonu.")
            return False

        problems = self.validate_form()
        if problems:
            summary = "\n".join(f"• {p}" for p in problems[:8])
            if len(problems) > 8:
                summary += f"\n• … a další ({len(problems) - 8})"
            widgets.show_error(
                self,
                f"Formulář není vyplněný — chybí {widgets.plural_places(len(problems))}.",
                detail=summary,
            )
            self.status.error(f"Vyplňte povinná pole ({len(problems)}).")
            return False

        try:
            snapshot = self._snapshot()
        except Exception as exc:  # noqa: BLE001
            self.status.error(str(exc))
            widgets.show_error(self, "Dopis se nepodařilo sestavit.", detail=str(exc))
            return False

        settings = self.settings()
        directory = self.output_dir()
        stem = self.filename_stem()
        values = self.values()
        warning = self._mapping_warning
        should_open = (
            bool(getattr(settings, "open_after_generate", True))
            if open_after is None
            else bool(open_after)
        )

        def build_failed(exc: BaseException) -> None:
            self._set_busy(False)
            self.status.idle()
            self.status.error(str(exc))
            widgets.show_error(self, "Dopis se nepodařilo sestavit.", detail=str(exc))

        def built(result: tuple[bytes, FillReport, list[str]]) -> None:
            filled, report, _inserted = result
            remaining = self.unfilled_placeholders(report)
            question: list[str] = []
            if warning:
                question.append(warning)
            if remaining:
                listed = ", ".join(remaining[:MAX_LISTED_UNFILLED])
                if len(remaining) > MAX_LISTED_UNFILLED:
                    listed += f" a další ({len(remaining) - MAX_LISTED_UNFILLED})"
                question.append(f"Konkrétně: {listed}")
            if question:
                headline = (
                    f"V dokumentu zůstane {widgets.plural_places(len(remaining))} nevyplněných."
                    if remaining
                    else "Šablona se od posledního mapování změnila."
                )
                question.append("Chcete dopis přesto vygenerovat?")
                if not widgets.ask_yes_no(
                    self,
                    headline,
                    detail="\n\n".join(question),
                    title="Nevyplněná místa" if remaining else "Upravená šablona",
                    default_yes=True,
                ):
                    self._set_busy(False)
                    self.status.idle()
                    self.status.set("Generování zrušeno.")
                    return

            chce_pdf = self.wants_pdf()

            def save() -> tuple[Path, "Path | None", str]:
                # Dopis se uloží VŽDYCKY; PDF je nadstavba. Když převod selže,
                # vrátíme důvod a .docx zůstane, jak byl — nesmí se stát, že
                # kvůli chybějícímu Wordu přijde uživatel o hotový dopis.
                path = _save_docx(directory, stem, filled)
                if not chce_pdf:
                    return path, None, ""
                from .. import pdf as pdf_module  # importuje se až tady

                try:
                    return path, pdf_module.convert(path), ""
                except Exception as exc:  # noqa: BLE001 - PDF nesmí shodit uložení
                    return path, None, str(exc)

            def done(result: tuple[Path, "Path | None", str]) -> None:
                path, pdf_path, pdf_problem = result
                self._finish(
                    path, report, values, should_open,
                    pdf_path=pdf_path, pdf_problem=pdf_problem,
                )

            def failed(exc: BaseException) -> None:
                self._set_busy(False)
                self.status.idle()
                detail = _describe_error(exc)
                self.status.error(detail)
                widgets.show_error(self, "Dopis se nepodařilo uložit.", detail=detail)

            widgets.run_in_thread(self, save, done, failed, name="dlg-generate")

        # Sestavení dopisu je u delší šablony práce na stovky ms — do vlákna
        # patří celé, ne až samotný zápis souboru.
        self._set_busy(True)
        self.status.busy("Generuji dopis…")
        widgets.run_in_thread(
            self,
            lambda: self._build(snapshot),
            built,
            build_failed,
            name="dlg-fill",
        )
        return True

    def _finish(
        self,
        path: Path,
        report: FillReport,
        values: Mapping[str, str],
        should_open: bool,
        *,
        pdf_path: "Path | None" = None,
        pdf_problem: str = "",
    ) -> None:
        self._set_busy(False)
        self.status.idle()
        self.last_output_path = Path(path)
        self.last_report = report
        # hodnoty byly použité — zavření aplikace se na ně už ptát nemusí
        self._baseline = (
            self.values(),
            {pid: bool(var.get()) for pid, var in self._paragraph_vars},
        )

        if self.history is not None:
            try:
                self.history.remember(values)
            except Exception:  # noqa: BLE001 - historie je jen pohodlí
                pass
            self._refresh_suggestions()

        self.last_pdf_path = Path(pdf_path) if pdf_path else None
        if pdf_path:
            self.status.success(f"Hotovo — dopis i PDF jsou uložené v {path.parent}.")
        else:
            self.status.success(f"Hotovo — dopis je uložený v {path}.")

        notes = list(report.warnings)
        if pdf_problem:
            notes.append(pdf_problem)
        remaining = self.unfilled_placeholders(report)
        if remaining:
            listed = ", ".join(remaining[:MAX_LISTED_UNFILLED])
            if len(remaining) > MAX_LISTED_UNFILLED:
                listed += f" a další ({len(remaining) - MAX_LISTED_UNFILLED})"
            notes.append(
                f"V dokumentu zůstalo {widgets.plural_places(len(remaining))} nevyplněných: {listed}"
            )
        if notes:
            widgets.show_warning(
                self,
                f"Dopis je uložený:\n{path}",
                detail="\n".join(notes),
                title="Dopis vygenerován s výhradami",
            )

        if should_open and not open_path(path):
            self.status.error(f"Soubor {path} se nepodařilo otevřít.")

        _invoke(self.on_generated, self.last_output_path)

    def _refresh_suggestions(self) -> None:
        """Po zapsání do historie doplní našeptávače o nově použité hodnoty."""

        for spec in self._specs:
            if spec.type != "text":
                continue
            widget = self._fields.get(spec.key)
            if isinstance(widget, widgets.LabeledCombobox):
                widget.set_values(self._suggestions(spec.key))

    def open_output_dir(self) -> bool:
        """Otevře složku s vygenerovanými dopisy ve správci souborů."""

        directory = self.output_dir()
        try:
            config.ensure_dir(directory)
        except Exception as exc:  # noqa: BLE001
            self.status.error(str(exc))
            widgets.show_error(self, "Složku s výstupy se nepodařilo otevřít.", detail=str(exc))
            return False
        if not open_path(directory):
            self.status.error(f"Složku {directory} se nepodařilo otevřít.")
            return False
        self.status.set(f"Otevřel jsem složku {directory}.")
        return True

    # ------------------------------------------------------------------
    def destroy(self) -> None:  # noqa: D102 - viz tkinter
        self.cancel_preview()
        super().destroy()
