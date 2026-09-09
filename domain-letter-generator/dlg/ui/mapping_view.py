"""Pohled „Pole šablony“ — editor formulářových polí jedné šablony.

Uživatel tu opravuje to, co :mod:`dlg.mapping` navrhlo automaticky:

* přejmenuje pole a jeho klíč, změní typ, varianty, výchozí hodnotu a povinnost,
* **sloučí** dvě pole do jednoho (jeden údaj vyplní víc míst v dokumentu)
  nebo je zase **rozdělí** zpátky,
* změní **pořadí** polí ve formuláři,
* označí odstavce, které půjde při generování **vypustit**,
* nastaví **vzor názvu** výstupního souboru.

Pohled si drží pracovní kopii metadat; do knihovny se zapíše až po stisku
*Uložit*. Odchod s neuloženými změnami se hlídá dotazem.

Použití z :mod:`dlg.app`::

    view = MappingView(container, store, on_done=self._back_to_templates, status=self.status)
    view.load(template_id)
"""

from __future__ import annotations

import inspect
import re
import tkinter as tk
from dataclasses import replace
from datetime import date
from typing import Any, Callable, Sequence
from tkinter import ttk

from .. import naming
from ..models import FieldSpec, OptionalParagraph, Placeholder, ScanResult, TemplateMeta
from . import theme, widgets

__all__ = [
    "FIELD_TYPE_LABELS",
    "KEY_RE",
    "MIN_PARAGRAPH_CHARS",
    "MappingView",
    "is_meaningful_paragraph",
    "normalize_key",
]

#: Klíč pole smí být jen z malých písmen, číslic a podtržítek.
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
#: Kratší odstavec už není text, ale výplň (odřádkování, oddělovač).
MIN_PARAGRAPH_CHARS = 3
#: Kolik znaků odstavce se ukáže v seznamu volitelných odstavců.
PARAGRAPH_PREVIEW_CHARS = 160
#: Kolik znaků kontextu se ukáže u placeholderu.
CONTEXT_PREVIEW_CHARS = 180

#: České názvy typů polí (pořadí je zároveň pořadí v comboboxu).
FIELD_TYPE_LABELS: tuple[tuple[str, str], ...] = (
    ("text", "text"),
    ("multiline", "víceřádkový"),
    ("choice", "výběr"),
    ("date", "datum"),
)
_TYPE_TO_LABEL = {key: label for key, label in FIELD_TYPE_LABELS}
_LABEL_TO_TYPE = {label: key for key, label in FIELD_TYPE_LABELS}

#: Lidské názvy částí dokumentu (kvůli odstavcům ze záhlaví a zápatí).
_PART_LABELS: tuple[tuple[str, str], ...] = (
    ("word/document.xml", ""),
    ("word/header", "záhlaví"),
    ("word/footer", "zápatí"),
    ("word/footnotes.xml", "poznámky pod čarou"),
    ("word/endnotes.xml", "vysvětlivky"),
)

PATTERN_HELP = (
    "{datum} = dnešní datum (RRRR-MM-DD), {nazev} = název šablony, "
    "{klic} = hodnota pole se stejným klíčem. "
    "Například: {datum}_{domena}_vyzva"
)


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


def normalize_key(text: str) -> str:
    """Z libovolného textu udělá použitelný strojový klíč (``[a-z][a-z0-9_]*``)."""

    import unicodedata

    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    chars = [ch.lower() if (ch.isascii() and ch.isalnum()) else "_" for ch in stripped]
    key = re.sub(r"_{2,}", "_", "".join(chars)).strip("_")
    if key and key[0].isdigit():
        key = "pole_" + key
    return key


def is_meaningful_paragraph(text: str) -> bool:
    """Má odstavec netriviální text? Prázdné a dekorativní odstavce nenabízíme."""

    value = " ".join(str(text or "").replace(" ", " ").split())
    if len(value) < MIN_PARAGRAPH_CHARS:
        return False
    return any(ch.isalnum() for ch in value)


def _part_label(part: str) -> str:
    """Lidský název části dokumentu (``""`` pro hlavní text)."""

    name = str(part or "")
    for prefix, label in _PART_LABELS:
        if name == prefix or (not prefix.endswith(".xml") and name.startswith(prefix)):
            return label
    return name


def _plural_placeholders(count: int) -> str:
    if count == 1:
        return "1 placeholder"
    if 2 <= count <= 4:
        return f"{count} placeholdery"
    return f"{count} placeholderů"


def _plural_fields(count: int) -> str:
    if count == 1:
        return "1 pole"
    if 2 <= count <= 4:
        return f"{count} pole"
    return f"{count} polí"


def _unique_key(base: str, used: set[str]) -> str:
    candidate = base or "pole"
    if candidate not in used:
        return candidate
    index = 2
    while f"{candidate}_{index}" in used:
        index += 1
    return f"{candidate}_{index}"


# ---------------------------------------------------------------------------
# pohled
# ---------------------------------------------------------------------------
class MappingView(ttk.Frame):
    """Editor polí jedné šablony.

    :param master: rodičovský widget
    :param store: :class:`dlg.store.TemplateStore`
    :param on_done: zavolá se při odchodu z pohledu; dostane ``True``, když
        se odcházelo po uložení (funkce bez argumentů je taky v pořádku)
    :param status: stavový řádek (:class:`dlg.ui.widgets.StatusBar`), funkce
        ``(text) -> None``, nebo ``None``
    """

    def __init__(
        self,
        master: tk.Misc | None = None,
        store: Any = None,
        on_done: Callable[..., Any] | None = None,
        status: Any = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("padding", theme.PAD_L)
        super().__init__(master, **kwargs)
        self.store = store
        self.on_done = on_done
        self.status = _Status(status)

        #: Načtená šablona (pracovní kopie metadat).
        self.meta: TemplateMeta | None = None
        self.scan: ScanResult | None = None
        self._placeholders: dict[str, Placeholder] = {}
        self._fields: list[FieldSpec] = []
        self._optional: dict[str, OptionalParagraph] = {}
        self._paragraph_rows: list[dict[str, Any]] = []
        self._snapshot: dict[str, Any] = {}
        #: Co se v meta.json nepodařilo přečíst — uložení by to zahodilo.
        self._load_problems: list[str] = []
        self._selected_index: int = -1
        self._loading = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_header()
        self._build_notebook()
        self._build_footer()
        self._set_editor_enabled(False)

    # ------------------------------------------------------------------
    # stavba rozhraní
    # ------------------------------------------------------------------
    def _build_header(self) -> None:
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_M))
        header.columnconfigure(0, weight=1)

        self.title_label = ttk.Label(header, style="Nadpis.TLabel", text="Pole šablony")
        self.title_label.grid(row=0, column=0, sticky="w")

        self.summary_label = ttk.Label(
            header,
            style="Napoveda.TLabel",
            text="Není načtená žádná šablona.",
            wraplength=900,
            justify="left",
        )
        self.summary_label.grid(row=1, column=0, sticky="w", pady=(theme.PAD_S, 0))

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        self.fields_tab = ttk.Frame(self.notebook, padding=theme.PAD_M)
        self.paragraphs_tab = ttk.Frame(self.notebook, padding=theme.PAD_M)
        self.notebook.add(self.fields_tab, text="Pole formuláře")
        self.notebook.add(self.paragraphs_tab, text="Volitelné odstavce")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")

        self._build_fields_tab()
        self._build_paragraphs_tab()

    # -- záložka s poli -------------------------------------------------
    def _build_fields_tab(self) -> None:
        tab = self.fields_tab
        tab.columnconfigure(0, weight=3, minsize=380)
        tab.columnconfigure(1, weight=2, minsize=320)
        tab.rowconfigure(0, weight=1)

        left = ttk.Frame(tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, theme.PAD_M))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        columns = ("popisek", "klic", "typ", "povinne", "mista")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        headings = {
            "popisek": ("Popisek", 190, "w"),
            "klic": ("Klíč", 120, "w"),
            "typ": ("Typ", 90, "w"),
            "povinne": ("Povinné", 70, "center"),
            "mista": ("Míst", 50, "center"),
        }
        for name, (text, width, anchor) in headings.items():
            self.tree.heading(name, text=text)
            self.tree.column(name, width=width, anchor=anchor, stretch=(name == "popisek"))
        self.tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select, add="+")

        self.fields_toolbar = widgets.Toolbar(left, padding=(0, theme.PAD_M, 0, 0))
        self.fields_toolbar.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.fields_toolbar.add_button("Nahoru", self.move_selected_up, key="nahoru")
        self.fields_toolbar.add_button("Dolů", self.move_selected_down, key="dolu")
        self.fields_toolbar.add_button("Sloučit s…", self.merge_selected, key="sloucit")
        self.fields_toolbar.add_button("Rozdělit", self.split_selected, key="rozdelit")

        self._build_editor(tab)

    def _build_editor(self, parent: tk.Misc) -> None:
        holder = widgets.ScrollableFrame(parent, padding=(0, 0, theme.PAD_S, 0))
        holder.grid(row=0, column=1, sticky="nsew")
        editor = holder.body
        editor.columnconfigure(0, weight=1)
        self.editor_holder = holder
        self.editor = editor

        row = 0
        ttk.Label(editor, style="Podnadpis.TLabel", text="Vlastnosti pole").grid(
            row=row, column=0, sticky="w"
        )
        row += 1

        self.label_field = widgets.LabeledEntry(
            editor, "Popisek ve formuláři", help_text="Text, který uvidí uživatel u pole."
        )
        self.label_field.grid(row=row, column=0, sticky="ew", pady=(theme.PAD_M, 0))
        row += 1

        self.key_field = widgets.LabeledEntry(
            editor,
            "Klíč",
            help_text="Jen malá písmena bez diakritiky, číslice a podtržítko "
            "(např. drzitel). Používá se ve vzoru názvu souboru a v profilu.",
        )
        self.key_field.grid(row=row, column=0, sticky="ew", pady=(theme.PAD_M, 0))
        row += 1

        self.type_field = widgets.LabeledCombobox(
            editor,
            "Typ",
            values=[label for _key, label in FIELD_TYPE_LABELS],
            readonly=True,
        )
        self.type_field.grid(row=row, column=0, sticky="ew", pady=(theme.PAD_M, 0))
        self.type_field.bind_change(lambda _value: self._sync_editor_state())
        row += 1

        self.options_field = widgets.LabeledText(
            editor,
            "Varianty (jedna na řádek)",
            height=4,
            help_text="Uplatní se jen u typu „výběr“.",
        )
        self.options_field.grid(row=row, column=0, sticky="ew", pady=(theme.PAD_M, 0))
        row += 1

        self.default_field = widgets.LabeledEntry(
            editor,
            "Výchozí hodnota",
            help_text="Předvyplní se ve formuláři, dokud ji uživatel nepřepíše.",
        )
        self.default_field.grid(row=row, column=0, sticky="ew", pady=(theme.PAD_M, 0))
        row += 1

        self.required_var = tk.BooleanVar(master=self, value=True)
        self.required_check = ttk.Checkbutton(
            editor, text="Povinné pole", variable=self.required_var
        )
        self.required_check.grid(row=row, column=0, sticky="w", pady=(theme.PAD_M, 0))
        row += 1

        ttk.Separator(editor, orient="horizontal").grid(
            row=row, column=0, sticky="ew", pady=theme.PAD_M
        )
        row += 1

        ttk.Label(editor, style="Popisek.TLabel", text="Místa v dokumentu, která pole plní").grid(
            row=row, column=0, sticky="w"
        )
        row += 1

        places = ttk.Frame(editor)
        places.grid(row=row, column=0, sticky="nsew", pady=(theme.PAD_S, 0))
        places.columnconfigure(0, weight=1)
        places.rowconfigure(0, weight=1)
        editor.rowconfigure(row, weight=1)

        self.places_text = tk.Text(
            places,
            height=8,
            wrap="word",
            borderwidth=1,
            relief="solid",
            highlightthickness=0,
            background=theme.COLOR_BG,
            foreground=theme.COLOR_TEXT,
            padx=theme.PAD_S,
            pady=theme.PAD_S - 2,
            font=theme.font_name(theme.FONT_SMALL, self),
        )
        self.places_text.grid(row=0, column=0, sticky="nsew")
        places_scroll = ttk.Scrollbar(places, orient="vertical", command=self.places_text.yview)
        places_scroll.grid(row=0, column=1, sticky="ns")
        self.places_text.configure(yscrollcommand=places_scroll.set, state="disabled")
        self.places_text.tag_configure("raw", foreground=theme.COLOR_ACCENT)
        self.places_text.tag_configure("kontext", foreground=theme.COLOR_TEXT_MUTED)

    # -- záložka s odstavci ---------------------------------------------
    def _build_paragraphs_tab(self) -> None:
        tab = self.paragraphs_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        ttk.Label(
            tab,
            style="Napoveda.TLabel",
            text="Zaškrtnuté odstavce půjde při generování jedním kliknutím vypustit. "
            "Nabízejí se jen odstavce s textem.",
            wraplength=900,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, theme.PAD_M))

        self.paragraphs_area = widgets.ScrollableFrame(tab)
        self.paragraphs_area.grid(row=1, column=0, sticky="nsew")

    # -- patička ---------------------------------------------------------
    def _build_footer(self) -> None:
        footer = ttk.Frame(self)
        footer.grid(row=2, column=0, sticky="ew", pady=(theme.PAD_M, 0))
        footer.columnconfigure(0, weight=1)

        self.pattern_field = widgets.LabeledEntry(
            footer, "Vzor názvu výstupního souboru", help_text=PATTERN_HELP
        )
        self.pattern_field.grid(row=0, column=0, sticky="ew")
        self.pattern_field.bind_change(lambda _value: self._update_pattern_preview())

        self.pattern_preview = ttk.Label(footer, style="Napoveda.TLabel", text="")
        self.pattern_preview.grid(row=1, column=0, sticky="w", pady=(2, 0))

        self.toolbar = widgets.Toolbar(footer, padding=(0, theme.PAD_M, 0, 0))
        self.toolbar.grid(row=2, column=0, sticky="ew")
        self.toolbar.add_button("Uložit", self.save, primary=True, key="ulozit")
        self.toolbar.add_button("Zpět", self.go_back, key="zpet")

    # ------------------------------------------------------------------
    # načtení šablony
    # ------------------------------------------------------------------
    @property
    def template_id(self) -> str:
        return self.meta.id if self.meta is not None else ""

    def load(self, template_id: str) -> bool:
        """Načte šablonu a vykreslí její pole i odstavce. Vrací úspěch."""

        if self.store is None:  # pragma: no cover - pojistka
            widgets.show_error(self, "Knihovna šablon není k dispozici.")
            return False

        try:
            meta = self.store.get(template_id)
            scan = self.store.scan(template_id)
        except Exception as exc:  # noqa: BLE001 - hlášku ukážeme uživateli
            self.meta = None
            self.scan = None
            self.status.error(str(exc))
            widgets.show_error(self, "Šablonu se nepodařilo načíst.", detail=str(exc))
            return False

        self._loading = True
        try:
            self.meta = meta
            self.scan = scan
            self._placeholders = {p.id: p for p in scan.placeholders}
            self._fields = [FieldSpec.from_dict(f.to_dict()) for f in meta.ordered_fields()]
            self._renumber()
            self._optional = {
                op.paragraph_id: OptionalParagraph.from_dict(op.to_dict())
                for op in meta.optional_paragraphs
            }

            self.title_label.configure(text=f"Pole šablony — {meta.name}")
            self.summary_label.configure(
                text=(
                    f"V šabloně bylo nalezeno {_plural_placeholders(len(scan.placeholders))}"
                    f" v {len(scan.paragraphs)} odstavcích; "
                    f"formulář z nich skládá {_plural_fields(len(self._fields))}."
                )
            )
            self.pattern_field.set(meta.output_pattern or naming.DEFAULT_PATTERN)

            self._refresh_tree(select_index=0)
            self._rebuild_paragraph_rows()
            self._update_pattern_preview()
        finally:
            self._loading = False

        self._snapshot = self._current_snapshot()
        problems = list(getattr(self.store, "load_problems", {}).get(meta.id, []))
        upravena = self._mapping_warning(meta)
        if problems:
            # Uložením by se poškozená část meta.json nenávratně přepsala,
            # proto o ní uživatel musí vědět předem.
            self._load_problems = problems
            self.status.error(
                "Část nastavení polí se nepodařilo přečíst — poškozené položky "
                "byly vynechány."
            )
            widgets.show_warning(
                self,
                "Část nastavení polí se nepodařilo přečíst.",
                detail="\n".join(problems)
                + "\n\nPoškozené položky byly vynechány. Uložením se nenávratně "
                "zahodí.",
                title="Poškozené nastavení polí",
            )
        elif upravena:
            self._load_problems = []
            self.status.error(upravena)
        else:
            self._load_problems = []
            self.status.set(f"Šablona „{meta.name}“ je připravená k úpravám.")
        return True

    def _mapping_warning(self, meta: TemplateMeta) -> str:
        """Sedí uložená pole ještě na dokument? (šablona jde upravit ve Wordu)"""

        verify = getattr(self.store, "verify_mapping", None)
        if not callable(verify):
            return ""
        try:
            check = verify(meta)
        except Exception:  # noqa: BLE001 - kontrola je pojistka, ne překážka
            return ""
        return str(getattr(check, "message", "") or "")

    # ------------------------------------------------------------------
    # tabulka polí
    # ------------------------------------------------------------------
    @staticmethod
    def _iid(index: int) -> str:
        """Identifikátor řádku je pořadí, ne klíč — klíč se smí měnit i duplikovat."""

        return f"pole{int(index)}"

    def _iid_index(self, iid: str) -> int:
        text = str(iid or "")
        if not text.startswith("pole"):
            return -1
        try:
            index = int(text[4:])
        except ValueError:  # pragma: no cover - cizí iid
            return -1
        return index if 0 <= index < len(self._fields) else -1

    @staticmethod
    def _row_values(spec: FieldSpec) -> tuple[Any, ...]:
        return (
            spec.label,
            spec.key,
            _TYPE_TO_LABEL.get(spec.type, spec.type),
            "ano" if spec.required else "ne",
            len(spec.placeholder_ids),
        )

    def _refresh_tree(self, select_index: int | None = None) -> None:
        """Překreslí tabulku a vybere řádek (výchozí je ten dosud vybraný)."""

        target = self._selected_index if select_index is None else int(select_index)
        self.tree.delete(*self.tree.get_children())
        for index, spec in enumerate(self._fields):
            self.tree.insert("", "end", iid=self._iid(index), values=self._row_values(spec))

        if not self._fields:
            self._selected_index = -1
            self._load_editor(None)
            return

        self._select_row(max(0, min(target, len(self._fields) - 1)))

    def _select_row(self, index: int) -> None:
        """Vybere řádek a naplní jím editační panel."""

        self._selected_index = index
        iid = self._iid(index)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)
        self._load_editor(self._fields[index])

    def _on_tree_select(self, _event: Any = None) -> None:
        if self._loading:
            return
        selection = self.tree.selection()
        index = self._iid_index(selection[0]) if selection else -1
        if index < 0 or index == self._selected_index:
            return
        self._flush_editor()
        self._select_row(index)

    def field_by_key(self, key: str) -> FieldSpec | None:
        for spec in self._fields:
            if spec.key == key:
                return spec
        return None

    def selected_field(self) -> FieldSpec | None:
        """Pole, které je právě vybrané v tabulce."""

        if 0 <= self._selected_index < len(self._fields):
            return self._fields[self._selected_index]
        return None

    def select_field(self, key: str) -> bool:
        """Vybere pole podle klíče a načte ho do editačního panelu."""

        if self._index_of(key) < 0:
            return False
        self._flush_editor()
        index = self._index_of(key)
        if index < 0:  # pragma: no cover - klíč se mezitím změnil
            return False
        self._select_row(index)
        return True

    def _index_of(self, key: str) -> int:
        for index, spec in enumerate(self._fields):
            if spec.key == key:
                return index
        return -1

    def _renumber(self) -> None:
        for index, spec in enumerate(self._fields):
            spec.order = index

    # ------------------------------------------------------------------
    # editační panel
    # ------------------------------------------------------------------
    def _set_editor_enabled(self, enabled: bool) -> None:
        for field in (
            self.label_field,
            self.key_field,
            self.type_field,
            self.options_field,
            self.default_field,
        ):
            field.set_enabled(enabled)
        try:
            self.required_check.state(["!disabled" if enabled else "disabled"])
        except tk.TclError:  # pragma: no cover
            pass
        for key in ("nahoru", "dolu", "sloucit", "rozdelit"):
            self.fields_toolbar.set_enabled(key, enabled)

    def _load_editor(self, spec: FieldSpec | None) -> None:
        previous = self._loading
        self._loading = True
        try:
            if spec is None:
                self.label_field.set("")
                self.key_field.set("")
                self.type_field.set("")
                self.options_field.set("")
                self.default_field.set("")
                self.required_var.set(True)
                self._show_places(None)
                self._set_editor_enabled(False)
                return

            self._set_editor_enabled(True)
            self.label_field.set(spec.label)
            self.key_field.set(spec.key)
            self.type_field.set(_TYPE_TO_LABEL.get(spec.type, "text"))
            self.options_field.set("\n".join(spec.options))
            self.default_field.set(spec.default)
            self.required_var.set(bool(spec.required))
            for field in (
                self.label_field,
                self.key_field,
                self.type_field,
                self.options_field,
                self.default_field,
            ):
                field.clear_error()
            self._show_places(spec)
            self._sync_editor_state()
        finally:
            self._loading = previous

    def _sync_editor_state(self) -> None:
        """Varianty dávají smysl jen u typu „výběr“."""

        chosen = _LABEL_TO_TYPE.get(self.type_field.get(), "text")
        self.options_field.set_enabled(chosen == "choice")
        if chosen == "choice":
            self.options_field.set_help("Každá varianta na svém řádku; první je výchozí.")
        else:
            self.options_field.set_help("Uplatní se jen u typu „výběr“.")

    def _show_places(self, spec: FieldSpec | None) -> None:
        text = self.places_text
        try:
            text.configure(state="normal")
            text.delete("1.0", "end")
            if spec is None or not spec.placeholder_ids:
                text.insert("end", "Pole zatím neplní žádné místo v dokumentu.\n")
            else:
                for pid in spec.placeholder_ids:
                    placeholder = self._placeholders.get(pid)
                    if placeholder is None:
                        text.insert("end", f"{pid} — v šabloně už není\n", ("kontext",))
                        continue
                    part = _part_label(placeholder.part)
                    head = widgets.shorten(placeholder.raw, 90)
                    text.insert("end", head, ("raw",))
                    if part:
                        text.insert("end", f"  ({part})", ("kontext",))
                    text.insert("end", "\n")
                    context = widgets.shorten(placeholder.context, CONTEXT_PREVIEW_CHARS)
                    if context and context != head:
                        text.insert("end", f"    {context}\n", ("kontext",))
            text.configure(state="disabled")
        except tk.TclError:  # pragma: no cover
            pass

    def _flush_editor(self) -> None:
        """Zapíše obsah editačního panelu do právě vybraného pole."""

        if self._loading:
            return
        spec = self.selected_field()
        if spec is None:
            return

        spec.label = self.label_field.get().strip()
        spec.key = self.key_field.get().strip()
        spec.type = _LABEL_TO_TYPE.get(self.type_field.get(), spec.type)
        spec.options = [
            line.strip() for line in self.options_field.get().splitlines() if line.strip()
        ]
        spec.default = self.default_field.get()
        spec.required = bool(self.required_var.get())

        iid = self._iid(self._selected_index)
        if self.tree.exists(iid):
            self.tree.item(iid, values=self._row_values(spec))

    # ------------------------------------------------------------------
    # pořadí, slučování, rozdělení
    # ------------------------------------------------------------------
    def move_field(self, key: str, delta: int) -> bool:
        """Posune pole o ``delta`` míst (−1 nahoru, +1 dolů)."""

        index = self._index_of(key)
        target = index + int(delta)
        if index < 0 or not (0 <= target < len(self._fields)):
            return False
        self._fields[index], self._fields[target] = self._fields[target], self._fields[index]
        self._renumber()
        self._refresh_tree(select_index=target)
        return True

    def _move_selected(self, delta: int, edge: str) -> None:
        self._flush_editor()
        spec = self.selected_field()
        if spec is None or not self.move_field(spec.key, delta):
            self.status.set(edge)

    def move_selected_up(self) -> None:
        self._move_selected(-1, "Pole už je nahoře.")

    def move_selected_down(self) -> None:
        self._move_selected(+1, "Pole už je dole.")

    def merge_fields(self, key: str, other_key: str) -> bool:
        """Pole ``key`` převezme místa pole ``other_key``; to se zruší."""

        index = self._index_of(key)
        other = self._index_of(other_key)
        if index < 0 or other < 0 or index == other:
            return False

        target = self._fields[index]
        source = self._fields[other]
        for pid in source.placeholder_ids:
            if pid not in target.placeholder_ids:
                target.placeholder_ids.append(pid)
        if not target.options and source.options:
            target.options = list(source.options)
            target.type = "choice"
        if not target.default and source.default:
            target.default = source.default
        del self._fields[other]
        self._renumber()
        self._refresh_tree(select_index=index - 1 if other < index else index)
        return True

    def merge_selected(self) -> None:
        self._flush_editor()
        spec = self.selected_field()
        if spec is None:
            return
        others = [f for f in self._fields if f.key != spec.key]
        if not others:
            widgets.show_info(self, "V šabloně není žádné další pole, se kterým by šlo sloučit.")
            return
        chosen = self._ask_merge_target(spec, others)
        if not chosen:
            return
        if self.merge_fields(spec.key, chosen):
            self.status.set(
                f"Pole „{spec.label or spec.key}“ teď vyplní "
                f"{widgets.plural_places(len(spec.placeholder_ids))}."
            )

    def _ask_merge_target(self, spec: FieldSpec, others: Sequence[FieldSpec]) -> str:
        """Modální dialog s výběrem pole, které se má do vybraného vlít."""

        dialog = tk.Toplevel(self)
        dialog.title("Sloučit pole")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        dialog.configure(background=theme.COLOR_SURFACE)

        body = ttk.Frame(dialog, padding=theme.PAD_L)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        ttk.Label(
            body,
            style="Popisek.TLabel",
            wraplength=380,
            justify="left",
            text=(
                f"Pole „{spec.label or spec.key}“ převezme všechna místa vybraného "
                "pole. Vybrané pole se z formuláře odstraní."
            ),
        ).grid(row=0, column=0, sticky="w")

        listbox = tk.Listbox(
            body,
            height=min(10, max(3, len(others))),
            exportselection=False,
            background=theme.COLOR_SURFACE,
            foreground=theme.COLOR_TEXT,
            selectbackground=theme.COLOR_ACCENT_SOFT,
            selectforeground=theme.COLOR_TEXT,
            highlightthickness=1,
            highlightbackground=theme.COLOR_BORDER,
            borderwidth=0,
        )
        for other in others:
            listbox.insert(
                "end", f"{other.label or other.key}  ({other.key}, {len(other.placeholder_ids)}×)"
            )
        listbox.selection_set(0)
        listbox.grid(row=1, column=0, sticky="ew", pady=(theme.PAD_M, 0))

        result = {"key": ""}

        def confirm(_event: Any = None) -> None:
            selection = listbox.curselection()
            if selection:
                result["key"] = others[int(selection[0])].key
            dialog.destroy()

        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, sticky="e", pady=(theme.PAD_M, 0))
        ttk.Button(buttons, text="Zrušit", command=dialog.destroy).pack(
            side="right", padx=(theme.PAD_S, 0)
        )
        ttk.Button(buttons, text="Sloučit", style="Primary.TButton", command=confirm).pack(
            side="right"
        )
        listbox.bind("<Double-Button-1>", confirm, add="+")
        dialog.bind("<Return>", confirm, add="+")
        dialog.bind("<Escape>", lambda _e: dialog.destroy(), add="+")

        try:
            dialog.grab_set()
        except tk.TclError:  # pragma: no cover - bez správce oken
            pass
        listbox.focus_set()
        self.wait_window(dialog)
        return result["key"]

    def split_field(self, key: str) -> list[str]:
        """Rozdělí pole zpátky na samostatná pole (jedno pro každé místo)."""

        index = self._index_of(key)
        if index < 0:
            return []
        spec = self._fields[index]
        if len(spec.placeholder_ids) < 2:
            return []

        used = {f.key for f in self._fields if f.key != spec.key}
        parts: list[FieldSpec] = []
        for position, pid in enumerate(spec.placeholder_ids, start=1):
            new_key = spec.key if position == 1 else _unique_key(f"{spec.key}_{position}", used)
            used.add(new_key)
            label = spec.label if position == 1 else f"{spec.label} ({position})"
            placeholder = self._placeholders.get(pid)
            if placeholder is not None and position > 1:
                context = widgets.shorten(placeholder.context or placeholder.raw, 60)
                if context:
                    label = f"{spec.label} ({position}) — {context}"
            parts.append(
                replace(
                    FieldSpec.from_dict(spec.to_dict()),
                    key=new_key,
                    label=widgets.shorten(label, 80),
                    placeholder_ids=[pid],
                )
            )

        self._fields[index : index + 1] = parts
        self._renumber()
        self._refresh_tree(select_index=index)
        return [p.key for p in parts]

    def split_selected(self) -> None:
        self._flush_editor()
        spec = self.selected_field()
        if spec is None:
            return
        if len(spec.placeholder_ids) < 2:
            widgets.show_info(
                self,
                "Toto pole vyplňuje jediné místo v dokumentu, není co rozdělovat.",
            )
            return
        created = self.split_field(spec.key)
        self.status.set(f"Pole se rozdělilo na {_plural_fields(len(created))}.")

    # ------------------------------------------------------------------
    # volitelné odstavce
    # ------------------------------------------------------------------
    def candidate_paragraphs(self) -> list[Any]:
        """Odstavce, které jde nabídnout jako volitelné (netriviální text)."""

        if self.scan is None:
            return []
        out = []
        for paragraph in self.scan.paragraphs:
            if is_meaningful_paragraph(paragraph.text) or paragraph.id in self._optional:
                out.append(paragraph)
        return out

    def _rebuild_paragraph_rows(self) -> None:
        self.paragraphs_area.clear()
        self._paragraph_rows = []
        body = self.paragraphs_area.body
        body.columnconfigure(0, weight=1)

        paragraphs = self.candidate_paragraphs()
        if not paragraphs:
            ttk.Label(
                body,
                style="Napoveda.TLabel",
                text="V šabloně není žádný odstavec s textem.",
            ).grid(row=0, column=0, sticky="w")
            return

        for index, paragraph in enumerate(paragraphs):
            existing = self._optional.get(paragraph.id)
            row = ttk.Frame(body, padding=(0, 0, 0, theme.PAD_M))
            row.grid(row=index, column=0, sticky="ew")
            row.columnconfigure(0, weight=1)

            enabled_var = tk.BooleanVar(master=self, value=existing is not None)
            label_var = tk.StringVar(master=self, value=(existing.label if existing else ""))
            default_var = tk.BooleanVar(
                master=self, value=(existing.included_by_default if existing else True)
            )

            preview = widgets.shorten(paragraph.text, PARAGRAPH_PREVIEW_CHARS) or "(prázdný odstavec)"
            part = _part_label(paragraph.part)
            caption = preview if not part else f"[{part}] {preview}"

            check = ttk.Checkbutton(row, text=caption, variable=enabled_var)
            check.grid(row=0, column=0, sticky="w")

            detail = ttk.Frame(row, padding=(theme.PAD_L + theme.PAD_M, theme.PAD_S, 0, 0))
            detail.grid(row=1, column=0, sticky="ew")
            detail.columnconfigure(1, weight=1)

            ttk.Label(detail, style="Napoveda.TLabel", text="Popisek:").grid(
                row=0, column=0, sticky="w", padx=(0, theme.PAD_S)
            )
            entry = ttk.Entry(detail, textvariable=label_var)
            entry.grid(row=0, column=1, sticky="ew")
            default_check = ttk.Checkbutton(
                detail, text="Ve výchozím stavu zahrnout", variable=default_var
            )
            default_check.grid(row=0, column=2, sticky="w", padx=(theme.PAD_M, 0))

            record = {
                "paragraph": paragraph,
                "enabled": enabled_var,
                "label": label_var,
                "default": default_var,
                "entry": entry,
                "check": default_check,
                "preview": preview,
            }
            self._paragraph_rows.append(record)
            enabled_var.trace_add("write", lambda *_a, r=record: self._sync_paragraph_row(r))
            self._sync_paragraph_row(record)

    def _sync_paragraph_row(self, record: dict[str, Any]) -> None:
        enabled = bool(record["enabled"].get())
        state = "!disabled" if enabled else "disabled"
        for widget in (record["entry"], record["check"]):
            try:
                widget.state([state])
            except tk.TclError:  # pragma: no cover
                pass

    def set_paragraph_optional(
        self, paragraph_id: str, optional: bool = True, *, label: str = "", included: bool = True
    ) -> bool:
        """Programové zapnutí/vypnutí volitelnosti odstavce (kvůli testům i app)."""

        for record in self._paragraph_rows:
            if record["paragraph"].id != paragraph_id:
                continue
            record["enabled"].set(bool(optional))
            if optional:
                if label:
                    record["label"].set(label)
                record["default"].set(bool(included))
            return True
        return False

    def optional_paragraph_specs(self) -> list[OptionalParagraph]:
        """Volitelné odstavce tak, jak jsou právě nastavené ve formuláři."""

        out: list[OptionalParagraph] = []
        for record in self._paragraph_rows:
            if not record["enabled"].get():
                continue
            paragraph = record["paragraph"]
            label = record["label"].get().strip() or widgets.shorten(record["preview"], 60)
            out.append(
                OptionalParagraph(
                    paragraph_id=paragraph.id,
                    label=label,
                    included_by_default=bool(record["default"].get()),
                )
            )
        return out

    # ------------------------------------------------------------------
    # vzor názvu souboru
    # ------------------------------------------------------------------
    def _update_pattern_preview(self) -> None:
        if self.meta is None:
            self.pattern_preview.configure(text="")
            return
        values = {spec.key: (spec.default or f"<{spec.key}>") for spec in self._fields}
        stem = naming.render_pattern(
            self.pattern_field.get(),
            values,
            template_name=self.meta.name,
            today=date.today(),
        )
        self.pattern_preview.configure(text=f"Náhled: {stem}.docx")

    # ------------------------------------------------------------------
    # kontrola a uložení
    # ------------------------------------------------------------------
    def field_specs(self) -> list[FieldSpec]:
        """Pole tak, jak jsou právě nastavená (včetně rozepsaného editoru)."""

        self._flush_editor()
        self._renumber()
        return [FieldSpec.from_dict(spec.to_dict()) for spec in self._fields]

    def validate(self) -> list[str]:
        """Vrátí seznam českých chybových hlášek; prázdný seznam = v pořádku."""

        self._flush_editor()
        problems: list[str] = []
        seen: dict[str, int] = {}

        for position, spec in enumerate(self._fields, start=1):
            name = spec.label or spec.key or f"pole č. {position}"
            if not spec.label.strip():
                problems.append(f"Pole č. {position} nemá popisek.")
            key = spec.key.strip()
            if not key:
                problems.append(f"Pole „{name}“ nemá klíč.")
            elif not KEY_RE.match(key):
                suggestion = normalize_key(key) or "pole"
                problems.append(
                    f"Klíč „{key}“ u pole „{name}“ není platný — použijte jen malá "
                    f"písmena bez diakritiky, číslice a podtržítko (např. „{suggestion}“)."
                )
            elif key in seen:
                problems.append(
                    f"Klíč „{key}“ je použitý dvakrát (pole č. {seen[key]} a č. {position}). "
                    "Klíče musí být v šabloně jedinečné."
                )
            if key and key not in seen:
                seen[key] = position
            if spec.type == "choice" and len(spec.options) < 2:
                problems.append(
                    f"Pole „{name}“ je typu „výběr“, ale nemá aspoň dvě varianty."
                )
        return problems

    def _mark_first_problem(self) -> None:
        """Vybere v tabulce první pole s vadným klíčem, ať je vidět, kde je chyba."""

        seen: set[str] = set()
        for index, spec in enumerate(self._fields):
            key = spec.key.strip()
            bad = (not key) or (not KEY_RE.match(key)) or (key in seen)
            seen.add(key)
            if bad or not spec.label.strip():
                self._select_row(index)
                if bad:
                    self.key_field.set_error(
                        "Klíč musí být jedinečný a jen z malých písmen, číslic a podtržítek."
                    )
                else:
                    self.label_field.set_error("Popisek nesmí být prázdný.")
                return

    def build_meta(self) -> TemplateMeta | None:
        """Pracovní kopie metadat se vším, co je právě ve formuláři."""

        if self.meta is None:
            return None
        meta = TemplateMeta.from_dict(self.meta.to_dict())
        meta.fields = self.field_specs()
        meta.optional_paragraphs = self.optional_paragraph_specs()
        meta.output_pattern = self.pattern_field.get().strip() or naming.DEFAULT_PATTERN
        return meta

    def _current_snapshot(self) -> dict[str, Any]:
        meta = self.build_meta()
        if meta is None:
            return {}
        return {
            "fields": [f.to_dict() for f in meta.fields],
            "optional_paragraphs": [p.to_dict() for p in meta.optional_paragraphs],
            "output_pattern": meta.output_pattern,
        }

    def has_unsaved_changes(self) -> bool:
        """Liší se to, co je ve formuláři, od naposledy uloženého stavu?"""

        if self.meta is None:
            return False
        return self._current_snapshot() != self._snapshot

    def save(self, *, close: bool = True) -> bool:
        """Zkontroluje a uloží metadata. Vrací ``True`` při úspěchu."""

        if self.meta is None or self.store is None:
            return False

        problems = self.validate()
        if problems:
            self._mark_first_problem()
            summary = "\n".join(f"• {p}" for p in problems[:8])
            if len(problems) > 8:
                summary += f"\n• … a další ({len(problems) - 8})"
            widgets.show_error(self, "Pole se nepodařilo uložit:", detail=summary)
            self.status.error(problems[0])
            return False

        meta = self.build_meta()
        if meta is None:  # pragma: no cover - ošetřeno výš
            return False
        if self._load_problems and not widgets.ask_yes_no(
            self,
            "Část původního nastavení polí se nepodařilo přečíst.",
            detail="\n".join(self._load_problems)
            + "\n\nUložením se tato část nenávratně zahodí. Chcete pokračovat?",
            title="Poškozené nastavení polí",
        ):
            self.status.set("Uložení zrušeno.")
            return False
        try:
            self.store.save_meta(meta)
        except Exception as exc:  # noqa: BLE001 - uživateli ukážeme hlášku
            self.status.error(str(exc))
            widgets.show_error(self, "Uložení se nepodařilo.", detail=str(exc))
            return False

        self.meta = meta
        self._load_problems = []
        self._snapshot = self._current_snapshot()
        self.status.success(f"Pole šablony „{meta.name}“ jsou uložená.")
        if close:
            _invoke(self.on_done, True)
        return True

    def confirm_leave(self) -> bool:
        """Smí se z pohledu odejít? Při neuložených změnách se zeptá."""

        if not self.has_unsaved_changes():
            return True
        return widgets.ask_yes_no(
            self,
            "V nastavení polí máte neuložené změny.",
            detail="Opravdu chcete odejít a změny zahodit?",
            title="Neuložené změny",
        )

    def go_back(self) -> None:
        """Tlačítko *Zpět* — s kontrolou neuložených změn."""

        if not self.confirm_leave():
            return
        _invoke(self.on_done, False)

    # ------------------------------------------------------------------
    # události
    # ------------------------------------------------------------------
    def _on_tab_changed(self, _event: Any = None) -> None:
        if not self._loading:
            self._flush_editor()
