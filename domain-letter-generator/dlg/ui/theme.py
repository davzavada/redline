"""Vzhled aplikace: ttk styly, barvy, odsazení a písma.

Modul jde naimportovat i na stroji bez displeje — samotný import nevytváří
okno ani interpret Tk. Barvy a odsazení jsou obyčejné konstanty, styly vzniknou
až voláním :func:`apply_theme` nad už existujícím oknem.

Pořadí při startu aplikace::

    from dlg.ui import theme

    theme.enable_dpi_awareness()      # JEŠTĚ PŘED vytvořením okna!
    root = tk.Tk()
    theme.apply_theme(root)

Pojmenované styly, na které se smí spoléhat zbytek aplikace (viz
:data:`STYLE_NAMES`)::

    Nadpis.TLabel        velký nadpis pohledu
    Podnadpis.TLabel     nadpis sekce / karty
    Popisek.TLabel       popisek formulářového pole
    Napoveda.TLabel      drobná šedá nápověda pod polem
    Chyba.TLabel         chybová hláška (červená)
    Uspech.TLabel        potvrzení akce (zelená)
    Primary.TButton      hlavní tlačítko dialogu / pohledu
    Nebezpecne.TButton   destruktivní akce (Smazat)
    Karta.TFrame         rámeček karty (1px rámeček)
    Nav.TFrame           podklad svislé navigace
    Nav.TLabel           text na podkladu navigace
    Nav.TButton          položka svislé navigace
    Nav.Selected.TButton vybraná položka svislé navigace
    Chyba.TEntry         vstupní pole s chybou
    Chyba.TCombobox      combobox s chybou
    Treeview             tabulka s vyšším řádkem (viz ROW_HEIGHT)
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Iterable

__all__ = [
    "BASE_FONT_SIZE",
    "COLOR_ACCENT",
    "COLOR_ACCENT_ACTIVE",
    "COLOR_ACCENT_SOFT",
    "COLOR_BG",
    "COLOR_BORDER",
    "COLOR_DISABLED",
    "COLOR_ERROR",
    "COLOR_ERROR_SOFT",
    "COLOR_SUCCESS",
    "COLOR_SURFACE",
    "COLOR_TEXT",
    "COLOR_TEXT_MUTED",
    "COLOR_WARNING",
    "COLORS",
    "FONT_BASE",
    "FONT_BOLD",
    "FONT_HEADING",
    "FONT_SMALL",
    "FONT_SUBHEADING",
    "FONT_TEXT",
    "PAD_L",
    "PAD_M",
    "PAD_S",
    "ROW_HEIGHT",
    "STYLE_NAMES",
    "WINDOW_MIN_SIZE",
    "apply_theme",
    "configure_fonts",
    "enable_dpi_awareness",
    "font_name",
    "is_windows",
    "preferred_theme_names",
]

# --- barvy -------------------------------------------------------------------
# Světlá, střízlivá paleta; modrá je jediný výrazný akcent (advokátní aplikace).

#: Plocha obsahu, karty, vstupní pole.
COLOR_SURFACE = "#ffffff"
#: Okno a svislá navigace — o odstín tmavší než obsah.
COLOR_BG = "#eef1f5"
#: Linky, rámečky, oddělovače.
COLOR_BORDER = "#c9d1da"
#: Základní barva textu.
COLOR_TEXT = "#1b2733"
#: Doplňkový text (nápovědy, stavový řádek).
COLOR_TEXT_MUTED = "#5a6a7a"
#: Akcent — tlačítka, výběr v navigaci.
COLOR_ACCENT = "#1f4e79"
#: Akcent pod kurzorem / stisknutý.
COLOR_ACCENT_ACTIVE = "#17395a"
#: Světlý akcent — podbarvení výběru.
COLOR_ACCENT_SOFT = "#dbe6f1"
#: Chyba.
COLOR_ERROR = "#a4262c"
#: Podbarvení pole s chybou.
COLOR_ERROR_SOFT = "#fdf5f5"
#: Potvrzení, hotovo.
COLOR_SUCCESS = "#1e7d34"
#: Varování.
COLOR_WARNING = "#8a6d1f"
#: Nedostupné ovládací prvky.
COLOR_DISABLED = "#95a3b1"

#: Všechny barvy pohromadě — hodí se pro ``tk`` widgety (Canvas, Text, …).
COLORS: dict[str, str] = {
    "surface": COLOR_SURFACE,
    "bg": COLOR_BG,
    "border": COLOR_BORDER,
    "text": COLOR_TEXT,
    "text_muted": COLOR_TEXT_MUTED,
    "accent": COLOR_ACCENT,
    "accent_active": COLOR_ACCENT_ACTIVE,
    "accent_soft": COLOR_ACCENT_SOFT,
    "error": COLOR_ERROR,
    "error_soft": COLOR_ERROR_SOFT,
    "success": COLOR_SUCCESS,
    "warning": COLOR_WARNING,
    "disabled": COLOR_DISABLED,
}

# --- odsazení ----------------------------------------------------------------

#: Malé odsazení (mezi popiskem a polem).
PAD_S = 4
#: Běžné odsazení (mezi prvky formuláře).
PAD_M = 8
#: Velké odsazení (okraje pohledu, mezi sekcemi).
PAD_L = 16
#: Výška řádku v tabulkách (``Treeview``).
ROW_HEIGHT = 26
#: Doporučená minimální velikost okna aplikace.
WINDOW_MIN_SIZE = (960, 640)

# --- písma -------------------------------------------------------------------

#: Základní velikost písma (v bodech).
BASE_FONT_SIZE = 10
#: Kandidáti na systémové písmo na Windows (první existující vyhraje).
WINDOWS_FONT_CANDIDATES: tuple[str, ...] = ("Segoe UI", "Tahoma", "Arial")

#: Systémové písmo — nikdy se nevytváří, Tk ho má vždy.
FONT_BASE = "TkDefaultFont"
#: Písmo víceřádkových polí (``tk.Text``).
FONT_TEXT = "TkTextFont"
#: Odvozená písma; vznikají v :func:`apply_theme`.
FONT_BOLD = "DlgTucne"
FONT_SMALL = "DlgMale"
FONT_HEADING = "DlgNadpis"
FONT_SUBHEADING = "DlgPodnadpis"

#: Styly, které :func:`apply_theme` zaručeně definuje.
STYLE_NAMES: tuple[str, ...] = (
    "Nadpis.TLabel",
    "Podnadpis.TLabel",
    "Popisek.TLabel",
    "Napoveda.TLabel",
    "Chyba.TLabel",
    "Uspech.TLabel",
    "Primary.TButton",
    "Nebezpecne.TButton",
    "Karta.TFrame",
    "Nav.TFrame",
    "Nav.TLabel",
    "Nav.TButton",
    "Nav.Selected.TButton",
    "Chyba.TEntry",
    "Chyba.TCombobox",
    "Treeview",
)


def is_windows() -> bool:
    """Oddělené kvůli testovatelnosti (v testech se dá podstrčit)."""

    return os.name == "nt"


def preferred_theme_names() -> tuple[str, ...]:
    """Pořadí, ve kterém se zkouší vestavěné ttk motivy."""

    if is_windows():
        return ("vista", "winnative", "clam", "default")
    return ("clam", "default")


def enable_dpi_awareness() -> bool:
    """Na Windows zapne DPI awareness, aby okno nebylo rozmazané.

    Musí se volat **před** vytvořením okna. Na ostatních platformách i při
    jakémkoli selhání jen vrátí ``False`` — nikdy nevyhodí výjimku.
    """

    if not is_windows():
        return False

    try:  # pragma: no cover - běží jen na Windows
        import ctypes

        # PROCESS_SYSTEM_DPI_AWARE = 1
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        return True
    except Exception:
        pass

    try:  # pragma: no cover - starší Windows bez shcore.dll
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


# --- písma: pomocné funkce ---------------------------------------------------


def _font_families(root: tk.Misc | None) -> set[str]:
    try:
        return {str(name) for name in tkfont.families(root)}
    except Exception:
        return set()


def _existing_font_names(root: tk.Misc | None) -> set[str]:
    try:
        return {str(name) for name in tkfont.names(root)}
    except Exception:
        return set()


def _nametofont(name: str, root: tk.Misc | None) -> tkfont.Font | None:
    try:
        return tkfont.nametofont(name, root=root)
    except TypeError:  # pragma: no cover - starší podpis bez ``root``
        try:
            return tkfont.nametofont(name)
        except Exception:
            return None
    except Exception:
        return None


def font_name(name: str, root: tk.Misc | None = None) -> str:
    """Vrátí jméno písma, existuje-li; jinak systémové ``TkDefaultFont``.

    Díky tomu se dá na písma odkazovat i v případě, že se je nepodařilo
    vytvořit (chybějící rodina písma, ořezaný Tk).
    """

    text = str(name or "")
    if not text:
        return FONT_BASE
    if text in _existing_font_names(root):
        return text
    return FONT_BASE


def configure_fonts(root: tk.Misc) -> str:
    """Nastaví systémová písma. Na Windows Segoe UI 10, jinde nechá výchozí.

    Vrací název použité rodiny písma (prázdný řetězec = ponecháno výchozí).
    Funkce nikdy nespadne — chybějící písmo prostě znamená výchozí vzhled.
    """

    if not is_windows():
        return ""

    families = _font_families(root)
    family = ""
    for candidate in WINDOWS_FONT_CANDIDATES:
        if candidate in families:
            family = candidate
            break
    if not family:
        return ""

    sizes = {
        "TkDefaultFont": BASE_FONT_SIZE,
        "TkTextFont": BASE_FONT_SIZE,
        "TkMenuFont": BASE_FONT_SIZE,
        "TkHeadingFont": BASE_FONT_SIZE,
        "TkIconFont": BASE_FONT_SIZE,
        "TkTooltipFont": BASE_FONT_SIZE - 1,
    }
    for name, size in sizes.items():
        font = _nametofont(name, root)
        if font is None:
            continue
        try:
            font.configure(family=family, size=size)
        except Exception:
            continue
    return family


def _base_metrics(root: tk.Misc | None) -> tuple[str, int, bool]:
    """Rodina a velikost základního písma; třetí položka = velikost v pixelech."""

    font = _nametofont(FONT_BASE, root)
    if font is None:
        return ("", BASE_FONT_SIZE, False)
    try:
        family = str(font.actual("family") or "")
        size = int(font.actual("size") or BASE_FONT_SIZE)
    except Exception:
        return ("", BASE_FONT_SIZE, False)
    if size == 0:
        size = BASE_FONT_SIZE
    return (family, abs(size), size < 0)


def _derive_font(root: tk.Misc, name: str, *, delta: int = 0, weight: str = "normal") -> str:
    """Vytvoří (nebo přenastaví) pojmenované písmo odvozené od základního."""

    family, size, in_pixels = _base_metrics(root)
    new_size = max(1, size + delta)
    if in_pixels:
        new_size = -new_size

    options: dict[str, object] = {"size": new_size, "weight": weight}
    if family:
        options["family"] = family

    try:
        if name in _existing_font_names(root):
            font = tkfont.Font(root=root, name=name, exists=True)
            font.configure(**options)  # type: ignore[arg-type]
        else:
            tkfont.Font(root=root, name=name, exists=False, **options)  # type: ignore[arg-type]
    except Exception:
        return FONT_BASE
    return name


def _create_fonts(root: tk.Misc) -> None:
    _derive_font(root, FONT_BOLD, delta=0, weight="bold")
    _derive_font(root, FONT_SMALL, delta=-1)
    _derive_font(root, FONT_HEADING, delta=6, weight="bold")
    _derive_font(root, FONT_SUBHEADING, delta=2, weight="bold")


# --- styly -------------------------------------------------------------------


def _use_theme(style: ttk.Style, names: Iterable[str]) -> str:
    """Zapne první dostupný motiv ze seznamu. Vrací jméno použitého motivu."""

    try:
        available = set(style.theme_names())
    except tk.TclError:  # pragma: no cover - rozbitý Tk
        return ""

    for name in names:
        if name not in available:
            continue
        try:
            style.theme_use(name)
            return name
        except tk.TclError:  # pragma: no cover - motiv se nedá zapnout
            continue
    try:
        return str(style.theme_use())
    except tk.TclError:  # pragma: no cover
        return ""


def _configure_styles(style: ttk.Style, root: tk.Misc) -> None:
    heading = font_name(FONT_HEADING, root)
    subheading = font_name(FONT_SUBHEADING, root)
    small = font_name(FONT_SMALL, root)
    bold = font_name(FONT_BOLD, root)
    base = font_name(FONT_BASE, root)

    # Výchozí vzhled všeho — díky tomu ladí popisky s podkladem i tam,
    # kde pohled použije obyčejný ttk.Frame bez vlastního stylu.
    style.configure(
        ".",
        background=COLOR_SURFACE,
        foreground=COLOR_TEXT,
        fieldbackground=COLOR_SURFACE,
        bordercolor=COLOR_BORDER,
        darkcolor=COLOR_SURFACE,
        lightcolor=COLOR_SURFACE,
        troughcolor=COLOR_BG,
        font=base,
    )
    style.configure("TFrame", background=COLOR_SURFACE)
    style.configure("TLabelframe", background=COLOR_SURFACE, bordercolor=COLOR_BORDER)
    style.configure("TLabelframe.Label", background=COLOR_SURFACE, font=bold)
    style.configure("TLabel", background=COLOR_SURFACE, foreground=COLOR_TEXT)
    style.configure("TSeparator", background=COLOR_BORDER)
    style.configure("TCheckbutton", background=COLOR_SURFACE)
    style.configure("TRadiobutton", background=COLOR_SURFACE)
    style.configure("TNotebook", background=COLOR_BG, bordercolor=COLOR_BORDER)
    style.configure("TNotebook.Tab", padding=(PAD_M, PAD_S))

    # Podklad okna a svislé navigace.
    style.configure("Okno.TFrame", background=COLOR_BG)
    style.configure("Nav.TFrame", background=COLOR_BG)
    style.configure(
        "Nav.TLabel", background=COLOR_BG, foreground=COLOR_TEXT_MUTED, font=small
    )

    # Karta = bílá plocha s tenkým rámečkem.
    style.configure(
        "Karta.TFrame",
        background=COLOR_SURFACE,
        bordercolor=COLOR_BORDER,
        lightcolor=COLOR_BORDER,
        darkcolor=COLOR_BORDER,
        relief="solid",
        borderwidth=1,
    )

    # Texty.
    style.configure("Nadpis.TLabel", font=heading, foreground=COLOR_TEXT)
    style.configure("Podnadpis.TLabel", font=subheading, foreground=COLOR_TEXT)
    style.configure("Popisek.TLabel", font=base, foreground=COLOR_TEXT)
    style.configure("Napoveda.TLabel", font=small, foreground=COLOR_TEXT_MUTED)
    style.configure("Chyba.TLabel", font=small, foreground=COLOR_ERROR)
    style.configure("Uspech.TLabel", font=small, foreground=COLOR_SUCCESS)
    style.configure("Zvyrazneno.TLabel", font=bold, foreground=COLOR_TEXT)

    # Tlačítka.
    style.configure("TButton", padding=(PAD_M, PAD_S), font=base)
    style.configure(
        "Primary.TButton",
        font=bold,
        foreground=COLOR_SURFACE,
        background=COLOR_ACCENT,
        bordercolor=COLOR_ACCENT,
        lightcolor=COLOR_ACCENT,
        darkcolor=COLOR_ACCENT,
        focuscolor=COLOR_SURFACE,
    )
    style.map(
        "Primary.TButton",
        background=[
            ("disabled", COLOR_DISABLED),
            ("pressed", COLOR_ACCENT_ACTIVE),
            ("active", COLOR_ACCENT_ACTIVE),
        ],
        foreground=[("disabled", COLOR_BG), ("!disabled", COLOR_SURFACE)],
        bordercolor=[("!disabled", COLOR_ACCENT)],
    )
    style.configure("Nebezpecne.TButton", foreground=COLOR_ERROR)
    style.map(
        "Nebezpecne.TButton",
        foreground=[("disabled", COLOR_DISABLED), ("!disabled", COLOR_ERROR)],
    )

    # Svislá navigace: široké, doleva zarovnané položky.
    style.configure(
        "Nav.TButton",
        anchor="w",
        padding=(PAD_M, PAD_S + 2),
        font=base,
        relief="flat",
        background=COLOR_BG,
        foreground=COLOR_TEXT,
        bordercolor=COLOR_BG,
        lightcolor=COLOR_BG,
        darkcolor=COLOR_BG,
        focuscolor=COLOR_BG,
    )
    style.map(
        "Nav.TButton",
        background=[("pressed", COLOR_ACCENT_SOFT), ("active", COLOR_ACCENT_SOFT)],
        foreground=[("disabled", COLOR_DISABLED), ("!disabled", COLOR_TEXT)],
    )
    style.configure(
        "Nav.Selected.TButton",
        anchor="w",
        padding=(PAD_M, PAD_S + 2),
        font=bold,
        relief="flat",
        background=COLOR_ACCENT_SOFT,
        foreground=COLOR_ACCENT,
        bordercolor=COLOR_ACCENT_SOFT,
        lightcolor=COLOR_ACCENT_SOFT,
        darkcolor=COLOR_ACCENT_SOFT,
        focuscolor=COLOR_ACCENT_SOFT,
    )
    style.map(
        "Nav.Selected.TButton",
        background=[("pressed", COLOR_ACCENT_SOFT), ("active", COLOR_ACCENT_SOFT)],
        foreground=[("disabled", COLOR_DISABLED), ("!disabled", COLOR_ACCENT)],
    )

    # Vstupní pole.
    style.configure(
        "TEntry",
        padding=(PAD_S, PAD_S - 1),
        fieldbackground=COLOR_SURFACE,
        foreground=COLOR_TEXT,
        bordercolor=COLOR_BORDER,
        insertcolor=COLOR_TEXT,
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", COLOR_BG), ("readonly", COLOR_BG)],
        foreground=[("disabled", COLOR_DISABLED)],
        bordercolor=[("focus", COLOR_ACCENT)],
    )
    style.configure(
        "Chyba.TEntry",
        padding=(PAD_S, PAD_S - 1),
        fieldbackground=COLOR_ERROR_SOFT,
        bordercolor=COLOR_ERROR,
        lightcolor=COLOR_ERROR,
        darkcolor=COLOR_ERROR,
    )
    style.map(
        "Chyba.TEntry",
        fieldbackground=[("disabled", COLOR_BG), ("!disabled", COLOR_ERROR_SOFT)],
        bordercolor=[("!disabled", COLOR_ERROR)],
    )

    style.configure(
        "TCombobox",
        padding=(PAD_S, PAD_S - 1),
        fieldbackground=COLOR_SURFACE,
        foreground=COLOR_TEXT,
        bordercolor=COLOR_BORDER,
        arrowcolor=COLOR_TEXT_MUTED,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("disabled", COLOR_BG), ("readonly", COLOR_SURFACE)],
        foreground=[("disabled", COLOR_DISABLED)],
        bordercolor=[("focus", COLOR_ACCENT)],
    )
    style.configure(
        "Chyba.TCombobox",
        padding=(PAD_S, PAD_S - 1),
        fieldbackground=COLOR_ERROR_SOFT,
        bordercolor=COLOR_ERROR,
        lightcolor=COLOR_ERROR,
        darkcolor=COLOR_ERROR,
    )
    style.map(
        "Chyba.TCombobox",
        fieldbackground=[("disabled", COLOR_BG), ("!disabled", COLOR_ERROR_SOFT)],
        bordercolor=[("!disabled", COLOR_ERROR)],
    )

    # Rozbalovací seznam comboboxu je klasické tk okno, ne ttk.
    try:
        root.option_add("*TCombobox*Listbox.background", COLOR_SURFACE)
        root.option_add("*TCombobox*Listbox.foreground", COLOR_TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", COLOR_ACCENT_SOFT)
        root.option_add("*TCombobox*Listbox.selectForeground", COLOR_TEXT)
        root.option_add("*tearOff", False)
    except tk.TclError:  # pragma: no cover - ořezaný Tk
        pass

    # Tabulky.
    style.configure(
        "Treeview",
        rowheight=ROW_HEIGHT,
        background=COLOR_SURFACE,
        fieldbackground=COLOR_SURFACE,
        foreground=COLOR_TEXT,
        bordercolor=COLOR_BORDER,
        font=base,
    )
    style.map(
        "Treeview",
        background=[("selected", COLOR_ACCENT_SOFT)],
        foreground=[("selected", COLOR_TEXT)],
    )
    style.configure(
        "Treeview.Heading",
        font=bold,
        background=COLOR_BG,
        foreground=COLOR_TEXT,
        padding=(PAD_S, PAD_S),
        relief="flat",
    )
    style.map("Treeview.Heading", background=[("active", COLOR_ACCENT_SOFT)])

    # Posuvníky a průběh.
    style.configure(
        "Vertical.TScrollbar",
        background=COLOR_BG,
        troughcolor=COLOR_SURFACE,
        bordercolor=COLOR_BORDER,
        arrowcolor=COLOR_TEXT_MUTED,
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=COLOR_BG,
        troughcolor=COLOR_SURFACE,
        bordercolor=COLOR_BORDER,
        arrowcolor=COLOR_TEXT_MUTED,
    )
    style.configure(
        "Horizontal.TProgressbar",
        background=COLOR_ACCENT,
        troughcolor=COLOR_BG,
        bordercolor=COLOR_BORDER,
    )


def apply_theme(root: tk.Misc) -> ttk.Style:
    """Nastaví motiv, písma a pojmenované styly. Vrací ``ttk.Style``.

    Volání je idempotentní — dá se zopakovat (např. po změně nastavení).
    Nikdy nevyhodí výjimku kvůli chybějícímu písmu nebo motivu.
    """

    style = ttk.Style(root)
    _use_theme(style, preferred_theme_names())
    configure_fonts(root)
    _create_fonts(root)
    _configure_styles(style, root)

    try:
        root.configure(background=COLOR_SURFACE)  # type: ignore[call-arg]
    except tk.TclError:  # pragma: no cover - widget bez volby background
        pass

    return style
