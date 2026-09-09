"""Cesty k datům aplikace, atomický zápis JSON a uživatelské nastavení.

Aplikace nikdy neodvozuje cesty od ``sys.argv[0]`` ani od ``__file__`` — pod
PyInstallerem by ukazovaly do dočasného rozbaleného adresáře (``_MEIPASS``)
nebo vedle ``.exe`` v ``Program Files``, kam se nesmí zapisovat. Data patří
výhradně do profilu uživatele.

Pořadí, ve kterém se hledá domovský adresář aplikace:

1. proměnná prostředí ``DLG_HOME`` (testy, dočasné přesměrování),
2. přenosný režim — soubor ``portable.txt`` vedle ``.exe`` (data na flash disku),
3. složka zvolená uživatelem v Nastavení,
4. ``%LOCALAPPDATA%\\DomainLetterGenerator`` na Windows,
   ``~/.local/share/domain-letter-generator`` jinde.

Ukazatel na ručně zvolenou složku leží ZÁMĚRNĚ mimo ni — v
``%APPDATA%\\DomainLetterGenerator\\location.json`` (resp.
``~/.config/domain-letter-generator/location.json``). Kdyby byl uvnitř,
aplikace by po přesunu složky svá data už nenašla.
"""

from __future__ import annotations

import errno
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .version import APP_ID, APP_NAME

__all__ = [
    "ConfigError",
    "DataHomeStatus",
    "Settings",
    "anchor_dir",
    "app_home",
    "data_home_status",
    "default_app_home",
    "default_output_dir",
    "describe_os_error",
    "has_data",
    "history_path",
    "load_settings",
    "location_path",
    "portable_data_dir",
    "read_json",
    "read_location",
    "save_settings",
    "set_app_home",
    "settings_path",
    "templates_dir",
    "write_json_atomic",
    "write_location",
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

#: Soubor s ukazatelem na složku, kterou si uživatel zvolil pro svá data.
#: Leží ZÁMĚRNĚ mimo tuto složku — jinak by ji po přesunu nešlo najít.
LOCATION_FILE_NAME = "location.json"

#: Soubor vedle ``.exe``, který zapíná přenosný režim (data u programu).
PORTABLE_MARKER_NAME = "portable.txt"
#: Podsložka s daty v přenosném režimu, když marker neurčí jinou cestu.
PORTABLE_DATA_DIR_NAME = "data"

#: Položky, které tvoří data aplikace — jen ty se při změně složky stěhují.
DATA_ENTRIES: tuple[str, ...] = (
    TEMPLATES_DIR_NAME,
    SETTINGS_FILE_NAME,
    HISTORY_FILE_NAME,
)


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


def describe_os_error(exc: OSError) -> str:
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
    if code == errno.EFBIG:
        return "soubor je pro tento disk příliš velký"
    if code == errno.EIO:  # pragma: no cover - vadný disk nebo síť
        return "při zápisu na disk došlo k chybě"
    if code == errno.EDQUOT:  # pragma: no cover - jen na síťových discích
        return "byla vyčerpána disková kvóta"
    strerror = getattr(exc, "strerror", None)
    return str(strerror) if strerror else str(exc)


#: Zpětně kompatibilní název (funkce byla dřív privátní).
_describe_os_error = describe_os_error


def default_app_home() -> Path:
    """Výchozí umístění dat, dokud si uživatel nezvolí jiné."""

    if _is_windows():
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = _as_path(local) if local else Path.home() / "AppData" / "Local"
        return base / APP_ID

    return Path.home() / ".local" / "share" / POSIX_DIR_NAME


def app_home() -> Path:
    """Adresář s daty aplikace (šablony, nastavení, historie).

    Hledá se v tomto pořadí:

    1. proměnná prostředí ``DLG_HOME`` (testy, dočasné přesměrování),
    2. přenosný režim — soubor ``portable.txt`` vedle ``.exe``,
    3. složka, kterou si uživatel zvolil v Nastavení (ukazatel na ni leží
       mimo ni, viz :func:`location_path`),
    4. výchozí umístění v profilu uživatele.
    """

    override = os.environ.get(ENV_HOME, "").strip()
    if override:
        return _as_path(override)

    portable = portable_data_dir()
    if portable is not None:
        return portable

    chosen = read_location()
    if chosen is not None:
        return chosen

    return default_app_home()


def _executable_dir() -> Path | None:
    """Složka, ve které leží ``.exe``. Mimo zabalený stav vrací ``None``."""

    if not getattr(sys, "frozen", False):
        return None
    try:
        return Path(sys.executable).resolve().parent
    except OSError:  # pragma: no cover - jen při rozbité instalaci
        return None


def portable_data_dir() -> Path | None:
    """Složka s daty v přenosném režimu, nebo ``None``.

    Přenosný režim se zapne souborem ``portable.txt`` vedle ``.exe``. Prázdný
    soubor znamená podsložku ``data`` u programu; jinak se použije cesta
    zapsaná uvnitř (absolutní i relativní k ``.exe``). Díky tomu jde celý
    program i s šablonami nosit na flash disku.
    """

    exe_dir = _executable_dir()
    if exe_dir is None:
        return None

    marker = exe_dir / PORTABLE_MARKER_NAME
    try:
        if not marker.is_file():
            return None
        text = marker.read_bytes().decode("utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        return None

    if not text:
        return exe_dir / PORTABLE_DATA_DIR_NAME

    candidate = Path(os.path.expandvars(text)).expanduser()
    if not candidate.is_absolute():
        candidate = exe_dir / candidate
    return candidate


def anchor_dir() -> Path:
    """Pevné místo, kam se ukládá ukazatel na složku s daty.

    Leží mimo složku s daty — kdyby byl uvnitř, po přesunu složky by ji
    aplikace už nenašla.
    """

    if _is_windows():
        roaming = os.environ.get("APPDATA", "").strip()
        base = _as_path(roaming) if roaming else Path.home() / "AppData" / "Roaming"
        return base / APP_ID

    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = _as_path(xdg) if xdg else Path.home() / ".config"
    return base / POSIX_DIR_NAME


def location_path() -> Path:
    """Cesta k souboru s ukazatelem na složku s daty."""

    return anchor_dir() / LOCATION_FILE_NAME


def read_location() -> Path | None:
    """Složka s daty zvolená uživatelem, nebo ``None`` pro výchozí umístění."""

    data = read_json(location_path(), default=None, strict=False)
    if not isinstance(data, Mapping):
        return None
    raw = str(data.get("data_dir") or "").strip()
    if not raw:
        return None
    return _as_path(raw)


def write_location(path: Path | None) -> None:
    """Uloží ukazatel na složku s daty. ``None`` znamená návrat k výchozí."""

    if path is None:
        try:
            location_path().unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ConfigError(
                "Volbu složky s daty se nepodařilo zrušit: "
                f"{_describe_os_error(exc)}."
            ) from exc
        return

    write_json_atomic(location_path(), {"data_dir": str(Path(path))})


@dataclass(frozen=True)
class DataHomeStatus:
    """Kde leží data aplikace a proč zrovna tam — podklad pro Nastavení."""

    path: Path
    source: str  # "env" | "portable" | "chosen" | "default"

    @property
    def is_default(self) -> bool:
        return self.source == "default"

    @property
    def can_change(self) -> bool:
        """Přes prostředí a přenosný režim se volba v aplikaci nedá přebít."""

        return self.source in ("chosen", "default")

    @property
    def description(self) -> str:
        """Česká věta pro obrazovku Nastavení."""

        if self.source == "env":
            return (
                f"Umístění vynucuje proměnná prostředí {ENV_HOME}. "
                "Dokud je nastavená, volba v aplikaci se neuplatní."
            )
        if self.source == "portable":
            return (
                "Přenosný režim — data leží u programu, protože vedle něj "
                f"je soubor {PORTABLE_MARKER_NAME}."
            )
        if self.source == "chosen":
            return "Složku jste zvolili ručně."
        return "Výchozí umístění v profilu uživatele."


def data_home_status() -> DataHomeStatus:
    """Zjistí, kde data leží a čím je to určené."""

    override = os.environ.get(ENV_HOME, "").strip()
    if override:
        return DataHomeStatus(_as_path(override), "env")

    portable = portable_data_dir()
    if portable is not None:
        return DataHomeStatus(portable, "portable")

    chosen = read_location()
    if chosen is not None:
        return DataHomeStatus(chosen, "chosen")

    return DataHomeStatus(default_app_home(), "default")


def _check_writable(path: Path) -> None:
    """Ověří, že do složky opravdu půjde zapisovat."""
    import tempfile

    ensure_dir(path)
    probe: str | None = None
    try:
        handle, probe = tempfile.mkstemp(prefix=".zapis-test-", dir=str(path))
        os.close(handle)
    except OSError as exc:
        raise ConfigError(
            f"Do složky „{path}“ nejde zapisovat: {_describe_os_error(exc)}."
        ) from exc
    finally:
        if probe is not None:
            try:
                os.unlink(probe)
            except OSError:
                pass


def has_data(path: Path) -> bool:
    """Leží už v této složce data generátoru?"""

    path = Path(path)
    templates = path / TEMPLATES_DIR_NAME
    if templates.is_dir() and any(templates.iterdir()):
        return True
    return (path / SETTINGS_FILE_NAME).is_file()


def _is_within(child: Path, parent: Path) -> bool:
    """Leží ``child`` uvnitř ``parent`` (nebo je to tatáž cesta)?"""

    try:
        child_res = Path(child).resolve()
        parent_res = Path(parent).resolve()
    except OSError:  # pragma: no cover - nedostupná síťová cesta
        child_res, parent_res = Path(child), Path(parent)
    return child_res == parent_res or parent_res in child_res.parents


def set_app_home(target: Path | str | None, *, move_existing: bool = False) -> Path:
    """Přepne aplikaci na jinou složku s daty.

    ``target=None`` vrátí aplikaci k výchozímu umístění.

    ``move_existing=True`` přestěhuje šablony, nastavení a historii ze
    současné složky do nové. Stěhování se odmítne, pokud v cílové složce
    už nějaká data jsou — přepsat cizí šablony by byla nevratná ztráta.
    """
    import shutil

    status = data_home_status()
    if not status.can_change:
        raise ConfigError(
            "Složku s daty teď nejde změnit. " + status.description
        )

    if target is None:
        destination = default_app_home()
    else:
        destination = _as_path(str(target))

    source = status.path

    if _is_within(destination, source) and destination != source:
        raise ConfigError(
            "Novou složku nelze umístit dovnitř té současné — "
            "zvolte složku mimo ni."
        )
    if _is_within(source, destination) and destination != source:
        raise ConfigError(
            "Současná složka s daty leží uvnitř zvolené složky — "
            "zvolte jinou."
        )

    _check_writable(destination)

    moved: list[tuple[Path, Path]] = []

    def rollback() -> list[str]:
        """Vrátí přesunuté položky zpět; vrací názvy těch, které se vrátit nedaly."""
        import shutil

        stuck: list[str] = []
        for src_entry, dst_entry in reversed(moved):
            try:
                shutil.move(str(dst_entry), str(src_entry))
            except (OSError, shutil.Error):
                stuck.append(dst_entry.name)
        moved.clear()
        return stuck

    def stuck_note(stuck: Sequence[str]) -> str:
        return (
            f"Aplikace používá dál složku „{source}“, ale tyto položky zůstaly "
            f"ve složce „{destination}“ a je potřeba je ručně vrátit: "
            + ", ".join(stuck)
            + "."
        )

    if move_existing and Path(source).exists() and destination != source:
        if has_data(destination):
            raise ConfigError(
                f"Ve složce „{destination}“ už nějaká data jsou. "
                "Buď zvolte prázdnou složku, nebo data přebírat nechte "
                "a ta stávající si přeneste ručně."
            )
        for name in DATA_ENTRIES:
            src_entry = Path(source) / name
            if not src_entry.exists():
                continue
            dst_entry = Path(destination) / name
            try:
                # shutil.Error (např. při kopii přes svazky) není OSError
                shutil.move(str(src_entry), str(dst_entry))
            except (OSError, shutil.Error) as exc:
                detail = (
                    _describe_os_error(exc)
                    if isinstance(exc, OSError)
                    else str(exc)
                )
                stuck = rollback()
                message = f"Položku „{name}“ se nepodařilo přesunout: {detail}. "
                message += stuck_note(stuck) if stuck else "Data zůstala v původní složce."
                raise ConfigError(message) from exc
            moved.append((src_entry, dst_entry))

    # Ukazatel je poslední krok transakce: kdyby se nezapsal, data už jsou
    # v nové složce, ale aplikace by dál četla tu starou.
    try:
        write_location(None if destination == default_app_home() else destination)
    except ConfigError as exc:
        if not moved:
            raise
        stuck = rollback()
        if stuck:
            raise ConfigError(
                f"Novou složku s daty se nepodařilo zapamatovat ({exc}) a data se "
                "nepodařilo vrátit zpět. " + stuck_note(stuck)
            ) from exc
        raise ConfigError(
            f"Novou složku s daty se nepodařilo zapamatovat ({exc}). "
            f"Data jsem vrátil zpět do složky „{source}“."
        ) from exc
    return destination


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
    import tempfile

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
        raw = path.read_bytes()
    except FileNotFoundError:
        return default
    except OSError as exc:
        if not strict:
            return default
        raise ConfigError(
            f"Soubor „{path}“ se nepodařilo přečíst: {_describe_os_error(exc)}."
        ) from exc

    # Dekódování musí být ve vlastním bloku: UnicodeDecodeError je potomek
    # ValueError, ne OSError, takže by jinak proletěl ven i při strict=False.
    # „utf-8-sig“ navíc snese BOM, který do souboru přidá Poznámkový blok
    # nebo PowerShell; pro soubor bez BOM se chová jako obyčejné UTF-8.
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        if not strict:
            return default
        raise ConfigError(
            f"Soubor „{path}“ je poškozený (není uložený v kódování UTF-8): {exc}."
        ) from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        if not strict:
            return default
        raise ConfigError(f"Soubor „{path}“ je poškozený (neplatný JSON): {exc}.") from exc


@dataclass
class Settings:
    """Uživatelské nastavení aplikace."""

    output_dir: str = ""
    open_after_generate: bool = True
    #: Uložit vedle dopisu i PDF (viz :mod:`dlg.pdf`).
    export_pdf: bool = False
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
            "export_pdf": self.export_pdf,
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
            export_pdf=bool(data.get("export_pdf", False)),
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
