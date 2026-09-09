"""Doplnění hodnot do .docx bajtovým splicem.

Vše se řeší jako editace nad původními bajty XML (viz :mod:`.xmlsplice`), nikdy
přeserializováním stromu — vygenerovaný dokument se od šablony liší jen v místech,
která jsme opravdu změnili.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from ..models import FillReport
from . import xmlsplice
from .parts import MAIN_PART, DocxPackage
from .scan import (
    ALTERNATE_BRANCHES,
    Hit,
    ParagraphView,
    PartView,
    analyse_package,
    analyse_part,
    preview_text,
)
from .xmlsplice import Node, Splicer

__all__ = ["fill_docx", "extract_text", "render_value"]

_LINE_BREAK = '</w:t><w:br/><w:t xml:space="preserve">'
_ALLOWED_CONTROL = ("\t", "\n")


def _xml_safe(ch: str) -> bool:
    """Smí znak podle produkce ``Char`` z XML 1.0 vůbec být v dokumentu?

    Word odmítne otevřít .docx, ve kterém je znak mimo tento rozsah — a text
    s U+FFFE nebo s osamoceným surrogátem se do XML nedostane ani jako entita.
    Hodnoty přitom chodí z formuláře, z profilu i z historie, kam se takový
    znak snadno dostane přes schránku.
    """

    code = ord(ch)
    return (
        code in (0x09, 0x0A)  # \t a \n (\r se výš normalizuje na \n)
        or 0x20 <= code <= 0xD7FF  # pod surrogáty
        or 0xE000 <= code <= 0xFFFD  # nad surrogáty, bez U+FFFE a U+FFFF
        or code >= 0x10000  # astrální roviny (emoji apod.)
    )


def _clean(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(ch for ch in text if _xml_safe(ch))


def render_value(value: str) -> bytes:
    """Připraví hodnotu k vložení mezi ``<w:t>`` a ``</w:t>``."""

    escaped = xmlsplice.escape_text(_clean(value))
    lines = escaped.split("\n")
    payload = lines[0]
    for line in lines[1:]:
        payload += _LINE_BREAK + line
    return payload.encode("utf-8")


@dataclass
class _PartState:
    """Co už jsme v této části udělali — aby se editace neopakovaly."""

    splicer: Splicer
    deleted: list[tuple[int, int]] = field(default_factory=list)
    preserved: set[int] = field(default_factory=set)
    cleared: set[int] = field(default_factory=set)
    unwrapped: set[int] = field(default_factory=set)
    dropped_paragraphs: set[int] = field(default_factory=set)

    def mark_deleted(self, start: int, end: int) -> None:
        self.deleted.append((start, end))

    def inside_deleted(self, start: int, end: int) -> bool:
        return any(lo <= start and end <= hi for lo, hi in self.deleted)


# ---------------------------------------------------------------------------
# jednotlivé úpravy
# ---------------------------------------------------------------------------

def _short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clear_highlight(state: _PartState, paragraph: ParagraphView, hit: Hit) -> None:
    targets: list[Node] = []
    for run in paragraph.runs(hit.start, hit.end):
        props = run.child("w:rPr")
        if props is not None:
            targets.extend(props.iter_children("w:highlight"))
    paragraph_props = paragraph.node.child("w:pPr")
    if paragraph_props is not None:
        run_props = paragraph_props.child("w:rPr")
        if run_props is not None:
            targets.extend(run_props.iter_children("w:highlight"))
    for element in targets:
        if element.start in state.cleared:
            continue
        if state.inside_deleted(element.start, element.end):
            continue
        state.cleared.add(element.start)
        state.splicer.remove_element(element)


def _unwrap_sdt(state: _PartState, node: Node) -> None:
    """Rozbalí všechny nadřazené ``w:sdt`` — hodnota tak zůstane statická."""

    for sdt in [ancestor for ancestor in node.ancestors() if ancestor.tag == "w:sdt"]:
        if sdt.start in state.unwrapped:
            continue
        content = sdt.child("w:sdtContent")
        if content is None or content.self_closing:
            continue
        state.unwrapped.add(sdt.start)
        state.splicer.delete(sdt.start, content.open_end)
        state.splicer.delete(content.inner_end, sdt.end)
        state.mark_deleted(sdt.start, content.open_end)
        state.mark_deleted(content.inner_end, sdt.end)


def _needs_preserve(text: str) -> bool:
    return bool(text) and (text[0].isspace() or text[-1].isspace())


def _drop_sdt_extra(state: _PartState, hit: Hit, report: FillReport) -> None:
    """Zahodí zbývající odstavce víceodstavcového ``w:sdtContent``.

    Placeholder kotví v prvním odstavci content-controlu. Po rozbalení ``w:sdt``
    by se ze zbytku (typicky „Klikněte a zvolte…“) stal natvrdo zapsaný text
    dopisu, proto ho odstraníme a napíšeme to do protokolu.
    """

    for paragraph in hit.sdt_extra:
        node = paragraph.node
        if node.start in state.dropped_paragraphs:
            continue
        if state.inside_deleted(node.start, node.end):
            continue
        state.dropped_paragraphs.add(node.start)
        state.splicer.delete(node.start, node.end)
        state.mark_deleted(node.start, node.end)
        if paragraph.info.id not in report.dropped_paragraphs:
            report.dropped_paragraphs.append(paragraph.info.id)


def _write_value(
    state: _PartState,
    hit: Hit,
    value: str,
    *,
    clear_highlight: bool,
    report: FillReport,
) -> None:
    segments = hit.paragraph.segments(hit.start, hit.end)
    if not segments:
        return
    first = segments[0]
    _unwrap_sdt(state, first.ref.node)
    _drop_sdt_extra(state, hit, report)
    state.splicer.replace(first.byte_start, first.byte_end, render_value(value))
    for segment in segments[1:]:
        state.splicer.delete(segment.byte_start, segment.byte_end)
    for position, segment in enumerate(segments):
        ref = segment.ref
        remaining = ref.text[: segment.start]
        if position == 0:
            remaining += _clean(value)
        remaining += ref.text[segment.end :]
        if not _needs_preserve(remaining):
            continue
        if ref.node.start in state.preserved:
            continue
        if state.inside_deleted(ref.node.start, ref.node.end):
            continue
        state.preserved.add(ref.node.start)
        state.splicer.insert_attr(ref.node, "xml:space", "preserve")
    if clear_highlight:
        _clear_highlight(state, hit.paragraph, hit)


#: Kontejnery, které podle OOXML musí obsahovat aspoň jeden ``w:p``.
#: Kdyby v nich žádný nezůstal, Word soubor prohlásí za poškozený.
_KEEP_ONE_PARAGRAPH = ("w:tc", "w:txbxContent")


def _keep_one_container(node: Node) -> Node | None:
    """Nejbližší předek, který musí obsahovat aspoň jeden ``w:p``."""

    for ancestor in node.ancestors():
        if ancestor.tag in _KEEP_ONE_PARAGRAPH:
            return ancestor
    return None


def _own_paragraphs(container: Node) -> list[Node]:
    """Odstavce patřící přímo tomuto kontejneru (ne vnořené buňce/rámečku).

    Hledá se mezi *potomky*, ne mezi přímými dětmi — jediný odstavec buňky
    bývá zabalený v blokovém ``w:sdt`` nebo v ``mc:AlternateContent``.
    """

    return [
        child
        for child in container.iter_descendants("w:p")
        if _keep_one_container(child) is container
    ]


def _alternate_twins(
    paragraph: ParagraphView, by_node: Mapping[int, ParagraphView]
) -> list[ParagraphView]:
    """Tentýž odstavec v ostatních větvích ``mc:AlternateContent``.

    Word píše obsah textového pole dvakrát (``mc:Choice`` i ``mc:Fallback``).
    Vypustit jen jednu větev by dokument rozdvojilo — podle toho, kdo ho
    otevře, by text buď byl, nebo nebyl.
    """

    branch: Node | None = None
    for ancestor in paragraph.node.ancestors():
        if ancestor.tag in ALTERNATE_BRANCHES:
            branch = ancestor
            break
    if branch is None or branch.parent is None:
        return []
    parent = branch.parent
    if parent.tag != "mc:AlternateContent":
        return []
    own = [node.start for node in branch.iter_descendants("w:p")]
    try:
        position = own.index(paragraph.node.start)
    except ValueError:  # pragma: no cover - nemělo by nastat
        return []
    out: list[ParagraphView] = []
    for sibling in parent.children:
        if sibling is branch or sibling.tag not in ALTERNATE_BRANCHES:
            continue
        others = list(sibling.iter_descendants("w:p"))
        if len(others) != len(own):
            continue
        twin = by_node.get(others[position].start)
        if twin is not None:
            out.append(twin)
    return out


def _drop_paragraphs(
    view: PartView,
    state: _PartState,
    drop_ids: set[str],
    report: FillReport,
) -> None:
    selected = [p for p in view.paragraphs if p.info.id in drop_ids]
    if not selected:
        return
    by_node = {paragraph.node.start: paragraph for paragraph in view.paragraphs}
    # obě větve mc:AlternateContent musí zmizet společně
    seen = {paragraph.node.start for paragraph in selected}
    for paragraph in list(selected):
        for twin in _alternate_twins(paragraph, by_node):
            if twin.node.start not in seen:
                seen.add(twin.node.start)
                selected.append(twin)
    # odstavec zanořený v jiném mazaném odstavci se řeší sám sebou
    selected = [
        paragraph
        for paragraph in selected
        if not any(
            other is not paragraph and other.node.contains(paragraph.node)
            for other in selected
        )
    ]
    chosen = {paragraph.node.start for paragraph in selected}
    reported: set[str] = set()
    for paragraph in selected:
        node = paragraph.node
        state.dropped_paragraphs.add(node.start)
        if paragraph.info.id in drop_ids:
            report.dropped_paragraphs.append(paragraph.info.id)
        props = node.child("w:pPr")
        ends_section = props is not None and props.child("w:sectPr") is not None
        container = _keep_one_container(node)
        keep_empty = False
        message = ""
        if container is not None and not any(
            other is not paragraph and other.node.contains(container)
            for other in selected
        ):
            own = _own_paragraphs(container)
            if own and all(child.start in chosen for child in own):
                keep_empty = node.start == own[-1].start
                if keep_empty:
                    where = (
                        "v buňce tabulky"
                        if container.tag == "w:tc"
                        else "v textovém poli"
                    )
                    message = (
                        f"Odstavec „{_short(paragraph.info.text)}“ je poslední "
                        f"{where}, proto byl jen vyprázdněn."
                    )
        if not keep_empty and ends_section:
            # Se značkou odstavce by zmizel i w:sectPr, a s ním vzhled stránky,
            # záhlaví i zápatí celé předchozí sekce. Radši prázdný odstavec.
            keep_empty = True
            message = (
                f"Odstavec „{_short(paragraph.info.text)}“ ukončuje sekci dokumentu "
                "(vlastní vzhled stránky, záhlaví a zápatí), proto byl jen "
                "vyprázdněn a nastavení sekce zůstalo zachováno."
            )
        if keep_empty:
            start = props.end if props is not None else node.inner_start
            end = node.inner_end
            if end > start:
                state.splicer.delete(start, end)
            state.mark_deleted(start, end)
            if message and message not in reported:
                reported.add(message)
                report.warnings.append(message)
        else:
            state.splicer.delete(node.start, node.end)
            state.mark_deleted(node.start, node.end)


def _fill_part(
    view: PartView,
    values: Mapping[str, str],
    drop_ids: set[str],
    *,
    clear_highlight: bool,
    keep_unfilled: bool,
    report: FillReport,
) -> bytes | None:
    state = _PartState(splicer=Splicer(view.data))
    _drop_paragraphs(view, state, drop_ids, report)
    for hit in view.hits:
        placeholder = hit.placeholder
        raw_value = values.get(placeholder.id)
        value = "" if raw_value is None else str(raw_value)
        node = hit.paragraph.node
        if node.start in state.dropped_paragraphs or state.inside_deleted(node.start, node.end):
            if value.strip():
                message = (
                    f"Hodnota pro „{_short(placeholder.inner, 40)}“ se nepoužila — "
                    "odstavec byl z dokumentu vypuštěn."
                )
                # obě větve mc:AlternateContent jsou totéž místo dokumentu
                if message not in report.warnings:
                    report.warnings.append(message)
            continue
        if not value.strip():
            report.unfilled.append(placeholder.id)
            if keep_unfilled:
                continue
            value = ""
        else:
            report.filled.append(placeholder.id)
        _write_value(
            state, hit, value, clear_highlight=clear_highlight, report=report
        )
    if not len(state.splicer):
        return None
    return state.splicer.apply()


# ---------------------------------------------------------------------------
# veřejné API
# ---------------------------------------------------------------------------

def fill_docx(
    src: "Path | str | bytes",
    values: Mapping[str, str],
    *,
    drop_paragraphs: Iterable[str] = (),
    clear_highlight: bool = True,
    keep_unfilled: bool = True,
) -> tuple[bytes, FillReport]:
    """Doplní hodnoty do šablony a vrátí hotový .docx i protokol.

    ``values`` je mapa ``Placeholder.id`` → hodnota. Prázdná hodnota (nebo hodnota
    jen z bílých znaků) se bere jako *nevyplněno*: podle ``keep_unfilled`` se
    placeholder buď nechá beze změny, nebo se z dokumentu odstraní.
    """

    package = DocxPackage.open(src)
    views = analyse_package(package)
    report = FillReport()

    known_placeholders = {hit.placeholder.id for view in views for hit in view.hits}
    for key, value in values.items():
        if key not in known_placeholders and str(value or "").strip():
            report.warnings.append(
                f"Hodnota pro neznámý placeholder „{key}“ byla ignorována."
            )
    drop_ids = {str(item) for item in drop_paragraphs}
    known_paragraphs = {p.info.id for view in views for p in view.paragraphs}
    for paragraph_id in sorted(drop_ids - known_paragraphs):
        report.warnings.append(
            f"Odstavec s id „{paragraph_id}“ v šabloně není, vypuštění se přeskočilo."
        )

    modified: dict[str, bytes] = {}
    for view in views:
        data = _fill_part(
            view,
            values,
            drop_ids,
            clear_highlight=clear_highlight,
            keep_unfilled=keep_unfilled,
            report=report,
        )
        if data is not None:
            modified[view.part] = data
    return package.rebuild(modified), report


def extract_text(path_or_bytes: "Path | str | bytes") -> str:
    """Prostý text hlavního dokumentu pro náhled (odstavce oddělené ``\\n``)."""

    package = DocxPackage.open(path_or_bytes)
    return preview_text(analyse_part(MAIN_PART, package.read(MAIN_PART)))
