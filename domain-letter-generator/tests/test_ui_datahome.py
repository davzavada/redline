"""Změna složky s daty přes obrazovku Nastavení."""

from __future__ import annotations

from pathlib import Path

import pytest

from dlg import config

tk = pytest.importorskip("tkinter")


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "profil"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "profil" / ".config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "profil" / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profil" / "AppData" / "Local"))
    monkeypatch.delenv(config.ENV_HOME, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "profil"))

    try:
        widget = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - bez displeje
        pytest.skip(f"Tk není k dispozici: {exc}")
    widget.withdraw()
    try:
        yield widget
    finally:
        widget.destroy()


def _view(root, **kwargs):
    from dlg.ui.settings_view import SettingsView

    view = SettingsView(root, **kwargs)
    view.update_idletasks()
    return view


def test_karta_ukazuje_vychozi_umisteni(root):
    view = _view(root)
    assert view.data_dir_label.cget("text") == str(config.default_app_home())
    assert "Výchozí" in view.data_source_label.cget("text")
    assert str(view.change_data_button.cget("state")) == "normal"
    assert str(view.reset_data_button.cget("state")) == "disabled"


def test_zmena_slozky_s_presunem_dat(root, tmp_path, monkeypatch):
    puvodni = config.app_home()
    (puvodni / "templates" / "vyzva-abc123").mkdir(parents=True)
    (puvodni / "templates" / "vyzva-abc123" / "template.docx").write_bytes(b"PK\x03\x04x")

    cil = tmp_path / "OneDrive" / "Dopisy"
    prekresleno: list[bool] = []
    view = _view(root, on_data_home_changed=lambda: prekresleno.append(True))

    monkeypatch.setattr("dlg.ui.settings_view.ask_directory", lambda *a, **k: str(cil))
    monkeypatch.setattr("dlg.ui.widgets.ask_yes_no", lambda *a, **k: True)

    view.change_data_dir()

    assert config.app_home() == cil
    assert (cil / "templates" / "vyzva-abc123" / "template.docx").exists()
    assert not (puvodni / "templates").exists()
    assert prekresleno == [True]
    assert view.data_dir_label.cget("text") == str(cil)
    assert "ručně" in view.data_source_label.cget("text")
    assert str(view.reset_data_button.cget("state")) == "normal"


def test_napojeni_na_existujici_data_nic_neprepise(root, tmp_path, monkeypatch):
    puvodni = config.app_home()
    (puvodni / "templates" / "moje").mkdir(parents=True)

    sdilena = tmp_path / "sdilene"
    (sdilena / "templates" / "cizi").mkdir(parents=True)
    (sdilena / "templates" / "cizi" / "template.docx").write_bytes(b"PK\x03\x04cizi")

    view = _view(root)
    monkeypatch.setattr("dlg.ui.settings_view.ask_directory", lambda *a, **k: str(sdilena))
    monkeypatch.setattr("dlg.ui.widgets.ask_yes_no", lambda *a, **k: True)

    view.change_data_dir()

    assert config.app_home() == sdilena
    assert (sdilena / "templates" / "cizi" / "template.docx").read_bytes() == b"PK\x03\x04cizi"
    # dosavadní šablony zůstaly nedotčené tam, kde byly
    assert (puvodni / "templates" / "moje").is_dir()


def test_zruseny_vyber_nic_nezmeni(root, monkeypatch):
    view = _view(root)
    monkeypatch.setattr("dlg.ui.settings_view.ask_directory", lambda *a, **k: "")

    view.change_data_dir()

    assert config.data_home_status().source == "default"


def test_obnoveni_vychoziho_umisteni(root, tmp_path, monkeypatch):
    config.set_app_home(tmp_path / "jinam")
    view = _view(root)
    assert str(view.reset_data_button.cget("state")) == "normal"

    monkeypatch.setattr("dlg.ui.widgets.ask_yes_no", lambda *a, **k: False)
    view.reset_data_dir()

    assert config.data_home_status().source == "default"
    assert str(view.reset_data_button.cget("state")) == "disabled"


def test_vynucene_umisteni_zamkne_tlacitka(root, tmp_path, monkeypatch):
    monkeypatch.setenv(config.ENV_HOME, str(tmp_path / "vynuceno"))
    view = _view(root)

    assert str(view.change_data_button.cget("state")) == "disabled"
    assert str(view.reset_data_button.cget("state")) == "disabled"
    assert config.ENV_HOME in view.data_source_label.cget("text")


def test_aplikace_se_prepne_na_novou_knihovnu(root, tmp_path, monkeypatch):
    """Po změně složky musí knihovna i historie ukazovat na nové místo."""

    from dlg.app import App

    monkeypatch.setattr(App, "_restore_geometry", lambda self: None, raising=False)
    app = App()
    try:
        app.withdraw()
        cil = tmp_path / "novadata"
        config.set_app_home(cil)
        app.reload_data_home()

        assert app.store.root == cil / "templates"
        assert app.history.path == cil / "history.json"
    finally:
        app.destroy()
