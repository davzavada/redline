"""Knihovna šablon na disku a historie vyplněných hodnot.

Rozvržení dat::

    app_home()/
      settings.json
      history.json
      templates/
        vyzva-k-naprave-1a2b3c/
          template.docx
          meta.json

``id`` šablony = slug názvu (ASCII, ``[a-z0-9-]``, max 40 znaků) + ``-`` +
6 hex znaků odvozených z názvu a obsahu souboru.
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import config
from .config import ConfigError, read_json, write_json_atomic
from .models import ScanResult, TemplateMeta

__all__ = [
    "StoreError",
    "TemplateNotFound",
    "TemplateStore",
    "ValueHistory",
    "make_template_id",
    "slugify",
]

#: Maximální délka slugu v id šablony.
SLUG_MAX_LENGTH = 40
#: Náhradní slug, když z názvu nezbude nic použitelného.
SLUG_FALLBACK = "sablona"
#: Kolik hodnot se pamatuje pro jeden klíč.
HISTORY_LIMIT = 10
#: Pojistka proti nahrání nesmyslně velkého souboru (50 MB).
MAX_TEMPLATE_BYTES = 50 * 1024 * 1024

TEMPLATE_FILE_NAME = "template.docx"
META_FILE_NAME = "meta.json"

_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")

#: Písmena, která NFKD nerozloží a přesto je chceme převést na ASCII.
_TRANSLITERATE = {
    "ß": "ss",
    "æ": "ae",
    "œ": "oe",
    "ø": "o",
    "đ": "d",
    "ð": "d",
    "þ": "th",
    "ł": "l",
    "ħ": "h",
}


class StoreError(Exception):
    """Chyba práce s knihovnou šablon — hlášky jsou české a pro uživatele."""


class TemplateNotFound(StoreError):
    """Šablona daného id v knihovně není."""


# ---------------------------------------------------------------------------
# Identita šablony
# ---------------------------------------------------------------------------
def slugify(name: str, *, max_length: int = SLUG_MAX_LENGTH, fallback: str = SLUG_FALLBACK) -> str:
    """Z názvu udělá ASCII slug: české diakritice sundá háčky a čárky."""

    text = str(name or "")
    text = "".join(_TRANSLITERATE.get(ch, _TRANSLITERATE.get(ch.lower(), ch)) for ch in text)
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))

    chars: list[str] = []
    for ch in stripped.lower():
        chars.append(ch if ch.isascii() and ch.isalnum() else "-")
    slug = re.sub(r"-{2,}", "-", "".join(chars)).strip("-")

    if len(slug) > max_length:
        slug = slug[:max_length].strip("-")
    return slug or fallback


def make_template_id(name: str, content: bytes | None = None, *, salt: str = "") -> str:
    """Deterministické id šablony.

    Hash se počítá z názvu a obsahu souboru — tentýž soubor pod týmž názvem
    dá vždy totéž id, takže je funkce testovatelná bez času a náhody.
    ``salt`` slouží k rozlišení kolizí (viz :meth:`TemplateStore.import_docx`).
    """

    digest = hashlib.sha1()
    digest.update(str(name or "").strip().encode("utf-8"))
    digest.update(b"\x00")
    digest.update(content or b"")
    digest.update(b"\x00")
    digest.update(str(salt or "").encode("utf-8"))
    return f"{slugify(name)}-{digest.hexdigest()[:6]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _sort_key(meta: TemplateMeta) -> tuple[str, str]:
    decomposed = unicodedata.normalize("NFKD", meta.name or "")
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    return (folded, meta.id)


def _check_docx(data: bytes, source_name: str) -> None:
    """Ověří, že jde opravdu o .docx (ZIP obsahující ``word/document.xml``)."""

    label = source_name or "soubor"
    if not data:
        raise StoreError(f"Soubor „{label}“ je prázdný.")
    if data[:2] == b"\xd0\xcf" or data[:4] == b"\xd0\xcf\x11\xe0":
        raise StoreError(
            f"Soubor „{label}“ je ve starém formátu .doc. "
            "Otevřete jej ve Wordu a uložte jako .docx."
        )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError) as exc:
        raise StoreError(
            f"Soubor „{label}“ není platný dokument .docx "
            "(nejde o ZIP archiv, který Word používá)."
        ) from exc
    if "word/document.xml" not in names:
        raise StoreError(
            f"Soubor „{label}“ není dokument Wordu — chybí v něm část "
            "„word/document.xml“."
        )


def _scan_docx(path: Path) -> ScanResult:
    """Zavolá skutečný engine. Import je líný — kvůli startu aplikace i testům."""

    from .docx_engine import scan_docx

    return scan_docx(path)


def _suggest(scan: ScanResult):
    from . import mapping

    return mapping.suggest_fields(scan), mapping.suggest_optional_paragraphs(scan)


# ---------------------------------------------------------------------------
# Knihovna šablon
# ---------------------------------------------------------------------------
class TemplateStore:
    """Šablony uložené v ``app_home()/templates``."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else config.templates_dir()
        self._scan_cache: dict[str, tuple[int, int, ScanResult]] = {}

    # -- cesty ---------------------------------------------------------
    def _validate_id(self, template_id: str) -> str:
        tid = str(template_id or "").strip()
        if not tid or not _ID_RE.match(tid) or tid in (".", ".."):
            raise TemplateNotFound(f"Neplatné označení šablony: „{template_id}“.")
        return tid

    def template_dir(self, template_id: str) -> Path:
        return self.root / self._validate_id(template_id)

    def docx_path(self, template_id: str) -> Path:
        return self.template_dir(template_id) / TEMPLATE_FILE_NAME

    def meta_path(self, template_id: str) -> Path:
        return self.template_dir(template_id) / META_FILE_NAME

    def exists(self, template_id: str) -> bool:
        try:
            return self.meta_path(template_id).is_file()
        except TemplateNotFound:
            return False

    # -- čtení ---------------------------------------------------------
    def list(self) -> list[TemplateMeta]:
        """Všechny šablony seřazené podle názvu (bez ohledu na diakritiku)."""

        items: list[TemplateMeta] = []
        if not self.root.is_dir():
            return items
        try:
            entries = sorted(self.root.iterdir())
        except OSError as exc:
            raise StoreError(
                f"Složku se šablonami „{self.root}“ se nepodařilo přečíst: {exc}."
            ) from exc

        for entry in entries:
            if not entry.is_dir():
                continue
            meta_file = entry / META_FILE_NAME
            if not meta_file.is_file():
                continue
            try:
                data = read_json(meta_file, default=None, strict=True)
            except ConfigError:
                # poškozenou šablonu jen přeskočíme, aplikace musí jet dál
                continue
            if not isinstance(data, Mapping):
                continue
            meta = TemplateMeta.from_dict(data)
            if not meta.id:
                meta.id = entry.name
            items.append(meta)

        items.sort(key=_sort_key)
        return items

    def get(self, template_id: str) -> TemplateMeta:
        meta_file = self.meta_path(template_id)
        try:
            data = read_json(meta_file, default=None, strict=True)
        except ConfigError as exc:
            raise StoreError(str(exc)) from exc
        if not isinstance(data, Mapping):
            raise TemplateNotFound(f"Šablona „{template_id}“ nebyla nalezena.")
        meta = TemplateMeta.from_dict(data)
        if not meta.id:
            meta.id = self._validate_id(template_id)
        return meta

    def read_docx(self, template_id: str) -> bytes:
        path = self.docx_path(template_id)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise TemplateNotFound(
                f"K šabloně „{template_id}“ chybí soubor {TEMPLATE_FILE_NAME}."
            ) from exc
        except OSError as exc:
            raise StoreError(
                f"Soubor šablony „{template_id}“ se nepodařilo přečíst: {exc}."
            ) from exc

    def scan(self, template_id: str) -> ScanResult:
        """Analýza šablony s cache podle velikosti a času změny souboru."""

        path = self.docx_path(template_id)
        try:
            stat = path.stat()
        except FileNotFoundError as exc:
            raise TemplateNotFound(
                f"K šabloně „{template_id}“ chybí soubor {TEMPLATE_FILE_NAME}."
            ) from exc
        except OSError as exc:
            raise StoreError(f"Šablonu „{template_id}“ nelze přečíst: {exc}.") from exc

        stamp = (stat.st_size, stat.st_mtime_ns)
        cached = self._scan_cache.get(template_id)
        if cached is not None and (cached[0], cached[1]) == stamp:
            return cached[2]

        result = _scan_docx(path)
        self._scan_cache[template_id] = (stamp[0], stamp[1], result)
        return result

    def invalidate_scan(self, template_id: str | None = None) -> None:
        if template_id is None:
            self._scan_cache.clear()
        else:
            self._scan_cache.pop(template_id, None)

    # -- zápis ---------------------------------------------------------
    def import_docx(
        self,
        src: Path,
        name: str,
        *,
        description: str = "",
        tags: Sequence[str] = (),
    ) -> TemplateMeta:
        """Nahraje šablonu do knihovny: kopie souboru, analýza, návrh polí."""

        source = Path(src)
        display = source.name or str(source)
        try:
            if not source.exists():
                raise StoreError(f"Soubor „{source}“ neexistuje.")
            if not source.is_file():
                raise StoreError(f"„{source}“ není soubor.")
            size = source.stat().st_size
            if size > MAX_TEMPLATE_BYTES:
                raise StoreError(
                    f"Soubor „{display}“ je příliš velký "
                    f"({size // (1024 * 1024)} MB, povoleno je 50 MB)."
                )
            data = source.read_bytes()
        except OSError as exc:
            raise StoreError(f"Soubor „{display}“ se nepodařilo přečíst: {exc}.") from exc

        _check_docx(data, display)

        title = str(name or "").strip() or Path(display).stem or "Šablona"

        template_id = make_template_id(title, data)
        salt = 1
        while (self.root / template_id).exists():
            salt += 1
            if salt > 50:
                raise StoreError(
                    f"Šablonu „{title}“ se nepodařilo uložit — v knihovně je "
                    "příliš mnoho stejných kopií."
                )
            template_id = make_template_id(title, data, salt=str(salt))

        target_dir = self.root / template_id
        config.ensure_dir(target_dir)
        created = True
        try:
            docx_target = target_dir / TEMPLATE_FILE_NAME
            docx_target.write_bytes(data)

            scan = _scan_docx(docx_target)
            fields, optional_paragraphs = _suggest(scan)

            now = _now_iso()
            meta = TemplateMeta(
                id=template_id,
                name=title,
                description=str(description or ""),
                tags=[str(t) for t in (tags or ()) if str(t).strip()],
                source_filename=display,
                imported_at=now,
                updated_at=now,
                fields=fields,
                optional_paragraphs=optional_paragraphs,
            )
            self._write_meta(meta)
        except StoreError:
            if created:
                self._remove_dir(target_dir)
            raise
        except OSError as exc:
            if created:
                self._remove_dir(target_dir)
            raise StoreError(f"Šablonu „{title}“ se nepodařilo uložit: {exc}.") from exc
        except Exception as exc:  # chyba enginu — ať uživatel ví, co se stalo
            if created:
                self._remove_dir(target_dir)
            raise StoreError(
                f"Šablonu „{display}“ se nepodařilo zpracovat: {exc}"
            ) from exc

        self._scan_cache.pop(template_id, None)
        return meta

    def save_meta(self, meta: TemplateMeta) -> None:
        """Uloží metadata (a orazítkuje ``updated_at``)."""

        self._validate_id(meta.id)
        if not self.docx_path(meta.id).is_file() and not self.template_dir(meta.id).is_dir():
            raise TemplateNotFound(f"Šablona „{meta.id}“ nebyla nalezena.")
        meta.updated_at = _now_iso()
        self._write_meta(meta)

    def _write_meta(self, meta: TemplateMeta) -> None:
        try:
            write_json_atomic(self.meta_path(meta.id), meta.to_dict())
        except ConfigError as exc:
            raise StoreError(str(exc)) from exc

    def duplicate(self, template_id: str, new_name: str) -> TemplateMeta:
        """Vytvoří kopii šablony pod novým názvem (včetně mapování polí)."""

        meta = self.get(template_id)
        title = str(new_name or "").strip() or f"{meta.name} (kopie)"
        data = self.read_docx(template_id)

        new_id = make_template_id(title, data)
        salt = 1
        while (self.root / new_id).exists():
            salt += 1
            if salt > 50:
                raise StoreError(
                    f"Kopii šablony „{title}“ se nepodařilo vytvořit — "
                    "v knihovně je příliš mnoho stejných kopií."
                )
            new_id = make_template_id(title, data, salt=str(salt))

        target_dir = self.root / new_id
        config.ensure_dir(target_dir)
        try:
            (target_dir / TEMPLATE_FILE_NAME).write_bytes(data)
            now = _now_iso()
            copy = TemplateMeta.from_dict(meta.to_dict())
            copy.id = new_id
            copy.name = title
            copy.imported_at = now
            copy.updated_at = now
            self._write_meta(copy)
        except StoreError:
            self._remove_dir(target_dir)
            raise
        except OSError as exc:
            self._remove_dir(target_dir)
            raise StoreError(f"Kopii šablony se nepodařilo uložit: {exc}.") from exc
        return copy

    def rename(self, template_id: str, new_name: str) -> TemplateMeta:
        """Přejmenuje šablonu. ``id`` zůstává, aby zůstaly platné cesty."""

        title = str(new_name or "").strip()
        if not title:
            raise StoreError("Název šablony nesmí být prázdný.")
        meta = self.get(template_id)
        meta.name = title
        self.save_meta(meta)
        return meta

    def delete(self, template_id: str) -> None:
        directory = self.template_dir(template_id)
        if not directory.is_dir():
            raise TemplateNotFound(f"Šablona „{template_id}“ nebyla nalezena.")
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            raise StoreError(
                f"Šablonu „{template_id}“ se nepodařilo smazat: {exc}."
            ) from exc
        self._scan_cache.pop(template_id, None)

    @staticmethod
    def _remove_dir(directory: Path) -> None:
        try:
            shutil.rmtree(directory)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Historie vyplněných hodnot
# ---------------------------------------------------------------------------
class ValueHistory:
    """Napovídání dříve vyplněných hodnot podle klíče pole."""

    def __init__(self, path: Path | None = None, *, limit: int = HISTORY_LIMIT) -> None:
        self.path = Path(path) if path is not None else config.history_path()
        self.limit = max(1, int(limit))
        self._values: dict[str, list[str]] | None = None

    # -- načtení / uložení --------------------------------------------
    def _load(self) -> dict[str, list[str]]:
        if self._values is not None:
            return self._values

        raw = read_json(self.path, default=None, strict=False)
        values: dict[str, list[str]] = {}
        source: Any = None
        if isinstance(raw, Mapping):
            source = raw.get("values") if isinstance(raw.get("values"), Mapping) else raw
        if isinstance(source, Mapping):
            for key, items in source.items():
                if key in ("schema_version", "values"):
                    continue
                if isinstance(items, (str, bytes)):
                    items = [items]
                if not isinstance(items, (list, tuple)):
                    continue
                cleaned: list[str] = []
                for item in items:
                    text = "" if item is None else str(item)
                    if text.strip() and text not in cleaned:
                        cleaned.append(text)
                if cleaned:
                    values[str(key)] = cleaned[: self.limit]

        self._values = values
        return values

    def _save(self) -> None:
        payload = {"schema_version": 1, "values": self._load()}
        try:
            write_json_atomic(self.path, payload)
        except ConfigError as exc:
            raise StoreError(str(exc)) from exc

    # -- API -----------------------------------------------------------
    def suggestions(self, key: str) -> list[str]:
        """Dříve použité hodnoty, nejnovější první (max ``limit``)."""

        return list(self._load().get(str(key), []))

    def remember(self, values: Mapping[str, str]) -> None:
        """Zapamatuje si vyplněné hodnoty. Prázdné se neukládají."""

        data = self._load()
        changed = False
        for key, value in (values or {}).items():
            text = "" if value is None else str(value)
            if not text.strip():
                continue
            name = str(key)
            existing = data.get(name, [])
            remaining = [v for v in existing if v != text]
            updated = [text] + remaining
            updated = updated[: self.limit]
            if updated != existing:
                data[name] = updated
                changed = True
        if changed:
            self._save()

    def clear(self, key: str | None = None) -> None:
        """Smaže historii — celou, nebo jen pro jeden klíč."""

        data = self._load()
        if key is None:
            if not data:
                return
            data.clear()
        elif str(key) in data:
            data.pop(str(key))
        else:
            return
        self._save()
