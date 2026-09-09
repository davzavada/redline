"""Práce s .docx jako se ZIP balíčkem a výběr částí ke skenování.

Balíček se načte celý do paměti (šablony mají jednotky MB) a při zápisu se
skládá znovu ve stejném pořadí položek: nedotčené se kopírují včetně původního
``compress_type`` (obrázky uložené bez komprese tak zůstanou bajt po bajtu
stejné), upravené se zapisují s ``ZIP_DEFLATED``.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from pathlib import Path
from typing import Iterator, Mapping, Sequence

__all__ = [
    "DocxError",
    "DocxPackage",
    "MAIN_PART",
    "is_scannable_part",
    "sort_parts",
]


class DocxError(Exception):
    """Soubor .docx se nepodařilo přečíst nebo není platný."""


MAIN_PART = "word/document.xml"

_HEADER_RE = re.compile(r"^word/header\d*\.xml$")
_FOOTER_RE = re.compile(r"^word/footer\d*\.xml$")
_NOTES = ("word/footnotes.xml", "word/endnotes.xml")
_NUMBER_RE = re.compile(r"(\d+)")


def is_scannable_part(name: str) -> bool:
    """Skenují se jen hlavní tělo, záhlaví, zápatí a poznámky — nikdy glossary."""

    if name.startswith("word/glossary/"):
        return False
    if name == MAIN_PART or name in _NOTES:
        return True
    return bool(_HEADER_RE.match(name) or _FOOTER_RE.match(name))


def _part_rank(name: str) -> tuple[int, int, str]:
    if name == MAIN_PART:
        return (0, 0, name)
    if _HEADER_RE.match(name):
        group = 1
    elif _FOOTER_RE.match(name):
        group = 2
    elif name == "word/footnotes.xml":
        return (3, 0, name)
    elif name == "word/endnotes.xml":
        return (4, 0, name)
    else:  # pragma: no cover - sem se dostane jen neznámá část
        return (5, 0, name)
    match = _NUMBER_RE.search(name)
    return (group, int(match.group(1)) if match else 0, name)


def sort_parts(names: Sequence[str]) -> list[str]:
    """Seřadí části do pořadí, ve kterém se prochází dokument."""

    return sorted(names, key=_part_rank)


def _load_bytes(src: "Path | str | bytes | bytearray | os.PathLike[str]") -> bytes:
    if isinstance(src, (bytes, bytearray)):
        return bytes(src)
    path = Path(src)
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise DocxError(f"Soubor „{path}“ neexistuje.") from exc
    except OSError as exc:
        raise DocxError(f"Soubor „{path}“ se nepodařilo přečíst: {exc}") from exc


class DocxPackage:
    """Obsah .docx v paměti se zachovaným pořadím a metadaty položek."""

    __slots__ = ("entries", "_contents", "comment")

    def __init__(
        self,
        entries: list[zipfile.ZipInfo],
        contents: dict[str, bytes],
        comment: bytes = b"",
    ) -> None:
        self.entries = entries
        self._contents = contents
        self.comment = comment

    # -- načtení -----------------------------------------------------------
    @classmethod
    def open(cls, src: "Path | str | bytes | bytearray | os.PathLike[str]") -> "DocxPackage":
        raw = _load_bytes(src)
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = list(archive.infolist())
                contents: dict[str, bytes] = {}
                for info in entries:
                    contents[info.filename] = b"" if info.is_dir() else archive.read(info)
                comment = archive.comment
        except zipfile.BadZipFile as exc:
            raise DocxError(
                "Soubor není platný .docx — nejde ho otevřít jako ZIP archiv."
            ) from exc
        if MAIN_PART not in contents:
            raise DocxError(
                "Soubor není platný .docx — chybí část word/document.xml."
            )
        return cls(entries, contents, comment)

    # -- čtení -------------------------------------------------------------
    def names(self) -> list[str]:
        return [info.filename for info in self.entries]

    def __contains__(self, name: object) -> bool:
        return name in self._contents

    def read(self, name: str) -> bytes:
        try:
            return self._contents[name]
        except KeyError as exc:
            raise DocxError(f"Část „{name}“ v dokumentu není.") from exc

    def scan_parts(self) -> list[str]:
        """Části k prohledání v pořadí dokumentu."""

        found = [
            info.filename
            for info in self.entries
            if not info.is_dir() and is_scannable_part(info.filename)
        ]
        return sort_parts(found)

    def iter_scan_parts(self) -> Iterator[tuple[str, bytes]]:
        for name in self.scan_parts():
            yield name, self._contents[name]

    # -- zápis -------------------------------------------------------------
    def rebuild(self, modified: Mapping[str, bytes] | None = None) -> bytes:
        """Složí nový .docx; ``modified`` přepisuje obsah vybraných částí."""

        changed = dict(modified or {})
        unknown = [name for name in changed if name not in self._contents]
        if unknown:
            raise DocxError(
                "Nelze zapsat části, které v dokumentu nejsou: " + ", ".join(sorted(unknown))
            )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as out:
            out.comment = self.comment
            for info in self.entries:
                name = info.filename
                is_dir = info.is_dir()
                payload = b"" if is_dir else changed.get(name, self._contents[name])
                target = zipfile.ZipInfo(name, date_time=info.date_time)
                target.compress_type = (
                    zipfile.ZIP_DEFLATED if (name in changed and not is_dir) else info.compress_type
                )
                target.external_attr = info.external_attr
                target.internal_attr = info.internal_attr
                target.create_system = info.create_system
                target.comment = info.comment
                # `extra` se záměrně nekopíruje: pole se zip64/časovými razítky
                # by v nově vytvořeném archivu neodpovídala skutečnosti.
                out.writestr(target, payload)
        return buffer.getvalue()
