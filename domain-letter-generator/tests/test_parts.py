"""Testy načítání .docx jako ZIP balíčku (:mod:`dlg.docx_engine.parts`).

Šablonu si uživatel vybírá sám a dostane ji e-mailem, ze síťového disku nebo
z OneDrive — může tedy přijít poškozená, zaheslovaná nebo úmyslně zákeřná.
Ani jeden z těch případů nesmí skončit tracebackem: aplikace má říct česky,
co se stalo.
"""

from __future__ import annotations

import io
import random
import zipfile
import zlib

import pytest

from dlg.docx_engine.parts import MAIN_PART, MAX_UNPACKED_BYTES, DocxError, DocxPackage


def _zip(polozky: dict[str, bytes], *, komprese: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", komprese) as archiv:
        for jmeno, data in polozky.items():
            archiv.writestr(jmeno, data)
    return buf.getvalue()


def test_platny_balicek_se_nacte() -> None:
    data = _zip({MAIN_PART: b"<w:document/>", "word/media/obrazek.png": b"\x89PNG"})
    balicek = DocxPackage.open(data)
    assert MAIN_PART in balicek
    assert balicek.read("word/media/obrazek.png") == b"\x89PNG"


def test_soubor_ktery_neni_zip() -> None:
    with pytest.raises(DocxError) as chyba:
        DocxPackage.open(b"tohle rozhodne neni zip archiv")
    assert "není platný .docx" in str(chyba.value)


def test_zip_bez_hlavni_casti() -> None:
    with pytest.raises(DocxError) as chyba:
        DocxPackage.open(_zip({"word/styles.xml": b"<x/>"}))
    assert "word/document.xml" in str(chyba.value)


def test_zip_bomba_se_odmitne() -> None:
    """Pár set kilobajtů se umí rozbalit na gigabajty — a aplikace se zadusí.

    Velikostní limit ve ``store`` hlídá jen soubor NA DISKU, ne to, na kolik
    se rozbalí, takže se pojistka musí uplatnit až tady.
    """

    data = _zip({
        MAIN_PART: b"<w:document/>",
        "word/media/velky.bin": b"\0" * (MAX_UNPACKED_BYTES + 1024 * 1024),
    })
    assert len(data) < 2 * 1024 * 1024, "archiv má být malý — o to v testu jde"

    with pytest.raises(DocxError) as chyba:
        DocxPackage.open(data)
    text = str(chyba.value)
    assert "nepřiměřeně velký" in text
    assert str(MAX_UNPACKED_BYTES // (1024 * 1024)) in text


def test_poskozeny_obsah_zipu() -> None:
    """Nedokončený přenos z OneDrive/síťového disku poškodí bajty uvnitř zipu.

    Hlavička zůstane v pořádku, takže se archiv otevře, a chyba přijde až při
    čtení položky — tady jako nesouhlasící kontrolní součet.
    """

    # nestlačitelný obsah (pevné semínko, ať je selhání reprodukovatelné)
    balast = random.Random(20260909).randbytes(40000)
    data = bytearray(_zip({MAIN_PART: b"<w:document/>", "word/media/foto.bin": balast}))
    stred = len(data) // 2
    for i in range(stred, stred + 400):
        data[i] ^= 0xFF

    with pytest.raises(DocxError) as chyba:
        DocxPackage.open(bytes(data))
    # ať už to zipfile ohlásí jakkoli, uživatel musí dostat českou větu
    text = str(chyba.value)
    assert "docx" in text.lower()
    assert "Traceback" not in text


def test_rozbite_rozbalovani_skonci_ceskou_hlaskou(monkeypatch) -> None:
    """Poškozený deflate proud hlásí ``zlib.error`` — nesmí proletět ven.

    Zkonstruovat takový zip spolehlivě je nevděčné (poškození obvykle dřív
    shodí kontrolní součet), proto se sem chyba podstrčí přímo.
    """

    puvodni_read = zipfile.ZipFile.read

    def rozbity_read(self, name, pwd=None):
        if getattr(name, "filename", name) == MAIN_PART:
            raise zlib.error("invalid distance too far back")
        return puvodni_read(self, name, pwd)

    monkeypatch.setattr(zipfile.ZipFile, "read", rozbity_read)

    with pytest.raises(DocxError) as chyba:
        DocxPackage.open(_zip({MAIN_PART: b"<w:document/>"}))
    assert "poškozený" in str(chyba.value)


def test_zaheslovany_archiv() -> None:
    """``zipfile`` hlásí zaheslovanou položku jako ``RuntimeError``."""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archiv:
        archiv.writestr(MAIN_PART, b"<w:document/>")
    data = bytearray(buf.getvalue())
    # zapnout příznak šifrování v lokální hlavičce i v adresáři
    data[6] |= 0x01
    stred = data.find(b"PK\x01\x02")
    if stred != -1:
        data[stred + 8] |= 0x01

    with pytest.raises(DocxError) as chyba:
        DocxPackage.open(bytes(data))
    assert "zaheslovaný" in str(chyba.value) or "poškozený" in str(chyba.value)
