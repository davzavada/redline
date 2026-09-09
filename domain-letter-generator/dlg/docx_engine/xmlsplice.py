"""Bajtově přesné úpravy XML uvnitř .docx.

Nad ``xml.parsers.expat`` se staví lehký strom uzlů, ve kterém si každý element
pamatuje své **bajtové** offsety v původních datech. Díky tomu jde dokument měnit
chirurgicky: vymění se jen ty bajty, které opravdu měníme, a všechno ostatní
(XML deklarace, komentáře, ``mc:AlternateContent``, neznámé prefixy, pořadí
atributů, formátování zápisu) zůstane bit po bitu stejné.

Pozor na dvě věci, na kterých to celé stojí:

* ``CurrentByteIndex`` je *bajtový* index, proto se pracuje výhradně nad
  ``bytes``; převod na ``str`` by offsety rozbil u každého vícebajtového znaku.
* Konec počáteční značky se hledá vlastním skenováním se stavem uvozovek,
  aby znak ``>`` uvnitř hodnoty atributu (``<w:t a="x>y">``) parsing nerozbil.
"""

from __future__ import annotations

import xml.parsers.expat
from typing import Iterable, Iterator, Sequence

from .parts import DocxError

__all__ = [
    "XmlError",
    "Node",
    "Document",
    "Splicer",
    "parse",
    "escape_text",
    "escape_attr",
    "decode_chars",
    "merge_ranges",
]


class XmlError(DocxError, ValueError):
    """Chyba při čtení nebo úpravě XML části dokumentu.

    Dědí z :class:`~dlg.docx_engine.parts.DocxError`, aby volajícímu stačilo
    ošetřit jedinou výjimku enginu — poškozené XML uvnitř jinak platného ZIPu
    je z pohledu uživatele totéž jako nečitelný .docx. ``ValueError`` zůstává
    kvůli zpětné kompatibilitě.
    """


_LT = 0x3C  # <
_GT = 0x3E  # >
_SLASH = 0x2F  # /
_AMP = 0x26  # &
_SEMI = 0x3B  # ;
_QUOT = 0x22  # "
_APOS = 0x27  # '
_EQ = 0x3D  # =
_WS = frozenset(b" \t\r\n")


# ---------------------------------------------------------------------------
# escapování / dekódování
# ---------------------------------------------------------------------------

def escape_text(value: str) -> str:
    """Ošetří text pro vložení mezi značky (``&``, ``<``, ``>``)."""

    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(value: str) -> str:
    """Ošetří text pro vložení do hodnoty atributu v dvojitých uvozovkách."""

    return (
        escape_text(value)
        .replace('"', "&quot;")
        .replace("\t", "&#9;")
        .replace("\n", "&#10;")
        .replace("\r", "&#13;")
    )


_NAMED_ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
}


def _utf8_len(first_byte: int) -> int:
    if first_byte < 0x80:
        return 1
    if first_byte >= 0xF0:
        return 4
    if first_byte >= 0xE0:
        return 3
    if first_byte >= 0xC0:
        return 2
    return 1  # osamocený pokračovací bajt — bereme po jednom


def _resolve_entity(name: str) -> str | None:
    if not name:
        return None
    if name[0] == "#":
        try:
            code = int(name[2:], 16) if name[1:2] in ("x", "X") else int(name[1:], 10)
        except ValueError:
            return None
        if 0 <= code <= 0x10FFFF:
            try:
                return chr(code)
            except ValueError:  # pragma: no cover - chr nikdy nespadne v tomto rozsahu
                return None
        return None
    return _NAMED_ENTITIES.get(name)


def decode_chars(data: bytes, start: int, end: int) -> tuple[str, list[int]]:
    """Dekóduje textový úsek a vrátí i mapu znak → bajtový offset.

    Vrací dvojici ``(text, offsets)``, kde ``offsets`` má délku ``len(text) + 1``:
    ``offsets[i]`` je bajtový offset začátku *i*-tého znaku a ``offsets[-1]``
    je ``end``. Entity (``&amp;``, ``&#x2022;``, …) se rozbalují na jeden znak,
    který ale v mapě zabírá celý svůj původní bajtový rozsah.
    """

    chars: list[str] = []
    offsets: list[int] = []
    i = start
    while i < end:
        byte = data[i]
        if byte == _AMP:
            limit = min(end, i + 34)
            semi = data.find(b";", i + 1, limit)
            if semi != -1:
                try:
                    name = data[i + 1 : semi].decode("ascii")
                except UnicodeDecodeError:
                    name = ""
                resolved = _resolve_entity(name)
                if resolved is not None and len(resolved) == 1:
                    chars.append(resolved)
                    offsets.append(i)
                    i = semi + 1
                    continue
        size = _utf8_len(byte)
        if i + size > end:
            size = 1
        chunk = data[i : i + size]
        chars.append(chunk.decode("utf-8", "replace")[:1] or "�")
        offsets.append(i)
        i += size
    offsets.append(end)
    return "".join(chars), offsets


# ---------------------------------------------------------------------------
# strom uzlů
# ---------------------------------------------------------------------------

class Node:
    """Jeden element s bajtovými offsety v původních datech.

    ``start``        offset znaku ``<`` počáteční značky
    ``open_end``     offset těsně za ``>`` počáteční značky
    ``inner_start``  začátek obsahu (u samouzavíracích == ``open_end``)
    ``inner_end``    konec obsahu (offset ``<`` koncové značky)
    ``end``          offset těsně za ``>`` koncové značky
    """

    __slots__ = (
        "tag",
        "attrs",
        "start",
        "open_end",
        "inner_start",
        "inner_end",
        "end",
        "self_closing",
        "children",
        "parent",
        "index",
        "depth",
    )

    def __init__(
        self,
        tag: str,
        attrs: tuple[tuple[str, str], ...],
        start: int,
        open_end: int,
        self_closing: bool,
        parent: "Node | None",
        index: int,
    ) -> None:
        self.tag = tag
        self.attrs = attrs
        self.start = start
        self.open_end = open_end
        self.inner_start = open_end
        self.inner_end = open_end
        self.end = open_end
        self.self_closing = self_closing
        self.children: list[Node] = []
        self.parent = parent
        self.index = index
        self.depth = 0 if parent is None else parent.depth + 1

    # -- atributy ----------------------------------------------------------
    def get(self, name: str, default: str | None = None) -> str | None:
        for key, value in self.attrs:
            if key == name:
                return value
        return default

    def has(self, name: str) -> bool:
        return any(key == name for key, _ in self.attrs)

    # -- procházení --------------------------------------------------------
    def iter_children(self, tag: str | None = None) -> Iterator["Node"]:
        for child in self.children:
            if tag is None or child.tag == tag:
                yield child

    def child(self, tag: str) -> "Node | None":
        for candidate in self.children:
            if candidate.tag == tag:
                return candidate
        return None

    def iter_descendants(self, tag: str | None = None) -> Iterator["Node"]:
        stack = list(reversed(self.children))
        while stack:
            node = stack.pop()
            if tag is None or node.tag == tag:
                yield node
            stack.extend(reversed(node.children))

    def descendant(self, tag: str) -> "Node | None":
        for node in self.iter_descendants(tag):
            return node
        return None

    def ancestors(self) -> Iterator["Node"]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def closest(self, tag: str) -> "Node | None":
        for node in self.ancestors():
            if node.tag == tag:
                return node
        return None

    def contains(self, other: "Node") -> bool:
        return self.start <= other.start and other.end <= self.end

    def __repr__(self) -> str:  # pragma: no cover - jen pro ladění
        return f"<Node {self.tag} {self.start}:{self.end}>"


class Document:
    """Naparsovaná XML část: původní bajty + strom uzlů."""

    __slots__ = ("data", "root", "nodes")

    def __init__(self, data: bytes, root: Node | None, nodes: list[Node]) -> None:
        self.data = data
        self.root = root
        self.nodes = nodes

    def iter(self, tag: str | None = None) -> Iterator[Node]:
        """Uzly v pořadí dokumentu (podle počáteční značky)."""

        for node in self.nodes:
            if tag is None or node.tag == tag:
                yield node

    def find_all(self, tag: str) -> list[Node]:
        return [node for node in self.nodes if node.tag == tag]

    def raw(self, node: Node) -> bytes:
        return self.data[node.start : node.end]

    def inner_raw(self, node: Node) -> bytes:
        return self.data[node.inner_start : node.inner_end]

    def text_of(self, node: Node) -> str:
        """Text elementu bez potomků (pro ``w:t`` je to jeho celý obsah)."""

        text, _ = decode_chars(self.data, node.inner_start, node.inner_end)
        return text

    def chars_of(self, node: Node) -> tuple[str, list[int]]:
        return decode_chars(self.data, node.inner_start, node.inner_end)


def _scan_start_tag(data: bytes, start: int) -> tuple[int, bool]:
    """Najde konec počáteční značky se stavem uvozovek. Vrací (offset za ``>``, samouzavírací)."""

    size = len(data)
    i = start + 1
    quote = 0
    while i < size:
        byte = data[i]
        if quote:
            if byte == quote:
                quote = 0
        elif byte in (_QUOT, _APOS):
            quote = byte
        elif byte == _GT:
            return i + 1, data[i - 1] == _SLASH
        i += 1
    raise XmlError("Neuzavřená počáteční značka v XML části dokumentu.")


def _scan_attributes(data: bytes, node: Node) -> list[tuple[str, int, int, int, int]]:
    """Rozebere počáteční značku na atributy.

    Vrací seznam ``(jméno, offset jména, offset hodnoty, konec hodnoty, konec atributu)``;
    offsety hodnoty ukazují dovnitř uvozovek.
    """

    end = node.open_end - (2 if node.self_closing else 1)
    i = node.start + 1
    while i < end and data[i] not in _WS and data[i] not in (_SLASH, _GT):
        i += 1
    out: list[tuple[str, int, int, int, int]] = []
    while i < end:
        while i < end and data[i] in _WS:
            i += 1
        if i >= end:
            break
        name_start = i
        while i < end and data[i] != _EQ and data[i] not in _WS:
            i += 1
        name = data[name_start:i].decode("utf-8", "replace")
        while i < end and data[i] in _WS:
            i += 1
        if i >= end or data[i] != _EQ:
            out.append((name, name_start, i, i, i))
            continue
        i += 1
        while i < end and data[i] in _WS:
            i += 1
        if i >= end or data[i] not in (_QUOT, _APOS):
            break
        quote = data[i]
        value_start = i + 1
        value_end = data.find(bytes([quote]), value_start)
        if value_end == -1 or value_end > end:
            raise XmlError("Neuzavřená hodnota atributu v XML části dokumentu.")
        out.append((name, name_start, value_start, value_end, value_end + 1))
        i = value_end + 1
    return out


def parse(data: bytes) -> Document:
    """Naparsuje XML část a postaví strom uzlů s bajtovými offsety."""

    if isinstance(data, bytearray):
        data = bytes(data)
    if not isinstance(data, bytes):
        raise XmlError("Ke zpracování XML je potřeba předat bajty (bytes).")

    parser = xml.parsers.expat.ParserCreate()
    parser.ordered_attributes = True
    parser.buffer_text = True

    nodes: list[Node] = []
    stack: list[Node] = []
    roots: list[Node] = []

    def start_element(name: str, attrs: Sequence[str]) -> None:
        offset = parser.CurrentByteIndex
        open_end, self_closing = _scan_start_tag(data, offset)
        pairs = tuple(
            (attrs[i], attrs[i + 1]) for i in range(0, len(attrs) - 1, 2)
        )
        parent = stack[-1] if stack else None
        node = Node(name, pairs, offset, open_end, self_closing, parent, len(nodes))
        nodes.append(node)
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)
        stack.append(node)

    def end_element(_name: str) -> None:
        offset = parser.CurrentByteIndex
        node = stack.pop()
        if node.self_closing:
            node.inner_start = node.open_end
            node.inner_end = node.open_end
            node.end = node.open_end
            return
        # Koncová značka nemá atributy, stačí nejbližší '>'.
        gt = data.find(b">", offset)
        if gt == -1:
            raise XmlError("Neuzavřená koncová značka v XML části dokumentu.")
        node.inner_start = node.open_end
        node.inner_end = offset
        node.end = gt + 1

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    try:
        parser.Parse(data, True)
    except xml.parsers.expat.ExpatError as exc:
        raise XmlError(f"Poškozené XML v dokumentu: {exc}") from exc

    return Document(data, roots[0] if roots else None, nodes)


# ---------------------------------------------------------------------------
# splice
# ---------------------------------------------------------------------------

class Splicer:
    """Sbírá bajtové editace a aplikuje je najednou od konce k začátku."""

    __slots__ = ("data", "_edits")

    def __init__(self, data: bytes) -> None:
        if isinstance(data, bytearray):
            data = bytes(data)
        if not isinstance(data, bytes):
            raise XmlError("Splicer pracuje s bajty (bytes).")
        self.data = data
        self._edits: list[tuple[int, int, bytes, int]] = []

    # -- základní operace --------------------------------------------------
    def replace(self, start: int, end: int, new: bytes | str) -> None:
        if start < 0 or end < start or end > len(self.data):
            raise XmlError(f"Neplatný rozsah editace ({start}, {end}).")
        payload = new.encode("utf-8") if isinstance(new, str) else bytes(new)
        self._edits.append((start, end, payload, len(self._edits)))

    def delete(self, start: int, end: int) -> None:
        if end > start:
            self.replace(start, end, b"")

    def insert(self, position: int, new: bytes | str) -> None:
        self.replace(position, position, new)

    def insert_attr(self, elem: Node, name: str, value: str) -> None:
        """Doplní (nebo přepíše) atribut v počáteční značce elementu."""

        for attr_name, _name_start, value_start, value_end, _attr_end in _scan_attributes(
            self.data, elem
        ):
            if attr_name == name:
                current = self.data[value_start:value_end]
                new_value = escape_attr(value).encode("utf-8")
                if current != new_value:
                    self.replace(value_start, value_end, new_value)
                return
        position = elem.open_end - (2 if elem.self_closing else 1)
        self.insert(position, f' {name}="{escape_attr(value)}"'.encode("utf-8"))

    def remove_element(self, elem: Node) -> None:
        self.delete(elem.start, elem.end)

    # -- vyhodnocení -------------------------------------------------------
    @property
    def edits(self) -> list[tuple[int, int, bytes]]:
        return [(s, e, payload) for s, e, payload, _ in self._edits]

    def __len__(self) -> int:
        return len(self._edits)

    def apply(self) -> bytes:
        """Ověří nepřekrývání a vrátí nová data."""

        if not self._edits:
            return self.data
        ordered = sorted(self._edits, key=lambda item: (item[0], item[1], item[3]))
        previous_start, previous_end = -1, -1
        for start, end, _payload, _seq in ordered:
            if start < previous_end:
                raise XmlError(
                    f"Editace XML se překrývají: ({previous_start}, {previous_end}) a ({start}, {end})."
                )
            previous_start, previous_end = start, end
        out = bytearray(self.data)
        for start, end, payload, _seq in reversed(ordered):
            out[start:end] = payload
        return bytes(out)


def merge_ranges(ranges: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sloučí bajtové rozsahy (pomůcka pro kontrolu „leží uvnitř smazaného“)."""

    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            last_start, last_end = merged[-1]
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged
