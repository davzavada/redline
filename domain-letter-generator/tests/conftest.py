"""Společná nastavení testů.

Kořen projektu se dává na ``sys.path``, aby balík ``dlg`` šel naimportovat
i při spuštění pytestu odjinud než z kořene repozitáře.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: Skutečná ukázková šablona; v CI není (leží v .gitignore).
SAMPLE_DOCX = PROJECT_ROOT / "samples_local" / "lego_cd_cs.docx"

#: Marker pro testy, které bez ukázkové šablony nedávají smysl.
requires_sample = pytest.mark.skipif(
    not SAMPLE_DOCX.exists(),
    reason="ukázková šablona samples_local/lego_cd_cs.docx není k dispozici",
)


@pytest.fixture(scope="session")
def sample_docx_path() -> Path:
    """Cesta k reálné ukázkové šabloně (jen pro testy s ``requires_sample``)."""

    if not SAMPLE_DOCX.exists():  # pragma: no cover - hlídá to už marker
        pytest.skip("ukázková šablona není k dispozici")
    return SAMPLE_DOCX


@pytest.fixture(scope="session")
def sample_docx_bytes(sample_docx_path: Path) -> bytes:
    return sample_docx_path.read_bytes()
