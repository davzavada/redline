"""Testy pro dlg.config — cesty k datům a atomický zápis nastavení."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from dlg import config
from dlg.config import ConfigError, Settings
from dlg.version import APP_ID


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Testy nesmí záviset na tom, co má vývojář nastavené v prostředí."""

    for name in (config.ENV_HOME, config.ENV_OUTPUT_DIR, "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    yield


# ---------------------------------------------------------------------------
# app_home a odvozené cesty
# ---------------------------------------------------------------------------
def test_app_home_prefers_dlg_home(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_is_windows", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv(config.ENV_HOME, str(tmp_path / "prenosne"))

    assert config.app_home() == tmp_path / "prenosne"


def test_app_home_expands_user_and_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv(config.ENV_HOME, "~/data-aplikace")

    assert config.app_home() == tmp_path / "data-aplikace"


def test_app_home_windows_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_is_windows", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))

    assert config.app_home() == tmp_path / "AppData" / "Local" / APP_ID


def test_app_home_posix_uses_local_share(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_is_windows", lambda: False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert config.app_home() == tmp_path / ".local" / "share" / config.POSIX_DIR_NAME


def test_app_home_is_absolute_even_for_relative_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(config.ENV_HOME, "relativni")

    home = config.app_home()
    assert home.is_absolute()
    assert home == tmp_path / "relativni"


def test_paths_hang_under_app_home(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_HOME, str(tmp_path / "home"))

    assert config.templates_dir() == tmp_path / "home" / "templates"
    assert config.settings_path() == tmp_path / "home" / "settings.json"
    assert config.history_path() == tmp_path / "home" / "history.json"


def test_paths_never_derived_from_executable_location():
    """Pod PyInstallerem by cesty odvozené od __file__/argv mířily do _MEIPASS."""

    import ast

    tree = ast.parse(Path(config.__file__).read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    assert "__file__" not in names
    assert "argv" not in attributes
    assert "_MEIPASS" not in names | attributes


def test_default_output_dir_posix(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_is_windows", lambda: False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert config.default_output_dir() == tmp_path / config.OUTPUT_DIR_NAME
    assert "Generátor dopisů" in str(config.default_output_dir())


def test_default_output_dir_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_is_windows", lambda: True)
    monkeypatch.setattr(config, "_windows_documents", lambda: tmp_path / "Dokumenty")

    assert config.default_output_dir() == tmp_path / "Dokumenty" / config.OUTPUT_DIR_NAME


def test_default_output_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_OUTPUT_DIR, str(tmp_path / "vystup"))

    assert config.default_output_dir() == tmp_path / "vystup"


# ---------------------------------------------------------------------------
# Atomický zápis JSON
# ---------------------------------------------------------------------------
def test_write_json_atomic_writes_utf8_without_escapes(tmp_path):
    target = tmp_path / "podadresar" / "data.json"
    config.write_json_atomic(target, {"název": "Výzva k nápravě", "n": 1})

    raw = target.read_text(encoding="utf-8")
    assert "Výzva k nápravě" in raw  # ensure_ascii=False
    assert "\n  " in raw  # indent=2
    assert json.loads(raw)["název"] == "Výzva k nápravě"


def test_write_json_atomic_leaves_no_temporary_files(tmp_path):
    target = tmp_path / "data.json"
    config.write_json_atomic(target, {"a": 1})
    config.write_json_atomic(target, {"a": 2})

    assert sorted(p.name for p in tmp_path.iterdir()) == ["data.json"]
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2}


def test_write_json_atomic_keeps_old_file_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "data.json"
    config.write_json_atomic(target, {"stav": "puvodni"})

    def boom(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(config.os, "replace", boom)

    with pytest.raises(ConfigError) as excinfo:
        config.write_json_atomic(target, {"stav": "novy"})

    assert "místa" in str(excinfo.value)
    assert json.loads(target.read_text(encoding="utf-8")) == {"stav": "puvodni"}
    assert [p.name for p in tmp_path.iterdir()] == ["data.json"]


def test_write_json_atomic_reports_permission_error_in_czech(tmp_path, monkeypatch):
    target = tmp_path / "data.json"

    def boom(src, dst):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(config.os, "replace", boom)

    with pytest.raises(ConfigError) as excinfo:
        config.write_json_atomic(target, {"a": 1})

    assert "oprávnění" in str(excinfo.value)


def test_write_json_atomic_rejects_unserializable_data(tmp_path):
    with pytest.raises(ConfigError):
        config.write_json_atomic(tmp_path / "data.json", {"objekt": object()})


def test_read_json_missing_returns_default(tmp_path):
    assert config.read_json(tmp_path / "nic.json", default={"x": 1}) == {"x": 1}


def test_read_json_broken_strict_raises_czech(tmp_path):
    broken = tmp_path / "data.json"
    broken.write_text("{tohle není json", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        config.read_json(broken)

    assert "poškozený" in str(excinfo.value)
    assert config.read_json(broken, default=None, strict=False) is None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def test_settings_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_HOME, str(tmp_path / "home"))

    original = Settings(
        output_dir=str(tmp_path / "Dopisy"),
        open_after_generate=False,
        clear_highlight=False,
        keep_unfilled=False,
        last_template_id="vyzva-abc123",
        profile={"email": "david.zavada@example.com", "jmeno": "Mgr. David Závada"},
    )
    config.save_settings(original)
    loaded = config.load_settings()

    assert loaded == original
    assert loaded.profile["jmeno"] == "Mgr. David Závada"
    assert config.settings_path().exists()


def test_load_settings_without_file_gives_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_HOME, str(tmp_path / "home"))
    monkeypatch.setenv(config.ENV_OUTPUT_DIR, str(tmp_path / "vystup"))

    settings = config.load_settings()

    assert settings.output_dir == str(tmp_path / "vystup")
    assert settings.open_after_generate is True
    assert settings.clear_highlight is True
    assert settings.keep_unfilled is True
    assert settings.profile == {}


def test_load_settings_survives_broken_file(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_HOME, str(tmp_path / "home"))
    path = config.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[rozbité", encoding="utf-8")

    settings = config.load_settings()

    assert isinstance(settings, Settings)
    assert settings.output_dir


def test_settings_from_dict_tolerates_garbage():
    settings = Settings.from_dict({"output_dir": None, "profile": {"a": 5}, "extra": "x"})

    assert settings.output_dir == ""
    assert settings.profile == {"a": "5"}


def test_resolved_output_dir_falls_back_to_default(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_OUTPUT_DIR, str(tmp_path / "vychozi"))

    assert Settings().resolved_output_dir() == tmp_path / "vychozi"
    assert Settings(output_dir=str(tmp_path / "jine")).resolved_output_dir() == tmp_path / "jine"


def test_ensure_dir_creates_and_reports_errors(tmp_path, monkeypatch):
    created = config.ensure_dir(tmp_path / "a" / "b")
    assert created.is_dir()

    def boom(self, *args, **kwargs):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(Path, "mkdir", boom)
    with pytest.raises(ConfigError) as excinfo:
        config.ensure_dir(tmp_path / "c")
    assert "oprávnění" in str(excinfo.value)


def test_settings_file_is_written_atomically(monkeypatch, tmp_path):
    """Při selhání zápisu nesmí zůstat rozepsaný soubor."""

    monkeypatch.setenv(config.ENV_HOME, str(tmp_path / "home"))
    config.save_settings(Settings(output_dir="A"))

    def boom(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as patched:
        patched.setattr(config.os, "replace", boom)
        with pytest.raises(ConfigError):
            config.save_settings(Settings(output_dir="B"))

    assert config.load_settings().output_dir == "A"
    assert not [p for p in config.app_home().iterdir() if p.name.endswith(".tmp")]


def test_os_error_description_is_czech():
    assert "místa" in config._describe_os_error(OSError(errno.ENOSPC, "x"))
    assert "oprávnění" in config._describe_os_error(OSError(errno.EACCES, "x"))
    assert config._describe_os_error(OSError(errno.EROFS, "x")) == "disk je jen pro čtení"
    assert os.name in ("posix", "nt")
