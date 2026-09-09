"""Detekce placeholderů v částech .docx.

Text odstavce se skládá ze všech ``w:t`` v pořadí dokumentu; současně se drží
mapa *znak → (uzel w:t, offset uvnitř)*, takže nalezený placeholder jde později
vyplnit bajtovým splicem přesně na svém místě.

Priorita nálezů: ``w:sdt`` > ``{{klic}}`` > ``[…]`` > zvýraznění. Kandidát, který
se překrývá s už přijatým nálezem, se zahodí.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..models import Placeholder, ParagraphInfo, ScanResult
from . import xmlsplice
from .parts import DocxPackage
from .xmlsplice import Document, Node

__all__ = [
    "TextRef",
    "is_fallback_node",
    "Segment",
    "ParagraphView",
    "Hit",
    "PartView",
    "analyse_part",
    "analyse_package",
    "scan_docx",
    "build_context",
    "split_options",
    "preview_text",
]

MUSTACHE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}", re.S)
BRACKET_RE = re.compile(r"\[([^\[\]]{1,400})\]", re.S)

OPTION_SEPARATOR = " / "
CONTEXT_LIMIT = 200
CONTEXT_OPEN = "»"  # »
CONTEXT_CLOSE = "«"  # «
ELLIPSIS = "…"


# ---------------------------------------------------------------------------
# pomocné struktury
# ---------------------------------------------------------------------------

@dataclass
class TextRef:
    """Jeden ``w:t`` a jeho místo v textu odstavce."""

    node: Node
    run: Node | None
    text: str
    offsets: list[int]
    char_start: int

    @property
    def char_end(self) -> int:
        return self.char_start + len(self.text)

    def byte_offset(self, char_index: int) -> int:
        return self.offsets[char_index]


@dataclass
class Segment:
    """Část placeholderu ležící v jednom ``w:t``."""

    ref: TextRef
    start: int  # znakový offset uvnitř ref.text
    end: int

    @property
    def byte_start(self) -> int:
        return self.ref.offsets[self.start]

    @property
    def byte_end(self) -> int:
        return self.ref.offsets[self.end]


@dataclass
class ParagraphView:
    """Odstavec připravený pro hledání i pro vyplňování."""

    part: str
    index: int  # pořadí odstavce uvnitř části
    node: Node
    refs: list[TextRef]
    text: str
    highlight: list[bool]
    info: ParagraphInfo
    #: Odstavec leží v ``mc:Fallback``, ke kterému existuje i ``mc:Choice``.
    #: Word vykreslí jen jednu větev, takže do náhledu ani do nabídky
    #: volitelných odstavců patří jen ta hlavní — vyplňovat se ale musí obě.
    in_fallback: bool = False

    def segments(self, start: int, end: int) -> list[Segment]:
        out: list[Segment] = []
        for ref in self.refs:
            lo = max(start, ref.char_start)
            hi = min(end, ref.char_end)
            if hi > lo:
                out.append(Segment(ref, lo - ref.char_start, hi - ref.char_start))
        return out

    def runs(self, start: int, end: int) -> list[Node]:
        seen: dict[int, Node] = {}
        for segment in self.segments(start, end):
            run = segment.ref.run
            if run is not None:
                seen.setdefault(run.start, run)
        return list(seen.values())


@dataclass
class Hit:
    """Nalezený placeholder i s tím, kde přesně v dokumentu leží."""

    placeholder: Placeholder
    paragraph: ParagraphView
    start: int
    end: int
    sdt: Node | None = None
    #: Další odstavce téhož ``w:sdtContent`` (víceodstavcový content-control).
    #: Kotvou placeholderu je první odstavec; zbytek se při vyplnění zahodí,
    #: jinak by vzorový text šablony zůstal v hotovém dopise.
    sdt_extra: tuple[ParagraphView, ...] = ()


@dataclass
class PartView:
    """Jedna naparsovaná část dokumentu."""

    part: str
    data: bytes
    doc: Document
    paragraphs: list[ParagraphView] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)

    def paragraph_by_id(self, paragraph_id: str) -> ParagraphView | None:
        for paragraph in self.paragraphs:
            if paragraph.info.id == paragraph_id:
                return paragraph
        return None


# ---------------------------------------------------------------------------
# skládání odstavců
# ---------------------------------------------------------------------------

def _run_is_highlighted(run: Node | None) -> bool:
    if run is None:
        return False
    props = run.child("w:rPr")
    if props is None:
        return False
    highlight = props.child("w:highlight")
    if highlight is None:
        return False
    value = highlight.get("w:val")
    return value not in ("none", "")


#: Větve ``mc:AlternateContent`` — Word do nich zapisuje TÝŽ obsah dvakrát.
ALTERNATE_BRANCHES = ("mc:Choice", "mc:Fallback")


def is_fallback_node(node: Node) -> bool:
    """Leží uzel v ``mc:Fallback``, ke kterému existuje i ``mc:Choice``?

    Textové pole Word ukládá dvakrát: jednou moderně (``mc:Choice``) a jednou
    jako VML (``mc:Fallback``). Vyplňovat se musí obě větve, ale uživateli se
    smí ukázat jen jedna — jinak vidí text dokumentu dvakrát.
    """

    for ancestor in node.ancestors():
        if ancestor.tag != "mc:Fallback":
            continue
        parent = ancestor.parent
        if parent is None or parent.tag != "mc:AlternateContent":
            continue
        if any(child.tag == "mc:Choice" for child in parent.children):
            return True
    return False


def _paragraph_style(paragraph: Node) -> str | None:
    props = paragraph.child("w:pPr")
    if props is None:
        return None
    style = props.child("w:pStyle")
    if style is None:
        return None
    return style.get("w:val")


def paragraph_id(part: str, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{part}|{index}|{text[:120]}".encode("utf-8")).hexdigest()
    return "pg_" + digest[:12]


def placeholder_id(part: str, index: int, start: int, end: int, raw: str) -> str:
    digest = hashlib.sha1(
        f"{part}|{index}|{start}|{end}|{raw}".encode("utf-8")
    ).hexdigest()
    return "ph_" + digest[:12]


def split_options(inner: str) -> tuple[str, ...]:
    """Rozdělí vnitřek placeholderu na varianty podle „ / “."""

    parts = inner.split(OPTION_SEPARATOR)
    if len(parts) < 2:
        return ()
    stripped = [part.strip() for part in parts]
    if not all(stripped):
        return ()
    return tuple(stripped)


def build_context(text: str, start: int, end: int, limit: int = CONTEXT_LIMIT) -> str:
    """Text odstavce zkrácený kolem nálezu, hodnota označená »takto«."""

    marked = text[:start] + CONTEXT_OPEN + text[start:end] + CONTEXT_CLOSE + text[end:]
    lo = start
    hi = end + 2
    if len(marked) <= limit:
        return marked.strip()
    value_len = hi - lo
    if value_len >= limit:
        return marked[lo : lo + limit].strip() + ELLIPSIS
    budget = limit - value_len
    left = min(lo, budget // 2)
    right = min(len(marked) - hi, budget - left)
    left = min(lo, budget - right)
    window_start = lo - left
    window_end = hi + right
    chunk = marked[window_start:window_end].strip()
    if window_start > 0:
        chunk = ELLIPSIS + chunk
    if window_end < len(marked):
        chunk = chunk + ELLIPSIS
    return chunk


def _collect_paragraphs(part: str, doc: Document) -> list[ParagraphView]:
    """Najde ``w:p`` kdekoliv (tabulky i textová pole) a poskládá jejich text.

    Každý ``w:t`` patří tomu nejbližšímu nadřazenému ``w:p``, takže odstavec
    v textovém poli si svůj text nebere s sebou do odstavce, ve kterém je
    zanořený.
    """

    views: dict[int, ParagraphView] = {}
    order: list[Node] = []
    for node in doc.iter("w:p"):
        order.append(node)
    for index, node in enumerate(order):
        views[node.start] = ParagraphView(
            part=part,
            index=index,
            node=node,
            refs=[],
            text="",
            highlight=[],
            info=ParagraphInfo(id="", part=part, order=index, text=""),
        )
    for node in doc.iter("w:t"):
        paragraph_node = node.closest("w:p")
        if paragraph_node is None:
            continue
        view = views.get(paragraph_node.start)
        if view is None:  # pragma: no cover - nemělo by nastat
            continue
        text, offsets = doc.chars_of(node)
        run = node.closest("w:r")
        ref = TextRef(
            node=node,
            run=run,
            text=text,
            offsets=offsets,
            char_start=len(view.text),
        )
        view.refs.append(ref)
        view.text += text
        view.highlight.extend([_run_is_highlighted(run)] * len(text))
    result = [views[node.start] for node in order]
    for view in result:
        view.in_fallback = is_fallback_node(view.node)
        view.info = ParagraphInfo(
            id=paragraph_id(part, view.index, view.text),
            part=part,
            order=view.index,
            text=view.text,
            style=_paragraph_style(view.node),
            in_table=view.node.closest("w:tc") is not None,
        )
    return result


# ---------------------------------------------------------------------------
# hledání placeholderů
# ---------------------------------------------------------------------------

@dataclass
class _Candidate:
    paragraph: ParagraphView
    start: int
    end: int
    kind: str
    raw: str
    inner: str
    sdt: Node | None = None
    extra_paragraphs: tuple[ParagraphView, ...] = ()


def _sdt_qualifies(sdt: Node) -> bool:
    props = sdt.child("w:sdtPr")
    if props is None:
        return False
    return (
        props.descendant("w:date") is not None
        or props.descendant("w:showingPlcHdr") is not None
    )


def _content_paragraphs(content: Node) -> list[Node]:
    """Odstavce, které patří přímo tomuto ``w:sdtContent`` (ne vnořenému rámečku)."""

    out: list[Node] = []
    for node in content.iter_descendants("w:p"):
        nested = False
        for ancestor in node.ancestors():
            if ancestor is content:
                break
            if ancestor.tag == "w:p":
                nested = True
                break
        if not nested:
            out.append(node)
    return out


def _sdt_candidates(
    doc: Document,
    by_node: dict[int, tuple[ParagraphView, TextRef]],
    by_paragraph: dict[int, ParagraphView] | None = None,
) -> list[_Candidate]:
    paragraphs = by_paragraph or {}
    out: list[_Candidate] = []
    for sdt in doc.iter("w:sdt"):
        if not _sdt_qualifies(sdt):
            continue
        content = sdt.child("w:sdtContent")
        if content is None or content.self_closing:
            continue
        found: dict[int, list[TextRef]] = {}
        first_paragraph: ParagraphView | None = None
        for node in content.iter_descendants("w:t"):
            entry = by_node.get(node.start)
            if entry is None:
                continue
            paragraph, ref = entry
            if first_paragraph is None:
                first_paragraph = paragraph
            found.setdefault(paragraph.node.start, []).append(ref)
        if first_paragraph is None:
            continue
        refs = found[first_paragraph.node.start]
        start = min(ref.char_start for ref in refs)
        end = max(ref.char_end for ref in refs)
        if end <= start:
            continue
        raw = first_paragraph.text[start:end]
        if not raw.strip():
            continue
        # Placeholder kotví v prvním odstavci (offsety i id jsou znakové
        # a platí uvnitř jednoho odstavce). Zbývající odstavce téhož
        # content-controlu si poznamenáme, aby je fill mohl zahodit —
        # jinak by po rozbalení sdt zůstal vzorový text v dopise.
        extra: list[ParagraphView] = []
        for node in _content_paragraphs(content):
            if node.start == first_paragraph.node.start:
                continue
            view = paragraphs.get(node.start)
            if view is not None:
                extra.append(view)
        out.append(
            _Candidate(
                paragraph=first_paragraph,
                start=start,
                end=end,
                kind="sdt",
                raw=raw,
                inner=raw,
                sdt=sdt,
                extra_paragraphs=tuple(extra),
            )
        )
    return out


def _regex_candidates(paragraph: ParagraphView) -> tuple[list[_Candidate], list[_Candidate]]:
    mustache: list[_Candidate] = []
    bracket: list[_Candidate] = []
    text = paragraph.text
    for match in MUSTACHE_RE.finditer(text):
        inner = match.group(1).strip()
        if not inner:
            continue
        mustache.append(
            _Candidate(
                paragraph=paragraph,
                start=match.start(),
                end=match.end(),
                kind="mustache",
                raw=match.group(0),
                inner=inner,
            )
        )
    for match in BRACKET_RE.finditer(text):
        inner = match.group(1)
        if not inner.strip():
            continue
        bracket.append(
            _Candidate(
                paragraph=paragraph,
                start=match.start(),
                end=match.end(),
                kind="bracket",
                raw=match.group(0),
                inner=inner,
            )
        )
    return mustache, bracket


def _highlight_candidates(paragraph: ParagraphView) -> list[_Candidate]:
    out: list[_Candidate] = []
    text = paragraph.text
    flags = paragraph.highlight
    index = 0
    size = len(flags)
    while index < size:
        if not flags[index]:
            index += 1
            continue
        start = index
        while index < size and flags[index]:
            index += 1
        end = index
        # okrajové bílé znaky do placeholderu nepatří
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end <= start:
            continue
        raw = text[start:end]
        out.append(
            _Candidate(
                paragraph=paragraph,
                start=start,
                end=end,
                kind="highlight",
                raw=raw,
                inner=raw,
            )
        )
    return out


def _accept(candidates: Iterable[_Candidate]) -> list[_Candidate]:
    """Přijme kandidáty v pořadí priority, překrývající se zahodí."""

    accepted: list[_Candidate] = []
    taken: dict[int, list[tuple[int, int]]] = {}
    for candidate in candidates:
        key = candidate.paragraph.node.start
        spans = taken.setdefault(key, [])
        if any(candidate.start < end and start < candidate.end for start, end in spans):
            continue
        spans.append((candidate.start, candidate.end))
        accepted.append(candidate)
    return accepted


def analyse_part(
    part: str,
    data: bytes,
    *,
    placeholder_offset: int = 0,
) -> PartView:
    """Naparsuje jednu část a najde v ní odstavce i placeholdery."""

    doc = xmlsplice.parse(data)
    paragraphs = _collect_paragraphs(part, doc)
    view = PartView(part=part, data=data, doc=doc, paragraphs=paragraphs)

    by_node: dict[int, tuple[ParagraphView, TextRef]] = {}
    for paragraph in paragraphs:
        for ref in paragraph.refs:
            by_node[ref.node.start] = (paragraph, ref)

    by_paragraph = {paragraph.node.start: paragraph for paragraph in paragraphs}
    sdt = sorted(
        _sdt_candidates(doc, by_node, by_paragraph),
        key=lambda c: (c.paragraph.index, c.start, -c.end),
    )
    mustache: list[_Candidate] = []
    bracket: list[_Candidate] = []
    highlight: list[_Candidate] = []
    for paragraph in paragraphs:
        found_mustache, found_bracket = _regex_candidates(paragraph)
        mustache.extend(found_mustache)
        bracket.extend(found_bracket)
        highlight.extend(_highlight_candidates(paragraph))

    accepted = _accept(sdt + mustache + bracket + highlight)
    accepted.sort(key=lambda c: (c.paragraph.index, c.start, c.end))

    for position, candidate in enumerate(accepted):
        paragraph = candidate.paragraph
        placeholder = Placeholder(
            id=placeholder_id(
                part, paragraph.index, candidate.start, candidate.end, candidate.raw
            ),
            part=part,
            kind=candidate.kind,
            raw=candidate.raw,
            inner=candidate.inner,
            options=split_options(candidate.inner),
            context=build_context(paragraph.text, candidate.start, candidate.end),
            paragraph_id=paragraph.info.id,
            order=placeholder_offset + position,
        )
        view.hits.append(
            Hit(
                placeholder=placeholder,
                paragraph=paragraph,
                start=candidate.start,
                end=candidate.end,
                sdt=candidate.sdt,
                sdt_extra=candidate.extra_paragraphs,
            )
        )
    return view


#: Prvky, které se v náhledu chovají jako bílý znak (v textu odstavce ne —
#: tam by rozbily mapu znak → bajt).
_BREAK_TAGS = {"w:br": "\n", "w:cr": "\n", "w:tab": "\t"}


def preview_text(view: PartView) -> str:
    """Text části pro náhled: zalomení a tabulátory se převedou na bílé znaky."""

    lines: list[str] = []
    for paragraph in view.paragraphs:
        if paragraph.in_fallback:
            continue  # tentýž text je i v mc:Choice — v náhledu jen jednou
        chunks: list[str] = []
        for node in paragraph.node.iter_descendants():
            if node.closest("w:p") is not paragraph.node:
                continue
            if node.tag == "w:t":
                chunks.append(view.doc.text_of(node))
            elif node.tag in _BREAK_TAGS and node.closest("w:r") is not None:
                chunks.append(_BREAK_TAGS[node.tag])
        lines.append("".join(chunks))
    return "\n".join(lines)


def analyse_package(package: DocxPackage) -> list[PartView]:
    """Naparsuje všechny skenované části v pořadí dokumentu."""

    views: list[PartView] = []
    placeholder_offset = 0
    for name, data in package.iter_scan_parts():
        view = analyse_part(name, data, placeholder_offset=placeholder_offset)
        placeholder_offset += len(view.hits)
        views.append(view)
    return views


def scan_docx(path_or_bytes: "Path | str | bytes") -> ScanResult:
    """Najde v šabloně všechny placeholdery a odstavce."""

    package = DocxPackage.open(path_or_bytes)
    result = ScanResult()
    order = 0
    for view in analyse_package(package):
        for paragraph in view.paragraphs:
            if paragraph.in_fallback:
                continue  # dvojče z mc:Choice stačí nabídnout jednou
            info = paragraph.info
            result.paragraphs.append(
                ParagraphInfo(
                    id=info.id,
                    part=info.part,
                    order=order,
                    text=info.text,
                    style=info.style,
                    in_table=info.in_table,
                )
            )
            order += 1
        for hit in view.hits:
            result.placeholders.append(hit.placeholder)
    return result
