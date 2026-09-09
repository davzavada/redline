"""Generátory testovacích .docx.

Dvě cesty, obě se hodí:

* :func:`make_docx` — ručně poskládaný minimální balíček, kde máme nad XML
  úplnou kontrolu (samouzavírací značky, atributy s ``>``, textová pole,
  ``mc:AlternateContent``, glossary, uložené obrázky bez komprese).
* :func:`docx_via_python_docx` — balíček vyrobený knihovnou ``python-docx``
  (vývojová závislost), takže odpovídá tomu, co doopravdy vyrábí Word.

Modul je záměrně bez závislosti na ``dlg`` — testovaný kód si nesmí připravovat
vlastní vstupy.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Iterable, Mapping, Sequence

W_NS = (
    'xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:w10="urn:schemas-microsoft-com:office:word" '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'mc:Ignorable="w14 wps"'
)

XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

_SECT_PR = (
    "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/>"
    "<w:pgMar w:top=\"1417\" w:right=\"1417\" w:bottom=\"1417\" w:left=\"1417\" "
    "w:header=\"708\" w:footer=\"708\" w:gutter=\"0\"/></w:sectPr>"
)

_RELS = (
    XML_DECL
    + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)

_CONTENT_TYPE_BY_PART = {
    "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "header": "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml",
    "footer": "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
    "footnotes": "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
    "endnotes": "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml",
    "styles": "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
}


# ---------------------------------------------------------------------------
# stavební kameny XML
# ---------------------------------------------------------------------------

def run(
    text: str,
    *,
    highlight: str | None = None,
    preserve: bool = False,
    rpr_extra: str = "",
    raw_text: bool = False,
) -> str:
    """Jeden ``w:r`` s jedním ``w:t``."""

    props = ""
    if highlight or rpr_extra:
        inner = ""
        if highlight:
            inner += f'<w:highlight w:val="{highlight}"/>'
        inner += rpr_extra
        props = f"<w:rPr>{inner}</w:rPr>"
    space = ' xml:space="preserve"' if preserve else ""
    body = text if raw_text else escape(text)
    return f"<w:r>{props}<w:t{space}>{body}</w:t></w:r>"


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def paragraph(*children: str, style: str | None = None, ppr_extra: str = "") -> str:
    """``w:p`` z hotových běhů."""

    props = ""
    if style or ppr_extra:
        inner = f'<w:pStyle w:val="{style}"/>' if style else ""
        props = f"<w:pPr>{inner}{ppr_extra}</w:pPr>"
    return f'<w:p w14:paraId="11111111">{props}{"".join(children)}</w:p>'


def text_paragraph(text: str, **kwargs: object) -> str:
    """Odstavec s jediným během."""

    return paragraph(run(text, **kwargs))  # type: ignore[arg-type]


def sdt_date(text: str, *, highlight: str | None = "yellow") -> str:
    """Blokový ``w:sdt`` s datem — přesně jako datum v reálné šabloně."""

    return (
        "<w:sdt><w:sdtPr>"
        '<w:rPr><w:highlight w:val="yellow"/></w:rPr>'
        '<w:alias w:val="Date"/><w:tag w:val="Date"/><w:id w:val="519664440"/>'
        '<w:date w:fullDate="2026-09-06T00:00:00Z">'
        '<w:dateFormat w:val="d. MMMM yyyy"/><w:lid w:val="cs-CZ"/>'
        "</w:date></w:sdtPr><w:sdtEndPr/><w:sdtContent>"
        + paragraph(run(text, highlight=highlight))
        + "</w:sdtContent></w:sdt>"
    )


def sdt_inline(text: str, *, showing_placeholder: bool = True) -> str:
    """Vnořený ``w:sdt`` uvnitř odstavce."""

    marker = "<w:showingPlcHdr/>" if showing_placeholder else ""
    return (
        f"<w:sdt><w:sdtPr>{marker}<w:alias w:val=\"Pole\"/></w:sdtPr>"
        "<w:sdtContent>" + run(text) + "</w:sdtContent></w:sdt>"
    )


def table(rows: Sequence[Sequence[Sequence[str]]]) -> str:
    """``w:tbl`` — řádky × buňky × odstavce (už jako XML)."""

    out = ["<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w=\"4000\"/></w:tblGrid>"]
    for row in rows:
        out.append("<w:tr>")
        for cell in row:
            out.append("<w:tc><w:tcPr/>" + "".join(cell) + "</w:tc>")
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def textbox(*paragraphs: str) -> str:
    """Odstavec s textovým polem — uvnitř jsou další ``w:p``."""

    inner = "".join(paragraphs)
    return (
        "<w:p><w:r><mc:AlternateContent><mc:Choice Requires=\"wps\">"
        "<w:drawing><wp:inline><wp:extent cx=\"1\" cy=\"1\"/>"
        "<wps:wsp><wps:txbx><w:txbxContent>" + inner + "</w:txbxContent></wps:txbx>"
        "</wps:wsp></wp:inline></w:drawing></mc:Choice>"
        "<mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent>"
        + inner
        + "</w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>"
    )


def document_xml(body: str, *, sect_pr: bool = True) -> bytes:
    """Zabalí tělo do celé části ``word/document.xml``."""

    tail = _SECT_PR if sect_pr else ""
    return (
        XML_DECL
        + f"<w:document {W_NS}>"
        + "<!-- ručně psaná testovací část -->"
        + f"<w:body>{body}{tail}</w:body></w:document>"
    ).encode("utf-8")


def header_xml(body: str, tag: str = "hdr") -> bytes:
    return (
        XML_DECL + f"<w:{tag} {W_NS}>{body}</w:{tag}>"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# balení do ZIPu
# ---------------------------------------------------------------------------

def _content_types(names: Iterable[str]) -> bytes:
    overrides = []
    for name in names:
        base = name.rsplit("/", 1)[-1].removesuffix(".xml")
        kind = base.rstrip("0123456789")
        content_type = _CONTENT_TYPE_BY_PART.get(kind)
        if content_type and name.endswith(".xml"):
            overrides.append(f'<Override PartName="/{name}" ContentType="{content_type}"/>')
    return (
        XML_DECL
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        + "".join(overrides)
        + "</Types>"
    ).encode("utf-8")


# 1×1 průhledný PNG — ukládá se bez komprese, ať je vidět, že přežije přebalení
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000100ffff0300000600"
    "05572bd8f60000000049454e44ae426082"
)


def make_docx(
    document: bytes | str,
    extra_parts: Mapping[str, bytes | str] | None = None,
    *,
    with_glossary: bool = True,
    with_image: bool = True,
    with_directory_entry: bool = True,
) -> bytes:
    """Poskládá minimální, ale platný .docx balíček."""

    if isinstance(document, str):
        document = document.encode("utf-8")
    parts: dict[str, bytes] = {"word/document.xml": document}
    for name, data in (extra_parts or {}).items():
        parts[name] = data.encode("utf-8") if isinstance(data, str) else data
    if with_glossary:
        parts.setdefault(
            "word/glossary/document.xml",
            document_xml(text_paragraph("[Glossary nemá být skenováno]")),
        )
    if with_image:
        parts.setdefault("word/media/image1.png", PNG_1PX)

    document_rels = [
        '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/%s" Target="%s"/>'
        % (
            index + 10,
            name.rsplit("/", 1)[-1].removesuffix(".xml").rstrip("0123456789"),
            name.split("/", 1)[1],
        )
        for index, name in enumerate(sorted(parts))
        if name.startswith("word/") and name != "word/document.xml" and name.endswith(".xml")
        and not name.startswith("word/glossary/")
    ]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if with_directory_entry:
            info = zipfile.ZipInfo("word/")
            info.external_attr = (0o40755 << 16) | 0x10
            archive.writestr(info, b"")
        archive.writestr("[Content_Types].xml", _content_types(parts))
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr(
            "word/_rels/document.xml.rels",
            XML_DECL
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(document_rels)
            + "</Relationships>",
        )
        for name in sorted(parts):
            data = parts[name]
            compress = zipfile.ZIP_STORED if name.endswith(".png") else zipfile.ZIP_DEFLATED
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 9, 12, 0, 0))
            info.compress_type = compress
            info.external_attr = 0o600 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def replace_part(source: bytes, name: str, data: bytes | str) -> bytes:
    """Vymění jednu část v hotovém balíčku (bez použití testovaného kódu)."""

    if isinstance(data, str):
        data = data.encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as src:
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
            for info in src.infolist():
                payload = data if info.filename == name else src.read(info.filename)
                out.writestr(info.filename, payload)
    return buffer.getvalue()


def part_bytes(source: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        return archive.read(name)


def part_text(source: bytes, name: str = "word/document.xml") -> str:
    return part_bytes(source, name).decode("utf-8")


# ---------------------------------------------------------------------------
# varianta přes python-docx (vývojová závislost)
# ---------------------------------------------------------------------------

def docx_via_python_docx() -> bytes:
    """Realistický balíček od ``python-docx``: text, zvýraznění, tabulka, sdt.

    Datum v ``w:sdt`` python-docx neumí, doplní se ručním splicem do těla.
    """

    import docx  # noqa: PLC0415 - jen vývojová závislost testů
    from docx.enum.text import WD_COLOR_INDEX

    document = docx.Document()
    document.add_paragraph("SDT_MARKER")
    first = document.add_paragraph()
    first.add_run("Vážený pane ")
    first.add_run("[")
    first.add_run("Jan Novák").font.highlight_color = WD_COLOR_INDEX.YELLOW
    first.add_run("],")
    document.add_paragraph("obracíme se na Vás ve věci domény {{ domena }}.")
    document.add_paragraph("Volitelný odstavec k vypuštění.")
    table_obj = document.add_table(rows=1, cols=2)
    table_obj.cell(0, 0).paragraphs[0].add_run("Držitel: [Petr Novák]")
    table_obj.cell(0, 1).paragraphs[0].add_run("Země: [Česká republika / Slovenská republika]")

    buffer = io.BytesIO()
    document.save(buffer)
    raw = buffer.getvalue()

    # odstavec s markerem vyměníme za blokový w:sdt s datem
    body = part_text(raw)
    pattern = re.compile(
        r"<w:p\b[^>]*>(?:(?!</w:p>).)*?SDT_MARKER(?:(?!</w:p>).)*?</w:p>", re.S
    )
    body, count = pattern.subn(lambda _match: sdt_date("6. září 2026"), body, count=1)
    if count != 1:  # pragma: no cover - pojistka proti změně python-docx
        raise AssertionError("Značku SDT_MARKER se nepodařilo najít.")
    return replace_part(raw, "word/document.xml", body)
