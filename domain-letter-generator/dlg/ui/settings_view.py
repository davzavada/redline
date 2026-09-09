"""Pohled „Nastavení“ — výstupní složka, chování generování a profil.

Nastavení se **neukládá tlačítkem**: každá změna se rovnou zapíše přes
``settings_saver`` a do stavového řádku se napíše, co se stalo. Psaní do
políčka s cestou se ukládá s krátkou prodlevou, aby se soubor nezapisoval
po každém stisku klávesy; :meth:`SettingsView.save_now` prodlevu přeskočí.

Profil je tabulka *klíč → hodnota*. Klíč odpovídá klíči pole ve formuláři
(``FieldSpec.key``), takže se hodnota předvyplní ve všech šablonách, kde se
takové pole vyskytuje — typicky ``advokat``, ``kontakt_email``, ``telefon``.

Seznam veřejného API::

    SettingsView(master, settings_getter, settings_saver, status)
        refresh()               znovu načte nastavení a překreslí pohled
        save_now()              okamžitě uloží (vrací True při úspěchu)
        current_settings()      Settings poskládané z ovládacích prvků
        profile_values()        aktuální profil jako slovník
        browse_output_dir()     „Procházet…“
        open_data_dir()         „Otevřít složku s daty“
        add_profile_entry() / edit_profile_entry() / remove_profile_entry()
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import tkinter as tk
from tkinter import filedialog, ttk

from .. import config
from ..config import Settings
from . import theme, widgets
from .templates_view import open_in_file_manager

__all__ = [
    "KEY_HELP",
    "PROFILE_KEY_RE",
    "ProfileEntryDialog",
    "SettingsView",
    "ask_directory",
    "validate_profile_key",
]

#: Povolený tvar klíče profilu — stejný jako u klíčů polí (``FieldSpec.key``).
PROFILE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")

#: Nápověda k tvaru klíče.
KEY_HELP = (
    "Malá písmena bez diakritiky, číslice a podtržítko; začíná písmenem. "
    "Například: advokat, kontakt_email, telefon."
)

#: Prodleva (ms) mezi posledním stiskem klávesy a uložením cesty.
SAVE_DELAY_MS = 400


def validate_profile_key(key: str) -> str:
    """Vrátí českou chybovou hlášku, nebo prázdný řetězec, je-li klíč v pořádku."""

    text = str(key or "").strip()
    if not text:
        return "Zadejte klíč údaje."
    if not PROFILE_KEY_RE.match(text):
        return "Klíč smí obsahovat jen malá písmena bez diakritiky, číslice a podtržítko."
    return ""


def ask_directory(parent: tk.Misc | None = None, initial: str = "") -> str:
    """Dialog pro výběr složky. Prázdný řetězec = uživatel zrušil.

    Samostatná funkce (ne metoda) schválně — v testech se dá snadno podstrčit.
    """

    options: dict[str, Any] = {"title": "Vyberte složku pro vygenerované dopisy"}
    if isinstance(parent, tk.Misc):
        options["parent"] = parent
    if initial:
        options["initialdir"] = str(initial)
    chosen = filedialog.askdirectory(**options)
    return str(chosen) if chosen else ""


# ---------------------------------------------------------------------------
# dialog jedné položky profilu
# ---------------------------------------------------------------------------
class ProfileEntryDialog(tk.Toplevel):
    """Modální okno na jednu dvojici klíč/hodnota profilu.

    Výsledek je ve :attr:`result`: ``None`` při zrušení, jinak
    ``{"key": str, "value": str}``.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str = "Výchozí hodnota",
        key: str = "",
        value: str = "",
        known_keys: Sequence[str] = (),
        lock_key: bool = False,
        ok_text: str = "Uložit",
    ) -> None:
        super().__init__(master)
        self.result: dict[str, str] | None = None

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

        self.key_field = widgets.LabeledCombobox(
            body,
            "Klíč údaje",
            values=[str(k) for k in (known_keys or ())],
            value=str(key),
            required=True,
            readonly=False,
            autocomplete=True,
            width=42,
            help_text=KEY_HELP,
        )
        self.key_field.grid(row=0, column=0, sticky="ew")
        if lock_key:
            self.key_field.set_enabled(False)

        self.value_field = widgets.LabeledText(
            body,
            "Výchozí hodnota",
            value=str(value),
            height=3,
            help_text="Předvyplní se do formuláře u každé šablony, kde takové pole je.",
        )
        self.value_field.grid(row=1, column=0, sticky="ew", pady=(theme.PAD_M, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, sticky="e", pady=(theme.PAD_L, 0))
        self.ok_button = ttk.Button(
            buttons, text=str(ok_text), style="Primary.TButton", command=self._on_ok
        )
        self.ok_button.pack(side="right")
        self.cancel_button = ttk.Button(buttons, text="Zrušit", command=self._on_cancel)
        self.cancel_button.pack(side="right", padx=(0, theme.PAD_S))

        self.bind("<Escape>", lambda _e: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self.update_idletasks()
        self._center_on(master)
        try:
            self.grab_set()
        except tk.TclError:  # pragma: no cover - bez správce oken
            pass
        (self.value_field if lock_key else self.key_field).focus_set()

    def _center_on(self, master: tk.Misc) -> None:
        widgets.center_on(self, master)

    def _on_ok(self) -> None:
        key = self.key_field.get().strip()
        problem = validate_profile_key(key)
        if problem:
            self.key_field.set_error(problem)
            self.key_field.focus_set()
            return
        self.result = {"key": key, "value": self.value_field.get().strip()}
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

    def show(self) -> dict[str, str] | None:
        """Počká na zavření okna a vrátí výsledek."""

        self.wait_window()
        return self.result


# ---------------------------------------------------------------------------
# pohled
# ---------------------------------------------------------------------------
class SettingsView(ttk.Frame):
    """Nastavení aplikace; ukládá se hned při každé změně."""

    def __init__(
        self,
        master: tk.Misc,
        settings_getter: Callable[[], Settings] | None = None,
        settings_saver: Callable[[Settings], None] | None = None,
        status: Callable[[str], None] | None = None,
        *,
        known_keys: Callable[[], Iterable[str]] | None = None,
        on_data_home_changed: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("padding", 0)
        super().__init__(master, **kwargs)

        self._getter = settings_getter or config.load_settings
        self._saver = settings_saver or config.save_settings
        self._status = status or (lambda _text: None)
        self._known_keys = known_keys
        self._on_data_home_changed = on_data_home_changed or (lambda: None)
        self._settings = Settings()
        self._profile: dict[str, str] = {}
        self._loading = True
        self._pending_save: str | None = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.scroller = widgets.ScrollableFrame(
            self, padding=(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        )
        self.scroller.grid(row=0, column=0, sticky="nsew")
        body = self.scroller.body
        body.columnconfigure(0, weight=1)

        self._build_header(body)
        self._build_output_card(body)
        self._build_behaviour_card(body)
        self._build_profile_card(body)
        self._build_data_card(body)

        self.bind("<Destroy>", self._on_destroy, add="+")
        self.refresh()

    def _on_destroy(self, event: Any) -> None:
        """Čekající uložení se při zániku pohledu zahodí (jinak by spadlo)."""

        if getattr(event, "widget", None) is not self:
            return
        self._loading = True
        self._cancel_pending_save()

    # -- stavba ----------------------------------------------------------
    def _build_header(self, body: ttk.Frame) -> None:
        ttk.Label(body, style="Nadpis.TLabel", text="Nastavení").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            body,
            style="Napoveda.TLabel",
            text="Změny se ukládají hned, žádné tlačítko Uložit tu není.",
            wraplength=640,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, theme.PAD_M))

    def _build_output_card(self, body: ttk.Frame) -> None:
        card = widgets.Card(
            body,
            "Výstupní složka",
            subtitle="Kam se ukládají vygenerované dopisy.",
        )
        card.grid(row=2, column=0, sticky="ew")

        self.output_field = widgets.LabeledEntry(
            card.body,
            "Složka",
            width=52,
            help_text="",
        )
        self.output_field.grid(row=0, column=0, sticky="ew")
        self.browse_button = ttk.Button(
            self.output_field, text="Procházet…", command=self.browse_output_dir
        )
        self.browse_button.grid(
            row=1, column=1, sticky="w", padx=(theme.PAD_S, 0), pady=(2, 0)
        )
        self.output_field.bind_change(self._on_output_changed)

    def _build_behaviour_card(self, body: ttk.Frame) -> None:
        card = widgets.Card(
            body,
            "Generování",
            subtitle="Co se má stát s hotovým dopisem a s neúplnými místy v šabloně.",
        )
        card.grid(row=3, column=0, sticky="ew", pady=(theme.PAD_M, 0))

        self.var_open_after = tk.BooleanVar(master=self, value=True)
        self.var_clear_highlight = tk.BooleanVar(master=self, value=True)
        self.var_keep_unfilled = tk.BooleanVar(master=self, value=True)

        self.check_open_after = self._add_check(
            card.body,
            0,
            self.var_open_after,
            "Otevřít dopis hned po vygenerování",
            "Hotový soubor se otevře ve Wordu.",
            "Dopis se po vygenerování bude otevírat ve Wordu.",
            "Dopis se po vygenerování otevírat nebude.",
        )
        self.check_clear_highlight = self._add_check(
            card.body,
            1,
            self.var_clear_highlight,
            "Odstranit žluté zvýraznění vyplněných míst",
            "Doplněný text nezůstane zvýrazněný jako v šabloně.",
            "Zvýraznění se z vyplněných míst odstraní.",
            "Zvýraznění zůstane i na vyplněných místech.",
        )
        self.check_keep_unfilled = self._add_check(
            card.body,
            2,
            self.var_keep_unfilled,
            "Ponechat nevyplněné placeholdery v dokumentu",
            "Nevyplněná místa zůstanou v dopise tak, jak jsou v šabloně — "
            "je vidět, co ještě chybí. Po vypnutí se z dopisu smažou.",
            "Nevyplněná místa zůstanou v dopise.",
            "Nevyplněná místa se z dopisu smažou.",
        )

    def _add_check(
        self,
        parent: ttk.Frame,
        row: int,
        variable: tk.BooleanVar,
        text: str,
        help_text: str,
        on_message: str,
        off_message: str,
    ) -> ttk.Checkbutton:
        check = ttk.Checkbutton(parent, text=text, variable=variable, onvalue=True, offvalue=False)
        check.grid(row=row * 2, column=0, sticky="w", pady=(0 if row == 0 else theme.PAD_S, 0))
        ttk.Label(
            parent,
            style="Napoveda.TLabel",
            text=help_text,
            wraplength=560,
            justify="left",
        ).grid(row=row * 2 + 1, column=0, sticky="w", padx=(theme.PAD_L + theme.PAD_S, 0))

        variable.trace_add(
            "write",
            lambda *_a, var=variable, yes=on_message, no=off_message: self._on_switch(
                var, yes, no
            ),
        )
        return check

    def _build_profile_card(self, body: ttk.Frame) -> None:
        card = widgets.Card(
            body,
            "Profil — výchozí hodnoty",
            subtitle=(
                "Hodnoty podle klíče pole. Předvyplní se do formuláře napříč všemi "
                "šablonami, takže podpis ani kontakt nemusíte psát pokaždé znovu."
            ),
        )
        card.grid(row=4, column=0, sticky="ew", pady=(theme.PAD_M, 0))
        card.body.rowconfigure(0, weight=1)

        holder = ttk.Frame(card.body)
        holder.grid(row=0, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        self.profile_tree = ttk.Treeview(
            holder,
            columns=("klic", "hodnota"),
            show="headings",
            selectmode="browse",
            height=6,
        )
        self.profile_tree.heading("klic", text="Klíč", anchor="w")
        self.profile_tree.heading("hodnota", text="Hodnota", anchor="w")
        self.profile_tree.column("klic", width=180, minwidth=120, anchor="w", stretch=False)
        self.profile_tree.column("hodnota", width=360, minwidth=160, anchor="w", stretch=True)
        self.profile_tree.grid(row=0, column=0, sticky="nsew")

        self.profile_scrollbar = ttk.Scrollbar(
            holder, orient="vertical", command=self.profile_tree.yview
        )
        self.profile_scrollbar.grid(row=0, column=1, sticky="ns")
        self.profile_tree.configure(yscrollcommand=self.profile_scrollbar.set)

        self.profile_tree.bind("<Double-1>", self._on_profile_double_click)
        self.profile_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_profile_buttons())

        self.profile_empty = ttk.Label(
            card.body,
            style="Napoveda.TLabel",
            text="Profil je zatím prázdný. Přidejte třeba klíč „advokat“ s vaším podpisem.",
            wraplength=560,
            justify="left",
        )
        self.profile_empty.grid(row=1, column=0, sticky="w", pady=(theme.PAD_S, 0))

        self.profile_toolbar = widgets.Toolbar(card.body, padding=(0, theme.PAD_M, 0, 0))
        self.profile_toolbar.grid(row=2, column=0, sticky="ew")
        self.profile_toolbar.add_button("Přidat…", self.add_profile_entry, key="pridat")
        self.profile_toolbar.add_button("Upravit…", self.edit_profile_entry, key="upravit")
        self.profile_toolbar.add_button(
            "Smazat", self.remove_profile_entry, danger=True, key="smazat"
        )

    def _build_data_card(self, body: ttk.Frame) -> None:
        card = widgets.Card(
            body,
            "Složka s daty",
            subtitle=(
                "Šablony, nastavení a historie vyplněných hodnot leží tady. "
                "Můžete je přesunout třeba na OneDrive nebo na síťový disk — "
                "budete je mít zálohované a dostanete se k nim i z jiného počítače."
            ),
        )
        card.grid(row=5, column=0, sticky="ew", pady=(theme.PAD_M, 0))
        card.body.columnconfigure(0, weight=1)

        self.data_dir_label = ttk.Label(
            card.body,
            style="Zvyrazneno.TLabel",
            text="",
            wraplength=560,
            justify="left",
        )
        self.data_dir_label.grid(row=0, column=0, sticky="w")

        self.data_source_label = ttk.Label(
            card.body,
            style="Napoveda.TLabel",
            text="",
            wraplength=560,
            justify="left",
        )
        self.data_source_label.grid(row=1, column=0, sticky="w", pady=(2, 0))

        buttons = ttk.Frame(card.body)
        buttons.grid(row=2, column=0, sticky="w", pady=(theme.PAD_M, 0))

        self.change_data_button = ttk.Button(
            buttons, text="Změnit složku…", command=self.change_data_dir
        )
        self.change_data_button.grid(row=0, column=0)

        self.reset_data_button = ttk.Button(
            buttons, text="Obnovit výchozí", command=self.reset_data_dir
        )
        self.reset_data_button.grid(row=0, column=1, padx=(theme.PAD_S, 0))

        ttk.Button(buttons, text="Otevřít složku", command=self.open_data_dir).grid(
            row=0, column=2, padx=(theme.PAD_S, 0)
        )

    # -- načtení a uložení -----------------------------------------------
    def refresh(self) -> None:
        """Znovu načte nastavení a promítne ho do ovládacích prvků."""

        self._cancel_pending_save()
        self._loading = True
        try:
            settings = self._getter()
        except Exception as exc:  # noqa: BLE001 - nastavení se nesmí rozbít o výjimku
            settings = Settings()
            self._status(f"Nastavení se nepodařilo načíst: {exc}")

        self._settings = settings
        self._profile = dict(settings.profile)

        self.output_field.set(settings.output_dir)
        self.var_open_after.set(bool(settings.open_after_generate))
        self.var_clear_highlight.set(bool(settings.clear_highlight))
        self.var_keep_unfilled.set(bool(settings.keep_unfilled))
        self._refresh_profile_tree()
        self._update_output_help()
        self._refresh_data_card()
        self._loading = False

    def current_settings(self) -> Settings:
        """Nastavení poskládané z ovládacích prvků (beze změny na disku)."""

        settings = Settings.from_dict(self._settings.to_dict())
        settings.output_dir = self.output_field.get().strip()
        settings.open_after_generate = bool(self.var_open_after.get())
        settings.clear_highlight = bool(self.var_clear_highlight.get())
        settings.keep_unfilled = bool(self.var_keep_unfilled.get())
        settings.profile = dict(self._profile)
        return settings

    def profile_values(self) -> dict[str, str]:
        """Aktuální profil jako slovník klíč → hodnota."""

        return dict(self._profile)

    def save_now(self, message: str = "") -> bool:
        """Uloží nastavení okamžitě (i čekající změnu cesty). Vrací úspěch."""

        self._cancel_pending_save()
        return self._save(message)

    def _save(self, message: str = "") -> bool:
        if self._loading:
            return False
        settings = self.current_settings()
        try:
            self._saver(settings)
        except Exception as exc:  # noqa: BLE001 - chybu ukážeme, nespadneme
            self._status(f"Nastavení se nepodařilo uložit: {exc}")
            widgets.show_error(self, "Nastavení se nepodařilo uložit.", detail=str(exc))
            return False
        self._settings = settings
        self._status(message or "Nastavení uloženo.")
        return True

    def _schedule_save(self, message: str = "") -> None:
        """Uložení s krátkou prodlevou — pro psaní do políčka."""

        if self._loading:
            return
        self._cancel_pending_save()
        try:
            self._pending_save = self.after(SAVE_DELAY_MS, lambda: self._flush_save(message))
        except tk.TclError:  # pragma: no cover - okno zaniklo
            self._pending_save = None

    def _flush_save(self, message: str = "") -> None:
        self._pending_save = None
        try:
            alive = bool(self.winfo_exists())
        except tk.TclError:  # pragma: no cover - interpret Tk je pryč
            alive = False
        if alive:
            self._save(message)

    def _cancel_pending_save(self) -> None:
        handle, self._pending_save = self._pending_save, None
        if handle is None:
            return
        try:
            self.after_cancel(handle)
        except (tk.TclError, ValueError):  # pragma: no cover
            pass

    # -- výstupní složka --------------------------------------------------
    def _on_output_changed(self, _value: str = "") -> None:
        self._update_output_help()
        self._schedule_save("Výstupní složka uložena.")

    def _update_output_help(self) -> None:
        raw = self.output_field.get().strip()
        if not raw:
            self.output_field.set_help(
                f"Prázdné pole znamená výchozí složku: {config.default_output_dir()}"
            )
            return
        path = Path(raw)
        if path.is_dir():
            self.output_field.set_help("Složka existuje, dopisy se ukládají do ní.")
        else:
            self.output_field.set_help(
                "Složka zatím neexistuje — vytvoří se při prvním vygenerování dopisu."
            )

    def browse_output_dir(self) -> None:
        """„Procházet…“ — výběr výstupní složky."""

        current = self.output_field.get().strip()
        initial = current if Path(current).is_dir() else str(config.default_output_dir().parent)
        chosen = ask_directory(self, initial)
        if not chosen:
            self._status("Výběr složky byl zrušen.")
            return
        self.output_field.set(chosen)
        self.save_now(f"Výstupní složka je teď „{chosen}“.")

    def _refresh_data_card(self) -> None:
        """Promítne do karty, kde data leží a čím je to určené."""

        status = config.data_home_status()
        self.data_dir_label.configure(text=str(status.path))
        self.data_source_label.configure(text=status.description)

        state = "normal" if status.can_change else "disabled"
        self.change_data_button.configure(state=state)
        self.reset_data_button.configure(
            state="normal" if status.can_change and not status.is_default else "disabled"
        )

    def change_data_dir(self) -> None:
        """Přepne aplikaci na jinou složku s daty."""

        status = config.data_home_status()
        if not status.can_change:
            widgets.show_error(self, "Složku s daty teď nejde změnit.", detail=status.description)
            return

        chosen = ask_directory(self, initial=str(status.path))
        if not chosen:
            self._status("Výběr složky byl zrušen.")
            return

        target = Path(chosen)
        if target == status.path:
            self._status("Zvolili jste složku, která se používá už teď.")
            return

        # Ve zvolené složce už data jsou -> napojíme se na ně, nic nepřepisujeme.
        move = False
        if config.has_data(target):
            if not widgets.ask_yes_no(
                self,
                "Napojit se na existující data?",
                detail=(
                    f"Ve složce „{target}“ už šablony jsou.\n\n"
                    "Aplikace je začne používat. Vaše dosavadní šablony zůstanou "
                    f"nedotčené v „{status.path}“ — jen se k nim přestane hlásit."
                ),
            ):
                self._status("Změna složky byla zrušena.")
                return
        elif config.has_data(status.path):
            move = widgets.ask_yes_no(
                self,
                "Přesunout stávající data?",
                detail=(
                    f"Zvolená složka „{target}“ je prázdná.\n\n"
                    "Ano — šablony, nastavení a historii tam přesunu.\n"
                    "Ne — začnu tam s prázdnou knihovnou a dosavadní data nechám, "
                    "kde jsou."
                ),
            )

        try:
            new_home = config.set_app_home(target, move_existing=move)
        except config.ConfigError as exc:
            self._status(f"Složku s daty se nepodařilo změnit: {exc}")
            widgets.show_error(self, "Složku s daty se nepodařilo změnit.", detail=str(exc))
            return

        self._after_data_home_changed(
            f"Data jsou teď ve složce {new_home}."
            if not move
            else f"Data jsem přesunul do složky {new_home}."
        )

    def reset_data_dir(self) -> None:
        """Vrátí aplikaci k výchozímu umístění dat v profilu uživatele."""

        status = config.data_home_status()
        if status.is_default or not status.can_change:
            return

        vychozi = config.default_app_home()
        move = False
        if config.has_data(status.path) and not config.has_data(vychozi):
            move = widgets.ask_yes_no(
                self,
                "Přesunout data zpět?",
                detail=(
                    f"Vrátit se k výchozímu umístění „{vychozi}“.\n\n"
                    "Ano — šablony, nastavení a historii tam přesunu.\n"
                    "Ne — jen se přepnu a data nechám, kde jsou."
                ),
            )

        try:
            new_home = config.set_app_home(None, move_existing=move)
        except config.ConfigError as exc:
            self._status(f"Výchozí umístění se nepodařilo obnovit: {exc}")
            widgets.show_error(self, "Výchozí umístění se nepodařilo obnovit.", detail=str(exc))
            return

        self._after_data_home_changed(f"Data jsou zpět ve výchozí složce {new_home}.")

    def _after_data_home_changed(self, message: str) -> None:
        """Po změně složky musí aplikace znovu načíst šablony i nastavení."""

        self.refresh()
        self._status(message)
        try:
            self._on_data_home_changed()
        except Exception as exc:  # noqa: BLE001 - překreslení nesmí nic shodit
            self._status(f"Data se přepnula, ale překreslení selhalo: {exc}")

    def open_data_dir(self) -> None:
        """„Otevřít složku s daty“ — domovský adresář aplikace."""

        target = config.app_home()
        try:
            target.mkdir(parents=True, exist_ok=True)
            open_in_file_manager(target)
        except OSError as exc:
            self._status(f"Složku s daty se nepodařilo otevřít: {exc}")
            widgets.show_error(self, "Složku s daty se nepodařilo otevřít.", detail=str(exc))
            return
        self._status(f"Otevřel jsem složku {target}.")

    # -- přepínače --------------------------------------------------------
    def _on_switch(self, variable: tk.BooleanVar, on_message: str, off_message: str) -> None:
        if self._loading:
            return
        try:
            value = bool(variable.get())
        except tk.TclError:  # pragma: no cover
            return
        self._save(on_message if value else off_message)

    # -- profil -----------------------------------------------------------
    def _refresh_profile_tree(self) -> None:
        keep = self.selected_profile_key()
        for row in self.profile_tree.get_children(""):
            self.profile_tree.delete(row)
        for key in sorted(self._profile):
            value = self._profile[key]
            self.profile_tree.insert(
                "", "end", iid=key, values=(key, value.replace("\n", " ⏎ "))
            )
        if keep and keep in self._profile:
            self.profile_tree.selection_set(keep)

        if self._profile:
            self.profile_empty.grid_remove()
        else:
            self.profile_empty.grid()
        self._update_profile_buttons()

    def _update_profile_buttons(self) -> None:
        has_selection = self.selected_profile_key() is not None
        self.profile_toolbar.set_enabled("upravit", has_selection)
        self.profile_toolbar.set_enabled("smazat", has_selection)

    def selected_profile_key(self) -> str | None:
        """Klíč vybraného řádku profilu, nebo ``None``."""

        selection = self.profile_tree.selection()
        if not selection:
            return None
        key = str(selection[0])
        return key if key in self._profile else None

    def _suggested_keys(self) -> list[str]:
        if self._known_keys is None:
            return []
        try:
            return sorted({str(k) for k in self._known_keys() if str(k)})
        except Exception:  # noqa: BLE001 - nabídka klíčů je jen pohodlí navíc
            return []

    def ask_profile_entry(
        self,
        *,
        title: str = "Výchozí hodnota",
        key: str = "",
        value: str = "",
        lock_key: bool = False,
        ok_text: str = "Uložit",
    ) -> dict[str, str] | None:
        """Otevře modální dialog jedné položky profilu (v testech se podstrčí)."""

        dialog = ProfileEntryDialog(
            self,
            title=title,
            key=key,
            value=value,
            known_keys=self._suggested_keys(),
            lock_key=lock_key,
            ok_text=ok_text,
        )
        return dialog.show()

    def add_profile_entry(self) -> None:
        """Přidá do profilu novou výchozí hodnotu."""

        entry = self.ask_profile_entry(title="Nová výchozí hodnota", ok_text="Přidat")
        if entry is None:
            self._status("Přidání výchozí hodnoty bylo zrušeno.")
            return

        key = entry.get("key", "").strip()
        problem = validate_profile_key(key)
        if problem:
            self._status(problem)
            widgets.show_error(self, problem, detail=KEY_HELP)
            return

        if key in self._profile and not widgets.ask_yes_no(
            self,
            f"Klíč „{key}“ už v profilu je.",
            detail="Chcete jeho hodnotu přepsat?",
            default_yes=True,
        ):
            self._status("Výchozí hodnota zůstala beze změny.")
            return

        self._profile[key] = entry.get("value", "")
        self._refresh_profile_tree()
        self.profile_tree.selection_set(key)
        self.save_now(f"Výchozí hodnota „{key}“ byla uložena.")

    def edit_profile_entry(self) -> None:
        """Upraví hodnotu vybraného řádku profilu."""

        key = self.selected_profile_key()
        if key is None:
            self._status("Nejdřív vyberte řádek profilu.")
            return

        entry = self.ask_profile_entry(
            title=f"Výchozí hodnota — {key}",
            key=key,
            value=self._profile.get(key, ""),
            lock_key=True,
        )
        if entry is None:
            self._status("Úprava výchozí hodnoty byla zrušena.")
            return

        self._profile[key] = entry.get("value", "")
        self._refresh_profile_tree()
        self.profile_tree.selection_set(key)
        self.save_now(f"Výchozí hodnota „{key}“ byla uložena.")

    def remove_profile_entry(self) -> None:
        """Smaže vybraný řádek profilu — po potvrzení."""

        key = self.selected_profile_key()
        if key is None:
            self._status("Nejdřív vyberte řádek profilu.")
            return

        if not widgets.ask_yes_no(
            self,
            f"Opravdu smazat výchozí hodnotu „{key}“?",
            detail="Ve formulářích se pak toto pole nebude předvyplňovat.",
        ):
            self._status("Mazání bylo zrušeno.")
            return

        self._profile.pop(key, None)
        self._refresh_profile_tree()
        self.save_now(f"Výchozí hodnota „{key}“ byla smazána.")

    def _on_profile_double_click(self, event: Any = None) -> str | None:
        row = ""
        try:
            row = str(self.profile_tree.identify_row(int(getattr(event, "y", 0) or 0)))
        except (tk.TclError, TypeError, ValueError):  # pragma: no cover
            row = ""
        if row and row in self._profile:
            self.profile_tree.selection_set(row)
        elif self.selected_profile_key() is None:
            return None
        self.edit_profile_entry()
        return "break"
