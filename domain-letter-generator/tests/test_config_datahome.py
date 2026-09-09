"""Volba složky s daty — ukazatel mimo složku, přenosný režim, stěhování."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dlg import config


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Každý test má vlastní profil uživatele a žádné DLG_HOME."""

    monkeypatch.delenv(config.ENV_HOME, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "profil"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "profil" / ".config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "profil" / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profil" / "AppData" / "Local"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "profil"))
    return tmp_path


def test_vychozi_umisteni_kdyz_nic_nezvoleno():
    status = config.data_home_status()
    assert status.source == "default"
    assert status.is_default
    assert status.can_change
    assert config.app_home() == config.default_app_home()


def test_ukazatel_lezi_mimo_slozku_s_daty(tmp_path):
    """Klíčová vlastnost: ukazatel nesmí být uvnitř dat, jinak by se ztratil."""

    cil = tmp_path / "OneDrive" / "Dopisy"
    config.set_app_home(cil)

    assert config.app_home() == cil
    assert not config._is_within(config.location_path(), cil)
    assert config.location_path().is_file()


def test_zvolena_slozka_prezije_novy_start(tmp_path):
    cil = tmp_path / "sitovy-disk" / "sablony"
    config.set_app_home(cil)

    # simulace nového spuštění — nic v paměti, jen soubory na disku
    assert config.app_home() == cil
    assert config.data_home_status().source == "chosen"
    assert config.templates_dir() == cil / "templates"
    assert config.settings_path() == cil / "settings.json"


def test_navrat_k_vychozimu_umisteni(tmp_path):
    config.set_app_home(tmp_path / "jinam")
    assert config.data_home_status().source == "chosen"

    config.set_app_home(None)

    assert config.data_home_status().source == "default"
    assert config.app_home() == config.default_app_home()
    assert not config.location_path().exists()


def test_volba_stejne_cesty_jako_vychozi_ukazatel_nezanecha():
    config.set_app_home(config.default_app_home())
    assert config.data_home_status().source == "default"


def test_presun_dat_do_nove_slozky(tmp_path):
    puvodni = config.app_home()
    (puvodni / "templates" / "vyzva-abc123").mkdir(parents=True)
    (puvodni / "templates" / "vyzva-abc123" / "template.docx").write_bytes(b"PK\x03\x04data")
    config.write_json_atomic(puvodni / "settings.json", {"open_after_generate": False})
    config.write_json_atomic(puvodni / "history.json", {"domena": ["lego-shop.cz"]})

    cil = tmp_path / "OneDrive" / "Generator"
    config.set_app_home(cil, move_existing=True)

    assert (cil / "templates" / "vyzva-abc123" / "template.docx").read_bytes() == b"PK\x03\x04data"
    assert json.loads((cil / "settings.json").read_text(encoding="utf-8"))[
        "open_after_generate"
    ] is False
    assert json.loads((cil / "history.json").read_text(encoding="utf-8"))["domena"] == [
        "lego-shop.cz"
    ]
    assert not (puvodni / "templates").exists()
    assert config.app_home() == cil


def test_prepnuti_na_slozku_s_existujicimi_daty_bez_stehovani(tmp_path):
    """Ukázat na složku, kde už šablony jsou (druhý počítač, sdílený disk)."""

    sdilena = tmp_path / "sdilene"
    (sdilena / "templates" / "vyzva-xyz789").mkdir(parents=True)
    (sdilena / "templates" / "vyzva-xyz789" / "template.docx").write_bytes(b"PK\x03\x04")

    config.set_app_home(sdilena)

    assert config.app_home() == sdilena
    assert config.has_data(sdilena)
    assert (config.templates_dir() / "vyzva-xyz789").is_dir()


def test_stehovani_neprepise_cizi_data(tmp_path):
    """Stěhování do složky s daty se musí odmítnout — jinak ztráta šablon."""

    puvodni = config.app_home()
    (puvodni / "templates" / "moje").mkdir(parents=True)

    cil = tmp_path / "obsazeno"
    (cil / "templates" / "cizi").mkdir(parents=True)
    (cil / "templates" / "cizi" / "template.docx").write_bytes(b"PK\x03\x04cizi")

    with pytest.raises(config.ConfigError) as chyba:
        config.set_app_home(cil, move_existing=True)

    assert "už nějaká data jsou" in str(chyba.value)
    assert (cil / "templates" / "cizi" / "template.docx").read_bytes() == b"PK\x03\x04cizi"
    assert (puvodni / "templates" / "moje").is_dir()
    assert config.data_home_status().source == "default"


def test_nova_slozka_nesmi_byt_uvnitr_stavajici(tmp_path):
    puvodni = config.app_home()
    puvodni.mkdir(parents=True, exist_ok=True)

    with pytest.raises(config.ConfigError) as chyba:
        config.set_app_home(puvodni / "uvnitr")

    assert "dovnitř" in str(chyba.value)


def test_stavajici_slozka_nesmi_byt_uvnitr_nove(tmp_path):
    puvodni = tmp_path / "rodic" / "data"
    config.set_app_home(puvodni)

    with pytest.raises(config.ConfigError) as chyba:
        config.set_app_home(tmp_path / "rodic")

    assert "uvnitř" in str(chyba.value)


def test_promenna_prostredi_prebiji_volbu(tmp_path, monkeypatch):
    config.set_app_home(tmp_path / "zvoleno")
    monkeypatch.setenv(config.ENV_HOME, str(tmp_path / "vynuceno"))

    status = config.data_home_status()
    assert status.source == "env"
    assert not status.can_change
    assert config.app_home() == tmp_path / "vynuceno"

    with pytest.raises(config.ConfigError):
        config.set_app_home(tmp_path / "jinam")


def test_prenosny_rezim_prazdny_marker(tmp_path, monkeypatch):
    """portable.txt vedle .exe => data ve složce data/ u programu."""

    program = tmp_path / "flashdisk" / "GeneratorDopisu"
    program.mkdir(parents=True)
    (program / config.PORTABLE_MARKER_NAME).write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "_executable_dir", lambda: program)

    assert config.portable_data_dir() == program / "data"
    assert config.app_home() == program / "data"

    status = config.data_home_status()
    assert status.source == "portable"
    assert not status.can_change


def test_prenosny_rezim_s_cestou_v_markeru(tmp_path, monkeypatch):
    program = tmp_path / "flashdisk"
    program.mkdir(parents=True)
    (program / config.PORTABLE_MARKER_NAME).write_text(
        "﻿sablony-dopisu\n", encoding="utf-8"
    )
    monkeypatch.setattr(config, "_executable_dir", lambda: program)

    assert config.app_home() == program / "sablony-dopisu"


def test_bez_markeru_prenosny_rezim_neplati(tmp_path, monkeypatch):
    program = tmp_path / "program"
    program.mkdir()
    monkeypatch.setattr(config, "_executable_dir", lambda: program)

    assert config.portable_data_dir() is None
    assert config.app_home() == config.default_app_home()


def test_poskozeny_ukazatel_spadne_na_vychozi(tmp_path):
    config.ensure_dir(config.anchor_dir())
    config.location_path().write_text("{tohle není JSON", encoding="utf-8")

    assert config.read_location() is None
    assert config.app_home() == config.default_app_home()


def test_prazdny_ukazatel_spadne_na_vychozi():
    config.write_json_atomic(config.location_path(), {"data_dir": "   "})

    assert config.read_location() is None
    assert config.app_home() == config.default_app_home()


def test_nezapisovatelna_slozka_hlasi_cesky(tmp_path):
    soubor = tmp_path / "tohle-je-soubor.txt"
    soubor.write_text("ne složka", encoding="utf-8")

    with pytest.raises(config.ConfigError) as chyba:
        config.set_app_home(soubor)

    assert "nepodařilo" in str(chyba.value) or "nejde zapisovat" in str(chyba.value)


def test_popis_stavu_je_cesky_a_srozumitelny(tmp_path):
    assert "Výchozí" in config.data_home_status().description
    config.set_app_home(tmp_path / "moje")
    assert "ručně" in config.data_home_status().description


# ---------------------------------------------------------------------------
# stěhování je transakce — po chybě je stav konzistentní a hláška pravdivá
# ---------------------------------------------------------------------------
def _pripravit_data(puvodni: Path) -> None:
    (puvodni / "templates" / "vyzva-abc123").mkdir(parents=True)
    (puvodni / "templates" / "vyzva-abc123" / "template.docx").write_bytes(b"PK\x03\x04data")
    config.write_json_atomic(puvodni / "settings.json", {"open_after_generate": False})
    config.write_json_atomic(puvodni / "history.json", {"domena": ["lego-shop.cz"]})


def test_selhany_presun_vrati_uz_prestehovane_zpet(tmp_path, monkeypatch):
    """Padne-li druhá položka, nesmí první zůstat v cizí složce."""

    puvodni = config.app_home()
    _pripravit_data(puvodni)
    cil = tmp_path / "D" / "Dopisy"

    puvodni_move = shutil.move

    def rozbity_move(src, dst):
        if Path(src).name == "settings.json" and Path(dst).parent == cil:
            raise PermissionError(13, "Permission denied")
        return puvodni_move(src, dst)

    monkeypatch.setattr(shutil, "move", rozbity_move)

    with pytest.raises(config.ConfigError) as chyba:
        config.set_app_home(cil, move_existing=True)

    assert "Data zůstala v původní složce" in str(chyba.value)
    assert (puvodni / "templates" / "vyzva-abc123" / "template.docx").is_file()
    assert not any((cil / name).exists() for name in config.DATA_ENTRIES)
    assert not config.location_path().exists()
    assert config.data_home_status().source == "default"
    assert config.app_home() == puvodni


def test_selhany_rollback_vyjmenuje_co_zustalo(tmp_path, monkeypatch):
    puvodni = config.app_home()
    _pripravit_data(puvodni)
    cil = tmp_path / "D" / "Dopisy"

    puvodni_move = shutil.move

    def rozbity_move(src, dst):
        if Path(src).name == "settings.json" and Path(dst).parent == cil:
            raise PermissionError(13, "Permission denied")
        if Path(dst).name == "templates" and Path(dst).parent == puvodni:
            raise PermissionError(13, "Permission denied")  # rollback neprojde
        return puvodni_move(src, dst)

    monkeypatch.setattr(shutil, "move", rozbity_move)

    with pytest.raises(config.ConfigError) as chyba:
        config.set_app_home(cil, move_existing=True)

    zprava = str(chyba.value)
    assert "Data zůstala v původní složce" not in zprava
    assert "templates" in zprava
    assert str(cil) in zprava
    assert config.app_home() == puvodni


def test_selhany_zapis_ukazatele_vrati_data_zpet(tmp_path, monkeypatch):
    """Data nesmí zůstat v nové složce, když aplikace míří na starou."""

    puvodni = config.app_home()
    _pripravit_data(puvodni)
    cil = tmp_path / "D" / "Dopisy"

    def rozbity_zapis(path):
        raise config.ConfigError("Do složky nejde zapisovat.")

    monkeypatch.setattr(config, "write_location", rozbity_zapis)

    with pytest.raises(config.ConfigError) as chyba:
        config.set_app_home(cil, move_existing=True)

    assert "zapamatovat" in str(chyba.value)
    assert (puvodni / "templates" / "vyzva-abc123" / "template.docx").is_file()
    assert (puvodni / "settings.json").is_file()
    assert not any((cil / name).exists() for name in config.DATA_ENTRIES)
