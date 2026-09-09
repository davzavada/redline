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
from .scan import Hit, ParagraphView, PartView, analyse_package, analyse_part, preview_text
from .xmlsplice import Node, Splicer

__all__ = ["fill_docx", "extract_text", "render_value"]

_LINE_BREAK = '</w:t><w:br/><w:t xml:space="preserve">'
_ALLOWED_CONTROL = ("\t", "\n")


def _clean(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(ch for ch in text if ch >= " " or ch in _ALLOWED_CONTROL)


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


def _write_value(state: _PartState, hit: Hit, value: str, *, clear_highlight: bool) -> None:
    segments = hit.paragraph.segments(hit.start, hit.end)
    if not segments:
        return
    first = segments[0]
    _unwrap_sdt(state, first.ref.node)
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


def _drop_paragraphs(
    view: PartView,
    state: _PartState,
    drop_ids: set[str],
    report: FillReport,
) -> None:
    selected = [p for p in view.paragraphs if p.info.id in drop_ids]
    if not selected:
        return
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
    for paragraph in selected:
        node = paragraph.node
        state.dropped_paragraphs.add(node.start)
        report.dropped_paragraphs.append(paragraph.info.id)
        cell = node.closest("w:tc")
        keep_empty = False
        if cell is not None:
            siblings = [child for child in cell.children if child.tag == "w:p"]
            if siblings and all(child.start in chosen for child in siblings):
                keep_empty = node.start == siblings[-1].start
        if keep_empty:
            props = node.child("w:pPr")
            start = props.end if props is not None else node.inner_start
            end = node.inner_end
            if end > start:
                state.splicer.delete(start, end)
            state.mark_deleted(start, end)
            report.warnings.append(
                f"Odstavec „{_short(paragraph.info.text)}“ je poslední v buňce tabulky, "
                "proto byl jen vyprázdněn."
            )
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
                report.warnings.append(
                    f"Hodnota pro „{_short(placeholder.inner, 40)}“ se nepoužila — "
                    "odstavec byl z dokumentu vypuštěn."
                )
            continue
        if not value.strip():
            report.unfilled.append(placeholder.id)
            if keep_unfilled:
                continue
            value = ""
        else:
            report.filled.append(placeholder.id)
        _write_value(state, hit, value, clear_highlight=clear_highlight)
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
