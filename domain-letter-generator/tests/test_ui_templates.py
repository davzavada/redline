"""Testy pohledů „Šablony“ (:mod:`dlg.ui.templates_view`) a „Nastavení“
(:mod:`dlg.ui.settings_view`).

Testy potřebují běžící X server — v CI se spouštějí přes ``xvfb-run``::

    xvfb-run -a python -m pytest -q tests/test_ui_templates.py

Bez displeje se celý soubor přeskočí (fixture ``root`` zavolá ``pytest.skip``).
Data aplikace míří přes ``DLG_HOME`` do ``tmp_path``, takže testy nesahají na
skutečný profil uživatele.

Modální dialogy a systémové dialogy se v testech nahrazují — otevřít je by
znamenalo zablokovat smyčku událostí, dokud je někdo ručně nezavře.
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter", reason="tkinter není k dispozici")
from tkinter import ttk  # noqa: E402  (až po importorskip)

from fixtures import document_xml, make_docx, text_paragraph  # noqa: E402

from dlg import config  # noqa: E402
from dlg.config import ConfigError, Settings  # noqa: E402
from dlg.store import TemplateStore  # noqa: E402
from dlg.ui import settings_view as sv  # noqa: E402
from dlg.ui import templates_view as tv  # noqa: E402
from dlg.ui import theme, widgets  # noqa: E402

pytestmark = pytest.mark.gui

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# fixtures a pomůcky
# ---------------------------------------------------------------------------
@pytest.fixture()
def root():
    """Okno pro jeden test; bez displeje se test přeskočí.

    Po testu se okno nejen zruší, ale hned se i uklidí odpad (``gc.collect``).
    Interpret Tk se totiž **musí** uvolnit v hlavním vlákně — kdyby na něj
    došlo až při úklidu v některém pracovním vlákně, Tcl celý proces ukončí
    hláškou „async handler deleted by the wrong thread“.
    """

    try:
        window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - závisí na prostředí
        pytest.skip(f"Tk se nepodařilo spustit: {exc}")
    window.geometry("1000x700")
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
        del window
        gc.collect()


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Dočasný domov aplikace — šablony i nastavení jdou do ``tmp_path``."""

    data = tmp_path / "data"
    monkeypatch.setenv(config.ENV_HOME, str(data))
    monkeypatch.setenv(config.ENV_OUTPUT_DIR, str(tmp_path / "dopisy"))
    return data


@pytest.fixture()
def store(home: Path) -> TemplateStore:
    return TemplateStore()


@pytest.fixture()
def status_log() -> list[str]:
    return []


def pump(window: tk.Misc, predicate, timeout: float = 10.0) -> bool:
    """Točí smyčku událostí, dokud podmínka neplatí (nebo nevyprší čas)."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window.update()
        if predicate():
            return True
        time.sleep(0.01)
    window.update()
    return predicate()


def write_template_docx(path: Path, *, domain: str = "priklad.cz") -> Path:
    """Malá, ale platná šablona se dvěma místy k vyplnění."""

    document = document_xml(
        text_paragraph("Vážený pane [Jan Novák],")
        + text_paragraph(f"jste držitelem doménového jména [{domain}].")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_docx(document))
    return path


def cells(view: tv.TemplatesView, template_id: str) -> list[str]:
    return [str(value) for value in view.tree.item(template_id, "values")]


class Recorder:
    """Zaznamená volání náhradního dialogu a vrátí připravenou odpověď."""

    def __init__(self, result=None) -> None:
        self.result = result
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result

    @property
    def called(self) -> bool:
        return bool(self.calls)

    def texts(self) -> str:
        """Všechny textové argumenty dohromady — pro kontrolu češtiny."""

        parts: list[str] = []
        for args, kwargs in self.calls:
            parts.extend(str(a) for a in args)
            parts.extend(str(v) for v in kwargs.values())
        return "\n".join(parts)


def silence_dialogs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Recorder]:
    """Nahradí messageboxy záznamníky, aby test nikdy nečekal na kliknutí."""

    recorders = {
        "error": Recorder(None),
        "warning": Recorder(None),
        "info": Recorder(None),
        "yes_no": Recorder(True),
    }
    monkeypatch.setattr(widgets, "show_error", recorders["error"])
    monkeypatch.setattr(widgets, "show_warning", recorders["warning"])
    monkeypatch.setattr(widgets, "show_info", recorders["info"])
    monkeypatch.setattr(widgets, "ask_yes_no", recorders["yes_no"])
    return recorders


def make_view(
    root: tk.Tk, store: TemplateStore, status_log: list[str]
) -> tuple[tv.TemplatesView, list[str], list[str]]:
    """Pohled Šablony + seznamy id, se kterými se zavolala navigace."""

    edited: list[str] = []
    generated: list[str] = []
    view = tv.TemplatesView(
        root,
        store,
        edited.append,
        generated.append,
        status_log.append,
    )
    view.pack(fill="both", expand=True)
    root.update()
    return view, edited, generated


def make_settings_view(
    root: tk.Tk, status_log: list[str], saver=None
) -> tuple[sv.SettingsView, list[Settings]]:
    saved: list[Settings] = []

    def default_saver(settings: Settings) -> None:
        saved.append(settings)
        config.save_settings(settings)

    view = sv.SettingsView(
        root,
        config.load_settings,
        saver or default_saver,
        status_log.append,
    )
    view.pack(fill="both", expand=True)
    root.update()
    return view, saved


# ---------------------------------------------------------------------------
# import modulů
# ---------------------------------------------------------------------------
def test_import_pohledu_bez_displeje_nevytvori_okno() -> None:
    """Oba pohledy musí jít naimportovat i na stroji bez X serveru."""

    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    code = (
        "import tkinter;"
        "from dlg.ui import settings_view, templates_view;"
        "assert tkinter._default_root is None, 'import vyrobil Tk okno';"
        "assert templates_view.EMPTY_HINT and settings_view.KEY_HELP;"
        "print('OK')"
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
    assert result.stdout.strip() == "OK"


# ---------------------------------------------------------------------------
# pomocné funkce pohledu Šablony
# ---------------------------------------------------------------------------
def test_pomocne_funkce_formatuji_cesky() -> None:
    assert tv.parse_tags("výzva, doména;  výzva , ") == ["výzva", "doména"]
    assert tv.format_tags(["výzva", " doména "]) == "výzva, doména"
    assert tv.format_stamp("2026-09-09T12:30:00+02:00") == "09.09.2026"
    assert tv.format_stamp("") == ""
    assert tv.count_fields(1) == "1 pole"
    assert tv.count_fields(3) == "3 pole"
    assert tv.count_fields(7) == "7 polí"


# ---------------------------------------------------------------------------
# Šablony — prázdný stav a seznam
# ---------------------------------------------------------------------------
def test_prazdny_stav_vysvetli_a_nabidne_nahrani(
    root: tk.Tk, store: TemplateStore, status_log: list[str]
) -> None:
    view, _edited, _generated = make_view(root, store, status_log)

    assert view.count == 0
    assert view.empty_holder.winfo_ismapped()
    assert not view.table_holder.winfo_ismapped()
    assert view.empty_label.cget("text").startswith("Zatím nemáte žádnou šablonu")
    assert str(view.empty_button.cget("text")) == "Nahrát šablonu…"

    # bez šablony nedávají akce nad výběrem smysl
    for key in tv._SELECTION_BUTTONS:
        assert "disabled" in view.toolbar.buttons[key].state()


def test_seznam_vykresli_nazev_stitky_pocet_poli_i_datum(
    root: tk.Tk, store: TemplateStore, status_log: list[str], tmp_path: Path
) -> None:
    source = write_template_docx(tmp_path / "vzor.docx")
    prvni = store.import_docx(source, "Výzva k nápravě", tags=["výzva", "doména"])
    druha = store.import_docx(source, "Předžalobní výzva", description="ostrá verze")

    view, _edited, _generated = make_view(root, store, status_log)

    assert view.count == 2
    assert view.table_holder.winfo_ismapped()
    assert not view.empty_holder.winfo_ismapped()

    # store.list() řadí podle názvu bez ohledu na diakritiku
    assert list(view.tree.get_children("")) == [druha.id, prvni.id]

    radek = cells(view, prvni.id)
    assert radek[0] == "Výzva k nápravě"
    assert radek[1] == "výzva, doména"
    assert radek[2] == str(len(prvni.fields))
    assert int(radek[2]) >= 2
    assert radek[3] == tv.format_stamp(prvni.imported_at)

    assert cells(view, druha.id)[1] == ""
    assert "2 šablony" in str(view.subtitle.cget("text"))


def test_refresh_zachova_vyber_a_zaregistruje_novou_sablonu(
    root: tk.Tk, store: TemplateStore, status_log: list[str], tmp_path: Path
) -> None:
    source = write_template_docx(tmp_path / "vzor.docx")
    prvni = store.import_docx(source, "Alfa")
    view, _edited, _generated = make_view(root, store, status_log)
    assert view.select(prvni.id)

    druha = store.import_docx(source, "Beta")
    view.refresh()
    root.update()

    assert view.count == 2
    assert view.selected_template_id() == prvni.id
    assert view.selected_meta() is not None
    assert druha.id in view.tree.get_children("")


def test_vyber_zpristupni_akce_a_dvojklik_upravi_pole(
    root: tk.Tk, store: TemplateStore, status_log: list[str], tmp_path: Path
) -> None:
    source = write_template_docx(tmp_path / "vzor.docx")
    meta = store.import_docx(source, "Výzva")
    view, edited, generated = make_view(root, store, status_log)

    assert view.selected_template_id() == meta.id
    for key in tv._SELECTION_BUTTONS:
        assert "disabled" not in view.toolbar.buttons[key].state()

    # dvojklik je navázaný a chová se jako „Upravit pole“
    assert view.tree.bind("<Double-1>")
    view._on_double_click(SimpleNamespace(y=0))
    assert edited == [meta.id]

    view.generate()
    assert generated == [meta.id]


def test_akce_bez_vyberu_jen_poradi_v_stavovem_radku(
    root: tk.Tk, store: TemplateStore, status_log: list[str]
) -> None:
    view, edited, generated = make_view(root, store, status_log)

    view.edit_fields()
    view.generate()

    assert edited == [] and generated == []
    assert status_log and "vyberte šablonu" in status_log[-1].lower()


# ---------------------------------------------------------------------------
# Šablony — nahrání
# ---------------------------------------------------------------------------
def test_nahrani_probehne_ve_vlakne_a_nabidne_upravu_poli(
    root: tk.Tk,
    store: TemplateStore,
    status_log: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialogs = silence_dialogs(monkeypatch)
    source = write_template_docx(tmp_path / "lego.docx")
    monkeypatch.setattr(tv, "ask_docx_path", lambda *_a, **_k: str(source))

    view, edited, _generated = make_view(root, store, status_log)
    details = Recorder({"name": "Výzva k nápravě", "description": "popis", "tags": ["LEGO"]})
    monkeypatch.setattr(view, "ask_details", details)

    view.import_template()
    assert pump(root, lambda: view.count == 1)

    assert details.called
    meta = store.list()[0]
    assert meta.name == "Výzva k nápravě"
    assert meta.tags == ["LEGO"]
    assert meta.description == "popis"

    # uživatel dostal na výběr a řekl ano => navigace na úpravu polí
    assert dialogs["yes_no"].called
    assert "Rozpoznáno" in dialogs["yes_no"].texts()
    assert edited == [meta.id]

    assert view.selected_template_id() == meta.id
    assert cells(view, meta.id)[0] == "Výzva k nápravě"
    hlaska = "\n".join(status_log)
    assert "rozpoznáno" in hlaska.lower()
    assert tv.count_fields(len(meta.fields)) in hlaska
    # po dokončení už pohled není zaneprázdněný
    assert "disabled" not in view.toolbar.buttons[tv.BTN_IMPORT].state()


def test_nahrani_lze_zrusit_v_obou_dialozich(
    root: tk.Tk,
    store: TemplateStore,
    status_log: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    silence_dialogs(monkeypatch)
    view, _edited, _generated = make_view(root, store, status_log)

    monkeypatch.setattr(tv, "ask_docx_path", lambda *_a, **_k: "")
    view.import_template()
    assert store.list() == []
    assert "zrušeno" in status_log[-1].lower()

    source = write_template_docx(tmp_path / "vzor.docx")
    monkeypatch.setattr(tv, "ask_docx_path", lambda *_a, **_k: str(source))
    monkeypatch.setattr(view, "ask_details", Recorder(None))
    view.import_template()
    root.update()
    assert store.list() == []
    assert "zrušeno" in status_log[-1].lower()


def test_nahrani_odmitne_soubor_ktery_neni_docx(
    root: tk.Tk,
    store: TemplateStore,
    status_log: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialogs = silence_dialogs(monkeypatch)
    starsi = tmp_path / "vzor.doc"
    starsi.write_bytes(b"cokoli")
    monkeypatch.setattr(tv, "ask_docx_path", lambda *_a, **_k: str(starsi))

    view, _edited, _generated = make_view(root, store, status_log)
    details = Recorder({"name": "X", "description": "", "tags": []})
    monkeypatch.setattr(view, "ask_details", details)

    view.import_template()

    assert not details.called  # k dialogu s údaji se to vůbec nedostane
    assert dialogs["error"].called
    assert ".docx" in dialogs["error"].texts()
    assert store.list() == []


def test_poskozeny_soubor_ohlasi_cesky_a_pohled_zustane_pouzitelny(
    root: tk.Tk,
    store: TemplateStore,
    status_log: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialogs = silence_dialogs(monkeypatch)
    rozbity = tmp_path / "rozbity.docx"
    rozbity.write_bytes(b"tohle rozhodne neni ZIP")

    view, _edited, _generated = make_view(root, store, status_log)
    view.import_file(rozbity, "Rozbitá")

    assert pump(root, lambda: dialogs["error"].called)
    hlaska = dialogs["error"].texts()
    assert "docx" in hlaska.lower()
    assert store.list() == []
    assert view.count == 0
    assert "disabled" not in view.toolbar.buttons[tv.BTN_IMPORT].state()
    assert status_log[-1]


def test_sablona_bez_poli_upozorni_a_prejde_na_pole(
    root: tk.Tk,
    store: TemplateStore,
    status_log: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialogs = silence_dialogs(monkeypatch)
    prazdna = tmp_path / "bez_poli.docx"
    prazdna.write_bytes(make_docx(document_xml(text_paragraph("Obyčejná věta bez míst."))))

    view, edited, _generated = make_view(root, store, status_log)
    view.import_file(prazdna, "Bez polí")

    assert pump(root, lambda: view.count == 1)
    assert dialogs["warning"].called
    assert "nenašli" in dialogs["warning"].texts().lower()
    assert edited == [store.list()[0].id]


# ---------------------------------------------------------------------------
# Šablony — přejmenování, duplikace, mazání, složka
# ---------------------------------------------------------------------------
def test_prejmenovani_a_duplikace(
    root: tk.Tk,
    store: TemplateStore,
    status_log: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    silence_dialogs(monkeypatch)
    source = write_template_docx(tmp_path / "vzor.docx")
    meta = store.import_docx(source, "Původní")
    view, _edited, _generated = make_view(root, store, status_log)

    monkeypatch.setattr(view, "ask_details", Recorder({"name": "Nový název", "tags": []}))
    view.rename()
    root.update()
    assert store.get(meta.id).name == "Nový název"
    assert cells(view, meta.id)[0] == "Nový název"

    monkeypatch.setattr(view, "ask_details", Recorder({"name": "Kopie výzvy", "tags": []}))
    view.duplicate()
    root.update()
    assert view.count == 2
    kopie = [m for m in store.list() if m.name == "Kopie výzvy"]
    assert len(kopie) == 1
    assert view.selected_template_id() == kopie[0].id
    assert kopie[0].fields  # kopie si nese i namapovaná pole


def test_mazani_vyzaduje_potvrzeni(
    root: tk.Tk,
    store: TemplateStore,
    status_log: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialogs = silence_dialogs(monkeypatch)
    source = write_template_docx(tmp_path / "vzor.docx")
    meta = store.import_docx(source, "Ke smazání")
    view, _edited, _generated = make_view(root, store, status_log)

    dialogs["yes_no"].result = False
    view.delete()
    root.update()
    assert view.count == 1
    assert dialogs["yes_no"].called
    assert meta.name in dialogs["yes_no"].texts()
    assert "zrušeno" in status_log[-1].lower()

    dialogs["yes_no"].result = True
    view.delete()
    root.update()
    assert view.count == 0
    assert store.list() == []
    assert view.empty_holder.winfo_ismapped()
    assert "smazána" in status_log[-1]


def test_otevrit_slozku_pouzije_spravce_souboru(
    root: tk.Tk,
    store: TemplateStore,
    status_log: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    silence_dialogs(monkeypatch)
    otevrene: list[Path] = []
    monkeypatch.setattr(tv, "open_in_file_manager", lambda path: otevrene.append(Path(path)))

    view, _edited, _generated = make_view(root, store, status_log)
    view.open_folder()  # bez šablony => celá knihovna
    assert otevrene == [Path(store.root)]
    assert Path(store.root).is_dir()

    source = write_template_docx(tmp_path / "vzor.docx")
    meta = store.import_docx(source, "Výzva")
    view.refresh()
    view.select(meta.id)
    view.open_folder()
    assert otevrene[-1] == Path(store.template_dir(meta.id))
    assert meta.name in status_log[-1]


def test_chyba_pri_otevirani_slozky_se_ohlasi(
    root: tk.Tk,
    store: TemplateStore,
    status_log: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialogs = silence_dialogs(monkeypatch)

    def selze(_path):
        raise OSError("xdg-open chybí")

    monkeypatch.setattr(tv, "open_in_file_manager", selze)
    view, _edited, _generated = make_view(root, store, status_log)
    view.open_folder()

    assert dialogs["error"].called
    assert "otevřít" in dialogs["error"].texts().lower()
    assert "nepodařilo" in status_log[-1].lower()


# ---------------------------------------------------------------------------
# Nastavení
# ---------------------------------------------------------------------------
def test_nastaveni_vykresli_ulozene_hodnoty(
    root: tk.Tk, home: Path, status_log: list[str], tmp_path: Path
) -> None:
    config.save_settings(
        Settings(
            output_dir=str(tmp_path / "vystup"),
            open_after_generate=False,
            clear_highlight=False,
            keep_unfilled=False,
            last_template_id="sablona-abc123",
            profile={"advokat": "Mgr. Jan Novák", "kontakt_email": "jan@example.cz"},
        )
    )

    view, _saved = make_settings_view(root, status_log)

    assert view.output_field.get() == str(tmp_path / "vystup")
    assert view.var_open_after.get() is False
    assert view.var_clear_highlight.get() is False
    assert view.var_keep_unfilled.get() is False
    assert view.profile_values() == {
        "advokat": "Mgr. Jan Novák",
        "kontakt_email": "jan@example.cz",
    }
    assert list(view.profile_tree.get_children("")) == ["advokat", "kontakt_email"]
    assert view.profile_tree.item("advokat", "values")[1] == "Mgr. Jan Novák"
    assert str(view.data_dir_label.cget("text")) == str(home)
    # samotné vykreslení nesmí nic uložit
    assert status_log == []


def test_prepinace_se_ukladaji_hned(
    root: tk.Tk, home: Path, status_log: list[str]
) -> None:
    view, saved = make_settings_view(root, status_log)

    view.var_open_after.set(False)
    root.update()
    assert saved and saved[-1].open_after_generate is False
    assert config.load_settings().open_after_generate is False
    assert status_log[-1] == "Dopis se po vygenerování otevírat nebude."

    view.check_clear_highlight.invoke()
    root.update()
    assert config.load_settings().clear_highlight is False
    assert "zvýraznění" in status_log[-1].lower()

    view.var_keep_unfilled.set(False)
    root.update()
    ulozene = config.load_settings()
    assert ulozene.keep_unfilled is False
    assert ulozene.open_after_generate is False  # dřívější změna se neztratila


def test_vystupni_slozka_se_ulozi_a_zachova_ostatni_nastaveni(
    root: tk.Tk, home: Path, status_log: list[str], tmp_path: Path
) -> None:
    config.save_settings(Settings(output_dir="", last_template_id="sablona-abc123"))
    view, _saved = make_settings_view(root, status_log)

    cil = tmp_path / "moje dopisy"
    view.output_field.set(str(cil))
    assert view.save_now() is True

    ulozene = config.load_settings()
    assert ulozene.output_dir == str(cil)
    assert ulozene.last_template_id == "sablona-abc123"
    assert "neexistuje" in view.output_field.help_label.cget("text")

    cil.mkdir(parents=True)
    view.output_field.set(str(cil))
    root.update()
    assert "existuje" in view.output_field.help_label.cget("text")


def test_prochazet_nastavi_slozku_z_dialogu(
    root: tk.Tk,
    home: Path,
    status_log: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    silence_dialogs(monkeypatch)
    cil = tmp_path / "vybrana"
    cil.mkdir()
    monkeypatch.setattr(sv, "ask_directory", lambda *_a, **_k: str(cil))

    view, _saved = make_settings_view(root, status_log)
    view.browse_output_dir()
    root.update()

    assert view.output_field.get() == str(cil)
    assert config.load_settings().output_dir == str(cil)
    assert str(cil) in status_log[-1]

    monkeypatch.setattr(sv, "ask_directory", lambda *_a, **_k: "")
    view.browse_output_dir()
    assert "zrušen" in status_log[-1].lower()
    assert view.output_field.get() == str(cil)


def test_profil_pridani_upravu_a_smazani(
    root: tk.Tk, home: Path, status_log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dialogs = silence_dialogs(monkeypatch)
    view, _saved = make_settings_view(root, status_log)

    assert view.profile_empty.winfo_ismapped()

    monkeypatch.setattr(
        view, "ask_profile_entry", Recorder({"key": "advokat", "value": "Mgr. Jan Novák"})
    )
    view.add_profile_entry()
    root.update()

    assert view.profile_values() == {"advokat": "Mgr. Jan Novák"}
    assert config.load_settings().profile == {"advokat": "Mgr. Jan Novák"}
    assert list(view.profile_tree.get_children("")) == ["advokat"]
    assert not view.profile_empty.winfo_ismapped()
    assert "advokat" in status_log[-1]

    # úprava vybraného řádku
    view.profile_tree.selection_set("advokat")
    monkeypatch.setattr(
        view, "ask_profile_entry", Recorder({"key": "advokat", "value": "JUDr. Jan Novák"})
    )
    view.edit_profile_entry()
    root.update()
    assert config.load_settings().profile["advokat"] == "JUDr. Jan Novák"

    # smazání se nejdřív potvrzuje
    dialogs["yes_no"].result = False
    view.profile_tree.selection_set("advokat")
    view.remove_profile_entry()
    root.update()
    assert view.profile_values() == {"advokat": "JUDr. Jan Novák"}

    dialogs["yes_no"].result = True
    view.profile_tree.selection_set("advokat")
    view.remove_profile_entry()
    root.update()
    assert view.profile_values() == {}
    assert config.load_settings().profile == {}
    assert view.profile_empty.winfo_ismapped()


def test_profil_odmitne_nesmyslny_klic(
    root: tk.Tk, home: Path, status_log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dialogs = silence_dialogs(monkeypatch)
    view, _saved = make_settings_view(root, status_log)

    monkeypatch.setattr(
        view, "ask_profile_entry", Recorder({"key": "Kontakt Email", "value": "x"})
    )
    view.add_profile_entry()
    root.update()

    assert view.profile_values() == {}
    assert dialogs["error"].called
    assert "klíč" in dialogs["error"].texts().lower()

    assert sv.validate_profile_key("kontakt_email") == ""
    assert sv.validate_profile_key("pole_1") == ""
    assert sv.validate_profile_key("") != ""
    assert sv.validate_profile_key("1pole") != ""
    assert sv.validate_profile_key("PSČ") != ""


def test_refresh_nastaveni_nic_neuklada(
    root: tk.Tk, home: Path, status_log: list[str]
) -> None:
    view, saved = make_settings_view(root, status_log)
    view.var_open_after.set(False)
    root.update()
    pocet = len(saved)

    config.save_settings(Settings(output_dir="/tmp/jinam", open_after_generate=True))
    view.refresh()
    root.update()

    assert len(saved) == pocet
    assert view.output_field.get() == "/tmp/jinam"
    assert view.var_open_after.get() is True


def test_chyba_ukladani_se_ohlasi_cesky(
    root: tk.Tk, home: Path, status_log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dialogs = silence_dialogs(monkeypatch)

    def rozbity_saver(_settings: Settings) -> None:
        raise ConfigError("Soubor „settings.json“ se nepodařilo uložit: chybí oprávnění.")

    view, _saved = make_settings_view(root, status_log, saver=rozbity_saver)
    view.var_clear_highlight.set(False)
    root.update()

    assert dialogs["error"].called
    assert "nepodařilo uložit" in dialogs["error"].texts()
    assert "nepodařilo uložit" in status_log[-1]


def test_otevrit_slozku_s_daty(
    root: tk.Tk, home: Path, status_log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    silence_dialogs(monkeypatch)
    otevrene: list[Path] = []
    monkeypatch.setattr(sv, "open_in_file_manager", lambda path: otevrene.append(Path(path)))

    view, _saved = make_settings_view(root, status_log)
    view.open_data_dir()

    assert otevrene == [home]
    assert home.is_dir()
    assert str(home) in status_log[-1]


def test_pohledy_pouzivaji_spolecne_prvky(
    root: tk.Tk, store: TemplateStore, home: Path, status_log: list[str]
) -> None:
    """Pohledy staví na ``dlg.ui.widgets`` — nic si nepředělávají po svém."""

    view, _edited, _generated = make_view(root, store, status_log)
    assert isinstance(view, ttk.Frame)
    assert isinstance(view.toolbar, widgets.Toolbar)
    assert isinstance(view.tree, ttk.Treeview)

    settings, _saved = make_settings_view(root, status_log)
    assert isinstance(settings, ttk.Frame)
    assert isinstance(settings.scroller, widgets.ScrollableFrame)
    assert isinstance(settings.output_field, widgets.LabeledEntry)
    assert isinstance(settings.profile_toolbar, widgets.Toolbar)
