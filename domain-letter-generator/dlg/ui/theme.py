"""Vzhled aplikace: ttk styly, odsazení a písma.

Aplikace **nemá vlastní vzhled**. Ovládací prvky kreslí ttk motiv operačního
systému, takže na Windows vypadají jako ve zbytku systému a nemůžou se rozejít
s tím, co motiv umí. Styly níž rozlišují prvky jen písmem a odsazením; jediná
barva, která něco znamená, je červená u chyb.

Modul jde naimportovat i na stroji bez displeje — samotný import nevytváří
okno ani interpret Tk. Konstanty jsou obyčejná data, styly vzniknou až voláním
:func:`apply_theme` nad už existujícím oknem.

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
    Uspech.TLabel        potvrzení akce
    Primary.TButton      hlavní tlačítko dialogu / pohledu (tučně)
    Nebezpecne.TButton   destruktivní akce (Smazat) — červené písmo
    Karta.TFrame         rámeček karty (1px rámeček)
    Nav.TLabel           text v navigaci
    Nav.TButton          položka svislé navigace
    Nav.Selected.TButton vybraná položka svislé navigace (tučně)
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
    "system_background",
]

# --- barvy -------------------------------------------------------------------
# Záměrně skoro žádné barvy. Aplikace je PoC — ovládací prvky (tlačítka, pole,
# tabulky, posuvníky) si kreslí sám ttk motiv operačního systému, takže vypadají
# přesně jako ve zbytku Windows a nikde se nemůžou rozejít s tím, co motiv umí.
#
# Konstanty níž slouží jen tam, kde ttk motiv NENÍ ve hře, tedy u obyčejných
# ``tk`` widgetů (``tk.Text``, ``tk.Listbox``, ``tk.Canvas``, ``tk.Toplevel``).
# U nich barvy fungují spolehlivě na všech platformách a bez jejich nastavení by
# se plochy rozjely s okolím. Viz :func:`_configure_styles`, kde je vysvětlené,
# proč se ttk prvky nebarví vůbec.

#: Plocha textových polí a seznamů.
COLOR_SURFACE = "#ffffff"
#: Podklad odstavených ploch (detail chyby) — šeď výchozího okna Windows.
COLOR_BG = "#f0f0f0"
#: Linky a rámečky.
COLOR_BORDER = "#b0b0b0"
#: Základní barva textu.
COLOR_TEXT = "#000000"
#: Doplňkový text (nápovědy, stavový řádek) — šedý, ale ještě čitelný.
COLOR_TEXT_MUTED = "#5a5a5a"
#: Zvýraznění (výběr textu, vyplněná místa v náhledu). Neutrální šeď, ne barva.
COLOR_ACCENT = "#333333"
COLOR_ACCENT_ACTIVE = "#000000"
COLOR_ACCENT_SOFT = "#dcdcdc"
#: Chyba. Jediná skutečná barva v aplikaci — nese informaci, ne ozdobu.
COLOR_ERROR = "#a4262c"
#: Podbarvení pole s chybou.
COLOR_ERROR_SOFT = "#fdf3f3"
#: Potvrzení, hotovo. Bez barvy — potvrzení nese text, ne odstín.
COLOR_SUCCESS = "#1b1b1b"
#: Varování.
COLOR_WARNING = "#6b4e00"
#: Nedostupné ovládací prvky.
COLOR_DISABLED = "#8d8d8d"

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
    "Zvyrazneno.TLabel",
    "Primary.TButton",
    "Nebezpecne.TButton",
    "Karta.TFrame",
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


def system_background(widget: tk.Misc | None = None) -> str:
    """Barva, kterou ttk motiv kreslí rámce — pro obyčejné ``tk`` widgety.

    ``tk.Canvas``, ``tk.Toplevel`` a spol. ttk styly neumí; bez téhle hodnoty
    by si vzaly svou vlastní výchozí barvu a udělaly v okně světlou díru
    (přesně tak vykukovalo plátno rolovatelné plochy pod posledním polem
    formuláře). Ptáme se proto přímo motivu, čím kreslí ``TFrame``, takže se
    aplikace trefí do vzhledu systému, ať běží kdekoli.
    """

    try:
        value = str(ttk.Style(widget).lookup("TFrame", "background") or "")
    except tk.TclError:  # pragma: no cover - okno zaniklo / ořezaný Tk
        value = ""
    return value or COLOR_BG


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


def _accepts_family(font: tkfont.Font, family: str) -> bool:
    """Zkusí písmu nastavit rodinu a zeptá se, jestli ji Tk opravdu vzalo.

    Levnější než výčet rodin: Tk u neznámé rodiny tiše degraduje na náhradní,
    takže se to pozná jedním dotazem ``actual("family")``.
    """

    try:
        font.configure(family=family)
        return str(font.actual("family") or "").casefold() == family.casefold()
    except Exception:
        return False


def configure_fonts(root: tk.Misc) -> str:
    """Nastaví systémová písma. Na Windows Segoe UI 10, jinde nechá výchozí.

    Vrací název použité rodiny písma (prázdný řetězec = ponecháno výchozí).
    Funkce nikdy nespadne — chybějící písmo prostě znamená výchozí vzhled.

    Rodina se **nehledá výčtem**. ``tkfont.families()`` projde na Windows přes
    ``EnumFontFamiliesEx`` všechna nainstalovaná písma (v kanceláři jich bývají
    stovky) jen proto, aby se ověřila tři jména — a děje se to ještě předtím,
    než se ukáže okno. Místo toho se rodina rovnou zkusí nastavit a jedním
    dotazem se ověří, jestli ji Tk přijalo.
    """

    if not is_windows():
        return ""

    base = _nametofont(FONT_BASE, root)
    if base is None:
        return ""

    try:
        puvodni = str(base.actual("family") or "")
    except Exception:
        puvodni = ""

    family = ""
    for candidate in WINDOWS_FONT_CANDIDATES:
        if _accepts_family(base, candidate):
            family = candidate
            break
    if not family:
        # nic z kandidátů tu není — vrátit původní rodinu a nechat výchozí vzhled
        if puvodni:
            try:
                base.configure(family=puvodni)
            except Exception:
                pass
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


#: Atribut na kořenovém okně, kde si držíme odkazy na vytvořená písma.
_FONTS_ATTR = "_dlg_named_fonts"


def _font_registry(root: tk.Misc) -> dict[str, tkfont.Font]:
    """Odkazy na vytvořená písma musí přežít — ``tkfont.Font.__del__`` je maže."""

    registry = getattr(root, _FONTS_ATTR, None)
    if not isinstance(registry, dict):
        registry = {}
        try:
            setattr(root, _FONTS_ATTR, registry)
        except Exception:  # pragma: no cover - widget bez __dict__
            pass
    return registry


def _derive_font(
    root: tk.Misc,
    name: str,
    *,
    delta: int = 0,
    weight: str = "normal",
    metrics: "tuple[str, int, bool] | None" = None,
) -> str:
    """Vytvoří (nebo přenastaví) pojmenované písmo odvozené od základního.

    ``metrics`` se dá předat, aby se rodina a velikost základního písma
    nezjišťovaly znovu u každého odvozeného písma (viz :func:`_create_fonts`).
    """

    family, size, in_pixels = metrics if metrics is not None else _base_metrics(root)
    new_size = max(1, size + delta)
    if in_pixels:
        new_size = -new_size

    options: dict[str, object] = {"size": new_size, "weight": weight}
    if family:
        options["family"] = family

    registry = _font_registry(root)
    try:
        if name in _existing_font_names(root):
            font = registry.get(name)
            if font is None:
                font = tkfont.Font(root=root, name=name, exists=True)
            font.configure(**options)  # type: ignore[arg-type]
        else:
            font = tkfont.Font(  # type: ignore[arg-type]
                root=root, name=name, exists=False, **options
            )
        registry[name] = font
    except Exception:
        return FONT_BASE
    return name


def _create_fonts(root: tk.Misc) -> None:
    # Metriky základního písma se mezi odvozenými písmy nemění — stačí je
    # zjistit jednou místo čtyřikrát (každé zjištění je několik dotazů do Tk).
    metrics = _base_metrics(root)
    _derive_font(root, FONT_BOLD, delta=0, weight="bold", metrics=metrics)
    _derive_font(root, FONT_SMALL, delta=-1, metrics=metrics)
    _derive_font(root, FONT_HEADING, delta=6, weight="bold", metrics=metrics)
    _derive_font(root, FONT_SUBHEADING, delta=2, weight="bold", metrics=metrics)


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
    """Nastaví pojmenované styly. Rozlišuje **jen písmem a odsazením**.

    Klíčové pravidlo: ovládací prvky se **nebarví**. Nativní motivy Windows
    (``vista``, ``winnative``) si tlačítko, vstupní pole i záhlaví tabulky
    kreslí jako obrázek od systému a volby ``background`` / ``bordercolor`` /
    ``lightcolor`` / ``darkcolor`` prostě zahodí — ``foreground`` a ``font``
    ale respektují. Kdo tedy nastaví obojí (bílé písmo na modrém podkladu),
    dostane na Linuxu modré tlačítko a na Windows bílé písmo na světle šedém
    nativním tlačítku, tedy nečitelnou skvrnu. Přesně tak vypadalo tlačítko
    „Generovat“.

    Proto se tady nastavuje jen to, co dopadne všude stejně:

    * ``font``    — hlavní tlačítko a vybraná položka navigace jsou tučně,
    * ``padding`` — odsazení,
    * ``foreground`` u **popisků** (``TLabel``), kde žádné nativní kreslení
      není a barva se tedy uplatní spolehlivě,
    * ``rowheight`` u tabulek (čitelnost).

    Zbytek vzhledu je práce motivu operačního systému. Aplikace tak vypadá jako
    každý jiný program na daném systému a nemá vlastní vzhled, který by se
    mohl rozbít.
    """

    heading = font_name(FONT_HEADING, root)
    subheading = font_name(FONT_SUBHEADING, root)
    small = font_name(FONT_SMALL, root)
    bold = font_name(FONT_BOLD, root)
    base = font_name(FONT_BASE, root)

    # --- texty ---------------------------------------------------------------
    # U popisků je barva bezpečná: ttk je kreslí sám na všech motivech.
    style.configure("Nadpis.TLabel", font=heading)
    style.configure("Podnadpis.TLabel", font=subheading)
    style.configure("Popisek.TLabel", font=base)
    style.configure("Napoveda.TLabel", font=small, foreground=COLOR_TEXT_MUTED)
    style.configure("Chyba.TLabel", font=small, foreground=COLOR_ERROR)
    style.configure("Uspech.TLabel", font=small)
    style.configure("Zvyrazneno.TLabel", font=bold)

    # --- tlačítka ------------------------------------------------------------
    style.configure("TButton", padding=(PAD_M, PAD_S))
    # Hlavní akce se pozná tučným písmem, ne barvou.
    style.configure("Primary.TButton", font=bold, padding=(PAD_M, PAD_S))
    # Destruktivní akce červeným písmem. Tmavě červená na světlém nativním
    # tlačítku je čitelná všude — na rozdíl od bílé.
    style.configure("Nebezpecne.TButton", foreground=COLOR_ERROR)
    style.map("Nebezpecne.TButton", foreground=[("disabled", COLOR_DISABLED)])

    # --- svislá navigace -----------------------------------------------------
    # Široké položky zarovnané doleva; vybraná je tučně.
    style.configure("Nav.TButton", anchor="w", padding=(PAD_M, PAD_S + 2), font=base)
    style.configure(
        "Nav.Selected.TButton", anchor="w", padding=(PAD_M, PAD_S + 2), font=bold
    )
    style.configure("Nav.TLabel", font=small, foreground=COLOR_TEXT_MUTED)

    # --- karta ---------------------------------------------------------------
    # Rámeček karty odděluje sekce; kreslí ho ttk výchozí barvou motivu.
    style.configure("Karta.TFrame", relief="solid", borderwidth=1)

    # --- vstupní pole --------------------------------------------------------
    style.configure("TEntry", padding=(PAD_S, PAD_S - 1))
    style.configure("TCombobox", padding=(PAD_S, PAD_S - 1))
    # Pole s chybou. Na nativním motivu Windows se podbarvení neuplatní (motiv
    # si pole kreslí sám) — na chybu proto vždycky upozorňuje i červený popisek
    # pod polem, viz widgets.LabeledField. Tady jde jen o bonus tam, kde funguje.
    style.configure("Chyba.TEntry", padding=(PAD_S, PAD_S - 1), fieldbackground=COLOR_ERROR_SOFT)
    style.configure(
        "Chyba.TCombobox", padding=(PAD_S, PAD_S - 1), fieldbackground=COLOR_ERROR_SOFT
    )

    # Rozbalovací seznam comboboxu je klasické tk okno, ne ttk — tady barvy platí.
    try:
        root.option_add("*TCombobox*Listbox.background", COLOR_SURFACE)
        root.option_add("*TCombobox*Listbox.foreground", COLOR_TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", COLOR_ACCENT_SOFT)
        root.option_add("*TCombobox*Listbox.selectForeground", COLOR_TEXT)
        root.option_add("*tearOff", False)
    except tk.TclError:  # pragma: no cover - ořezaný Tk
        pass

    # --- tabulky -------------------------------------------------------------
    # Vyšší řádek se čte líp; zbytek kreslí motiv.
    style.configure("Treeview", rowheight=ROW_HEIGHT, font=base)
    style.configure("Treeview.Heading", font=bold, padding=(PAD_S, PAD_S))


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

    return style
