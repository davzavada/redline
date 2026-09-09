"""Sestavení názvu výstupního souboru ze vzoru.

Vzor je obyčejný text s náhradami ve složených závorkách::

    {datum}_{nazev}          ->  2026-09-09_Vyzva k naprave
    {datum}_{domena}_vyzva   ->  2026-09-09_lego-shop.cz_vyzva

``{datum}`` je dnešek ve tvaru ``YYYY-MM-DD`` (volitelně s vlastním formátem —
``{datum:%d.%m.%Y}``), ``{nazev}`` je název šablony, cokoli dalšího je klíč
pole. Chybějící klíč se nahradí prázdným řetězcem.

Výsledek je vždy bezpečný název souboru pro Windows i Linux — bez přípony.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Mapping

__all__ = [
    "DEFAULT_PATTERN",
    "render_pattern",
    "sanitize_filename",
    "unique_path",
]

#: Výchozí vzor (shodný s ``TemplateMeta.output_pattern``).
DEFAULT_PATTERN = "{datum}_{nazev}"

#: Znaky, které Windows v názvu souboru nepovoluje.
FORBIDDEN_CHARS = '<>:"/\\|?*'
#: Vyhrazená jména MS-DOS — nesmí být ani se zdánlivou příponou (``CON.docx``).
RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
#: Delší název už nedává smysl (a na Windows hrozí limit 260 znaků na cestu).
MAX_STEM_LENGTH = 120
#: Náhradní název, kdyby ze vzoru nezbylo nic.
DEFAULT_STEM = "dopis"
#: Pojistka proti nekonečnému hledání volného čísla.
MAX_UNIQUE_ATTEMPTS = 9999

_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_filename(
    name: str, *, max_length: int = MAX_STEM_LENGTH, fallback: str = DEFAULT_STEM
) -> str:
    """Očistí text tak, aby z něj šel udělat název souboru (bez přípony)."""

    text = _CONTROL_RE.sub(" ", str(name or ""))
    for ch in FORBIDDEN_CHARS:
        text = text.replace(ch, "-")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"-{2,}", "-", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    text = text.strip(" .-_")

    if len(text) > max_length:
        text = text[:max_length]
    # Windows umí smazat koncové tečky a mezery — radši je uřízneme sami.
    text = text.rstrip(" .")

    if not text:
        return fallback

    # „CON“ i „CON.docx“ jsou pro Windows vyhrazená jména — odlišíme podtržítkem
    head, dot, rest = text.partition(".")
    if head.upper() in RESERVED_NAMES:
        text = f"{head}_{dot}{rest}"

    return text


def render_pattern(
    pattern: str,
    values: Mapping[str, str],
    *,
    template_name: str = "",
    today: date | None = None,
    fallback: str = DEFAULT_STEM,
) -> str:
    """Ze vzoru a vyplněných hodnot poskládá bezpečný název souboru."""

    day = today or date.today()
    text = str(pattern if pattern is not None else "")
    if not text.strip():
        text = DEFAULT_PATTERN

    data = {str(k): ("" if v is None else str(v)) for k, v in (values or {}).items()}

    def replace(match: re.Match[str]) -> str:
        token = match.group(1).strip()
        if not token:
            return ""
        key, _, spec = token.partition(":")
        key = key.strip()
        spec = spec.strip()

        lowered = key.lower()
        if lowered in ("datum", "date"):
            if spec:
                try:
                    return day.strftime(spec)
                except (ValueError, TypeError):
                    return day.isoformat()
            return day.isoformat()
        if lowered in ("nazev", "název", "sablona", "šablona"):
            return str(template_name or "")
        if key in data:
            return data[key]
        for name, value in data.items():
            if name.lower() == lowered:
                return value
        return ""

    rendered = _TOKEN_RE.sub(replace, text)
    return sanitize_filename(rendered, fallback=fallback)


def unique_path(directory: Path, stem: str, suffix: str = ".docx") -> Path:
    """Vrátí volnou cestu; existující soubor doplní o „ (2)“, „ (3)“ …"""

    folder = Path(directory)
    safe_stem = sanitize_filename(stem)
    ext = str(suffix or "")
    if ext and not ext.startswith("."):
        ext = "." + ext

    candidate = folder / f"{safe_stem}{ext}"
    if not candidate.exists():
        return candidate

    for index in range(2, MAX_UNIQUE_ATTEMPTS + 1):
        candidate = folder / f"{safe_stem} ({index}){ext}"
        if not candidate.exists():
            return candidate

    raise FileExistsError(
        f"Ve složce „{folder}“ už je příliš mnoho souborů s názvem "
        f"„{safe_stem}“ — ukliďte je nebo zvolte jiný název."
    )
