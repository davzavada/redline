"""Testy bajtově přesného parsování a splicování XML."""

from __future__ import annotations

import xml.dom.minidom

import pytest

from dlg.docx_engine import xmlsplice
from dlg.docx_engine.xmlsplice import Splicer, XmlError, decode_chars, parse


def node_bytes(doc, node) -> bytes:
    return doc.data[node.start : node.end]


# ---------------------------------------------------------------------------
# parsování a offsety
# ---------------------------------------------------------------------------

def test_offsety_ukazuji_presne_na_znacky() -> None:
    data = b'<w:p><w:r><w:t>Ahoj</w:t></w:r></w:p>'
    doc = parse(data)
    paragraph = doc.root
    assert paragraph is not None
    assert paragraph.tag == "w:p"
    text_node = doc.find_all("w:t")[0]
    assert data[text_node.start : text_node.open_end] == b"<w:t>"
    assert data[text_node.inner_start : text_node.inner_end] == b"Ahoj"
    assert data[text_node.end - 6 : text_node.end] == b"</w:t>"
    assert node_bytes(doc, text_node) == b"<w:t>Ahoj</w:t>"


def test_atribut_obsahujici_vetsitko_nerozbije_parsing() -> None:
    data = '<r><a k="x>y" j=\'p>q\'>text</a><b k="&gt;"/></r>'.encode("utf-8")
    doc = parse(data)
    a = doc.find_all("a")[0]
    assert a.get("k") == "x>y"
    assert a.get("j") == "p>q"
    assert doc.text_of(a) == "text"
    b = doc.find_all("b")[0]
    assert b.self_closing is True
    assert b.get("k") == ">"


def test_samouzavirajici_element_ma_prazdny_obsah() -> None:
    data = b'<r><w:br/><w:t>A</w:t><w:tab /></r>'
    doc = parse(data)
    br = doc.find_all("w:br")[0]
    tab = doc.find_all("w:tab")[0]
    assert br.self_closing and tab.self_closing
    assert br.inner_start == br.inner_end == br.end == br.open_end
    assert node_bytes(doc, br) == b"<w:br/>"
    assert node_bytes(doc, tab) == b"<w:tab />"


def test_utf8_vicebajtove_znaky_maji_bajtove_offsety() -> None:
    text = "čeština ● a\xa0mezera ř"
    data = f"<w:t>{text}</w:t>".encode("utf-8")
    doc = parse(data)
    node = doc.root
    assert node is not None
    assert doc.text_of(node) == text
    # inner_end je bajtový offset, ne znakový
    assert node.inner_end - node.inner_start == len(text.encode("utf-8"))
    assert node.inner_end - node.inner_start > len(text)


def test_decode_chars_mapuje_znaky_na_bajty() -> None:
    data = "<t>a&amp;b č&#x2022;</t>".encode("utf-8")
    doc = parse(data)
    node = doc.root
    assert node is not None
    text, offsets = decode_chars(data, node.inner_start, node.inner_end)
    assert text == "a&b č•"
    assert len(offsets) == len(text) + 1
    assert offsets[-1] == node.inner_end
    # entita &amp; zabírá pět bajtů, ale jen jeden znak
    assert offsets[2] - offsets[1] == 5
    # 'č' jsou dva bajty
    assert offsets[5] - offsets[4] == 2
    # každý offset ukazuje na začátek znaku v původních bajtech
    for index, char in enumerate(text):
        chunk = data[offsets[index] : offsets[index + 1]].decode("utf-8")
        assert chunk == char or (chunk.startswith("&") and chunk.endswith(";"))


def test_komentare_a_deklarace_zustavaji_beze_zmeny() -> None:
    data = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        b"<!-- poznamka --><r><!--vnitrni--><a/></r>"
    )
    doc = parse(data)
    splicer = Splicer(data)
    assert splicer.apply() == data
    assert doc.root is not None and doc.root.tag == "r"


def test_neznamy_prefix_a_alternate_content() -> None:
    data = (
        b'<w:p xmlns:w="urn:w" xmlns:mc="urn:mc" xmlns:foo="urn:foo">'
        b"<mc:AlternateContent><mc:Choice><foo:bar>x</foo:bar></mc:Choice>"
        b"<mc:Fallback><w:t>y</w:t></mc:Fallback></mc:AlternateContent></w:p>"
    )
    doc = parse(data)
    tags = [node.tag for node in doc.iter()]
    assert "mc:AlternateContent" in tags
    assert "foo:bar" in tags
    assert Splicer(data).apply() == data


def test_prazdny_dokument_je_chyba() -> None:
    with pytest.raises(XmlError):
        parse(b"")
    with pytest.raises(XmlError):
        parse(b"<w:p>")


def test_dokument_bez_obsahu_se_naparsuje() -> None:
    data = b'<w:document xmlns:w="urn:w"><w:body/></w:document>'
    doc = parse(data)
    assert doc.root is not None
    assert doc.find_all("w:p") == []
    assert doc.find_all("w:body")[0].self_closing


def test_vnorene_odstavce_v_textovem_poli() -> None:
    data = (
        b"<w:p><w:r><w:txbxContent><w:p><w:r><w:t>vnitrni</w:t></w:r></w:p>"
        b"</w:txbxContent></w:r><w:r><w:t>vnejsi</w:t></w:r></w:p>"
    )
    doc = parse(data)
    paragraphs = doc.find_all("w:p")
    assert len(paragraphs) == 2
    outer, inner = paragraphs
    assert outer.contains(inner)
    assert inner.closest("w:p") is outer
    inner_text, outer_text = doc.find_all("w:t")
    assert inner_text.closest("w:p") is inner
    assert outer_text.closest("w:p") is outer


def test_parse_odmitne_text() -> None:
    with pytest.raises(XmlError):
        parse("<r/>")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# splice
# ---------------------------------------------------------------------------

def test_replace_a_delete() -> None:
    data = b"<r><a>1</a><b>2</b></r>"
    doc = parse(data)
    a, b = doc.find_all("a")[0], doc.find_all("b")[0]
    splicer = Splicer(data)
    splicer.replace(a.inner_start, a.inner_end, "X")
    splicer.delete(b.start, b.end)
    assert splicer.apply() == b"<r><a>X</a></r>"


def test_editace_se_aplikuji_od_konce_a_nezmeni_ostatni_offsety() -> None:
    data = "<r><t>ěěě</t><t>ř</t><t>●</t></r>".encode("utf-8")
    doc = parse(data)
    splicer = Splicer(data)
    for node in doc.find_all("t"):
        splicer.replace(node.inner_start, node.inner_end, "ABCDEF")
    assert splicer.apply().decode("utf-8") == "<r><t>ABCDEF</t><t>ABCDEF</t><t>ABCDEF</t></r>"


def test_prekryvajici_editace_skonci_chybou() -> None:
    data = b"<r><t>abcdef</t></r>"
    splicer = Splicer(data)
    splicer.replace(6, 10, b"X")
    splicer.replace(8, 12, b"Y")
    with pytest.raises(XmlError, match="překrývají"):
        splicer.apply()


def test_dva_vkladane_kousky_na_stejnem_miste_zustanou_v_poradi() -> None:
    data = b"<r></r>"
    splicer = Splicer(data)
    splicer.insert(3, b"A")
    splicer.insert(3, b"B")
    assert splicer.apply() == b"<r>AB</r>"


def test_bez_editaci_vrati_puvodni_bajty() -> None:
    data = b'<?xml version="1.0"?><r a="1"/>'
    assert Splicer(data).apply() is data


def test_insert_attr_pridava_i_prepisuje() -> None:
    data = b'<r><w:t>a</w:t><w:t xml:space="collapse">b</w:t><w:t/></r>'
    doc = parse(data)
    first, second, third = doc.find_all("w:t")
    splicer = Splicer(data)
    splicer.insert_attr(first, "xml:space", "preserve")
    splicer.insert_attr(second, "xml:space", "preserve")
    splicer.insert_attr(third, "xml:space", "preserve")
    result = splicer.apply().decode()
    assert '<w:t xml:space="preserve">a</w:t>' in result
    assert '<w:t xml:space="preserve">b</w:t>' in result
    assert '<w:t xml:space="preserve"/>' in result


def test_insert_attr_nic_nedela_kdyz_hodnota_uz_sedi() -> None:
    data = b'<w:t xml:space="preserve">a</w:t>'
    doc = parse(data)
    splicer = Splicer(data)
    assert doc.root is not None
    splicer.insert_attr(doc.root, "xml:space", "preserve")
    assert len(splicer) == 0
    assert splicer.apply() == data


def test_insert_attr_escapuje_hodnotu() -> None:
    data = b"<r/>"
    doc = parse(data)
    splicer = Splicer(data)
    assert doc.root is not None
    splicer.insert_attr(doc.root, "k", 'a "b" & <c>')
    assert splicer.apply() == b'<r k="a &quot;b&quot; &amp; &lt;c&gt;"/>'


def test_escape_text_a_attr() -> None:
    assert xmlsplice.escape_text("a & b < c > d") == "a &amp; b &lt; c &gt; d"
    assert xmlsplice.escape_attr('a"b') == "a&quot;b"
    assert xmlsplice.escape_attr("a\nb") == "a&#10;b"


def test_vysledek_zustava_platnym_xml() -> None:
    data = (
        '<?xml version="1.0" encoding="UTF-8"?><w:p xmlns:w="urn:w">'
        "<w:r><w:t>Jméno</w:t></w:r></w:p>"
    ).encode("utf-8")
    doc = parse(data)
    node = doc.find_all("w:t")[0]
    splicer = Splicer(data)
    splicer.replace(node.inner_start, node.inner_end, "Petr &amp; spol.")
    out = splicer.apply()
    parsed = xml.dom.minidom.parseString(out)
    assert parsed.documentElement.getElementsByTagName("w:t")[0].firstChild.nodeValue == (
        "Petr & spol."
    )


def test_merge_ranges() -> None:
    assert xmlsplice.merge_ranges([(0, 5), (3, 8), (10, 12)]) == [(0, 8), (10, 12)]
    assert xmlsplice.merge_ranges([]) == []


def test_neplatny_rozsah_editace() -> None:
    splicer = Splicer(b"<r/>")
    with pytest.raises(XmlError):
        splicer.replace(3, 1, b"")
    with pytest.raises(XmlError):
        splicer.replace(0, 99, b"")
