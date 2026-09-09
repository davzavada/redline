"""Testy převodu dopisu do PDF (:mod:`dlg.pdf`).

Většina testů si převodník podstrčí, aby nezávisely na tom, co je na stroji
nainstalované. Jeden test převádí doopravdy — spustí se jen tam, kde je
LibreOffice, jinak se přeskočí.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dlg import pdf

from fixtures import docx_via_python_docx


@pytest.fixture()
def dopis(tmp_path: Path) -> Path:
    """Realistický .docx (od python-docx), který umí otevřít i LibreOffice."""

    cesta = tmp_path / "Výzva – Novák (1).docx"
    cesta.write_bytes(docx_via_python_docx())
    return cesta


# ---------------------------------------------------------------------------
# hledání převodníku
# ---------------------------------------------------------------------------
def test_bez_prevodniku_se_nic_nepredstira(monkeypatch, dopis: Path) -> None:
    monkeypatch.setattr(pdf, "_word_present", lambda: False)
    monkeypatch.setattr(pdf, "_find_soffice", lambda: "")

    assert pdf.find_converter() == ("", "")
    assert pdf.available() is False
    assert pdf.converter_name() == ""

    with pytest.raises(pdf.PdfError) as chyba:
        pdf.convert(dopis)
    # hláška musí uživateli říct, co s tím — ne jen že to nešlo
    assert "Word" in str(chyba.value)
    assert "Uložit jako" in str(chyba.value)
    assert dopis.is_file(), "původní dopis musí zůstat netknutý"


def test_word_ma_prednost_pred_libreoffice(monkeypatch) -> None:
    """Word dělá věrnější PDF a uživatel v něm šablonu psal."""

    monkeypatch.setattr(pdf, "_word_present", lambda: True)
    monkeypatch.setattr(pdf, "_find_powershell", lambda: r"C:\powershell.exe")
    monkeypatch.setattr(pdf, "_find_soffice", lambda: "/usr/bin/soffice")

    assert pdf.find_converter() == ("word", r"C:\powershell.exe")
    assert pdf.converter_name() == "Microsoft Word"


def test_bez_powershellu_se_spadne_na_libreoffice(monkeypatch) -> None:
    monkeypatch.setattr(pdf, "_word_present", lambda: True)
    monkeypatch.setattr(pdf, "_find_powershell", lambda: "")
    monkeypatch.setattr(pdf, "_find_soffice", lambda: "/usr/bin/soffice")

    assert pdf.find_converter() == ("soffice", "/usr/bin/soffice")
    assert pdf.converter_name() == "LibreOffice"


def test_chybejici_soubor_se_pozna_hned(tmp_path: Path) -> None:
    with pytest.raises(pdf.PdfError) as chyba:
        pdf.convert(tmp_path / "neexistuje.docx")
    assert "neexistuje" in str(chyba.value)


# ---------------------------------------------------------------------------
# chování při selhání převodníku
# ---------------------------------------------------------------------------
def test_selhany_prevod_rekne_duvod_a_dopis_nechá(monkeypatch, dopis: Path) -> None:
    monkeypatch.setattr(pdf, "_word_present", lambda: False)
    monkeypatch.setattr(pdf, "_find_soffice", lambda: "/usr/bin/soffice")

    def rozbity_run(command, *, timeout, env=None):
        return subprocess.CompletedProcess(
            command, 1, stdout=b"", stderr=b"Error: source file could not be loaded\n"
        )

    monkeypatch.setattr(pdf, "_run", rozbity_run)

    with pytest.raises(pdf.PdfError) as chyba:
        pdf.convert(dopis)
    text = str(chyba.value)
    assert "LibreOffice" in text
    # skutečná hláška převodníku se uživateli ukáže — bez ní se nedá poradit
    assert "source file could not be loaded" in text
    assert dopis.is_file()
    assert not dopis.with_suffix(".pdf").exists()


def test_prilis_dlouhy_prevod_se_prerusi(monkeypatch, dopis: Path) -> None:
    monkeypatch.setattr(pdf, "_word_present", lambda: False)
    monkeypatch.setattr(pdf, "_find_soffice", lambda: "/usr/bin/soffice")

    def zaseknuty_run(command, *, timeout, env=None):
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(pdf, "_run", zaseknuty_run)

    with pytest.raises(pdf.PdfError) as chyba:
        pdf.convert(dopis, timeout=5)
    assert "5 s" in str(chyba.value)


def test_word_dostane_cesty_prostredim_ne_v_prikazu(monkeypatch, dopis: Path) -> None:
    """Diakritika a mezery v názvu se nesmí lámat o uvozovky v příkazu."""

    monkeypatch.setattr(pdf, "_word_present", lambda: True)
    monkeypatch.setattr(pdf, "_find_powershell", lambda: r"C:\powershell.exe")
    zaznam: dict = {}

    def falesny_run(command, *, timeout, env=None):
        zaznam["command"] = command
        zaznam["env"] = env
        Path(env["DLG_PDF_DST"]).write_bytes(b"%PDF-1.7\n")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(pdf, "_run", falesny_run)

    vysledek = pdf.convert(dopis)
    assert vysledek == dopis.with_suffix(".pdf")
    assert vysledek.read_bytes().startswith(b"%PDF")
    # cesta jde proměnnou prostředí, ne slepená do textu příkazu
    assert zaznam["env"]["DLG_PDF_SRC"] == str(dopis)
    assert zaznam["env"]["DLG_PDF_DST"] == str(vysledek)
    assert not any(str(dopis) in str(part) for part in zaznam["command"])
    # a Word se vždycky ukončí, i kdyby SaveAs spadlo
    skript = zaznam["command"][-1]
    assert "finally" in skript and "$word.Quit()" in skript


def test_vlastni_cil_se_respektuje(monkeypatch, dopis: Path, tmp_path: Path) -> None:
    monkeypatch.setattr(pdf, "_word_present", lambda: True)
    monkeypatch.setattr(pdf, "_find_powershell", lambda: r"C:\powershell.exe")

    def falesny_run(command, *, timeout, env=None):
        Path(env["DLG_PDF_DST"]).write_bytes(b"%PDF-1.7\n")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(pdf, "_run", falesny_run)

    cil = tmp_path / "jinam" / "dopis.pdf"
    cil.parent.mkdir()
    assert pdf.convert(dopis, cil) == cil
    assert cil.is_file()


# ---------------------------------------------------------------------------
# skutečný převod
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not pdf._find_soffice(), reason="LibreOffice není k dispozici")
def test_skutecny_prevod_libreoffice(monkeypatch, dopis: Path) -> None:
    """Doopravdy převede dopis na PDF. Pomalý (~10 s), ale je to to podstatné."""

    monkeypatch.setattr(pdf, "_word_present", lambda: False)

    vysledek = pdf.convert(dopis)

    assert vysledek == dopis.with_suffix(".pdf")
    assert vysledek.parent == dopis.parent
    data = vysledek.read_bytes()
    assert data.startswith(b"%PDF"), "výstup není PDF"
    assert len(data) > 1000, "PDF je podezřele malé"
    assert dopis.is_file(), "původní dopis musí zůstat"
