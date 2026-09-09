"""Smoke testy základu uživatelského rozhraní (``dlg.ui.theme`` a ``dlg.ui.widgets``).

Testy potřebují běžící X server — v CI se spouštějí přes ``xvfb-run``::

    xvfb-run -a python -m pytest -q tests/test_ui_widgets.py

Bez displeje se celý soubor přeskočí (fixture ``root`` zavolá ``pytest.skip``).
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter", reason="tkinter není k dispozici")
from tkinter import ttk  # noqa: E402  (až po importorskip)

from dlg.ui import theme  # noqa: E402
from dlg.ui import widgets as W  # noqa: E402
from dlg.version import APP_NAME  # noqa: E402

pytestmark = pytest.mark.gui

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def root():
    """Okno pro jeden test; bez displeje se test přeskočí."""

    try:
        window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - závisí na prostředí
        pytest.skip(f"Tk se nepodařilo spustit: {exc}")
    window.geometry("900x620")
    theme.apply_theme(window)
    window.update()
    try:
        yield window
    finally:
        try:
            window.update_idletasks()
            window.destroy()
        except tk.TclError:  # pragma: no cover
            pass
        # Tk interpret se musí uvolnit dřív, než se postaví další. Bez toho se
        # instance hromadí a sběr odpadu pak padne doprostřed startu vlákna
        # v run_in_thread (Fatal Python error: Aborted). Stejně to dělá
        # tests/test_app.py:close_app.
        del window
        gc.collect()


def _pump(window: tk.Misc, predicate, timeout: float = 5.0) -> bool:
    """Točí smyčku událostí, dokud podmínka neplatí (nebo nevyprší čas)."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window.update()
        if predicate():
            return True
        time.sleep(0.01)
    return False


# --- import bez displeje -----------------------------------------------------


def test_import_bez_displeje_nevytvori_okno() -> None:
    """Moduly UI musí jít naimportovat i na stroji bez X serveru."""

    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    code = (
        "import tkinter;"
        "from dlg.ui import theme, widgets;"
        "assert tkinter._default_root is None, 'import vyrobil Tk okno';"
        "print(theme.PAD_M, len(widgets.__all__))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith(str(theme.PAD_M))


# --- theme -------------------------------------------------------------------


def test_apply_theme_definuje_vsechny_styly(root: tk.Tk) -> None:
    style = theme.apply_theme(root)
    assert isinstance(style, ttk.Style)
    assert style.theme_use() in theme.preferred_theme_names()

    for name in theme.STYLE_NAMES:
        popis = [
            style.lookup(name, option) for option in ("font", "background", "foreground")
        ]
        assert any(popis), f"styl {name} není nijak nastavený"


def test_apply_theme_ma_vyssi_radek_v_tabulce(root: tk.Tk) -> None:
    style = ttk.Style(root)
    assert int(style.lookup("Treeview", "rowheight")) == theme.ROW_HEIGHT
    assert theme.ROW_HEIGHT >= 24


def test_apply_theme_vyrobi_odvozena_pisma(root: tk.Tk) -> None:
    style = ttk.Style(root)
    assert style.lookup("Nadpis.TLabel", "font") == theme.FONT_HEADING
    assert style.lookup("Napoveda.TLabel", "font") == theme.FONT_SMALL

    from tkinter import font as tkfont

    nadpis = tkfont.nametofont(theme.FONT_HEADING, root=root)
    zaklad = tkfont.nametofont(theme.FONT_BASE, root=root)
    assert abs(int(nadpis.actual("size"))) > abs(int(zaklad.actual("size")))
    assert nadpis.actual("weight") == "bold"


def test_apply_theme_je_idempotentni(root: tk.Tk) -> None:
    prvni = theme.apply_theme(root)
    druhy = theme.apply_theme(root)
    assert prvni.lookup("Nadpis.TLabel", "font") == druhy.lookup("Nadpis.TLabel", "font")


def test_zadne_tlacitko_si_nebarvi_podklad(root: tk.Tk) -> None:
    """Regrese: bílé písmo na obarveném podkladu je na Windows nečitelné.

    Nativní motivy Windows (``vista``, ``winnative``) kreslí tlačítko jako
    obrázek od systému. Volbu ``background`` přitom zahodí, ale ``foreground``
    respektují — takže styl, který nastaví obojí, dopadne na Linuxu podle
    očekávání a na Windows jako bílé písmo na světle šedém tlačítku. Přesně tak
    zmizelo tlačítko „Generovat“.

    Invariant: **žádný styl tlačítka nesmí nastavovat podklad.** Hlavní akce se
    odliší písmem (viz ``Primary.TButton`` = tučně).
    """

    style = theme.apply_theme(root)
    styly_tlacitek = [name for name in theme.STYLE_NAMES if name.endswith("TButton")]
    assert styly_tlacitek, "seznam stylů tlačítek se vyprázdnil — test by nic nehlídal"

    zakazane = ("background", "lightcolor", "darkcolor", "bordercolor", "focuscolor")
    for name in styly_tlacitek:
        # `configure(name)` vrací jen to, co nastavuje styl sám; `lookup` by
        # dotáhl i hodnoty zděděné z motivu, které aplikace neovlivňuje.
        vlastni = dict(style.configure(name) or {})
        prohresky = {k: v for k, v in vlastni.items() if k in zakazane}
        assert not prohresky, (
            f"Styl {name} nastavuje {prohresky}. Nativní motiv Windows tyhle volby "
            f"ignoruje, ale foreground ne — text by se stal nečitelným."
        )

        # Totéž pro stavové mapy (`style.map`) — i tam se dřív barvil podklad.
        for volba in zakazane:
            mapa = style.map(name, query_opt=volba)
            assert not mapa, f"Styl {name} mapuje {volba}={mapa!r}; podklad patří motivu."


def test_hlavni_tlacitko_se_odlisi_pismem(root: tk.Tk) -> None:
    """Bez barvy musí jít hlavní akci poznat po písmu — jinak nejde poznat vůbec."""

    from tkinter import font as tkfont

    style = theme.apply_theme(root)
    assert style.lookup("Primary.TButton", "font") == theme.FONT_BOLD
    assert style.lookup("Nav.Selected.TButton", "font") == theme.FONT_BOLD
    tucne = tkfont.nametofont(theme.FONT_BOLD, root=root)
    assert str(tucne.actual("weight")) == "bold"


def test_font_name_ma_zaloznu_hodnotu(root: tk.Tk) -> None:
    assert theme.font_name(theme.FONT_HEADING, root) == theme.FONT_HEADING
    assert theme.font_name("TohleNeexistuje", root) == theme.FONT_BASE
    assert theme.font_name("", root) == theme.FONT_BASE


def test_enable_dpi_awareness_nikdy_nespadne() -> None:
    vysledek = theme.enable_dpi_awareness()
    assert isinstance(vysledek, bool)
    if os.name != "nt":
        assert vysledek is False


# --- ScrollableFrame ---------------------------------------------------------


def test_scrollable_frame_sirka_a_scrollregion(root: tk.Tk) -> None:
    ramec = W.ScrollableFrame(root)
    ramec.pack(fill="both", expand=True)
    for i in range(60):
        ttk.Label(ramec.body, text=f"řádek {i}").pack(anchor="w")
    root.update()

    assert ramec.body is ramec.interior
    # Vnitřní rámec kopíruje šířku plátna — roluje se jen svisle.
    assert ramec.body.winfo_width() == ramec.canvas.winfo_width()
    assert ramec.body.winfo_height() > ramec.canvas.winfo_height()
    assert ramec.scrollable() is True

    ramec.scroll_to_bottom()
    root.update()
    assert ramec.canvas.yview()[1] == pytest.approx(1.0, abs=0.01)
    ramec.scroll_to_top()
    root.update()
    assert ramec.canvas.yview()[0] == pytest.approx(0.0, abs=0.01)

    ramec.clear()
    root.update()
    assert ramec.body.winfo_children() == []


@pytest.mark.parametrize(
    ("udalost", "ocekavano"),
    [
        # --- X11: kolečko chodí jako tlačítka 4 a 5, `delta` je nepoužité ---
        (SimpleNamespace(num=4, delta="??"), -1),   # nahoru
        (SimpleNamespace(num=5, delta="??"), 1),    # dolů
        # --- Windows: `<MouseWheel>` nese `delta`, `num` je nepoužité ---
        # POZOR: Tk do nepoužitých polí dosazuje řetězec "??", ne nulu.
        # Kvůli tomu tady dřív padalo `int(event.num)` na ValueError.
        (SimpleNamespace(num="??", delta=120), -1),   # nahoru
        (SimpleNamespace(num="??", delta=-120), 1),   # dolů
        (SimpleNamespace(num="??", delta=240), -2),   # rychlé otočení nahoru
        (SimpleNamespace(num="??", delta=-240), 2),   # rychlé otočení dolů
        (SimpleNamespace(num="??", delta=-40), 1),    # přesný touchpad
        # Zaokrouhlení musí být symetrické: nedotočené kolečko oběma směry o 1.
        (SimpleNamespace(num="??", delta=121), -1),
        (SimpleNamespace(num="??", delta=-121), 1),
        # --- macOS: malá `delta`, `num` bývá 0 ---
        (SimpleNamespace(num=0, delta=3), -1),
        (SimpleNamespace(num=0, delta=-3), 1),
        # --- nesmysly nesmí shodit obsluhu ---
        (SimpleNamespace(num=0, delta=0), 0),
        (SimpleNamespace(num="??", delta="??"), 0),
        (SimpleNamespace(), 0),
        (SimpleNamespace(num=None, delta=None), 0),
    ],
)
def test_wheel_units(udalost, ocekavano) -> None:
    assert W._wheel_units(udalost) == ocekavano


def test_skutecne_kolecko_projde_celym_retezcem(root: tk.Tk) -> None:
    """Nejdůležitější test kolečka: událost vyrábí Tk, ne my.

    Ostatní testy volají obsluhu s vlastní ``SimpleNamespace``, tedy s událostí,
    která se chová hezčeji než skutečná — právě tím původní pád proklouzl až
    k uživateli. Skutečné ``<MouseWheel>`` má ``num == "??"`` i na Linuxu
    (ověřeno), takže tenhle test hlídá regresi i v CI.

    Výjimka z obsluhy Tk nepropadne ven — skončí v ``report_callback_exception``.
    Test si ho proto přebere sám, jinak by pád nepoznal.
    """

    chyby: list[BaseException] = []
    root.report_callback_exception = (  # type: ignore[assignment]
        lambda exc, val, tb: chyby.append(val)
    )

    ramec = W.ScrollableFrame(root)
    ramec.pack(fill="both", expand=True)
    for i in range(80):
        ttk.Label(ramec.body, text=f"položka {i}").pack(anchor="w")
    root.update()

    root.event_generate(
        "<MouseWheel>",
        delta=-120,
        rootx=ramec.canvas.winfo_rootx() + 20,
        rooty=ramec.canvas.winfo_rooty() + 20,
    )
    root.update()

    assert not chyby, f"obsluha kolečka spadla: {chyby!r}"
    assert ramec.canvas.yview()[0] > 0.0, "kolečko plochu neodrolovalo"


def test_event_int_snese_neposlusna_pole() -> None:
    """Tk vyplňuje i pole, která pro danou událost nedávají smysl — řetězcem „??“."""

    udalost = SimpleNamespace(num="??", delta=-120, y="??", width=None)
    assert W.event_int(udalost, "num") == 0
    assert W.event_int(udalost, "delta") == -120
    assert W.event_int(udalost, "y") == 0
    assert W.event_int(udalost, "width") == 0
    assert W.event_int(udalost, "chybi") == 0
    assert W.event_int(udalost, "chybi", 7) == 7
    assert W.event_int(udalost, "num", -1) == -1


def test_kolecko_na_windows_neshodi_okno(root: tk.Tk) -> None:
    """Regrese: skutečná událost `<MouseWheel>` z Windows má `num == "??"`.

    Testy si událost vyráběly samy jako `SimpleNamespace(num=0, …)`, což je
    hezčí, než jaké Tk doopravdy je — chyba proto proklouzla až k uživateli:
    první otočení kolečka nad libovolným místem aplikace shodilo obsluhu na
    `ValueError: invalid literal for int() with base 10: '??'`.
    """

    ramec = W.ScrollableFrame(root)
    ramec.pack(fill="both", expand=True)
    for i in range(80):
        ttk.Label(ramec.body, text=f"položka {i}").pack(anchor="w")
    root.update()

    windows_dolu = SimpleNamespace(
        num="??",
        delta=-120,
        x_root=ramec.canvas.winfo_rootx() + 20,
        y_root=ramec.canvas.winfo_rooty() + 20,
    )

    assert ramec.handle_wheel(windows_dolu) is True
    root.update()
    assert ramec.canvas.yview()[0] > 0.0
    # a stejnou cestou, jakou událost chodí za běhu — přes obsluhu na okně
    assert W._dispatch_wheel(root, windows_dolu) == "break"


def test_scrollable_frame_roluje_koleckem(root: tk.Tk) -> None:
    ramec = W.ScrollableFrame(root)
    ramec.pack(fill="both", expand=True)
    for i in range(80):
        ttk.Label(ramec.body, text=f"položka {i}").pack(anchor="w")
    root.update()

    x = ramec.canvas.winfo_rootx() + 20
    y = ramec.canvas.winfo_rooty() + 20
    dolu = SimpleNamespace(num=5, delta=0, x_root=x, y_root=y)
    nahoru = SimpleNamespace(num=4, delta=0, x_root=x, y_root=y)

    assert ramec.pointer_inside(dolu) is True
    assert ramec.handle_wheel(dolu) is True
    root.update()
    assert ramec.canvas.yview()[0] > 0.0

    assert ramec.handle_wheel(nahoru) is True
    root.update()

    # Kolečko mimo plochu se ignoruje.
    mimo = SimpleNamespace(num=5, delta=0, x_root=-5000, y_root=-5000)
    assert ramec.pointer_inside(mimo) is False
    assert ramec.handle_wheel(mimo) is False

    # Obsluha na okně najde správnou plochu.
    assert W._dispatch_wheel(root, dolu) == "break"
    assert W._dispatch_wheel(root, mimo) is None


def test_ctrl_o_nevklada_novy_radek(root: tk.Tk) -> None:
    """Regrese: Ctrl+O rozseklo rozepsaný text ve víceřádkovém poli.

    ``tk.Text`` má vestavěnou vazbu ``<Control-o>``, která vloží nový řádek, a
    vazby na třídu běží dřív než vazba okna. Uživatel chtěl nahrát šablonu,
    zmáčkl Ctrl+O v poli s adresou a dostal navíc prázdný řádek doprostřed.
    """

    W.free_app_shortcuts(root)
    text = tk.Text(root, height=3)
    text.pack()
    text.insert("1.0", "Jan Novák, Dlouhá 5, Praha")
    text.mark_set("insert", "1.10")
    text.focus_set()
    root.update()

    pred = text.get("1.0", "end-1c")
    text.event_generate("<Control-o>")
    root.update()
    assert text.get("1.0", "end-1c") == pred, "Ctrl+O vložilo do textu nový řádek"


def test_ceska_shoda_v_hlaskach_o_nevyplnenych() -> None:
    """Věty se skládaly z číslovky a pevného zbytku, takže se nikdy neshodly."""

    assert W.plural_unfilled(1) == "zůstane 1 nevyplněné místo"
    assert W.plural_unfilled(3) == "zůstanou 3 nevyplněná místa"
    assert W.plural_unfilled(7) == "zůstane 7 nevyplněných míst"

    assert W.plural_unfilled_past(1) == "zůstalo 1 nevyplněné místo"
    assert W.plural_unfilled_past(2) == "zůstala 2 nevyplněná místa"
    assert W.plural_unfilled_past(9) == "zůstalo 9 nevyplněných míst"

    assert W.plural_problems(1) == "1 pole potřebuje opravit"
    assert W.plural_problems(4) == "4 pole potřebují opravit"
    assert W.plural_problems(5) == "5 polí potřebuje opravit"


def test_kolecko_nad_comboboxem_nemeni_hodnotu(root: tk.Tk) -> None:
    """Regrese: rolování formuláře si tiše přepisovalo hodnoty v polích.

    ``ttk`` váže na třídu ``TCombobox`` vlastní obsluhu kolečka, která posune
    vybranou položku. Formulář „Generovat“ je z comboboxů poskládaný celý, takže
    kdo kolečkem projel formulář, přepsal si políčka, přes která projel — a
    v dopise to zjistil až po vygenerování.
    """

    ramec = W.ScrollableFrame(root)
    ramec.pack(fill="both", expand=True)
    combo = ttk.Combobox(ramec.body, values=["text", "víceřádkový", "výběr", "datum"])
    combo.current(0)
    combo.pack()
    for i in range(60):
        ttk.Label(ramec.body, text=f"položka {i}").pack(anchor="w")
    root.update()

    # přesně to, co pošle Windows na jedno cvaknutí kolečka dolů
    combo.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
    root.update()
    assert combo.get() == "text", "kolečko přepsalo hodnotu comboboxu"

    # a na X11 chodí kolečko jako tlačítka 4/5
    combo.event_generate("<Button-5>", x=5, y=5)
    root.update()
    assert combo.get() == "text", "kolečko (X11) přepsalo hodnotu comboboxu"


def test_scrollable_frame_neroluje_za_vnorene_okno(root: tk.Tk) -> None:
    """Text/tabulka uvnitř plochy si kolečko obslouží sama — nesmí rolovat obojí."""

    ramec = W.ScrollableFrame(root)
    ramec.pack(fill="both", expand=True)
    dlouhy = tk.Text(ramec.body, height=4, width=40)
    dlouhy.pack(fill="x")
    dlouhy.insert("1.0", "\n".join(f"řádek {i}" for i in range(200)))
    kratky = tk.Text(ramec.body, height=4, width=40)
    kratky.pack(fill="x")
    kratky.insert("1.0", "jen jeden řádek")
    for i in range(60):
        ttk.Label(ramec.body, text=f"položka {i}").pack(anchor="w")
    root.update()

    def udalost(widget: tk.Misc):
        return SimpleNamespace(
            num=5,
            delta=0,
            x_root=widget.winfo_rootx() + 10,
            y_root=widget.winfo_rooty() + 10,
        )

    assert ramec.scrollable() is True
    # nad rolovatelným Textem se vnější plocha nehýbe
    assert ramec.handle_wheel(udalost(dlouhy)) is False
    assert ramec.canvas.yview()[0] == pytest.approx(0.0, abs=0.001)
    # nad Textem, ve kterém není co rolovat, se roluje plocha
    assert ramec.handle_wheel(udalost(kratky)) is True
    root.update()
    assert ramec.canvas.yview()[0] > 0.0


def test_scrollable_frame_se_po_zruseni_odhlasi(root: tk.Tk) -> None:
    ramec = W.ScrollableFrame(root)
    ramec.pack(fill="both", expand=True)
    root.update()
    cile = getattr(root, W._WHEEL_TARGETS_ATTR)
    assert ramec in cile

    ramec.destroy()
    root.update()
    assert ramec not in cile


def test_scrollable_frame_schova_posuvnik_kdyz_neni_co_rolovat(root: tk.Tk) -> None:
    ramec = W.ScrollableFrame(root)
    ramec.pack(fill="both", expand=True)
    ttk.Label(ramec.body, text="krátký obsah").pack(anchor="w")
    root.update()
    assert ramec.scrollable() is False
    assert ramec.scrollbar.grid_info() == {}


# --- formulářová pole --------------------------------------------------------


def test_labeled_entry_hodnota_a_chyba(root: tk.Tk) -> None:
    pole = W.LabeledEntry(
        root, "Držitel domény", help_text="Podle výpisu z rejstříku", required=True
    )
    pole.pack(fill="x")
    root.update()

    assert pole.label.cget("text") == "Držitel domény" + W.REQUIRED_MARK
    assert pole.help_label.grid_info() != {}
    assert pole.error_label.grid_info() == {}

    # prázdné povinné pole
    assert pole.validate() == W.REQUIRED_ERROR
    assert pole.has_error is True
    assert pole.error_label.grid_info() != {}
    assert pole.entry.cget("style") == "Chyba.TEntry"

    zmeny: list[str] = []
    pole.bind_change(zmeny.append)
    pole.set("Jan Novák")
    root.update()
    assert pole.get() == "Jan Novák"
    assert zmeny == ["Jan Novák"]
    # zápis do pole chybu schová
    assert pole.has_error is False
    assert pole.error_label.grid_info() == {}
    assert pole.entry.cget("style") == "TEntry"
    assert pole.validate() == ""

    pole.set_help("")
    assert pole.help_label.grid_info() == {}
    pole.set_enabled(False)
    assert "disabled" in pole.entry.state()
    pole.set_enabled(True)
    assert "disabled" not in pole.entry.state()


def test_labeled_combobox(root: tk.Tk) -> None:
    pole = W.LabeledCombobox(
        root,
        "Země",
        values=["Česká republika", "Slovenská republika"],
        value="Česká republika",
    )
    pole.pack(fill="x")
    root.update()

    assert pole.get() == "Česká republika"
    assert pole.values == ["Česká republika", "Slovenská republika"]
    assert "readonly" in pole.combobox.state()

    pole.set_values(["Česká republika", "Rakousko"])
    assert pole.values == ["Česká republika", "Rakousko"]
    assert pole.get() == "Česká republika"

    pole.set_error("Vyberte variantu.")
    assert pole.combobox.cget("style") == "Chyba.TCombobox"
    pole.clear_error()
    assert pole.combobox.cget("style") == "TCombobox"


def test_labeled_combobox_s_napovidanim(root: tk.Tk) -> None:
    pole = W.LabeledCombobox(
        root, "E-mail", values=["novak@example.com", "svoboda@example.com"],
        readonly=False, autocomplete=True,
    )
    pole.pack(fill="x")
    root.update()

    assert isinstance(pole.combobox, W.AutocompleteCombobox)
    pole.set_values(["a@b.cz"])
    assert pole.combobox.completion_values() == ["a@b.cz"]


def test_labeled_text_viceradkovy(root: tk.Tk) -> None:
    pole = W.LabeledText(root, "Poznámka", height=3, help_text="Nepovinné")
    pole.pack(fill="x")
    root.update()

    zmeny: list[str] = []
    pole.bind_change(zmeny.append)

    pole.set("první řádek\ndruhý řádek")
    root.update()
    assert pole.get() == "první řádek\ndruhý řádek"

    pole.text.insert("end", "\ntřetí")
    root.update()
    assert pole.get().endswith("třetí")
    assert zmeny, "změna textu se neohlásila"

    pole.set("")
    assert pole.get() == ""

    assert pole.validate() == ""  # není povinné
    pole.set_error("Text je moc dlouhý.")
    assert pole.text.cget("background") == theme.COLOR_ERROR_SOFT
    pole.clear_error()
    assert pole.text.cget("background") == theme.COLOR_SURFACE


def test_labeled_date(root: tk.Tk) -> None:
    pole = W.LabeledDate(root, "Datum odeslání", required=True)
    pole.pack(fill="x")
    root.update()

    assert pole.validate() == W.DATE_REQUIRED_ERROR
    pole.set("31.2.2026")
    assert pole.validate() == W.DATE_IMPOSSIBLE_ERROR
    pole.set("nesmysl")
    assert W.DATE_FORMAT_HINT in pole.validate()

    pole.set_date(date(2026, 9, 9))
    assert pole.get() == "09.09.2026"
    assert pole.get_date() == date(2026, 9, 9)
    assert pole.validate() == ""
    assert pole.error_label.grid_info() == {}


# --- AutocompleteCombobox ----------------------------------------------------


HISTORIE = ["Nováková Jana", "Novák Jan", "Svoboda Petr", "novak@example.com"]


def _napis(combo: W.AutocompleteCombobox, text: str) -> None:
    combo.delete(0, "end")
    combo.insert(0, text)


def test_autocomplete_filtruje_pri_psani(root: tk.Tk) -> None:
    combo = W.AutocompleteCombobox(root, completion_values=HISTORIE, width=40)
    combo.pack(fill="x")
    root.update()

    assert combo.completion_values() == HISTORIE
    assert list(combo.cget("values")) == HISTORIE

    _napis(combo, "nov")
    nalezene = combo.refresh_suggestions()
    assert nalezene == ["Nováková Jana", "Novák Jan", "novak@example.com"]
    assert list(combo.cget("values")) == nalezene
    # text ani kurzor se bez inline doplňování nemění
    assert combo.get() == "nov"
    assert combo.selection_present() is False

    # bez diakritiky i bez ohledu na velikost písmen
    _napis(combo, "NOVÁKOVÁ")
    assert combo.refresh_suggestions() == ["Nováková Jana"]

    # shoda uvnitř textu
    _napis(combo, "example")
    assert combo.refresh_suggestions() == ["novak@example.com"]

    # nic nesedí -> nabídne se zase všechno
    _napis(combo, "xyz")
    assert combo.refresh_suggestions() == []
    assert list(combo.cget("values")) == HISTORIE

    # prázdné pole -> celá historie
    _napis(combo, "")
    assert combo.refresh_suggestions() == HISTORIE

    combo.reset_suggestions()
    assert list(combo.cget("values")) == HISTORIE


def test_autocomplete_nerozbije_mazani(root: tk.Tk) -> None:
    combo = W.AutocompleteCombobox(
        root, completion_values=HISTORIE, inline_completion=True, width=40
    )
    combo.pack(fill="x")
    combo.focus_set()
    root.update()

    # doplnění zbytku hodnoty; dopsaná část zůstane vybraná
    _napis(combo, "Nováková")
    combo.refresh_suggestions()
    assert combo.get() == "Nováková Jana"
    assert combo.index("insert") == len("Nováková")
    assert combo.selection_present() is True

    # při mazání se nikdy nic nedoplňuje
    _napis(combo, "Novák")
    combo.refresh_suggestions(deleting=True)
    assert combo.get() == "Novák"

    _napis(combo, "Nov")
    combo.refresh_suggestions(deleting=True)
    assert combo.get() == "Nov"


def test_autocomplete_reaguje_na_klavesu(root: tk.Tk) -> None:
    combo = W.AutocompleteCombobox(root, completion_values=HISTORIE, width=40)
    combo.pack(fill="x")
    combo.focus_force()  # klávesy chodí jen do pole s kurzorem
    root.update()

    _napis(combo, "svo")
    combo.event_generate("<KeyRelease>", keysym="o")
    root.update()
    assert list(combo.cget("values")) == ["Svoboda Petr"]

    # pohybové klávesy nabídku nepřepočítávají
    _napis(combo, "nov")
    combo.event_generate("<KeyRelease>", keysym="Down")
    root.update()
    assert list(combo.cget("values")) == ["Svoboda Petr"]

    combo.event_generate("<KeyRelease>", keysym="Escape")
    root.update()
    assert list(combo.cget("values")) == HISTORIE


def test_autocomplete_prijima_values_v_konstruktoru(root: tk.Tk) -> None:
    combo = W.AutocompleteCombobox(root, values=["Alfa", "Beta"])
    assert combo.completion_values() == ["Alfa", "Beta"]


# --- DateEntry ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "ocekavano"),
    [
        ("9.9.2026", date(2026, 9, 9)),
        ("09.09.2026", date(2026, 9, 9)),
        ("9. 9. 2026", date(2026, 9, 9)),
        ("1/2/2026", date(2026, 2, 1)),
        ("1-2-2026", date(2026, 2, 1)),
        ("9.9.26", date(2026, 9, 9)),
        ("2026-09-09", date(2026, 9, 9)),
        ("  9.9.2026  ", date(2026, 9, 9)),
        ("31.2.2026", None),
        ("32.1.2026", None),
        ("nesmysl", None),
        ("", None),
        ("9.9", None),
    ],
)
def test_parse_date(text: str, ocekavano: date | None) -> None:
    assert W.parse_date(text) == ocekavano


def test_format_date() -> None:
    assert W.format_date(date(2026, 9, 9)) == "09.09.2026"
    assert W.format_date(date(2026, 12, 31)) == "31.12.2026"


def test_validate_date_text() -> None:
    assert W.validate_date_text("") == ""
    assert W.validate_date_text("", allow_empty=False) == W.DATE_REQUIRED_ERROR
    assert W.validate_date_text("9.9.2026") == ""
    assert W.validate_date_text("31.2.2026") == W.DATE_IMPOSSIBLE_ERROR
    assert W.validate_date_text("nesmysl") == W.DATE_INVALID_ERROR
    assert W.DATE_FORMAT_HINT in W.DATE_INVALID_ERROR


def test_date_entry_tlacitko_dnes(root: tk.Tk) -> None:
    pole = W.DateEntry(root)
    pole.pack(fill="x")
    root.update()

    assert pole.get() == ""
    assert pole.is_valid() is True  # prázdné je povoleno

    pole.today_button.invoke()
    root.update()
    assert pole.get() == W.format_date(date.today())
    assert pole.get_date() == date.today()
    assert pole.error == ""


def test_date_entry_kontrola(root: tk.Tk) -> None:
    pole = W.DateEntry(root, allow_empty=False)
    pole.pack(fill="x")
    root.update()

    assert pole.validate() == W.DATE_REQUIRED_ERROR
    assert pole.error_label is not None
    assert pole.error_label.grid_info() != {}
    assert pole.entry.cget("style") == "Chyba.TEntry"

    pole.set("30.2.2026")
    root.update()
    # zápis chybu skryje, kontrola ji vrátí
    assert pole.error == ""
    assert pole.validate() == W.DATE_IMPOSSIBLE_ERROR

    pole.set_date(date(2026, 1, 15))
    assert pole.validate() == ""
    assert pole.is_valid() is True
    assert pole.error_label.grid_info() == {}

    # kontrola při opuštění pole
    pole.set("nesmysl")
    pole.entry.event_generate("<FocusOut>")
    root.update()
    assert pole.error == W.DATE_INVALID_ERROR

    pole.set_enabled(False)
    assert "disabled" in pole.entry.state()
    assert "disabled" in pole.today_button.state()


def test_date_entry_sdileni_promenne(root: tk.Tk) -> None:
    promenna = tk.StringVar(master=root, value="09.09.2026")
    pole = W.DateEntry(root, textvariable=promenna)
    pole.pack(fill="x")
    root.update()
    assert pole.get_date() == date(2026, 9, 9)
    promenna.set("10.09.2026")
    assert pole.get_date() == date(2026, 9, 10)


# --- Toolbar, StatusBar, Card ------------------------------------------------


def test_toolbar(root: tk.Tk) -> None:
    lista = W.Toolbar(root)
    lista.pack(fill="x")

    volane: list[str] = []
    hlavni = lista.add_button("Generovat", lambda: volane.append("gen"), primary=True, key="gen")
    lista.add_button("Smazat", danger=True, key="del")
    lista.add_separator()
    lista.add_spacer()
    popisek = lista.add_label("3 šablony")
    root.update()

    assert hlavni.cget("style") == "Primary.TButton"
    assert lista.buttons["del"].cget("style") == "Nebezpecne.TButton"
    assert popisek.cget("text") == "3 šablony"

    hlavni.invoke()
    assert volane == ["gen"]

    lista.set_enabled("gen", False)
    assert "disabled" in hlavni.state()
    lista.set_enabled("gen", True)
    assert "disabled" not in hlavni.state()
    lista.set_enabled("neexistuje", False)  # nesmí spadnout


def test_statusbar(root: tk.Tk) -> None:
    radek = W.StatusBar(root, text="Připraveno")
    radek.pack(fill="x", side="bottom")
    root.update()

    assert radek.label.cget("text") == "Připraveno"
    radek.set_error("Šablonu se nepodařilo načíst.")
    assert radek.label.cget("style") == "Chyba.TLabel"
    radek.set_success("Dopis byl vygenerován.")
    assert radek.label.cget("style") == "Uspech.TLabel"
    radek.set("Připraveno")
    assert radek.label.cget("style") == "Napoveda.TLabel"

    radek.start_progress("Načítám šablonu…")
    root.update()
    assert radek.progress is not None
    assert radek.progress.grid_info() != {}
    radek.stop_progress("Hotovo")
    root.update()
    assert radek.progress.grid_info() == {}
    assert radek.label.cget("text") == "Hotovo"

    radek.clear()
    assert radek.label.cget("text") == ""


def test_card(root: tk.Tk) -> None:
    karta = W.Card(root, "Údaje o držiteli", subtitle="Vyplňte podle rejstříku.")
    karta.pack(fill="x")
    ttk.Label(karta.body, text="obsah").pack()
    root.update()

    assert karta.cget("style") == "Karta.TFrame"
    assert karta.title_label.cget("text") == "Údaje o držiteli"
    assert karta.title_label.grid_info() != {}
    assert karta.subtitle_label.grid_info() != {}
    assert len(karta.body.winfo_children()) == 1

    karta.set_title("")
    assert karta.title_label.grid_info() == {}
    karta.set_subtitle("")
    assert karta.subtitle_label.grid_info() == {}

    prazdna = W.Card(root)
    assert prazdna.title_label.grid_info() == {}


# --- dialogy, kurzor, vlákna -------------------------------------------------


def test_dialogy_maji_cesky_titulek(root: tk.Tk, monkeypatch: pytest.MonkeyPatch) -> None:
    zaznam: dict[str, object] = {}

    def falesny(title, message, **kwargs):
        zaznam["title"] = title
        zaznam["message"] = message
        zaznam["kwargs"] = kwargs
        return True

    monkeypatch.setattr(W.messagebox, "showerror", falesny)
    monkeypatch.setattr(W.messagebox, "showinfo", falesny)
    monkeypatch.setattr(W.messagebox, "showwarning", falesny)
    monkeypatch.setattr(W.messagebox, "askyesno", falesny)

    W.show_error(root, "Šablonu se nepodařilo načíst.", detail="Soubor chybí.")
    assert APP_NAME in str(zaznam["title"])
    assert "chyba" in str(zaznam["title"]).lower()
    assert "Soubor chybí." in str(zaznam["message"])
    assert zaznam["kwargs"] == {"parent": root}  # type: ignore[comparison-overlap]

    W.show_info(root, "Dopis byl vygenerován.")
    assert zaznam["title"] == APP_NAME

    W.show_warning(root, "Některé placeholdery zůstaly nevyplněné.")
    assert "upozornění" in str(zaznam["title"])

    assert W.ask_yes_no(root, "Opravdu smazat šablonu?", default_yes=False) is True
    kwargs = zaznam["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["parent"] is root
    assert kwargs["default"] == W.messagebox.NO

    # bez rodiče se ``parent`` vůbec nepředává
    W.show_info(None, "Bez okna")
    assert zaznam["kwargs"] == {}


def test_busy_cursor_vraci_puvodni_kurzor(root: tk.Tk) -> None:
    puvodni = str(root.cget("cursor"))
    with W.busy_cursor(root):
        assert str(root.cget("cursor")) == "watch"
    assert str(root.cget("cursor")) == puvodni

    # výjimka uvnitř bloku kurzor stejně vrátí
    with pytest.raises(ValueError):
        with W.busy_cursor(root):
            raise ValueError("bum")
    assert str(root.cget("cursor")) == puvodni

    with W.busy_cursor(None):  # nesmí spadnout
        pass


def test_run_in_thread_doruci_vysledek_do_tk_vlakna(root: tk.Tk) -> None:
    hlavni = threading.get_ident()
    zaznam: dict[str, object] = {}

    def prace() -> str:
        assert threading.get_ident() != hlavni, "práce má běžet ve vlákně"
        time.sleep(0.02)
        return "hotovo"

    def hotovo(vysledek: object) -> None:
        zaznam["vysledek"] = vysledek
        zaznam["vlakno"] = threading.get_ident()

    vlakno = W.run_in_thread(root, prace, hotovo, poll_ms=5)
    assert _pump(root, lambda: "vysledek" in zaznam), "výsledek nedorazil"
    vlakno.join(timeout=2)

    assert zaznam["vysledek"] == "hotovo"
    assert zaznam["vlakno"] == hlavni


def test_run_in_thread_preda_vyjimku(root: tk.Tk) -> None:
    hlavni = threading.get_ident()
    zaznam: dict[str, object] = {}

    def prace() -> None:
        raise ValueError("šablona je poškozená")

    def chyba(exc: BaseException) -> None:
        zaznam["chyba"] = exc
        zaznam["vlakno"] = threading.get_ident()

    W.run_in_thread(root, prace, None, chyba, poll_ms=5)
    assert _pump(root, lambda: "chyba" in zaznam), "chyba nedorazila"

    assert isinstance(zaznam["chyba"], ValueError)
    assert str(zaznam["chyba"]) == "šablona je poškozená"
    assert zaznam["vlakno"] == hlavni


def test_run_in_thread_bez_on_error_jen_vypise(root: tk.Tk, capsys: pytest.CaptureFixture) -> None:
    hotovo: list[object] = []
    W.run_in_thread(root, lambda: 1 / 0, poll_ms=5)
    W.run_in_thread(root, lambda: 42, hotovo.append, poll_ms=5)
    assert _pump(root, lambda: bool(hotovo))
    assert hotovo == [42]
    assert "ZeroDivisionError" in capsys.readouterr().err
