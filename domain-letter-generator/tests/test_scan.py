"""Testy detekce placeholderů."""

from __future__ import annotations

from conftest import requires_sample
from dlg.docx_engine import extract_text, scan_docx
from dlg.docx_engine.parts import DocxPackage, is_scannable_part, sort_parts
from dlg.docx_engine.scan import analyse_part, build_context, split_options
from fixtures import (
    document_xml,
    docx_via_python_docx,
    header_xml,
    make_docx,
    paragraph,
    run,
    sdt_date,
    sdt_inline,
    table,
    text_paragraph,
    textbox,
)


def scan_body(body: str, **kwargs: object):
    return scan_docx(make_docx(document_xml(body), **kwargs))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# výběr částí
# ---------------------------------------------------------------------------

def test_skenuji_se_jen_povolene_casti() -> None:
    assert is_scannable_part("word/document.xml")
    assert is_scannable_part("word/header3.xml")
    assert is_scannable_part("word/footer1.xml")
    assert is_scannable_part("word/footnotes.xml")
    assert is_scannable_part("word/endnotes.xml")
    assert not is_scannable_part("word/glossary/document.xml")
    assert not is_scannable_part("word/styles.xml")
    assert not is_scannable_part("word/settings.xml")
    assert not is_scannable_part("word/comments.xml")


def test_poradi_casti() -> None:
    names = [
        "word/endnotes.xml",
        "word/footer10.xml",
        "word/header2.xml",
        "word/document.xml",
        "word/footnotes.xml",
        "word/footer2.xml",
        "word/header1.xml",
    ]
    assert sort_parts(names) == [
        "word/document.xml",
        "word/header1.xml",
        "word/header2.xml",
        "word/footer2.xml",
        "word/footer10.xml",
        "word/footnotes.xml",
        "word/endnotes.xml",
    ]


def test_glossary_se_neskenuje() -> None:
    result = scan_body(text_paragraph("[Jméno]"))
    assert [p.raw for p in result.placeholders] == ["[Jméno]"]
    assert all(p.part == "word/document.xml" for p in result.paragraphs)


def test_placeholdery_v_zahlavi_i_zapati() -> None:
    docx = make_docx(
        document_xml(text_paragraph("Tělo [A]")),
        {
            "word/header1.xml": header_xml(text_paragraph("Záhlaví [B]")),
            "word/footer1.xml": header_xml(text_paragraph("Zápatí [C]"), tag="ftr"),
            "word/footnotes.xml": header_xml(text_paragraph("Pozn. [D]"), tag="footnotes"),
        },
    )
    result = scan_docx(docx)
    assert [(p.part, p.raw, p.order) for p in result.placeholders] == [
        ("word/document.xml", "[A]", 0),
        ("word/header1.xml", "[B]", 1),
        ("word/footer1.xml", "[C]", 2),
        ("word/footnotes.xml", "[D]", 3),
    ]
    assert [p.order for p in result.paragraphs] == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# skládání textu odstavce
# ---------------------------------------------------------------------------

def test_placeholder_rozdeleny_do_vice_behu() -> None:
    body = paragraph(
        run("["),
        run("Jan Novák", highlight="yellow"),
        run("]"),
    )
    result = scan_body(body)
    (placeholder,) = result.placeholders
    assert placeholder.kind == "bracket"
    assert placeholder.raw == "[Jan Novák]"
    assert placeholder.inner == "Jan Novák"


def test_entity_v_textu_se_dekoduji() -> None:
    body = paragraph(run("[Novák & syn <s.r.o.>]"))
    result = scan_body(body)
    (placeholder,) = result.placeholders
    assert placeholder.inner == "Novák & syn <s.r.o.>"


def test_odstavce_v_tabulce_a_styl() -> None:
    body = table([[[text_paragraph("[Buňka]")], [paragraph(run("bez pole"), style="Nadpis")]]])
    result = scan_docx(make_docx(document_xml(body)))
    assert [p.in_table for p in result.paragraphs] == [True, True]
    assert [p.style for p in result.paragraphs] == [None, "Nadpis"]
    assert [p.raw for p in result.placeholders] == ["[Buňka]"]


def test_odstavec_v_textovem_poli_ma_vlastni_text() -> None:
    body = textbox(text_paragraph("[Uvnitř pole]"))
    result = scan_docx(make_docx(document_xml(body)))
    texts = [p.text for p in result.paragraphs]
    # vnější odstavec textové pole nepohltí
    assert "" in texts
    # mc:Fallback je jen kopie pro staré prohlížeče — uživateli se odstavec
    # nabídne jednou, ale vyplnit se musí obě větve
    assert texts.count("[Uvnitř pole]") == 1
    assert [p.raw for p in result.placeholders] == ["[Uvnitř pole]", "[Uvnitř pole]"]
    assert result.placeholders[0].id != result.placeholders[1].id


def test_nahled_textoveho_pole_neni_zdvojeny() -> None:
    body = textbox(text_paragraph("Kontakt: [telefon]"))
    docx = make_docx(document_xml(body))
    assert extract_text(docx).count("Kontakt: [telefon]") == 1
    # obě větve ale zůstávají mezi placeholdery, ať je fill vyplní
    assert len(scan_docx(docx).placeholders) == 2


# ---------------------------------------------------------------------------
# priorita nálezů
# ---------------------------------------------------------------------------

def test_mustache_ma_prednost_pred_hranatymi_zavorkami() -> None:
    result = scan_body(text_paragraph("[{{ jmeno }}]"))
    (placeholder,) = result.placeholders
    assert placeholder.kind == "mustache"
    assert placeholder.inner == "jmeno"
    assert placeholder.raw == "{{ jmeno }}"


def test_zvyrazneni_jen_tam_kde_nic_jineho_neni() -> None:
    body = paragraph(
        run("["),
        run("v závorce", highlight="yellow"),
        run("] a "),
        run("jen zvýrazněno", highlight="cyan"),
        run(" konec"),
    )
    result = scan_body(body)
    kinds = [(p.kind, p.raw) for p in result.placeholders]
    assert kinds == [("bracket", "[v závorce]"), ("highlight", "jen zvýrazněno")]


def test_zvyrazneni_none_se_ignoruje() -> None:
    body = paragraph(run("nic", highlight="none"), run("taky nic"))
    assert scan_body(body).placeholders == []


def test_zvyrazneni_bez_okrajovych_mezer() -> None:
    body = paragraph(run("A "), run("  hodnota  ", highlight="yellow"), run(" B"))
    (placeholder,) = scan_body(body).placeholders
    assert placeholder.raw == "hodnota"


def test_sdt_s_datem_je_placeholder() -> None:
    result = scan_body(sdt_date("6. září 2026"))
    (placeholder,) = result.placeholders
    assert placeholder.kind == "sdt"
    assert placeholder.raw == "6. září 2026"
    assert placeholder.inner == "6. září 2026"


def test_sdt_ma_prednost_pred_zavorkami_uvnitr() -> None:
    body = (
        "<w:sdt><w:sdtPr><w:showingPlcHdr/></w:sdtPr><w:sdtContent>"
        + text_paragraph("[uvnitř sdt]")
        + "</w:sdtContent></w:sdt>"
    )
    result = scan_docx(make_docx(document_xml(body)))
    (placeholder,) = result.placeholders
    assert placeholder.kind == "sdt"
    assert placeholder.raw == "[uvnitř sdt]"


def test_sdt_bez_data_a_bez_plcHdr_se_neignoruje_jako_sdt() -> None:
    body = (
        "<w:sdt><w:sdtPr><w:alias w:val=\"X\"/></w:sdtPr><w:sdtContent>"
        + text_paragraph("[obyčejná závorka]")
        + "</w:sdtContent></w:sdt>"
    )
    (placeholder,) = scan_docx(make_docx(document_xml(body))).placeholders
    assert placeholder.kind == "bracket"


def test_vnoreny_inline_sdt() -> None:
    body = paragraph(run("Datum: "), sdt_inline("6. 9. 2026"), run(" konec"))
    (placeholder,) = scan_docx(make_docx(document_xml(body))).placeholders
    assert placeholder.kind == "sdt"
    assert placeholder.raw == "6. 9. 2026"
    assert placeholder.context.startswith("Datum: »")


# ---------------------------------------------------------------------------
# vnitřek, varianty, kontext, id
# ---------------------------------------------------------------------------

def test_prazdna_zavorka_se_ignoruje() -> None:
    result = scan_body(text_paragraph("[] [ ] [x]"))
    assert [p.raw for p in result.placeholders] == ["[x]"]


def test_prilis_dlouha_zavorka_se_ignoruje() -> None:
    result = scan_body(text_paragraph("[" + "a" * 401 + "]"))
    assert result.placeholders == []
    result = scan_body(text_paragraph("[" + "a" * 400 + "]"))
    assert len(result.placeholders) == 1


def test_varianty_podle_mezera_lomitko_mezera() -> None:
    assert split_options("Česká republika / Slovenská republika") == (
        "Česká republika",
        "Slovenská republika",
    )
    assert split_options("LEGO Holding A/S") == ()
    assert split_options("a / b / c") == ("a", "b", "c")
    assert split_options("a /  / c") == ()
    assert split_options("bez lomítka") == ()


def test_options_v_placeholderu() -> None:
    (placeholder,) = scan_body(
        text_paragraph("[převedli na LEGO Holding A/S / zrušili registraci]")
    ).placeholders
    assert placeholder.options == (
        "převedli na LEGO Holding A/S",
        "zrušili registraci",
    )
    assert placeholder.is_choice


def test_kontext_oznaci_hodnotu_a_zkrati_text() -> None:
    text = "x" * 300 + " [Jméno] " + "y" * 300
    (placeholder,) = scan_body(text_paragraph(text)).placeholders
    assert "»[Jméno]«" in placeholder.context
    assert len(placeholder.context) <= 210
    assert placeholder.context.startswith("…")
    assert placeholder.context.endswith("…")


def test_kontext_kratkeho_odstavce_je_cely() -> None:
    assert build_context("Vážený [Jan], dobrý den.", 7, 12) == "Vážený »[Jan]«, dobrý den."


def test_id_je_deterministicke_a_odlisne() -> None:
    body = text_paragraph("[A] a [B]")
    first = scan_body(body)
    second = scan_body(body)
    assert [p.id for p in first.placeholders] == [p.id for p in second.placeholders]
    assert first.placeholders[0].id != first.placeholders[1].id
    assert all(p.id.startswith("ph_") and len(p.id) == 15 for p in first.placeholders)
    assert all(p.id.startswith("pg_") and len(p.id) == 15 for p in first.paragraphs)


def test_placeholder_odkazuje_na_svuj_odstavec() -> None:
    result = scan_body(text_paragraph("první") + text_paragraph("druhý [X]"))
    (placeholder,) = result.placeholders
    paragraph_info = result.paragraph(placeholder.paragraph_id)
    assert paragraph_info is not None
    assert paragraph_info.text == "druhý [X]"
    assert paragraph_info.order == 1


def test_serializace_vysledku_prezije_kolecko() -> None:
    from dlg.models import ScanResult

    result = scan_body(text_paragraph("[A / B]"))
    restored = ScanResult.from_dict(result.to_dict())
    assert restored.placeholders == result.placeholders
    assert restored.paragraphs == result.paragraphs


def test_scan_prijme_cestu_i_bajty(tmp_path) -> None:
    data = make_docx(document_xml(text_paragraph("[X]")))
    path = tmp_path / "sablona.docx"
    path.write_bytes(data)
    from_bytes = scan_docx(data)
    from_path = scan_docx(path)
    from_str = scan_docx(str(path))
    assert [p.id for p in from_bytes.placeholders] == [p.id for p in from_path.placeholders]
    assert [p.id for p in from_str.placeholders] == [p.id for p in from_path.placeholders]


def test_prazdny_dokument_bez_odstavcu() -> None:
    result = scan_body("")
    assert result.placeholders == []
    assert result.paragraphs == []


def test_analyse_part_drzi_mapu_znaku_na_bajty() -> None:
    data = document_xml(paragraph(run("["), run("Jan"), run("]")))
    view = analyse_part("word/document.xml", data)
    (hit,) = view.hits
    segments = hit.paragraph.segments(hit.start, hit.end)
    assert len(segments) == 3
    reconstructed = "".join(
        data[segment.byte_start : segment.byte_end].decode("utf-8") for segment in segments
    )
    assert reconstructed == "[Jan]"


# ---------------------------------------------------------------------------
# balíček od python-docx
# ---------------------------------------------------------------------------

def test_scan_balicku_od_python_docx() -> None:
    result = scan_docx(docx_via_python_docx())
    kinds = {(p.kind, p.raw) for p in result.placeholders}
    assert ("sdt", "6. září 2026") in kinds
    assert ("bracket", "[Jan Novák]") in kinds
    assert ("mustache", "{{ domena }}") in kinds
    assert ("bracket", "[Petr Novák]") in kinds
    assert ("bracket", "[Česká republika / Slovenská republika]") in kinds
    in_table = [p for p in result.paragraphs if p.in_table]
    assert len(in_table) == 2


# ---------------------------------------------------------------------------
# reálná šablona
# ---------------------------------------------------------------------------

@requires_sample
def test_realna_sablona_ma_ocekavane_placeholdery(sample_docx_path) -> None:
    result = scan_docx(sample_docx_path)
    raws = [p.raw for p in result.placeholders]
    assert "[Jan Novák]" in raws  # jméno držitele
    assert "[Nová 1]" in raws  # ulice
    assert "[110 00]" in raws  # PSČ
    assert "[Česká republika / Slovenská republika]" in raws  # země, 2 varianty
    assert "[●]" in raws  # doménové jméno
    variant = [p for p in result.placeholders if p.raw.startswith("[zrušili registraci")]
    assert len(variant) == 1
    assert len(variant[0].options) == 2
    assert variant[0].options[1].endswith("provedení takového převodu")
    country = [p for p in result.placeholders if p.raw.startswith("[Česká")][0]
    assert country.options == ("Česká republika", "Slovenská republika")
    dates = [p for p in result.placeholders if p.kind == "sdt"]
    assert len(dates) == 1
    assert "2026" in dates[0].raw
    assert len(result.placeholders) == 7
    assert result.placeholders[0].order == 0
    assert [p.order for p in result.placeholders] == list(range(7))


@requires_sample
def test_realna_sablona_neskenuje_glossary(sample_docx_path) -> None:
    package = DocxPackage.open(sample_docx_path)
    assert "word/glossary/document.xml" in package.names()
    assert "word/glossary/document.xml" not in package.scan_parts()
    assert all(
        p.part not in ("word/glossary/document.xml",)
        for p in scan_docx(sample_docx_path).placeholders
    )
