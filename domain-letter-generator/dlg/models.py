"""Sdílené datové struktury generátoru dopisů.

Všechny třídy se serializují do JSON explicitně přes ``to_dict``/``from_dict``.
Nepoužívej ``dataclasses.asdict`` — vnořené seznamy dataklas by se rozpadly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

PlaceholderKind = str  # "bracket" | "highlight" | "mustache" | "sdt"
FieldType = str  # "text" | "multiline" | "choice" | "date"

FIELD_TYPES: tuple[str, ...] = ("text", "multiline", "choice", "date")
PLACEHOLDER_KINDS: tuple[str, ...] = ("bracket", "highlight", "mustache", "sdt")


def _as_str(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _as_list_str(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value.decode() if isinstance(value, bytes) else value]
    return [str(v) for v in value]


@dataclass(frozen=True)
class Placeholder:
    """Jedno místo v šabloně, které se dá vyplnit."""

    id: str
    part: str
    kind: PlaceholderKind
    raw: str
    inner: str
    options: tuple[str, ...] = ()
    context: str = ""
    paragraph_id: str = ""
    order: int = 0

    @property
    def is_choice(self) -> bool:
        return len(self.options) >= 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "part": self.part,
            "kind": self.kind,
            "raw": self.raw,
            "inner": self.inner,
            "options": list(self.options),
            "context": self.context,
            "paragraph_id": self.paragraph_id,
            "order": self.order,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Placeholder":
        return cls(
            id=_as_str(data.get("id")),
            part=_as_str(data.get("part")),
            kind=_as_str(data.get("kind"), "bracket"),
            raw=_as_str(data.get("raw")),
            inner=_as_str(data.get("inner")),
            options=tuple(_as_list_str(data.get("options"))),
            context=_as_str(data.get("context")),
            paragraph_id=_as_str(data.get("paragraph_id")),
            order=int(data.get("order", 0) or 0),
        )


@dataclass(frozen=True)
class ParagraphInfo:
    """Odstavec dokumentu — kvůli volitelným odstavcům a náhledu."""

    id: str
    part: str
    order: int
    text: str
    style: str | None = None
    in_table: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "part": self.part,
            "order": self.order,
            "text": self.text,
            "style": self.style,
            "in_table": self.in_table,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParagraphInfo":
        style = data.get("style")
        return cls(
            id=_as_str(data.get("id")),
            part=_as_str(data.get("part")),
            order=int(data.get("order", 0) or 0),
            text=_as_str(data.get("text")),
            style=None if style is None else str(style),
            in_table=bool(data.get("in_table", False)),
        )


@dataclass
class ScanResult:
    """Výsledek analýzy šablony."""

    placeholders: list[Placeholder] = field(default_factory=list)
    paragraphs: list[ParagraphInfo] = field(default_factory=list)

    def placeholder(self, placeholder_id: str) -> Placeholder | None:
        for ph in self.placeholders:
            if ph.id == placeholder_id:
                return ph
        return None

    def paragraph(self, paragraph_id: str) -> ParagraphInfo | None:
        for pg in self.paragraphs:
            if pg.id == paragraph_id:
                return pg
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "placeholders": [p.to_dict() for p in self.placeholders],
            "paragraphs": [p.to_dict() for p in self.paragraphs],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScanResult":
        return cls(
            placeholders=[Placeholder.from_dict(d) for d in data.get("placeholders", [])],
            paragraphs=[ParagraphInfo.from_dict(d) for d in data.get("paragraphs", [])],
        )


@dataclass
class FieldSpec:
    """Jeden údaj ve formuláři — může plnit několik placeholderů najednou."""

    key: str
    label: str
    type: FieldType = "text"
    options: list[str] = field(default_factory=list)
    default: str = ""
    required: bool = True
    help: str = ""
    placeholder_ids: list[str] = field(default_factory=list)
    order: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "options": list(self.options),
            "default": self.default,
            "required": self.required,
            "help": self.help,
            "placeholder_ids": list(self.placeholder_ids),
            "order": self.order,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FieldSpec":
        ftype = _as_str(data.get("type"), "text")
        if ftype not in FIELD_TYPES:
            ftype = "text"
        return cls(
            key=_as_str(data.get("key")),
            label=_as_str(data.get("label")),
            type=ftype,
            options=_as_list_str(data.get("options")),
            default=_as_str(data.get("default")),
            required=bool(data.get("required", True)),
            help=_as_str(data.get("help")),
            placeholder_ids=_as_list_str(data.get("placeholder_ids")),
            order=int(data.get("order", 0) or 0),
        )


@dataclass
class OptionalParagraph:
    """Odstavec, který jde při generování vypustit."""

    paragraph_id: str
    label: str = ""
    included_by_default: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "paragraph_id": self.paragraph_id,
            "label": self.label,
            "included_by_default": self.included_by_default,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OptionalParagraph":
        return cls(
            paragraph_id=_as_str(data.get("paragraph_id")),
            label=_as_str(data.get("label")),
            included_by_default=bool(data.get("included_by_default", True)),
        )


SCHEMA_VERSION = 1


@dataclass
class TemplateMeta:
    """Metadata jedné uložené šablony."""

    id: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    source_filename: str = ""
    imported_at: str = ""
    updated_at: str = ""
    fields: list[FieldSpec] = field(default_factory=list)
    optional_paragraphs: list[OptionalParagraph] = field(default_factory=list)
    output_pattern: str = "{datum}_{nazev}"
    schema_version: int = SCHEMA_VERSION

    def field_by_key(self, key: str) -> FieldSpec | None:
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def ordered_fields(self) -> list[FieldSpec]:
        return sorted(self.fields, key=lambda f: (f.order, f.label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "source_filename": self.source_filename,
            "imported_at": self.imported_at,
            "updated_at": self.updated_at,
            "fields": [f.to_dict() for f in self.fields],
            "optional_paragraphs": [p.to_dict() for p in self.optional_paragraphs],
            "output_pattern": self.output_pattern,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TemplateMeta":
        return cls(
            id=_as_str(data.get("id")),
            name=_as_str(data.get("name")),
            description=_as_str(data.get("description")),
            tags=_as_list_str(data.get("tags")),
            source_filename=_as_str(data.get("source_filename")),
            imported_at=_as_str(data.get("imported_at")),
            updated_at=_as_str(data.get("updated_at")),
            fields=[FieldSpec.from_dict(d) for d in data.get("fields", [])],
            optional_paragraphs=[
                OptionalParagraph.from_dict(d) for d in data.get("optional_paragraphs", [])
            ],
            output_pattern=_as_str(data.get("output_pattern"), "{datum}_{nazev}"),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION) or SCHEMA_VERSION),
        )


@dataclass
class FillReport:
    """Co se při generování skutečně stalo."""

    filled: list[str] = field(default_factory=list)
    unfilled: list[str] = field(default_factory=list)
    dropped_paragraphs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filled": list(self.filled),
            "unfilled": list(self.unfilled),
            "dropped_paragraphs": list(self.dropped_paragraphs),
            "warnings": list(self.warnings),
        }


def merge_values(
    fields: Sequence[FieldSpec], values: Mapping[str, str]
) -> dict[str, str]:
    """Z hodnot podle klíče pole udělá hodnoty podle id placeholderu."""

    out: dict[str, str] = {}
    for spec in fields:
        value = values.get(spec.key, spec.default)
        for pid in spec.placeholder_ids:
            out[pid] = value
    return out
