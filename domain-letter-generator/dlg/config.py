"""Cesty k datům aplikace, atomický zápis JSON a uživatelské nastavení.

Aplikace nikdy neodvozuje cesty od ``sys.argv[0]`` ani od ``__file__`` — pod
PyInstallerem by ukazovaly do dočasného rozbaleného adresáře (``_MEIPASS``)
nebo vedle ``.exe`` v ``Program Files``, kam se nesmí zapisovat. Data patří
výhradně do profilu uživatele.

Pořadí, ve kterém se hledá domovský adresář aplikace:

1. proměnná prostředí ``DLG_HOME`` (přenosný režim, testy),
2. ``%LOCALAPPDATA%\\DomainLetterGenerator`` na Windows,
3. ``~/.local/share/domain-letter-generator`` jinde.
"""

from __future__ import annotations

import errno
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .version import APP_ID, APP_NAME

__all__ = [
    "ConfigError",
    "Settings",
    "app_home",
    "default_output_dir",
    "history_path",
    "load_settings",
    "read_json",
    "save_settings",
    "settings_path",
    "templates_dir",
    "write_json_atomic",
]

#: Proměnná prostředí, která přebíjí umístění dat aplikace.
ENV_HOME = "DLG_HOME"
#: Proměnná prostředí, která přebíjí výchozí výstupní složku.
ENV_OUTPUT_DIR = "DLG_OUTPUT_DIR"

#: Název adresáře v ``~/.local/share`` na systémech mimo Windows.
POSIX_DIR_NAME = "domain-letter-generator"
#: Název složky pro vygenerované dopisy (v Dokumentech / domovském adresáři).
OUTPUT_DIR_NAME = APP_NAME

TEMPLATES_DIR_NAME = "templates"
SETTINGS_FILE_NAME = "settings.json"
HISTORY_FILE_NAME = "history.json"


class ConfigError(Exception):
    """Chyba práce se soubory nastavení — hlášky jsou české a pro uživatele."""


def _is_windows() -> bool:
    """Oddělené kvůli testovatelnosti (v testech se dá podstrčit)."""

    return os.name == "nt"


def _as_path(raw: str) -> Path:
    """Z textu z prostředí udělá absolutní cestu (bez rozplétání symlinků)."""

    path = Path(os.path.expandvars(str(raw))).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _describe_os_error(exc: OSError) -> str:
    """Česky popíše, proč se souborová operace nepovedla."""

    code = getattr(exc, "errno", None)
    if code == errno.ENOSPC:
        return "na disku není dost volného místa"
    if code in (errno.EACCES, errno.EPERM):
        return "chybí oprávnění k zápisu"
    if code == errno.EROFS:
        return "disk je jen pro čtení"
    if code == errno.ENOENT:
        return "cílová složka neexistuje"
    if code == errno.EDQUOT:  # pragma: no cover - jen na síťových discích
        return "byla vyčerpána disková kvóta"
    strerror = getattr(exc, "strerror", None)
    return str(strerror) if strerror else str(exc)


def app_home() -> Path:
    """Adresář s daty aplikace (šablony, nastavení, historie)."""

    override = os.environ.get(ENV_HOME, "").strip()
    if override:
        return _as_path(override)

    if _is_windows():
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = _as_path(local) if local else Path.home() / "AppData" / "Local"
        return base / APP_ID

    return Path.home() / ".local" / "share" / POSIX_DIR_NAME


def templates_dir() -> Path:
    """Knihovna šablon: ``app_home()/templates``."""

    return app_home() / TEMPLATES_DIR_NAME


def settings_path() -> Path:
    return app_home() / SETTINGS_FILE_NAME


def history_path() -> Path:
    return app_home() / HISTORY_FILE_NAME


def _windows_documents() -> Path:
    """Složka Dokumenty na Windows (respektuje přesměrování na OneDrive)."""

    try:  # pragma: no cover - běží jen na Windows
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_PERSONAL = 5, SHGFP_TYPE_CURRENT = 0
        result = ctypes.windll.shell32.SHGetFolderPathW(  # type: ignore[attr-defined]
            None, 5, None, 0, buf
        )
        if result == 0 and buf.value:
            return Path(buf.value)
        del wintypes
    except Exception:
        pass

    profile = os.environ.get("USERPROFILE", "").strip()
    base = _as_path(profile) if profile else Path.home()
    return base / "Documents"


def default_output_dir() -> Path:
    """Výchozí složka pro vygenerované dopisy."""

    override = os.environ.get(ENV_OUTPUT_DIR, "").strip()
    if override:
        return _as_path(override)

    if _is_windows():
        return _windows_documents() / OUTPUT_DIR_NAME

    return Path.home() / OUTPUT_DIR_NAME


def ensure_dir(path: Path) -> Path:
    """Vytvoří adresář (i s rodiči) a vrátí ho; chyby hlásí česky."""

    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"Složku „{path}“ se nepodařilo vytvořit: {_describe_os_error(exc)}."
        ) from exc
    return path


def write_json_atomic(path: Path, data: Any) -> None:
    """Zapíše JSON atomicky — dočasný soubor ve stejné složce + ``os.replace``.

    UTF-8, ``ensure_ascii=False``, ``indent=2``. Buď se povede celý zápis,
    nebo na disku zůstane původní verze souboru.
    """

    path = Path(path)
    ensure_dir(path.parent)

    tmp_name: str | None = None
    try:
        handle, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError as exc:
        raise ConfigError(
            f"Soubor „{path}“ se nepodařilo uložit: {_describe_os_error(exc)}."
        ) from exc
    except (TypeError, ValueError) as exc:  # neserializovatelná data
        raise ConfigError(f"Data pro soubor „{path}“ nelze uložit do JSON: {exc}") from exc
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def read_json(path: Path, default: Any = None, *, strict: bool = True) -> Any:
    """Načte JSON. Neexistující soubor vrátí ``default``.

    ``strict=False`` znamená, že se poškozený soubor tiše nahradí výchozí
    hodnotou — aplikace se kvůli rozbité historii nesmí odmítnout spustit.
    """

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except OSError as exc:
        if not strict:
            return default
        raise ConfigError(
            f"Soubor „{path}“ se nepodařilo přečíst: {_describe_os_error(exc)}."
        ) from exc

    try:
        return json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        if not strict:
            return default
        raise ConfigError(f"Soubor „{path}“ je poškozený (neplatný JSON): {exc}.") from exc


@dataclass
class Settings:
    """Uživatelské nastavení aplikace."""

    output_dir: str = ""
    open_after_generate: bool = True
    clear_highlight: bool = True
    keep_unfilled: bool = True
    last_template_id: str = ""
    profile: dict[str, str] = field(default_factory=dict)

    def resolved_output_dir(self) -> Path:
        """Výstupní složka jako cesta; prázdné nastavení = výchozí složka."""

        value = (self.output_dir or "").strip()
        return _as_path(value) if value else default_output_dir()

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": self.output_dir,
            "open_after_generate": self.open_after_generate,
            "clear_highlight": self.clear_highlight,
            "keep_unfilled": self.keep_unfilled,
            "last_template_id": self.last_template_id,
            "profile": dict(self.profile),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "Settings":
        data = data or {}
        raw_profile = data.get("profile") or {}
        profile: dict[str, str] = {}
        if isinstance(raw_profile, Mapping):
            for key, value in raw_profile.items():
                if value is None:
                    continue
                profile[str(key)] = str(value)
        return cls(
            output_dir=str(data.get("output_dir") or ""),
            open_after_generate=bool(data.get("open_after_generate", True)),
            clear_highlight=bool(data.get("clear_highlight", True)),
            keep_unfilled=bool(data.get("keep_unfilled", True)),
            last_template_id=str(data.get("last_template_id") or ""),
            profile=profile,
        )


def load_settings(path: Path | None = None) -> Settings:
    """Načte nastavení; při chybějícím nebo poškozeném souboru vrátí výchozí."""

    target = Path(path) if path is not None else settings_path()
    data = read_json(target, default=None, strict=False)
    if not isinstance(data, Mapping):
        return Settings(output_dir=str(default_output_dir()))
    settings = Settings.from_dict(data)
    if not settings.output_dir:
        settings.output_dir = str(default_output_dir())
    return settings


def save_settings(s: Settings, path: Path | None = None) -> None:
    """Uloží nastavení atomicky."""

    target = Path(path) if path is not None else settings_path()
    write_json_atomic(target, s.to_dict())
