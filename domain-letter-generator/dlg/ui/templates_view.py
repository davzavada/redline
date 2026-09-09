"""Pohled „Šablony“ — knihovna nahraných vzorů dopisů.

Obsahuje tabulku šablon, lištu akcí (nahrát, upravit pole, generovat,
duplikovat, přejmenovat, smazat, otevřít složku) a prázdný stav pro první
spuštění aplikace.

Pohled sám o sobě nic nenaviguje — o přepnutí na jiný pohled si řekne
zpětnými voláními ``on_edit_fields`` a ``on_generate``, které dostane
v konstruktoru. Veškerá práce s disky jde přes :class:`dlg.store.TemplateStore`.

Nahrání šablony (kopie souboru + analýza + návrh polí) může u velkého
dokumentu trvat déle než zlomek vteřiny, proto běží ve vlákně přes
:func:`dlg.ui.widgets.run_in_thread`; hlavní vlákno se mezitím nezasekne.

Seznam veřejného API::

    TemplatesView(master, store, on_edit_fields, on_generate, status)
        refresh()                 překreslí seznam ze store
        select(template_id)       vybere řádek
        selected_template_id()    id vybrané šablony (nebo None)
        selected_meta()           metadata vybrané šablony (nebo None)
        count                     počet šablon v seznamu
        import_template()         „Nahrát šablonu…“ (dialog + vlákno)
        import_file(path, name)   nahrání bez dialogu (dialog už proběhl)
        edit_fields() / generate() / duplicate() / rename() / delete()
        open_folder()
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import tkinter as tk
from tkinter import filedialog, ttk

from ..models import TemplateMeta
from ..store import StoreError
from . import theme, widgets

__all__ = [
    "EMPTY_HINT",
    "EMPTY_TITLE",
    "TemplateDetailsDialog",
    "TemplatesView",
    "ask_docx_path",
    "open_in_file_manager",
]

#: Nadpis prázdného stavu.
EMPTY_TITLE = "Zatím nemáte žádnou šablonu"
#: Vysvětlení pod nadpisem prázdného stavu.
EMPTY_HINT = (
    "Zatím nemáte žádnou šablonu. Nahrajte dokument .docx s vaším vzorem "
    "dopisu — aplikace v něm sama najde místa k vyplnění (text v hranatých "
    "závorkách, žlutě zvýrazněné úseky a pole pro datum) a připraví z nich "
    "formulář."
)

#: Sloupce tabulky: (klíč, nadpis, šířka, zarovnání, minimální šířka).
COLUMNS: tuple[tuple[str, str, int, str, int], ...] = (
    ("nazev", "Název šablony", 300, "w", 160),
    ("stitky", "Štítky", 180, "w", 90),
    ("pole", "Polí", 60, "center", 50),
    ("nahrano", "Nahráno", 110, "center", 90),
)

#: Klíče tlačítek v liště (kvůli ``Toolbar.set_enabled``).
BTN_IMPORT = "import"
BTN_EDIT = "upravit"
BTN_GENERATE = "generovat"
BTN_DUPLICATE = "duplikovat"
BTN_RENAME = "prejmenovat"
BTN_DELETE = "smazat"
BTN_FOLDER = "slozka"

#: Tlačítka, která dávají smysl jen s vybranou šablonou.
_SELECTION_BUTTONS: tuple[str, ...] = (
    BTN_EDIT,
    BTN_GENERATE,
    BTN_DUPLICATE,
    BTN_RENAME,
    BTN_DELETE,
)


# ---------------------------------------------------------------------------
# drobné pomůcky
# ---------------------------------------------------------------------------
def _noop(_text: str) -> None:
    """Náhrada za nepředané zpětné volání."""


def parse_tags(text: str) -> list[str]:
    """Ze zápisu „výzva, LEGO; doména“ udělá seznam štítků bez duplicit."""

    items: list[str] = []
    for chunk in str(text or "").replace(";", ",").split(","):
        tag = chunk.strip()
        if tag and tag not in items:
            items.append(tag)
    return items


def format_tags(tags: Iterable[str]) -> str:
    """Štítky do jednoho řádku pro tabulku i pro dialog."""

    return ", ".join(str(tag).strip() for tag in (tags or ()) if str(tag).strip())


def format_stamp(value: str) -> str:
    """Z ISO 8601 udělá české ``DD.MM.RRRR``; nesrozumitelný zápis nechá být."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return widgets.format_date(datetime.fromisoformat(raw).date())
    except ValueError:
        pass
    parsed = widgets.parse_date(raw[:10])
    return widgets.format_date(parsed) if parsed is not None else raw[:10]


def count_fields(count: int) -> str:
    """České skloňování: 1 pole, 3 pole, 5 polí."""

    number = max(0, int(count))
    if number == 1:
        return "1 pole"
    if 2 <= number <= 4:
        return f"{number} pole"
    return f"{number} polí"


def ask_docx_path(parent: tk.Misc | None = None) -> str:
    """Dialog pro výběr souboru .docx. Prázdný řetězec = uživatel zrušil.

    Samostatná funkce (ne metoda) schválně — v testech se dá snadno podstrčit.
    """

    options: dict[str, Any] = {
        "title": "Vyberte šablonu dopisu (.docx)",
        "filetypes": [("Dokumenty Wordu", "*.docx"), ("Všechny soubory", "*.*")],
    }
    if isinstance(parent, tk.Misc):
        options["parent"] = parent
    chosen = filedialog.askopenfilename(**options)
    return str(chosen) if chosen else ""


def open_in_file_manager(path: Path | str) -> None:
    """Otevře složku (nebo soubor) ve správci souborů systému.

    Na Windows ``os.startfile``, na macOS ``open``, jinde ``xdg-open``.
    Selhání hlásí jako ``OSError`` — volající ho převede na českou hlášku.
    """

    target = Path(path)
    if os.name == "nt":  # pragma: no cover - běží jen na Windows
        os.startfile(str(target))  # type: ignore[attr-defined]
        return

    command = ["open" if sys.platform == "darwin" else "xdg-open", str(target)]
    try:
        subprocess.Popen(  # noqa: S603 - pevně daný příkaz, cesta jde jako argument
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:  # pragma: no cover - závisí na prostředí
        raise OSError(
            f"Ve systému chybí program „{command[0]}“, kterým se složka otevírá."
        ) from exc


# ---------------------------------------------------------------------------
# dialog s údaji o šabloně
# ---------------------------------------------------------------------------
class TemplateDetailsDialog(tk.Toplevel):
    """Modální okno na název (a volitelně popis a štítky) šablony.

    Výsledek je ve :attr:`result`: ``None`` při zrušení, jinak slovník
    ``{"name": str, "description": str, "tags": list[str]}``.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str,
        name: str = "",
        description: str = "",
        tags: Sequence[str] = (),
        with_details: bool = True,
        ok_text: str = "Uložit",
        intro: str = "",
    ) -> None:
        super().__init__(master)
        self.result: dict[str, Any] | None = None
        self._with_details = bool(with_details)

        self.title(str(title))
        try:
            self.transient(master.winfo_toplevel())
        except tk.TclError:  # pragma: no cover - okno bez rodiče
            pass
        self.resizable(False, False)
        self.configure(background=theme.COLOR_SURFACE)

        body = ttk.Frame(self, padding=theme.PAD_L)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        row = 0
        if intro:
            ttk.Label(
                body,
                style="Napoveda.TLabel",
                text=str(intro),
                wraplength=380,
                justify="left",
            ).grid(row=row, column=0, sticky="w", pady=(0, theme.PAD_M))
            row += 1

        self.name_field = widgets.LabeledEntry(
            body,
            "Název šablony",
            value=str(name),
            required=True,
            width=44,
            help_text="Pod tímto názvem uvidíte šablonu v seznamu.",
        )
        self.name_field.grid(row=row, column=0, sticky="ew")
        row += 1

        self.description_field: widgets.LabeledText | None = None
        self.tags_field: widgets.LabeledEntry | None = None
        if self._with_details:
            self.description_field = widgets.LabeledText(
                body,
                "Popis",
                value=str(description),
                height=3,
                help_text="Nepovinná poznámka, k čemu se šablona používá.",
            )
            self.description_field.grid(row=row, column=0, sticky="ew", pady=(theme.PAD_M, 0))
            row += 1

            self.tags_field = widgets.LabeledEntry(
                body,
                "Štítky",
                value=format_tags(tags),
                width=44,
                help_text="Oddělte čárkou, například: výzva, doména, klient LEGO.",
            )
            self.tags_field.grid(row=row, column=0, sticky="ew", pady=(theme.PAD_M, 0))
            row += 1

        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, sticky="e", pady=(theme.PAD_L, 0))
        self.ok_button = ttk.Button(
            buttons, text=str(ok_text), style="Primary.TButton", command=self._on_ok
        )
        self.ok_button.pack(side="right")
        self.cancel_button = ttk.Button(buttons, text="Zrušit", command=self._on_cancel)
        self.cancel_button.pack(side="right", padx=(0, theme.PAD_S))

        self.bind("<Return>", self._on_return)
        self.bind("<Escape>", lambda _e: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self.update_idletasks()
        self._center_on(master)
        try:
            self.grab_set()
        except tk.TclError:  # pragma: no cover - bez správce oken
            pass
        self.name_field.focus_set()

    # -- pomocné -------------------------------------------------------
    def _center_on(self, master: tk.Misc) -> None:
        widgets.center_on(self, master)

    def _on_return(self, event: Any = None) -> str | None:
        # V popisu je Enter obyčejný nový řádek, ne potvrzení dialogu.
        widget = getattr(event, "widget", None)
        if self.description_field is not None and widget is self.description_field.text:
            return None
        self._on_ok()
        return "break"

    def values(self) -> dict[str, Any]:
        """Aktuální obsah dialogu (i bez potvrzení — kvůli testům)."""

        return {
            "name": self.name_field.get().strip(),
            "description": (
                self.description_field.get().strip()
                if self.description_field is not None
                else ""
            ),
            "tags": parse_tags(self.tags_field.get()) if self.tags_field is not None else [],
        }

    # -- konec ---------------------------------------------------------
    def _on_ok(self) -> None:
        data = self.values()
        if not data["name"]:
            self.name_field.set_error("Zadejte název šablony.")
            self.name_field.focus_set()
            return
        self.result = data
        self._close()

    def _on_cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:  # pragma: no cover
            pass
        self.destroy()

    def show(self) -> dict[str, Any] | None:
        """Počká na zavření okna a vrátí výsledek."""

        self.wait_window()
        return self.result


# ---------------------------------------------------------------------------
# pohled
# ---------------------------------------------------------------------------
class TemplatesView(ttk.Frame):
    """Seznam šablon s akcemi nad nimi."""

    def __init__(
        self,
        master: tk.Misc,
        store: Any,
        on_edit_fields: Callable[[str], None] | None = None,
        on_generate: Callable[[str], None] | None = None,
        status: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("padding", (theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_M))
        super().__init__(master, **kwargs)

        self._store = store
        self._on_edit_fields = on_edit_fields or (lambda _tid: None)
        self._on_generate = on_generate or (lambda _tid: None)
        self._status = status or _noop
        self._items: dict[str, TemplateMeta] = {}
        self._busy = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._build_header()
        self._build_toolbar()
        self._build_content()

        self.refresh()

    # -- stavba ---------------------------------------------------------
    def _build_header(self) -> None:
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, style="Nadpis.TLabel", text="Šablony").grid(row=0, column=0, sticky="w")
        self.subtitle = ttk.Label(
            header,
            style="Napoveda.TLabel",
            text="",
            wraplength=640,
            justify="left",
        )
        self.subtitle.grid(row=1, column=0, sticky="w", pady=(2, theme.PAD_M))

    def _build_toolbar(self) -> None:
        self.toolbar = widgets.Toolbar(self)
        self.toolbar.grid(row=1, column=0, sticky="ew")

        self.toolbar.add_button(
            "Nahrát šablonu…", self.import_template, primary=True, key=BTN_IMPORT
        )
        self.toolbar.add_button("Upravit pole", self.edit_fields, key=BTN_EDIT)
        self.toolbar.add_button("Generovat", self.generate, key=BTN_GENERATE)
        self.toolbar.add_separator()
        self.toolbar.add_button("Duplikovat", self.duplicate, key=BTN_DUPLICATE)
        self.toolbar.add_button("Přejmenovat", self.rename, key=BTN_RENAME)
        self.toolbar.add_button("Smazat", self.delete, danger=True, key=BTN_DELETE)
        self.toolbar.add_button("Otevřít složku", self.open_folder, key=BTN_FOLDER)

    def _build_content(self) -> None:
        content = ttk.Frame(self)
        content.grid(row=2, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.content = content

        # --- tabulka ---
        self.table_holder = ttk.Frame(content)
        self.table_holder.grid(row=0, column=0, sticky="nsew")
        self.table_holder.columnconfigure(0, weight=1)
        self.table_holder.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            self.table_holder,
            columns=[key for key, *_ in COLUMNS],
            show="headings",
            selectmode="browse",
        )
        for key, heading, width, anchor, minwidth in COLUMNS:
            self.tree.heading(key, text=heading, anchor="w")
            self.tree.column(
                key,
                width=width,
                minwidth=minwidth,
                anchor=anchor,
                stretch=(key == "nazev"),
            )
        self.tree.grid(row=0, column=0, sticky="nsew")

        self.scrollbar = ttk.Scrollbar(
            self.table_holder, orient="vertical", command=self.tree.yview
        )
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=self.scrollbar.set)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Return>", self._on_return_key)
        self.tree.bind("<Delete>", self._on_delete_key)

        # --- prázdný stav ---
        self.empty_holder = ttk.Frame(content, padding=theme.PAD_L)
        self.empty_holder.grid(row=0, column=0, sticky="nsew")
        self.empty_holder.columnconfigure(0, weight=1)

        ttk.Label(self.empty_holder, style="Podnadpis.TLabel", text=EMPTY_TITLE).grid(
            row=0, column=0, sticky="w"
        )
        self.empty_label = ttk.Label(
            self.empty_holder,
            style="Napoveda.TLabel",
            text=EMPTY_HINT,
            wraplength=560,
            justify="left",
        )
        self.empty_label.grid(row=1, column=0, sticky="w", pady=(theme.PAD_S, theme.PAD_M))
        self.empty_button = ttk.Button(
            self.empty_holder,
            text="Nahrát šablonu…",
            style="Primary.TButton",
            command=self.import_template,
        )
        self.empty_button.grid(row=2, column=0, sticky="w")
        self.empty_holder.grid_remove()

    # -- načtení dat -----------------------------------------------------
    def refresh(self) -> None:
        """Znovu načte seznam šablon z knihovny a překreslí tabulku."""

        keep = self.selected_template_id()
        try:
            metas = list(self._store.list())
        except StoreError as exc:
            metas = []
            self._status(str(exc))
            widgets.show_error(self, "Seznam šablon se nepodařilo načíst.", detail=str(exc))

        self._items = {meta.id: meta for meta in metas}

        for row in self.tree.get_children(""):
            self.tree.delete(row)
        for meta in metas:
            self.tree.insert(
                "",
                "end",
                iid=meta.id,
                values=(
                    meta.name,
                    format_tags(meta.tags),
                    str(len(meta.fields)),
                    format_stamp(meta.imported_at),
                ),
            )

        if keep and keep in self._items:
            self.select(keep)
        elif metas:
            self.select(metas[0].id)

        self._update_empty_state()
        self._update_buttons()
        self._update_subtitle()

    def _update_empty_state(self) -> None:
        if self._items:
            self.empty_holder.grid_remove()
            self.table_holder.grid()
        else:
            self.table_holder.grid_remove()
            self.empty_holder.grid()

    def _update_subtitle(self) -> None:
        count = len(self._items)
        if not count:
            self.subtitle.configure(text="Knihovna šablon je prázdná.")
            return
        if count == 1:
            text = "V knihovně je 1 šablona."
        elif 2 <= count <= 4:
            text = f"V knihovně jsou {count} šablony."
        else:
            text = f"V knihovně je {count} šablon."
        self.subtitle.configure(
            text=text + " Dvojklikem na řádek upravíte pole vybrané šablony."
        )

    def _update_buttons(self) -> None:
        has_selection = self.selected_template_id() is not None
        for key in _SELECTION_BUTTONS:
            self.toolbar.set_enabled(key, has_selection and not self._busy)
        self.toolbar.set_enabled(BTN_IMPORT, not self._busy)
        self.toolbar.set_enabled(BTN_FOLDER, not self._busy)
        try:
            self.empty_button.state(["disabled" if self._busy else "!disabled"])
        except tk.TclError:  # pragma: no cover
            pass

    # -- výběr -----------------------------------------------------------
    @property
    def count(self) -> int:
        """Počet šablon v seznamu."""

        return len(self._items)

    def selected_template_id(self) -> str | None:
        """Id vybrané šablony, nebo ``None``."""

        selection = self.tree.selection()
        if not selection:
            return None
        tid = str(selection[0])
        return tid if tid in self._items else None

    def selected_meta(self) -> TemplateMeta | None:
        """Metadata vybrané šablony, nebo ``None``."""

        tid = self.selected_template_id()
        return self._items.get(tid) if tid else None

    def select(self, template_id: str) -> bool:
        """Vybere šablonu podle id. Vrací ``False``, pokud v seznamu není."""

        tid = str(template_id or "")
        if tid not in self._items:
            return False
        try:
            self.tree.selection_set(tid)
            self.tree.focus(tid)
            self.tree.see(tid)
        except tk.TclError:  # pragma: no cover
            return False
        self._update_buttons()
        return True

    def _require_selection(self) -> TemplateMeta | None:
        meta = self.selected_meta()
        if meta is None:
            self._status("Nejdřív vyberte šablonu v seznamu.")
        return meta

    # -- události --------------------------------------------------------
    def _on_select(self, _event: Any = None) -> None:
        self._update_buttons()

    def _on_double_click(self, event: Any = None) -> str | None:
        row = ""
        try:
            row = str(self.tree.identify_row(int(getattr(event, "y", 0) or 0)))
        except (tk.TclError, TypeError, ValueError):  # pragma: no cover
            row = ""
        if row and row in self._items:
            self.select(row)
        elif self.selected_template_id() is None:
            return None
        self.edit_fields()
        return "break"

    def _on_return_key(self, _event: Any = None) -> str | None:
        if self.selected_template_id() is None:
            return None
        self.edit_fields()
        return "break"

    def _on_delete_key(self, _event: Any = None) -> str | None:
        if self.selected_template_id() is None:
            return None
        self.delete()
        return "break"

    # -- nahrání šablony -------------------------------------------------
    def ask_details(
        self,
        *,
        title: str,
        name: str = "",
        description: str = "",
        tags: Sequence[str] = (),
        with_details: bool = True,
        ok_text: str = "Uložit",
        intro: str = "",
    ) -> dict[str, Any] | None:
        """Otevře modální dialog s údaji o šabloně (v testech se podstrčí)."""

        dialog = TemplateDetailsDialog(
            self,
            title=title,
            name=name,
            description=description,
            tags=tags,
            with_details=with_details,
            ok_text=ok_text,
            intro=intro,
        )
        return dialog.show()

    def import_template(self) -> None:
        """„Nahrát šablonu…“ — výběr souboru, dialog s údaji, nahrání ve vlákně."""

        if self._busy:
            return
        chosen = ask_docx_path(self)
        if not chosen:
            self._status("Nahrání šablony bylo zrušeno.")
            return

        source = Path(chosen)
        if source.suffix.lower() != ".docx":
            widgets.show_error(
                self,
                "Vyberte prosím soubor ve formátu .docx.",
                detail=(
                    f"Soubor „{source.name}“ nemá příponu .docx. Starší formát .doc "
                    "otevřete ve Wordu a uložte jako .docx."
                ),
            )
            self._status("Šablona musí být soubor .docx.")
            return

        details = self.ask_details(
            title="Nahrát šablonu",
            name=source.stem,
            with_details=True,
            ok_text="Nahrát",
            intro="Zkontrolujte název, pod kterým se šablona uloží do knihovny.",
        )
        if details is None:
            self._status("Nahrání šablony bylo zrušeno.")
            return

        self.import_file(
            source,
            details.get("name", ""),
            description=details.get("description", ""),
            tags=details.get("tags", ()),
        )

    def import_file(
        self,
        path: Path | str,
        name: str,
        *,
        description: str = "",
        tags: Sequence[str] = (),
    ) -> None:
        """Nahraje šablonu do knihovny ve vlákně (dialogy už proběhly)."""

        if self._busy:
            return

        source = Path(path)
        title = str(name or "").strip() or source.stem
        tag_list = [str(t) for t in (tags or ())]
        note = str(description or "")

        self._set_busy(True, f"Nahrávám šablonu „{title}“ — analyzuji dokument…")

        def work() -> TemplateMeta:
            return self._store.import_docx(source, title, description=note, tags=tag_list)

        widgets.run_in_thread(self, work, self._on_import_done, self._on_import_error)

    def _alive(self) -> bool:
        """Okno mohlo mezitím zaniknout — výsledek vlákna pak nemá kam doručit."""

        try:
            return bool(self.winfo_exists())
        except tk.TclError:  # pragma: no cover - interpret Tk je pryč
            return False

    def _on_import_done(self, meta: TemplateMeta) -> None:
        if not self._alive():  # pragma: no cover - okno zavřené během nahrávání
            return
        self._set_busy(False, "")
        self.refresh()
        self.select(meta.id)

        found = count_fields(len(meta.fields))
        self._status(f"Šablona „{meta.name}“ je nahraná — rozpoznáno {found}.")

        if not meta.fields:
            widgets.show_warning(
                self,
                f"Šablona „{meta.name}“ je nahraná, ale nenašli jsme v ní žádné pole "
                "k vyplnění.",
                detail=(
                    "Místa k doplnění se poznají podle hranatých závorek "
                    "(například [Jan Novák]), žlutého zvýraznění nebo pole pro datum. "
                    "Pole si můžete doplnit ručně v pohledu Pole šablony."
                ),
            )
            self._on_edit_fields(meta.id)
            return

        if widgets.ask_yes_no(
            self,
            f"Šablona „{meta.name}“ je nahraná. Rozpoznáno {found}.",
            detail="Chcete teď zkontrolovat a upravit rozpoznaná pole?",
            default_yes=True,
        ):
            self._on_edit_fields(meta.id)

    def _on_import_error(self, exc: BaseException) -> None:
        if not self._alive():  # pragma: no cover - okno zavřené během nahrávání
            return
        self._set_busy(False, "")
        self.refresh()
        if isinstance(exc, StoreError):
            message = str(exc)
            detail = ""
        else:
            message = "Šablonu se nepodařilo nahrát."
            detail = str(exc) or exc.__class__.__name__
        self._status(message)
        widgets.show_error(self, message, detail=detail)

    def _set_busy(self, busy: bool, message: str) -> None:
        self._busy = bool(busy)
        self._update_buttons()
        if message:
            self._status(message)

    # -- akce nad vybranou šablonou --------------------------------------
    def edit_fields(self) -> None:
        """Přepne na úpravu polí vybrané šablony."""

        meta = self._require_selection()
        if meta is None:
            return
        self._on_edit_fields(meta.id)

    def generate(self) -> None:
        """Přepne na generování dopisu z vybrané šablony."""

        meta = self._require_selection()
        if meta is None:
            return
        self._on_generate(meta.id)

    def duplicate(self) -> None:
        """Vytvoří kopii vybrané šablony pod novým názvem."""

        meta = self._require_selection()
        if meta is None:
            return
        details = self.ask_details(
            title="Duplikovat šablonu",
            name=f"{meta.name} (kopie)",
            with_details=False,
            ok_text="Duplikovat",
            intro=f"Vznikne kopie šablony „{meta.name}“ včetně nastavení polí.",
        )
        if details is None:
            self._status("Duplikování bylo zrušeno.")
            return

        try:
            copy = self._store.duplicate(meta.id, details.get("name", ""))
        except StoreError as exc:
            self._status(str(exc))
            widgets.show_error(self, "Kopii šablony se nepodařilo vytvořit.", detail=str(exc))
            return

        self.refresh()
        self.select(copy.id)
        self._status(f"Vznikla kopie „{copy.name}“.")

    def rename(self) -> None:
        """Přejmenuje vybranou šablonu."""

        meta = self._require_selection()
        if meta is None:
            return
        details = self.ask_details(
            title="Přejmenovat šablonu",
            name=meta.name,
            with_details=False,
            ok_text="Přejmenovat",
        )
        if details is None:
            self._status("Přejmenování bylo zrušeno.")
            return

        new_name = details.get("name", "")
        if new_name == meta.name:
            self._status("Název zůstal beze změny.")
            return

        try:
            renamed = self._store.rename(meta.id, new_name)
        except StoreError as exc:
            self._status(str(exc))
            widgets.show_error(self, "Šablonu se nepodařilo přejmenovat.", detail=str(exc))
            return

        self.refresh()
        self.select(renamed.id)
        self._status(f"Šablona se teď jmenuje „{renamed.name}“.")

    def delete(self) -> None:
        """Smaže vybranou šablonu — po potvrzení."""

        meta = self._require_selection()
        if meta is None:
            return
        if not widgets.ask_yes_no(
            self,
            f"Opravdu smazat šablonu „{meta.name}“?",
            detail=(
                "Smaže se nahraný soubor .docx i nastavení polí. "
                "Už vygenerované dopisy zůstanou beze změny."
            ),
        ):
            self._status("Mazání bylo zrušeno.")
            return

        try:
            self._store.delete(meta.id)
        except StoreError as exc:
            self._status(str(exc))
            widgets.show_error(self, "Šablonu se nepodařilo smazat.", detail=str(exc))
            return

        self.refresh()
        self._status(f"Šablona „{meta.name}“ byla smazána.")

    def open_folder(self) -> None:
        """Otevře složku vybrané šablony (bez výběru celou knihovnu)."""

        meta = self.selected_meta()
        try:
            if meta is not None:
                target = Path(self._store.template_dir(meta.id))
                label = f"Otevřel jsem složku šablony „{meta.name}“."
            else:
                target = Path(self._store.root)
                label = "Otevřel jsem složku s knihovnou šablon."
            target.mkdir(parents=True, exist_ok=True)
            open_in_file_manager(target)
        except (OSError, StoreError) as exc:
            self._status(f"Složku se nepodařilo otevřít: {exc}")
            widgets.show_error(self, "Složku se nepodařilo otevřít.", detail=str(exc))
            return
        self._status(label)
