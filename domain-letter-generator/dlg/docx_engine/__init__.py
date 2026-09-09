"""Jádro práce s .docx — skenování šablon a doplňování hodnot.

Veřejné API modulu:

``scan_docx(path_or_bytes) -> ScanResult``
    Najde v šabloně placeholdery a odstavce.
``fill_docx(src, values, ...) -> tuple[bytes, FillReport]``
    Doplní hodnoty a vrátí hotový .docx.
``extract_text(path_or_bytes) -> str``
    Prostý text hlavního dokumentu pro náhled.

Vše stojí jen na standardní knihovně (``zipfile`` + ``xml.parsers.expat``).
"""

from __future__ import annotations

from .fill import extract_text, fill_docx
from .parts import DocxError, DocxPackage, is_scannable_part
from .scan import scan_docx
from .xmlsplice import XmlError

__all__ = [
    "scan_docx",
    "fill_docx",
    "extract_text",
    "DocxError",
    "DocxPackage",
    "XmlError",
    "is_scannable_part",
]
