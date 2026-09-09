"""Okno aplikace: svislá navigace, přepínání pohledů, zkratky a ošetření chyb.

Modul sešívá dohromady všechno ostatní:

* :class:`dlg.store.TemplateStore` a :class:`dlg.store.ValueHistory` (data),
* :mod:`dlg.config` (nastavení a cesty),
* pohledy z :mod:`dlg.ui` (Šablony, Pole šablony, Generovat, Nastavení).

Pohledy vznikají **líně** — až když je uživatel poprvé otevře — a pak se drží
jedna jediná instance každého z nich, takže se rozdělaná práce (rozepsaný
formulář, rozeditovaná pole) přepnutím na jiný pohled neztratí.

Chyba nesmí aplikaci shodit: výjimka z callbacku Tk (``report_callback_exception``)
i výjimka mimo něj (``sys.excepthook``) skončí v českém chybovém okně, ze
kterého jde podrobnosti zkopírovat do schránky.

Spuštění::

    python -m dlg          # dlg/__main__.py zavolá dlg.app.main()

Velikost a pozice okna se pamatují v souboru ``window.json`` v datové složce
aplikace (vedle ``settings.json``). Záměrně to není součást
:class:`dlg.config.Settings` — do nastavení, které si uživatel edituje
v pohledu *Nastavení*, poloha okna nepatří a nemá se s ním přepisovat.
"""

from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import tkinter as tk
from tkinter import ttk

from . import config
from .config import Settings
from .store import TemplateStore, ValueHistory
from .ui import theme, widgets
from .version import APP_NAME, __version__

__all__ = [
    "App",
    "DEFAULT_SIZE",
    "ErrorDialog",
    "NAV_FIELDS",
    "NAV_GENERATE",
    "NAV_ITEMS",
    "NAV_SETTINGS",
    "NAV_TEMPLATES",
    "START_ERROR_LOG_NAME",
    "TK_MISSING_MESSAGE",
    "WINDOW_FILE_NAME",
    "app_icon_path",
    "apply_icon",
    "format_exception",
    "load_window_state",
    "main",
    "report_exception",
    "resource_path",
    "sanitize_geometry",
    "save_window_state",
    "window_state_path",
]


# ---------------------------------------------------------------------------
# klíče pohledů a texty
# ---------------------------------------------------------------------------

NAV_TEMPLATES = "sablony"
NAV_FIELDS = "pole"
NAV_GENERATE = "generovat"
NAV_SETTINGS = "nastaveni"

#: Položky svislé navigace v pořadí, v jakém se zobrazují.
NAV_ITEMS: tuple[tuple[str, str], ...] = (
    (NAV_TEMPLATES, "Šablony"),
    (NAV_FIELDS, "Pole šablony"),
    (NAV_GENERATE, "Generovat"),
    (NAV_SETTINGS, "Nastavení"),
)

#: Pohledy, které jdou otevřít jen s vybranou šablonou.
NEEDS_TEMPLATE: tuple[str, ...] = (NAV_FIELDS,)

#: Šířka svislé navigace ve znacích (ttk.Button měří šířku v písmenech).
NAV_BUTTON_WIDTH = 18

#: Uvítání při úplně prvním spuštění (v knihovně není žádná šablona).
FIRST_RUN_STATUS = (
    "Vítejte! Zatím nemáte žádnou šablonu — nahrajte svůj vzor dopisu .docx "
    "tlačítkem „Nahrát šablonu…“ (nebo klávesami Ctrl+O)."
)
#: Hláška, když se uživatel snaží upravovat pole bez vybrané šablony.
NO_TEMPLATE_MESSAGE = "Nejdřív vyberte šablonu v části Šablony."

TK_MISSING_MESSAGE = (
    "Chybí knihovna tkinter, bez které nelze zobrazit okno aplikace.\n"
    "Na Windows ji doinstalujete opravou instalace Pythonu "
    "(volba „tcl/tk and IDLE“), na Linuxu balíčkem python3-tk."
)
DISPLAY_MISSING_MESSAGE = (
    "Okno aplikace se nepodařilo otevřít — grafické prostředí není k dispozici."
)
START_FAILED_MESSAGE = "Aplikaci se nepodařilo spustit."

#: Nadpis chybového okna.
ERROR_TITLE = f"{APP_NAME} — neočekávaná chyba"
#: Vysvětlení v chybovém okně.
ERROR_INTRO = (
    "V programu nastala chyba. Aplikace běží dál — rozdělaná práce zůstala "
    "zachovaná. Pokud se chyba opakuje, zkopírujte podrobnosti tlačítkem níž "
    "a pošlete je vývojáři."
)


# ---------------------------------------------------------------------------
# soubory vedle aplikace (PyInstaller) a ikona
# ---------------------------------------------------------------------------

#: Ikona okna — v repozitáři i v balíčku z PyInstalleru leží ve složce assets.
ICON_RELATIVE: tuple[str, ...] = ("assets", "app.ico")


def resource_path(*parts: str) -> Path:
    """Cesta k souboru dodávanému s programem (ikona, …).

    Pod PyInstallerem jsou data rozbalená v dočasné složce ``sys._MEIPASS``,
    při běhu ze zdrojáků leží v kořeni projektu vedle balíčku ``dlg``.
    Funkce **nikdy** neurčuje, kam se zapisuje — od toho je :mod:`dlg.config`.
    """

    base = getattr(sys, "_MEIPASS", "")
    root = Path(str(base)) if base else Path(__file__).resolve().parent.parent
    return root.joinpath(*[str(part) for part in parts])


def app_icon_path() -> Path | None:
    """Cesta k ``assets/app.ico``, pokud soubor existuje."""

    try:
        path = resource_path(*ICON_RELATIVE)
        return path if path.is_file() else None
    except OSError:  # pragma: no cover - nedostupná cesta
        return None


def apply_icon(window: Any) -> bool:
    """Na Windows nastaví ikonu okna; jinde a při chybě mlčky nedělá nic.

    Formát ``.ico`` umí ``iconbitmap`` spolehlivě jen na Windows — na Linuxu
    by volání skončilo chybou, a kvůli ikoně se aplikace spouštět nepřestane.
    """

    if not theme.is_windows():
        return False
    path = app_icon_path()
    if path is None:
        return False
    setter = getattr(window, "iconbitmap", None)
    if not callable(setter):  # pragma: no cover - pojistka
        return False
    try:  # pragma: no cover - běží jen na Windows
        setter(str(path))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# velikost a pozice okna
# ---------------------------------------------------------------------------

#: Soubor s velikostí a pozicí okna (v datové složce aplikace).
WINDOW_FILE_NAME = "window.json"
#: Výchozí velikost okna při prvním spuštění.
DEFAULT_SIZE: tuple[int, int] = (1120, 720)

_GEOMETRY_RE = re.compile(r"^\s*(\d{2,5})x(\d{2,5})(?:\+(-?\d{1,6})\+(-?\d{1,6}))?\s*$")


def window_state_path(path: Path | None = None) -> Path:
    """Kam se ukládá velikost a pozice okna."""

    return Path(path) if path is not None else config.app_home() / WINDOW_FILE_NAME


def parse_geometry(text: str) -> tuple[int, int, int | None, int | None] | None:
    """Z ``"1120x720+40+30"`` udělá ``(1120, 720, 40, 30)``; nesmysl vrátí ``None``."""

    match = _GEOMETRY_RE.match(str(text or ""))
    if match is None:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return None
    if match.group(3) is None:
        return (width, height, None, None)
    return (width, height, int(match.group(3)), int(match.group(4)))


def sanitize_geometry(
    text: str,
    *,
    screen: tuple[int, int],
    minimum: tuple[int, int] = theme.WINDOW_MIN_SIZE,
) -> str:
    """Ořízne uloženou geometrii tak, aby se okno vešlo na obrazovku.

    Vrací prázdný řetězec, když se zápis nedá použít vůbec (poškozený soubor,
    nesmyslné rozměry). Díky tomu se aplikace neotevře mimo viditelnou plochu
    ani menší, než je použitelné minimum — typicky po odpojení druhého monitoru.
    """

    parsed = parse_geometry(text)
    if parsed is None:
        return ""

    width, height, x, y = parsed
    screen_w = max(int(screen[0]), minimum[0])
    screen_h = max(int(screen[1]), minimum[1])

    width = max(minimum[0], min(int(width), screen_w))
    height = max(minimum[1], min(int(height), screen_h))
    if x is None or y is None:
        return f"{width}x{height}"

    x = max(0, min(int(x), max(0, screen_w - width)))
    y = max(0, min(int(y), max(0, screen_h - height)))
    return f"{width}x{height}+{x}+{y}"


def load_window_state(path: Path | None = None) -> dict[str, Any]:
    """Načte uloženou geometrii okna. Poškozený soubor = výchozí stav."""

    data = config.read_json(window_state_path(path), default=None, strict=False)
    if not isinstance(data, Mapping):
        return {"geometry": "", "zoomed": False}
    return {
        "geometry": str(data.get("geometry") or ""),
        "zoomed": bool(data.get("zoomed", False)),
    }


def save_window_state(state: Mapping[str, Any], path: Path | None = None) -> bool:
    """Uloží geometrii okna. Vrací ``False``, když se zápis nepovedl.

    Neúspěch je ošetřený mlčky — kvůli nezapsané poloze okna se aplikace při
    zavírání nesmí zaseknout ani zahlásit chybu.
    """

    payload = {
        "geometry": str(state.get("geometry") or ""),
        "zoomed": bool(state.get("zoomed", False)),
    }
    if not payload["geometry"]:
        return False
    try:
        config.write_json_atomic(window_state_path(path), payload)
    except Exception:  # noqa: BLE001 - viz docstring
        return False
    return True


# ---------------------------------------------------------------------------
# chybové okno
# ---------------------------------------------------------------------------


def format_exception(exc_type: Any, value: Any, tb: Any) -> str:
    """Výjimka jako text pro vývojáře (traceback + hlavička s verzemi)."""

    header = (
        f"{APP_NAME} {__version__}\n"
        f"Python {sys.version.split()[0]} ({sys.platform})\n"
    )
    try:
        if isinstance(value, BaseException):
            lines = traceback.format_exception(
                type(value), value, value.__traceback__ if tb is None else tb
            )
        else:
            lines = traceback.format_exception(exc_type, value, tb)
        body = "".join(lines).strip()
    except Exception:  # noqa: BLE001 - i formátování chyby může selhat
        body = f"{exc_type}: {value}"
    return f"{header}\n{body}".strip()


def describe_exception(exc_type: Any, value: Any) -> str:
    """Krátký popis chyby do stavového řádku a nadpisu okna."""

    text = str(value or "").strip()
    if text:
        return text
    name = getattr(exc_type, "__name__", None) or str(exc_type)
    return f"chyba typu {name}"


class ErrorDialog(tk.Toplevel):
    """Modální okno s českou hláškou a podrobnostmi ke zkopírování."""

    def __init__(
        self,
        master: tk.Misc | None = None,
        message: str = "",
        detail: str = "",
        *,
        title: str = "",
        intro: str = ERROR_INTRO,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, **kwargs)
        self.detail = str(detail or "")
        self._closed = False

        self.title(title or ERROR_TITLE)
        if isinstance(master, tk.Misc):
            try:
                self.transient(master.winfo_toplevel())
            except tk.TclError:  # pragma: no cover - rodič mezitím zanikl
                pass
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.minsize(520, 320)

        body = ttk.Frame(self, padding=theme.PAD_L)
        body.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        self.message_label = ttk.Label(
            body,
            style="Podnadpis.TLabel",
            text=str(message or "Nastala neočekávaná chyba."),
            wraplength=560,
            justify="left",
        )
        self.message_label.grid(row=0, column=0, sticky="w")

        self.intro_label = ttk.Label(
            body, style="Napoveda.TLabel", text=str(intro), wraplength=560, justify="left"
        )
        self.intro_label.grid(row=1, column=0, sticky="w", pady=(theme.PAD_S, theme.PAD_M))

        holder = ttk.Frame(body)
        holder.grid(row=2, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        self.text = tk.Text(
            holder,
            height=12,
            width=78,
            wrap="none",
            font=theme.font_name(theme.FONT_SMALL, self),
            background=theme.COLOR_BG,
            foreground=theme.COLOR_TEXT,
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        self.text.insert("1.0", self.detail)
        self.text.configure(state="disabled")

        y_scroll = ttk.Scrollbar(holder, orient="vertical", command=self.text.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(holder, orient="horizontal", command=self.text.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.hint = ttk.Label(body, style="Napoveda.TLabel", text="")
        self.hint.grid(row=3, column=0, sticky="w", pady=(theme.PAD_S, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, sticky="e", pady=(theme.PAD_M, 0))
        self.copy_button = ttk.Button(
            buttons, text="Kopírovat podrobnosti", command=self.copy_detail
        )
        self.copy_button.pack(side="left", padx=(0, theme.PAD_S))
        self.close_button = ttk.Button(
            buttons, text="Zavřít", style="Primary.TButton", command=self.close
        )
        self.close_button.pack(side="left")

        self.bind("<Escape>", self._on_key_close, add="+")
        self.bind("<Return>", self._on_key_close, add="+")

    # -- obsah ------------------------------------------------------------
    def detail_text(self) -> str:
        """Text podrobností tak, jak je vidí uživatel (kvůli testům)."""

        try:
            return str(self.text.get("1.0", "end-1c"))
        except tk.TclError:  # pragma: no cover - okno je pryč
            return self.detail

    def copy_detail(self) -> bool:
        """Zkopíruje podrobnosti do schránky. Vrací ``True`` při úspěchu."""

        try:
            self.clipboard_clear()
            self.clipboard_append(self.detail)
            self.update_idletasks()
        except tk.TclError:  # pragma: no cover - schránka nemusí být dostupná
            self.hint.configure(
                text="Podrobnosti se do schránky zkopírovat nepodařilo.", style="Chyba.TLabel"
            )
            return False
        self.hint.configure(
            text="Podrobnosti jsou zkopírované do schránky.", style="Uspech.TLabel"
        )
        return True

    # -- zavření ----------------------------------------------------------
    def _on_key_close(self, _event: Any = None) -> str:
        self.close()
        return "break"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for step in (self.grab_release, self.destroy):
            try:
                step()
            except tk.TclError:  # pragma: no cover - okno už zaniklo
                pass

    def _center_on(self, master: tk.Misc | None) -> None:
        """Vystředí okno nad rodičem (stejně jako ostatní dialogy aplikace)."""

        widgets.center_on(self, master)

    def show(self) -> None:
        """Otevře okno modálně a počká, než ho uživatel zavře."""

        self._center_on(self.master)
        for step in (self.grab_set, self.close_button.focus_set, self.wait_window):
            try:
                step()
            except tk.TclError:  # pragma: no cover - okno zaniklo dřív
                return


def report_exception(parent: tk.Misc | None, exc_type: Any, value: Any, tb: Any) -> None:
    """Ukáže české chybové okno. Když ani to nejde, vypíše chybu na ``stderr``."""

    detail = format_exception(exc_type, value, tb)
    message = f"Nastala neočekávaná chyba: {describe_exception(exc_type, value)}"
    try:
        dialog = ErrorDialog(parent, message, detail)
        dialog.show()
    except Exception:  # noqa: BLE001 - hlášení chyby nesmí vyrobit další chybu
        try:
            print(detail, file=sys.stderr)
        except Exception:  # pragma: no cover - i stderr může být zavřený
            pass


# ---------------------------------------------------------------------------
# okno aplikace
# ---------------------------------------------------------------------------


class App(tk.Tk):
    """Hlavní okno: navigace vlevo, pohled vpravo, stavový řádek dole."""

    def __init__(
        self,
        *,
        store: Any = None,
        history: Any = None,
        settings: Settings | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self.store = store if store is not None else self._make_store()
        self.history = history if history is not None else self._make_history()
        self._settings = settings if settings is not None else self._load_settings()

        self._views: dict[str, ttk.Frame] = {}
        self._nav_buttons: dict[str, ttk.Button] = {}
        self._current: str = ""
        self._template_id: str = str(getattr(self._settings, "last_template_id", "") or "")
        self._fields_need_reload = False
        self._error_visible = False
        self._normal_geometry = ""
        self._closing = False

        self.title(f"{APP_NAME} {__version__}")
        theme.apply_theme(self)
        apply_icon(self)
        self.minsize(*theme.WINDOW_MIN_SIZE)
        self._restore_window_state()

        self._build_layout()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Configure>", self._on_configure, add="+")

        self._previous_excepthook = sys.excepthook
        self._installed_excepthook = self._on_uncaught_exception
        sys.excepthook = self._installed_excepthook

        self._open_initial_view()

    # ------------------------------------------------------------------
    # stavba rozhraní
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        self.configure(background=theme.COLOR_BG)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.nav = ttk.Frame(self, style="Nav.TFrame", padding=(theme.PAD_M, theme.PAD_M))
        self.nav.grid(row=0, column=0, sticky="ns")

        ttk.Label(self.nav, style="Nav.TLabel", text=APP_NAME.upper()).pack(
            anchor="w", padx=theme.PAD_S, pady=(0, theme.PAD_M)
        )

        for key, label in NAV_ITEMS:
            button = ttk.Button(
                self.nav,
                text=label,
                style="Nav.TButton",
                width=NAV_BUTTON_WIDTH,
                command=lambda k=key: self.show_view(k),
            )
            button.pack(fill="x", pady=(0, 2))
            self._nav_buttons[key] = button

        ttk.Label(self.nav, style="Nav.TLabel", text=f"verze {__version__}").pack(
            side="bottom", anchor="w", padx=theme.PAD_S, pady=(theme.PAD_M, 0)
        )

        self.container = ttk.Frame(self, style="TFrame")
        self.container.grid(row=0, column=1, sticky="nsew")
        self.container.columnconfigure(0, weight=1)
        self.container.rowconfigure(0, weight=1)

        self.status_bar = widgets.StatusBar(self)
        self.status_bar.grid(row=1, column=0, columnspan=2, sticky="ew")

    def _bind_shortcuts(self) -> None:
        """Klávesové zkratky platí v celém okně, i když je kurzor ve formuláři."""

        bindings: tuple[tuple[tuple[str, ...], Callable[[Any], Any]], ...] = (
            (("<Control-g>", "<Control-G>"), self._shortcut_generate),
            (("<Control-o>", "<Control-O>"), self._shortcut_import),
            (("<F5>",), self._shortcut_refresh),
            (("<Control-q>", "<Control-Q>"), self._shortcut_quit),
        )
        for sequences, handler in bindings:
            for sequence in sequences:
                self.bind_all(sequence, handler, add="+")

    # ------------------------------------------------------------------
    # nastavení
    # ------------------------------------------------------------------
    @staticmethod
    def _make_store() -> TemplateStore:
        """Knihovna šablon. Rozbité umístění dat nesmí zabránit startu."""

        try:
            return TemplateStore()
        except Exception:  # noqa: BLE001 - nečitelný location.json apod.
            return TemplateStore(config.default_app_home() / config.TEMPLATES_DIR_NAME)

    @staticmethod
    def _make_history() -> ValueHistory:
        try:
            return ValueHistory()
        except Exception:  # noqa: BLE001
            return ValueHistory(config.default_app_home() / config.HISTORY_FILE_NAME)

    def _load_settings(self) -> Settings:
        try:
            return config.load_settings()
        except Exception:  # noqa: BLE001 - rozbité nastavení nesmí zabránit startu
            return Settings(output_dir=str(config.default_output_dir()))

    def settings(self) -> Settings:
        """Aktuální nastavení (pohledy si o ně říkají přes tuhle funkci)."""

        return self._settings

    def save_settings(self, settings: Settings) -> None:
        """Uloží nastavení na disk a zapamatuje si ho pro ostatní pohledy."""

        self._settings = settings
        config.save_settings(settings)

    def known_field_keys(self) -> list[str]:
        """Klíče polí ze všech šablon — nabídka klíčů v profilu (Nastavení)."""

        keys: set[str] = set()
        try:
            metas = list(self.store.list())
        except Exception:  # noqa: BLE001 - nabídka klíčů je jen pohodlí
            return []
        for meta in metas:
            for spec in meta.fields:
                if spec.key:
                    keys.add(spec.key)
        return sorted(keys)

    def remember_template(self, template_id: str) -> None:
        """Zapamatuje si naposledy použitou šablonu (do nastavení)."""

        template_id = str(template_id or "")
        self._template_id = template_id or self._template_id
        if not template_id or self._settings.last_template_id == template_id:
            self._update_nav_state()
            return
        self._settings.last_template_id = template_id
        try:
            config.save_settings(self._settings)
        except Exception:  # noqa: BLE001 - nezapsaná drobnost nesmí nic shodit
            pass
        view = self._views.get(NAV_SETTINGS)
        if view is not None and self._current != NAV_SETTINGS and _alive(view):
            view.refresh()
        self._update_nav_state()

    # ------------------------------------------------------------------
    # stavový řádek
    # ------------------------------------------------------------------
    def set_status(self, text: str) -> None:
        self.status_bar.set(text)

    def set_status_error(self, text: str) -> None:
        self.status_bar.set_error(text)

    def set_status_success(self, text: str) -> None:
        self.status_bar.set_success(text)

    # ------------------------------------------------------------------
    # šablony a navigace
    # ------------------------------------------------------------------
    def template_count(self) -> int:
        """Počet šablon v knihovně; při chybě čtení vrací 0."""

        try:
            return len(list(self.store.list()))
        except Exception as exc:  # noqa: BLE001 - hlášku ukážeme ve stavovém řádku
            self.set_status_error(f"Knihovnu šablon se nepodařilo načíst: {exc}")
            return 0

    def current_template_id(self) -> str:
        """Šablona, se kterou se právě pracuje (výběr v seznamu, formulář, …)."""

        candidates: list[str] = []
        view = self._views.get(NAV_TEMPLATES)
        if view is not None and _alive(view):
            candidates.append(str(view.selected_template_id() or ""))
        view = self._views.get(NAV_FIELDS)
        if view is not None and _alive(view):
            candidates.append(str(view.template_id or ""))
        view = self._views.get(NAV_GENERATE)
        if view is not None and _alive(view):
            candidates.append(str(view.template_id or ""))
        candidates.append(self._template_id)
        return next((c for c in candidates if c), "")

    def nav_button(self, key: str) -> ttk.Button | None:
        """Tlačítko svislé navigace podle klíče pohledu (``None`` = neznámý klíč)."""

        return self._nav_buttons.get(key)

    def _update_nav_state(self, _event: Any = None) -> None:
        """Zvýrazní aktivní položku a zakáže pohledy bez vybrané šablony."""

        has_template = bool(self.current_template_id())
        for key, button in self._nav_buttons.items():
            if not _alive(button):  # pragma: no cover - okno se zavírá
                continue
            button.configure(
                style="Nav.Selected.TButton" if key == self._current else "Nav.TButton"
            )
            enabled = has_template or key not in NEEDS_TEMPLATE
            button.state(["!disabled" if enabled else "disabled"])

    # ------------------------------------------------------------------
    # pohledy
    # ------------------------------------------------------------------
    def view(self, key: str) -> ttk.Frame | None:
        """Už vytvořený pohled, nebo ``None`` (pohledy vznikají líně)."""

        view = self._views.get(key)
        return view if view is not None and _alive(view) else None

    @property
    def current_view_key(self) -> str:
        """Klíč právě zobrazeného pohledu."""

        return self._current

    def _create_view(self, key: str) -> ttk.Frame:
        """Vytvoří pohled. Moduly se importují až tady — start je tím rychlejší."""

        if key == NAV_TEMPLATES:
            from .ui.templates_view import TemplatesView

            return TemplatesView(
                self.container,
                self.store,
                on_edit_fields=self.open_fields,
                on_generate=self.open_generate,
                status=self.set_status,
            )
        if key == NAV_FIELDS:
            from .ui.mapping_view import MappingView

            return MappingView(
                self.container,
                self.store,
                on_done=self._on_fields_done,
                status=self.status_bar,
            )
        if key == NAV_GENERATE:
            from .ui.generate_view import GenerateView

            view = GenerateView(
                self.container,
                self.store,
                self.history,
                settings_getter=self.settings,
                status=self.status_bar,
            )
            view.on_generated = self._on_generated
            return view
        if key == NAV_SETTINGS:
            from .ui.settings_view import SettingsView

            return SettingsView(
                self.container,
                settings_getter=self.settings,
                settings_saver=self.save_settings,
                status=self.set_status,
                known_keys=self.known_field_keys,
                on_data_home_changed=self.reload_data_home,
            )
        raise KeyError(f"Neznámý pohled: {key!r}")

    def _ensure_view(self, key: str) -> ttk.Frame | None:
        """Vrátí jedinou instanci pohledu; první volání ji vyrobí."""

        view = self.view(key)
        if view is not None:
            return view
        try:
            view = self._create_view(key)
        except Exception as exc:  # noqa: BLE001 - rozbitý pohled nesmí shodit okno
            self.set_status_error(f"Pohled se nepodařilo otevřít: {exc}")
            report_exception(self, type(exc), exc, exc.__traceback__)
            return None
        view.grid(row=0, column=0, sticky="nsew")
        view.grid_remove()
        self._views[key] = view
        if key == NAV_TEMPLATES:
            # Výběr v seznamu rozhoduje o dostupnosti pohledu „Pole šablony“.
            view.tree.bind("<<TreeviewSelect>>", self._update_nav_state, add="+")
        return view

    def show_view(self, key: str, *, template_id: str = "", confirm: bool = True) -> bool:
        """Přepne na pohled ``key``. Vrací ``True``, když se přepnutí povedlo."""

        if key not in dict(NAV_ITEMS):
            return False

        wanted = str(template_id or "")
        if key in NEEDS_TEMPLATE:
            wanted = wanted or self.current_template_id()
            if not wanted:
                widgets.show_warning(self, NO_TEMPLATE_MESSAGE)
                self.set_status(NO_TEMPLATE_MESSAGE)
                if self._current != NAV_TEMPLATES:
                    self.show_view(NAV_TEMPLATES)
                return False

        if confirm and not self._confirm_leave(key):
            return False

        view = self._ensure_view(key)
        if view is None:
            return False

        previous = self.view(self._current) if self._current else None
        if previous is not None and previous is not view:
            previous.grid_remove()

        view.grid()
        self._current = key
        self._activate_view(key, view, wanted)
        self._update_nav_state()
        try:
            view.focus_set()
        except tk.TclError:  # pragma: no cover - okno se zavírá
            pass
        return True

    def _activate_view(self, key: str, view: Any, template_id: str) -> None:
        """Doplní pohledu čerstvá data podle toho, co se mezitím změnilo."""

        try:
            if key == NAV_TEMPLATES:
                view.refresh()
                if template_id:
                    view.select(template_id)
            elif key == NAV_FIELDS:
                if self._fields_need_reload or view.template_id != template_id:
                    if view.load(template_id):
                        self._fields_need_reload = False
                        self.remember_template(template_id)
            elif key == NAV_GENERATE:
                view.refresh_templates()
                if template_id and view.template_id != template_id:
                    # i tudy se dá přijít o rozepsaný formulář
                    if view.confirm_discard("Přepnutím na jinou šablonu o ně přijdete."):
                        view.load(template_id)
                elif not view.template_id:
                    view.load()
                # výstupní složka se mohla mezitím změnit v Nastavení
                view.update_output_hint()
                if view.template_id:
                    self.remember_template(view.template_id)
            elif key == NAV_SETTINGS:
                view.refresh()
        except Exception as exc:  # noqa: BLE001 - data pohledu nesmí shodit okno
            self.set_status_error(str(exc))
            report_exception(self, type(exc), exc, exc.__traceback__)

    def _confirm_leave(self, key: str) -> bool:
        """Odchod z rozeditovaných polí se hlídá dotazem na neuložené změny."""

        if key == self._current or self._current != NAV_FIELDS:
            return True
        view = self.view(NAV_FIELDS)
        if view is None:
            return True
        try:
            return bool(view.confirm_leave())
        except tk.TclError:  # pragma: no cover - okno se zavírá
            return True

    # -- navigační zkratky pro pohledy -----------------------------------
    def open_templates(self, template_id: str = "") -> bool:
        return self.show_view(NAV_TEMPLATES, template_id=template_id)

    def open_fields(self, template_id: str = "") -> bool:
        """Otevře úpravu polí (volá se i z pohledu Šablony)."""

        return self.show_view(NAV_FIELDS, template_id=template_id)

    def open_generate(self, template_id: str = "") -> bool:
        """Otevře generování dopisu (volá se i z pohledu Šablony)."""

        return self.show_view(NAV_GENERATE, template_id=template_id)

    def open_settings(self) -> bool:
        return self.show_view(NAV_SETTINGS)

    def _on_fields_done(self, saved: bool = False) -> None:
        """Odchod z pohledu „Pole šablony“ — zpátky na seznam šablon."""

        self._fields_need_reload = True
        template_id = self.current_template_id()
        self.show_view(NAV_TEMPLATES, template_id=template_id, confirm=False)
        if saved:
            self.set_status_success("Pole šablony jsou uložená.")

    def _on_generated(self, path: Any = None) -> None:
        """Po vygenerování dopisu si zapamatujeme použitou šablonu."""

        view = self.view(NAV_GENERATE)
        if view is not None:
            self.remember_template(view.template_id)

    def _open_initial_view(self) -> None:
        """První spuštění: bez šablon rovnou Šablony i s vysvětlením."""

        if self.template_count() == 0:
            self.show_view(NAV_TEMPLATES)
            self.set_status(FIRST_RUN_STATUS)
            return
        if not self.show_view(NAV_GENERATE):  # pragma: no cover - pojistka
            self.show_view(NAV_TEMPLATES)

    # ------------------------------------------------------------------
    # klávesové zkratky
    # ------------------------------------------------------------------
    def _shortcut_generate(self, _event: Any = None) -> str:
        """Ctrl+G — přepne na Generovat; podruhé dopis rovnou vygeneruje."""

        view = self.view(NAV_GENERATE)
        if self._current == NAV_GENERATE and view is not None and not view.busy:
            view.generate()
        else:
            self.show_view(NAV_GENERATE)
        return "break"

    def _shortcut_import(self, _event: Any = None) -> str:
        """Ctrl+O — nahrání šablony (z libovolného pohledu)."""

        if not self.show_view(NAV_TEMPLATES):
            return "break"
        view = self.view(NAV_TEMPLATES)
        if view is not None:
            view.import_template()
        return "break"

    def _shortcut_refresh(self, _event: Any = None) -> str:
        """F5 — znovu načte data z disku."""

        self.refresh()
        return "break"

    def _shortcut_quit(self, _event: Any = None) -> str:
        """Ctrl+Q — zavřít aplikaci."""

        self.close()
        return "break"

    def reload_data_home(self) -> None:
        """Uživatel zvolil jinou složku s daty — přepnout na ni knihovnu i historii."""

        try:
            self.store.rebind()
            self.history.rebind()
        except Exception as exc:  # noqa: BLE001 - hlášku ukážeme ve stavovém řádku
            self.set_status_error(f"Přepnutí na novou složku selhalo: {exc}")
            return

        # Nastavení leží uvnitř složky s daty, takže se mění spolu s ní.
        self._settings = self._load_settings()
        self._template_id = ""

        # Pohledy vázané na starou knihovnu je nutné vyrobit znovu.
        for key in (NAV_TEMPLATES, NAV_FIELDS, NAV_GENERATE):
            view = self._views.pop(key, None)
            if view is not None and _alive(view):
                try:
                    view.destroy()
                except Exception:  # noqa: BLE001 - zanikající widget nás nezajímá
                    pass

        if self._current in (NAV_FIELDS, NAV_GENERATE):
            self.show_view(NAV_TEMPLATES)
        else:
            view = self._ensure_view(self._current)
            if view is not None:
                self._activate_view(self._current, view, "")
        self._update_nav_state()

    def refresh(self) -> None:
        """Zahodí mezipaměti, načte nastavení z disku a překreslí pohled."""

        try:
            self.store.invalidate_scan()
        except Exception:  # noqa: BLE001 - mezipaměť je jen zrychlení
            pass
        self._settings = self._load_settings()

        view = self.view(self._current)
        if view is not None:
            self._activate_view(self._current, view, self.current_template_id())
        self._update_nav_state()
        self.set_status("Data jsou znovu načtená.")

    # ------------------------------------------------------------------
    # velikost a pozice okna
    # ------------------------------------------------------------------
    def screen_size(self) -> tuple[int, int]:
        try:
            return (int(self.winfo_screenwidth()), int(self.winfo_screenheight()))
        except tk.TclError:  # pragma: no cover - rozbitý displej
            return (1920, 1080)

    def default_geometry(self) -> str:
        """Výchozí velikost okna vystředěná na obrazovce."""

        screen_w, screen_h = self.screen_size()
        width = max(theme.WINDOW_MIN_SIZE[0], min(DEFAULT_SIZE[0], screen_w))
        height = max(theme.WINDOW_MIN_SIZE[1], min(DEFAULT_SIZE[1], screen_h))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        return f"{width}x{height}+{x}+{y}"

    def _restore_window_state(self) -> None:
        state = load_window_state()
        geometry = sanitize_geometry(state.get("geometry", ""), screen=self.screen_size())
        if not geometry:
            geometry = self.default_geometry()
        try:
            self.geometry(geometry)
        except tk.TclError:  # pragma: no cover - nesmyslná geometrie
            pass
        self._normal_geometry = geometry
        if state.get("zoomed"):
            self.maximize()

    def maximize(self) -> bool:
        """Roztáhne okno přes obrazovku (Windows i X11 to dělají jinak)."""

        try:
            self.state("zoomed")
            return True
        except tk.TclError:
            pass
        try:  # X11
            self.attributes("-zoomed", True)
            return True
        except tk.TclError:  # pragma: no cover - okenní správce to neumí
            return False

    def is_zoomed(self) -> bool:
        try:
            if str(self.state()) == "zoomed":
                return True
        except tk.TclError:  # pragma: no cover
            return False
        try:
            return bool(self.attributes("-zoomed"))
        except tk.TclError:
            return False

    def _on_configure(self, event: Any = None) -> None:
        """Průběžně si pamatuje velikost okna v „normálním“ (nezvětšeném) stavu."""

        if event is not None and getattr(event, "widget", None) is not self:
            return
        if self._closing or self.is_zoomed():
            return
        geometry = self.current_geometry()
        if geometry:
            self._normal_geometry = geometry

    def current_geometry(self) -> str:
        """Velikost a pozice okna jako ``"1120x720+40+30"``."""

        try:
            width = int(self.winfo_width())
            height = int(self.winfo_height())
            x = int(self.winfo_x())
            y = int(self.winfo_y())
        except tk.TclError:  # pragma: no cover - okno je pryč
            return ""
        if width <= 1 or height <= 1:
            return ""
        return f"{width}x{height}+{max(0, x)}+{max(0, y)}"

    def window_state(self) -> dict[str, Any]:
        """Stav okna k uložení — u zvětšeného okna se pamatuje původní velikost."""

        zoomed = self.is_zoomed()
        if zoomed:
            geometry = self._normal_geometry
        else:
            geometry = self.current_geometry() or self._normal_geometry
        return {"geometry": geometry, "zoomed": zoomed}

    def remember_window_state(self) -> bool:
        """Uloží velikost a pozici okna do datové složky aplikace."""

        return save_window_state(self.window_state())

    # ------------------------------------------------------------------
    # ošetření chyb
    # ------------------------------------------------------------------
    def report_callback_exception(self, exc: Any, val: Any, tb: Any) -> None:  # noqa: N802
        """Výjimka z callbacku Tk — Tk ji posílá sem místo pádu aplikace."""

        self.report_error(exc, val, tb)

    def _on_uncaught_exception(self, exc_type: Any, value: Any, tb: Any) -> None:
        """Náhrada za ``sys.excepthook`` — chyba mimo smyčku událostí."""

        if isinstance(value, KeyboardInterrupt):
            previous = getattr(self, "_previous_excepthook", None)
            if callable(previous):
                previous(exc_type, value, tb)
            return
        self.report_error(exc_type, value, tb)

    def report_error(self, exc_type: Any, value: Any, tb: Any) -> None:
        """Zapíše chybu na ``stderr``, do stavového řádku a ukáže ji uživateli."""

        detail = format_exception(exc_type, value, tb)
        try:
            print(detail, file=sys.stderr)
        except Exception:  # pragma: no cover - zavřený stderr
            pass
        try:
            self.set_status_error(f"Nastala chyba: {describe_exception(exc_type, value)}")
        except Exception:  # noqa: BLE001 - stavový řádek už nemusí existovat
            pass

        if self._error_visible or self._closing:
            return
        self._error_visible = True
        try:
            report_exception(self, exc_type, value, tb)
        finally:
            self._error_visible = False

    # ------------------------------------------------------------------
    # zavření okna
    # ------------------------------------------------------------------
    def _confirm_close(self) -> bool:
        view = self.view(NAV_FIELDS)
        if view is not None:
            try:
                if not view.confirm_leave():
                    return False
            except tk.TclError:  # pragma: no cover
                pass
        view = self.view(NAV_GENERATE)
        if view is not None:
            try:
                if not view.confirm_discard(
                    "Opravdu chcete aplikaci zavřít a rozepsaný dopis zahodit?"
                ):
                    return False
            except tk.TclError:  # pragma: no cover
                pass
        if view is not None and view.busy:
            return widgets.ask_yes_no(
                self,
                "Právě probíhá generování dopisu.",
                detail="Opravdu chcete aplikaci zavřít?",
                title="Zavřít aplikaci",
            )
        return True

    def close(self, _event: Any = None) -> str:
        """Zavře aplikaci — s dotazem na neuloženou práci a uložením okna."""

        if self._closing:
            return "break"
        if not self._confirm_close():
            return "break"

        self._closing = True
        self.remember_window_state()
        view = self.view(NAV_SETTINGS)
        if view is not None:
            try:
                view.save_now("")
            except Exception:  # noqa: BLE001 - nastavení se ukládá průběžně
                pass
        try:
            self.destroy()
        except tk.TclError:  # pragma: no cover - okno už zaniklo
            pass
        return "break"

    def destroy(self) -> None:  # noqa: D102 - viz tkinter
        self._closing = True
        previous = getattr(self, "_previous_excepthook", None)
        installed = getattr(self, "_installed_excepthook", None)
        if previous is not None and installed is not None and sys.excepthook is installed:
            sys.excepthook = previous
        super().destroy()


def _alive(widget: Any) -> bool:
    """Existuje widget ještě? (okno se mohlo mezitím zavřít)"""

    try:
        return bool(widget.winfo_exists())
    except (tk.TclError, AttributeError):  # pragma: no cover - interpret Tk je pryč
        return False


# ---------------------------------------------------------------------------
# spuštění
# ---------------------------------------------------------------------------


def _require_tkinter() -> None:
    """Ověří dostupnost tkinteru. Vyhodí ``ImportError``, když chybí."""

    import tkinter  # noqa: F401
    import tkinter.ttk  # noqa: F401


#: Soubor, do kterého se zapíše důvod neúspěšného startu (stdio pod
#: ``console=False`` neexistuje, takže hláška nemá kam jinam jít).
START_ERROR_LOG_NAME = "start-error.log"


def _show_fatal_dialog(text: str) -> None:
    """Poslední pokus o zobrazení hlášky přes Tk (mimo Windows).

    Vlastní funkce záměrně: modální okno by v testech zastavilo běh, proto
    se dá podstrčit. V ostrém provozu je to poslední záchrana pro uživatele,
    který spustil program bez konzole.
    """

    try:  # pragma: no cover - modální okno se v testech neotevírá
        import tkinter as _tk
        from tkinter import messagebox as _mb

        root = _tk.Tk()
        root.withdraw()
        try:
            _mb.showerror(f"{APP_NAME} — spuštění se nezdařilo", text, parent=root)
        finally:
            root.destroy()
    except Exception:  # noqa: BLE001 - hlášení chyby nesmí vyrobit další chybu
        pass


def _fatal(message: str, detail: str = "") -> None:
    """Oznámí selhání startu i tam, kde ``sys.stderr`` neexistuje.

    Zabalené ``.exe`` běží s ``console=False``, takže CPython nastaví
    ``sys.stdout`` i ``sys.stderr`` na ``None`` a ``print`` tiše nic neudělá.
    Uživatel by po dvojkliku viděl jen bliknutí procesu. Proto se hláška
    zkusí doručit několika cestami po sobě — a žádná z nich nesmí vyrobit
    další chybu.
    """

    text = message if not detail else f"{message}\n\nPodrobnosti:\n{detail}"

    # 1) standardní chybový výstup, když existuje (konzole, `python -m dlg`, testy)
    on_stderr = False
    try:
        if sys.stderr is not None:
            print(text, file=sys.stderr)
            on_stderr = True
    except Exception:  # noqa: BLE001 - hlášení chyby nesmí vyrobit další chybu
        pass

    # 2) log do datové složky, ať jsou podrobnosti dohledatelné i zpětně
    try:
        from datetime import datetime

        path = config.app_home() / START_ERROR_LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            stamp = datetime.now().isoformat(timespec="seconds")
            fh.write(f"--- {stamp} ---\n{text}\n")
    except Exception:  # noqa: BLE001
        pass

    # 3) nativní okno Windows — funguje i bez tkinteru a bez Tcl/Tk
    if sys.platform.startswith("win"):
        try:  # pragma: no cover - jen na Windows
            import ctypes
            from ctypes import wintypes

            box = ctypes.windll.user32.MessageBoxW  # type: ignore[attr-defined]
            box.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
            box.restype = ctypes.c_int
            # MB_OK | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST
            box(None, text[:4000], f"{APP_NAME} — spuštění se nezdařilo", 0x10 | 0x10000 | 0x40000)
            return
        except Exception:  # noqa: BLE001
            pass

    # 4) poslední pokus jinde než na Windows — jen když hláška neměla kam jít
    #    a Tk vůbec žije (v konzoli ji uživatel už vidí, okno by jen otravovalo)
    if not on_stderr:
        _show_fatal_dialog(text)


def main(argv: Sequence[str] | None = None) -> int:
    """Spustí aplikaci. Vrací návratový kód procesu (0 = v pořádku).

    Aplikace nemá žádné parametry příkazové řádky; ``argv`` se přijímá jen
    kvůli obvyklému tvaru vstupního bodu.
    """

    del argv  # program se nespouští s parametry

    try:
        _require_tkinter()
    except Exception:  # noqa: BLE001 - chybí tkinter nebo jeho nativní část
        _fatal(TK_MISSING_MESSAGE)
        return 1

    theme.enable_dpi_awareness()

    try:
        app = App()
    except tk.TclError as exc:
        _fatal(DISPLAY_MISSING_MESSAGE, str(exc))
        return 1
    except Exception:  # noqa: BLE001 - uživateli nesmí zůstat jen traceback
        _fatal(START_FAILED_MESSAGE, traceback.format_exc())
        return 1

    try:
        app.mainloop()
    except KeyboardInterrupt:  # pragma: no cover - Ctrl+C v konzoli
        try:
            app.destroy()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001 - poslední záchranná síť
        _fatal(START_FAILED_MESSAGE, traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - obvykle se spouští `python -m dlg`
    raise SystemExit(main())
