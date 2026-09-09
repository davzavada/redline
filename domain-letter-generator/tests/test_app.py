"""Testy okna aplikace (:mod:`dlg.app`) — navigace, zkratky, ošetření chyb.

Testy potřebují běžící X server; v CI se spouštějí přes ``xvfb-run``::

    xvfb-run -a python -m pytest -q tests/test_app.py

Bez displeje se test přeskočí (fixture ``app`` zavolá ``pytest.skip``). Data
aplikace míří přes ``DLG_HOME`` do ``tmp_path``, takže se nikde nesahá na
skutečný profil uživatele.

Modální dialogy se nahrazují záznamníky — otevřít je by znamenalo zablokovat
smyčku událostí, dokud je někdo ručně nezavře.
"""

from __future__ import annotations

import gc
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

tk = pytest.importorskip("tkinter", reason="tkinter není k dispozici")
from tkinter import ttk  # noqa: E402  (až po importorskip)

from fixtures import document_xml, make_docx, text_paragraph  # noqa: E402

from dlg import app as app_module  # noqa: E402
from dlg import config  # noqa: E402
from dlg.store import StoreError, TemplateStore  # noqa: E402
from dlg.ui import theme, widgets  # noqa: E402
from dlg.version import APP_NAME, __version__  # noqa: E402

pytestmark = pytest.mark.gui


# ---------------------------------------------------------------------------
# prostředí a pomůcky
# ---------------------------------------------------------------------------
@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Dočasné ``DLG_HOME`` — testy se nesmí dotknout skutečného profilu."""

    root = tmp_path / "dlg-home"
    monkeypatch.setenv(config.ENV_HOME, str(root))
    monkeypatch.setenv(config.ENV_OUTPUT_DIR, str(tmp_path / "vystup"))
    assert config.app_home() == root
    return root


@pytest.fixture(autouse=True)
def bez_dialogu(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Modální okna nahradíme záznamem — jinak by test čekal na kliknutí."""

    recorded: dict[str, list[Any]] = {"error": [], "warning": [], "info": [], "otazka": []}

    def record(name: str, answer: Any = None) -> Callable[..., Any]:
        def handler(*args: Any, **kwargs: Any) -> Any:
            recorded[name].append((args, kwargs))
            return answer

        return handler

    monkeypatch.setattr(widgets, "show_error", record("error"))
    monkeypatch.setattr(widgets, "show_warning", record("warning"))
    monkeypatch.setattr(widgets, "show_info", record("info"))
    monkeypatch.setattr(widgets, "ask_yes_no", record("otazka", True))
    return recorded


def make_template_docx(path: Path, *, jmeno: str = "Jan Novák") -> Path:
    """Malá, ale platná šablona se dvěma místy k vyplnění."""

    document = document_xml(
        text_paragraph(f"Vážený pane [{jmeno}],")
        + text_paragraph("jste držitelem doménového jména {{domena}}.")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_docx(document))
    return path


@pytest.fixture()
def store(home: Path) -> TemplateStore:
    """Prázdná knihovna šablon."""

    return TemplateStore(config.templates_dir())


@pytest.fixture()
def store_se_sablonou(home: Path, tmp_path: Path) -> TemplateStore:
    """Knihovna s jednou nahranou šablonou."""

    library = TemplateStore(config.templates_dir())
    library.import_docx(make_template_docx(tmp_path / "sablona.docx"), "Výzva k nápravě")
    return library


def new_app(**kwargs: Any) -> app_module.App:
    """Okno aplikace; bez displeje se test přeskočí."""

    try:
        return app_module.App(**kwargs)
    except tk.TclError as exc:  # pragma: no cover - závisí na prostředí
        pytest.skip(f"Tk se nepodařilo spustit: {exc}")


def close_app(window: app_module.App | None) -> None:
    """Úklid po testu — Tk se musí uvolnit v hlavním vlákně."""

    if window is None:
        return
    try:
        window.update_idletasks()
    except tk.TclError:  # pragma: no cover - okno už zaniklo
        pass
    try:
        window.destroy()
    except tk.TclError:  # pragma: no cover
        pass
    del window
    gc.collect()


@pytest.fixture()
def app(store: TemplateStore):
    """Aplikace nad prázdnou knihovnou (stav „první spuštění“)."""

    window = new_app(store=store)
    window.update()
    try:
        yield window
    finally:
        close_app(window)


@pytest.fixture()
def app_se_sablonou(store_se_sablonou: TemplateStore):
    """Aplikace nad knihovnou s jednou šablonou."""

    window = new_app(store=store_se_sablonou)
    window.update()
    try:
        yield window
    finally:
        close_app(window)


def nav_enabled(window: app_module.App, key: str) -> bool:
    button = window.nav_button(key)
    assert button is not None
    return "disabled" not in button.state()


def status_text(window: app_module.App) -> str:
    return str(window.status_bar.label.cget("text"))


# ---------------------------------------------------------------------------
# start okna
# ---------------------------------------------------------------------------
def test_okno_nastartuje_a_ma_cesky_titulek(app: app_module.App) -> None:
    """Ověřuje totéž co ruční spuštění: okno vznikne a má správný titulek."""

    app.update_idletasks()
    assert app.title() == f"{APP_NAME} {__version__}"
    assert app.winfo_exists()
    assert app.minsize() == tuple(theme.WINDOW_MIN_SIZE)


def test_upravena_pole_se_promitnou_do_generovat(
    app_se_sablonou: app_module.App, store_se_sablonou: TemplateStore
) -> None:
    """Regrese: úprava v „Pole šablony“ se do formuláře v Generovat nedostala.

    Pohled Generovat staví formulář z definice polí. Příznak „pole se změnila“
    se ale testoval jen ve větvi pro „Pole šablony“, takže se uživatel vrátil
    na Generovat a viděl dál staré popisky a typy — s tím, že přejmenované
    pole vůbec nešlo vyplnit.
    """

    app = app_se_sablonou
    meta = store_se_sablonou.list()[0]
    app.show_view(app_module.NAV_GENERATE, template_id=meta.id)
    app.update()

    pohled = app.view(app_module.NAV_GENERATE)
    puvodni = [spec.label for spec in pohled._specs]
    assert puvodni, "předpoklad testu: formulář má pole"

    # uživatel v „Pole šablony“ přejmenuje první pole a uloží
    upravena = store_se_sablonou.get(meta.id)
    upravena.fields[0].label = "Držitel domény"
    store_se_sablonou.save_meta(upravena)
    app._on_fields_done(saved=True)
    app.update()

    app.show_view(app_module.NAV_GENERATE)
    app.update()

    pohled = app.view(app_module.NAV_GENERATE)
    popisky = [spec.label for spec in pohled._specs]
    assert "Držitel domény" in popisky, (
        f"formulář zůstal u staré definice polí: {popisky}"
    )


def test_zkratky_neplati_v_modalnim_dialogu(app: app_module.App) -> None:
    """Regrese: Ctrl+Q stisknuté v modálním dialogu zavřelo celou aplikaci.

    Zkratky se vázaly přes ``bind_all``, tedy na značku ``all``, kterou nese
    každý widget aplikace — i widgety v modálních dialozích. Modální
    ``grab_set()`` proti tomu nechrání, takže překlep v poli „Název šablony“
    zavřel aplikaci i s rozdělanou prací.
    """

    app.update()
    dialog = tk.Toplevel(app)
    dialog.transient(app)
    dialog.grab_set()
    entry = ttk.Entry(dialog)
    entry.pack()
    entry.focus_set()
    app.update()

    try:
        entry.event_generate("<Control-q>")
        app.update()
        assert app.winfo_exists(), "Ctrl+Q v dialogu zavřelo celou aplikaci"
        entry.event_generate("<Control-g>")
        entry.event_generate("<Control-o>")
        entry.event_generate("<F5>")
        app.update()
        assert app.winfo_exists()
    finally:
        try:
            dialog.grab_release()
            dialog.destroy()
        except tk.TclError:  # pragma: no cover
            pass


def test_zkratky_plati_v_hlavnim_okne(app: app_module.App) -> None:
    """Zkratky musí dál fungovat i s kurzorem uvnitř formuláře v hlavním okně.

    Vazba na okno (místo ``bind_all``) se na potomky přenáší přes ``bindtags`` —
    značku toplevelu nese každý widget uvnitř okna. Test to ověřuje na vlastní
    sekvenci, aby si nelezl do cesty s obsluhami aplikace (ty vracejí ``break``).
    """

    app.update()
    stisky: list[str] = []
    app.bind("<Control-y>", lambda _e: stisky.append("y"), add="+")
    pole = ttk.Entry(app)
    pole.grid()
    pole.focus_set()
    app.update()

    pole.event_generate("<Control-y>")
    app.update()
    assert stisky == ["y"], "zkratka nedorazila z widgetu uvnitř hlavního okna"
    pole.destroy()


def test_navigace_ma_ctyri_ceske_polozky(app: app_module.App) -> None:
    texts = [str(app.nav_button(key).cget("text")) for key, _ in app_module.NAV_ITEMS]
    assert texts == ["Šablony", "Pole šablony", "Generovat", "Nastavení"]


def test_stavovy_radek_je_dole(app: app_module.App) -> None:
    app.set_status("Zkouška")
    assert status_text(app) == "Zkouška"
    app.set_status_error("Něco se pokazilo.")
    assert status_text(app) == "Něco se pokazilo."
    assert app.status_bar.winfo_manager() == "grid"
    assert app.status_bar.grid_info()["row"] == 1


def test_prvni_spusteni_otevre_sablony(app: app_module.App) -> None:
    """Bez jediné šablony se rovnou ukáže knihovna i s vysvětlením."""

    assert app.current_view_key == app_module.NAV_TEMPLATES
    assert status_text(app) == app_module.FIRST_RUN_STATUS
    view = app.view(app_module.NAV_TEMPLATES)
    assert view is not None and view.count == 0
    assert view.empty_holder.winfo_manager() == "grid"
    assert not nav_enabled(app, app_module.NAV_FIELDS)


def test_se_sablonou_startuje_v_generovani(app_se_sablonou: app_module.App) -> None:
    app = app_se_sablonou
    assert app.current_view_key == app_module.NAV_GENERATE
    view = app.view(app_module.NAV_GENERATE)
    assert view is not None and view.template_id
    assert nav_enabled(app, app_module.NAV_FIELDS)


def test_rozbita_knihovna_start_nezastavi(
    home: Path, bez_dialogu: dict[str, list[Any]]
) -> None:
    class RozbitaKnihovna:
        root = home / "templates"

        def list(self) -> list[Any]:
            raise StoreError("Knihovnu šablon se nepodařilo přečíst.")

        def invalidate_scan(self, template_id: str | None = None) -> None:
            pass

    window = new_app(store=RozbitaKnihovna())
    try:
        window.update()
        assert window.winfo_exists()
        assert window.current_view_key == app_module.NAV_TEMPLATES
    finally:
        close_app(window)


# ---------------------------------------------------------------------------
# přepínání pohledů
# ---------------------------------------------------------------------------
def test_pohledy_vznikaji_lize(app_se_sablonou: app_module.App) -> None:
    app = app_se_sablonou
    assert app.view(app_module.NAV_SETTINGS) is None

    assert app.show_view(app_module.NAV_SETTINGS)
    app.update()
    view = app.view(app_module.NAV_SETTINGS)
    assert view is not None
    assert app.current_view_key == app_module.NAV_SETTINGS


def test_pohled_se_vyrabi_jen_jednou(app_se_sablonou: app_module.App) -> None:
    app = app_se_sablonou
    app.show_view(app_module.NAV_TEMPLATES)
    app.update()
    first = app.view(app_module.NAV_TEMPLATES)

    app.show_view(app_module.NAV_GENERATE)
    app.update()
    app.show_view(app_module.NAV_TEMPLATES)
    app.update()

    assert app.view(app_module.NAV_TEMPLATES) is first


def test_videt_je_vzdy_jen_jeden_pohled(app_se_sablonou: app_module.App) -> None:
    app = app_se_sablonou
    for key, _label in app_module.NAV_ITEMS:
        assert app.show_view(key), key
        app.update()
        visible = [
            k for k, _ in app_module.NAV_ITEMS if (v := app.view(k)) and v.winfo_manager()
        ]
        assert visible == [key]


def test_aktivni_polozka_navigace_je_zvyraznena(app_se_sablonou: app_module.App) -> None:
    app = app_se_sablonou
    app.show_view(app_module.NAV_SETTINGS)
    app.update()
    assert str(app.nav_button(app_module.NAV_SETTINGS).cget("style")) == "Nav.Selected.TButton"
    assert str(app.nav_button(app_module.NAV_GENERATE).cget("style")) == "Nav.TButton"


def test_pole_sablony_bez_sablony_nejde_otevrit(
    app: app_module.App, bez_dialogu: dict[str, list[Any]]
) -> None:
    assert app.show_view(app_module.NAV_FIELDS) is False
    assert app.current_view_key == app_module.NAV_TEMPLATES
    assert bez_dialogu["warning"], "uživatel má dostat vysvětlení"
    assert app_module.NO_TEMPLATE_MESSAGE in status_text(app)


def test_pole_sablony_se_otevre_s_vybranou_sablonou(
    app_se_sablonou: app_module.App, store_se_sablonou: TemplateStore
) -> None:
    app = app_se_sablonou
    template_id = store_se_sablonou.list()[0].id

    assert app.open_fields(template_id)
    app.update()
    view = app.view(app_module.NAV_FIELDS)
    assert view is not None and view.template_id == template_id
    assert app.current_template_id() == template_id


def test_sablony_navigují_na_upravu_poli(
    app_se_sablonou: app_module.App, store_se_sablonou: TemplateStore
) -> None:
    """Tlačítko *Upravit pole* v pohledu Šablony přepne okno na Pole šablony."""

    app = app_se_sablonou
    template_id = store_se_sablonou.list()[0].id

    app.show_view(app_module.NAV_TEMPLATES)
    app.update()
    templates = app.view(app_module.NAV_TEMPLATES)
    assert templates is not None and templates.select(template_id)
    templates.edit_fields()
    app.update()

    assert app.current_view_key == app_module.NAV_FIELDS
    assert app.view(app_module.NAV_FIELDS).template_id == template_id


def test_odchod_z_poli_zpet_na_sablony(
    app_se_sablonou: app_module.App, store_se_sablonou: TemplateStore
) -> None:
    app = app_se_sablonou
    template_id = store_se_sablonou.list()[0].id
    app.open_fields(template_id)
    app.update()

    app.view(app_module.NAV_FIELDS).go_back()
    app.update()
    assert app.current_view_key == app_module.NAV_TEMPLATES


def test_neulozene_zmeny_poli_se_hlidaji(
    app_se_sablonou: app_module.App,
    store_se_sablonou: TemplateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = app_se_sablonou
    template_id = store_se_sablonou.list()[0].id
    app.open_fields(template_id)
    app.update()

    view = app.view(app_module.NAV_FIELDS)
    view.pattern_field.set("{datum}_jinak")
    assert view.has_unsaved_changes()

    monkeypatch.setattr(widgets, "ask_yes_no", lambda *a, **k: False)
    assert app.show_view(app_module.NAV_GENERATE) is False
    assert app.current_view_key == app_module.NAV_FIELDS

    monkeypatch.setattr(widgets, "ask_yes_no", lambda *a, **k: True)
    assert app.show_view(app_module.NAV_GENERATE)
    assert app.current_view_key == app_module.NAV_GENERATE


# ---------------------------------------------------------------------------
# klávesové zkratky
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sequence", ["<Control-g>", "<Control-o>", "<F5>", "<Control-q>"]
)
def test_zkratky_jsou_navazane(app: app_module.App, sequence: str) -> None:
    # Vázané na OKNO, ne přes bind_all — jinak by platily i v modálních dialozích.
    assert app.bind(sequence), f"zkratka {sequence} chybí"
    assert not app.bind_all(sequence), (
        f"zkratka {sequence} visí na značce „all“, takže by platila i v dialozích"
    )


def test_ctrl_g_prepne_a_pak_generuje(
    app_se_sablonou: app_module.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = app_se_sablonou
    app.show_view(app_module.NAV_TEMPLATES)
    app.update()

    app.event_generate("<Control-g>")
    app.update()
    assert app.current_view_key == app_module.NAV_GENERATE

    view = app.view(app_module.NAV_GENERATE)
    volani: list[bool] = []
    monkeypatch.setattr(view, "generate", lambda *a, **k: volani.append(True))
    app.event_generate("<Control-g>")
    app.update()
    assert volani == [True]


def test_ctrl_o_nabidne_nahrani_sablony(
    app: app_module.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dlg.ui import templates_view as tv

    monkeypatch.setattr(tv, "ask_docx_path", lambda *a, **k: "")
    app.show_view(app_module.NAV_SETTINGS)
    app.update()

    app.event_generate("<Control-o>")
    app.update()

    assert app.current_view_key == app_module.NAV_TEMPLATES
    assert "zrušeno" in status_text(app)


def test_f5_znovu_nacte_data(app: app_module.App) -> None:
    app.event_generate("<F5>")
    app.update()
    assert status_text(app) == "Data jsou znovu načtená."


def test_ctrl_q_zavre_okno(app_se_sablonou: app_module.App) -> None:
    app = app_se_sablonou
    app.event_generate("<Control-q>")
    try:
        alive = bool(app.winfo_exists())
    except tk.TclError:  # pragma: no cover - interpret Tk je pryč
        alive = False
    assert not alive


# ---------------------------------------------------------------------------
# ošetření chyb
# ---------------------------------------------------------------------------
def test_chyba_v_callbacku_neshodi_aplikaci(
    app: app_module.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    hlaseno: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        app_module, "report_exception", lambda *args: hlaseno.append(args)
    )

    chyba = ValueError("pokusná chyba")
    app.report_callback_exception(type(chyba), chyba, None)
    app.update()

    assert hlaseno, "chyba se má ukázat v okně"
    assert app.winfo_exists(), "aplikace nesmí spadnout"
    assert "pokusná chyba" in status_text(app)


def test_chyba_v_callbacku_prochazi_z_tlacitka(
    app: app_module.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tk posílá výjimky z callbacků do ``report_callback_exception``."""

    hlaseno: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        app_module, "report_exception", lambda *args: hlaseno.append(args)
    )

    def rozbite() -> None:
        raise RuntimeError("prasklo to")

    app.after_idle(rozbite)
    app.update()

    assert hlaseno
    assert app.winfo_exists()


def test_excepthook_se_nastavi_a_vrati(store: TemplateStore) -> None:
    puvodni = sys.excepthook
    window = new_app(store=store)
    try:
        assert sys.excepthook is not puvodni
    finally:
        close_app(window)
    assert sys.excepthook is puvodni


def test_chybove_okno_ma_ceske_texty_a_kopiruje(app: app_module.App) -> None:
    chyba = ValueError("pokusná chyba")
    detail = app_module.format_exception(type(chyba), chyba, None)
    dialog = app_module.ErrorDialog(app, "Nastala neočekávaná chyba", detail)
    try:
        app.update()
        assert "chyba" in str(dialog.title()).lower()
        assert "ValueError" in dialog.detail_text()
        assert __version__ in dialog.detail_text()
        assert dialog.copy_detail()
        assert dialog.clipboard_get() == detail
    finally:
        dialog.close()


def test_format_exception_obsahuje_hlavicku() -> None:
    chyba = ValueError("rozbité")
    text = app_module.format_exception(type(chyba), chyba, None)
    assert APP_NAME in text
    assert "ValueError: rozbité" in text


# ---------------------------------------------------------------------------
# velikost a pozice okna
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, cekano",
    [
        ("1000x680+40+30", "1000x680+40+30"),
        ("4000x3000+9000+9000", "1280x1024+0+0"),  # nevejde se — ořízne se
        ("100x50+10+10", "960x640+10+10"),  # menší než minimum
        ("nesmysl", ""),
        ("", ""),
    ],
)
def test_sanitize_geometry(text: str, cekano: str) -> None:
    assert app_module.sanitize_geometry(text, screen=(1280, 1024)) == cekano


def test_okno_si_pamatuje_velikost(home: Path, store: TemplateStore) -> None:
    window = new_app(store=store)
    try:
        window.geometry("1000x680+40+30")
        window.update()
        window.close()
    finally:
        close_app(window)

    ulozeno = app_module.load_window_state()
    assert ulozeno["geometry"] == "1000x680+40+30"
    assert (home / app_module.WINDOW_FILE_NAME).is_file()

    obnovene = new_app(store=TemplateStore(config.templates_dir()))
    try:
        obnovene.update()
        assert obnovene.geometry().startswith("1000x680")
    finally:
        close_app(obnovene)


def test_poskozeny_soubor_s_geometrii_nevadi(home: Path, store: TemplateStore) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / app_module.WINDOW_FILE_NAME).write_text("{tohle není JSON", encoding="utf-8")

    assert app_module.load_window_state() == {"geometry": "", "zoomed": False}
    window = new_app(store=store)
    try:
        window.update()
        assert window.geometry().startswith(f"{app_module.DEFAULT_SIZE[0]}x")
    finally:
        close_app(window)


# ---------------------------------------------------------------------------
# PyInstaller a ikona
# ---------------------------------------------------------------------------
def test_resource_path_ze_zdrojaku() -> None:
    assert app_module.resource_path("assets", "app.ico").is_file()


def test_resource_path_pod_pyinstallerem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert app_module.resource_path("assets", "app.ico") == tmp_path / "assets" / "app.ico"


def test_ikona_bez_souboru_nevadi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert app_module.app_icon_path() is None
    assert app_module.apply_icon(object()) is False


def test_ikona_se_mimo_windows_preskoci(
    app: app_module.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(theme, "is_windows", lambda: False)
    assert app_module.apply_icon(app) is False


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------
class FalesnaAplikace:
    """Náhrada za :class:`dlg.app.App` — ``main()`` nesmí v testu čekat na okno."""

    instance: list["FalesnaAplikace"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.smycka = 0
        FalesnaAplikace.instance.append(self)

    def mainloop(self) -> None:
        self.smycka += 1

    def destroy(self) -> None:  # pragma: no cover - volá se jen při Ctrl+C
        pass


def test_main_spusti_smycku(monkeypatch: pytest.MonkeyPatch) -> None:
    FalesnaAplikace.instance.clear()
    monkeypatch.setattr(app_module, "App", FalesnaAplikace)
    assert app_module.main() == 0
    assert FalesnaAplikace.instance and FalesnaAplikace.instance[0].smycka == 1


def test_main_hlasi_chybejici_tkinter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def bez_tkinteru() -> None:
        raise ImportError("No module named 'tkinter'")

    monkeypatch.setattr(app_module, "_require_tkinter", bez_tkinteru)
    assert app_module.main() == 1
    chyby = capsys.readouterr().err
    assert "tkinter" in chyby
    assert "python3-tk" in chyby


def test_main_hlasi_chybejici_displej(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def bez_displeje(**kwargs: Any) -> None:
        raise tk.TclError("no display name and no $DISPLAY environment variable")

    monkeypatch.setattr(app_module, "App", bez_displeje)
    assert app_module.main() == 1
    assert app_module.DISPLAY_MISSING_MESSAGE in capsys.readouterr().err


def test_main_hlasi_neuspesny_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def rozbity_start(**kwargs: Any) -> None:
        raise RuntimeError("něco se pokazilo")

    monkeypatch.setattr(app_module, "App", rozbity_start)
    assert app_module.main() == 1
    assert app_module.START_FAILED_MESSAGE in capsys.readouterr().err


def test_main_hlasi_start_i_bez_stderru(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pod ``console=False`` je ``sys.stderr`` None — hláška nesmí zmizet."""

    def rozbity_start(**kwargs: Any) -> None:
        raise RuntimeError("něco se pokazilo")

    monkeypatch.setattr(app_module, "App", rozbity_start)
    monkeypatch.setattr(app_module, "_show_fatal_dialog", lambda _text: None)
    monkeypatch.setattr(app_module.sys, "stderr", None)
    monkeypatch.setattr(app_module.sys, "stdout", None)

    assert app_module.main() == 1

    log = home / app_module.START_ERROR_LOG_NAME
    assert log.is_file()
    obsah = log.read_text(encoding="utf-8")
    assert app_module.START_FAILED_MESSAGE in obsah
    assert "něco se pokazilo" in obsah


def test_main_hlasi_chybejici_tkinter_i_do_logu(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bez_tkinteru() -> None:
        raise ImportError("No module named 'tkinter'")

    monkeypatch.setattr(app_module, "_require_tkinter", bez_tkinteru)
    monkeypatch.setattr(app_module, "_show_fatal_dialog", lambda _text: None)
    monkeypatch.setattr(app_module.sys, "stderr", None)

    assert app_module.main() == 1
    obsah = (home / app_module.START_ERROR_LOG_NAME).read_text(encoding="utf-8")
    assert "python3-tk" in obsah


def test_app_prezije_necitelny_ukazatel_na_slozku(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ukazatel uložený v ANSI nesmí shodit start (tichý pád .exe)."""

    monkeypatch.delenv(config.ENV_HOME, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "profil"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "profil" / ".config"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "profil"))
    misto = config.location_path()
    misto.parent.mkdir(parents=True, exist_ok=True)
    misto.write_bytes('{"data_dir": "D:\\\\Šablony"}'.encode("cp1250"))

    assert config.app_home() == config.default_app_home()  # po opravě read_json
    store = app_module.App._make_store()
    assert store.root.is_absolute()
    assert app_module.App._make_history().path.is_absolute()


def test_rozepsany_dopis_se_pri_zavreni_hlida(
    app_se_sablonou: app_module.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dvacet ručně vyplněných údajů klienta nesmí zmizet bez dotazu."""

    app = app_se_sablonou
    app.show_view(app_module.NAV_GENERATE)
    app.update()

    view = app.view(app_module.NAV_GENERATE)
    assert view is not None and view.meta is not None
    klic = next(f.key for f in view.meta.ordered_fields())
    view.set_value(klic, "Jan Novák")
    assert view.has_unsaved_input() is True

    monkeypatch.setattr(widgets, "ask_yes_no", lambda *a, **k: False)
    app.event_generate("<Control-q>")
    app.update()
    assert app.winfo_exists()

    monkeypatch.setattr(widgets, "ask_yes_no", lambda *a, **k: True)
    app.event_generate("<Control-q>")
    try:
        alive = bool(app.winfo_exists())
    except tk.TclError:  # pragma: no cover - interpret Tk je pryč
        alive = False
    assert not alive
