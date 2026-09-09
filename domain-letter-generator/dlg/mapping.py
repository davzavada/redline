"""Automatický návrh formulářových polí z nalezených placeholderů.

Hlavní přidaná hodnota: placeholdery se **shodným vnitřkem** (``inner``) se
sloučí do jednoho pole. Když se v dopise „[Jan Novák]“ objeví na pěti místech,
uživatel to vyplňuje jednou.

Hádání klíče a popisku je tabulka pravidel (``FIELD_RULES``) — data, ne kód.
Přidat další vzor znamená přidat jeden ``FieldRule``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping, Sequence

from .models import FieldSpec, OptionalParagraph, Placeholder, ScanResult

__all__ = [
    "FieldRule",
    "FIELD_RULES",
    "apply_fields",
    "suggest_fields",
    "suggest_optional_paragraphs",
]

#: Delší text se ve formuláři edituje ve víceřádkovém poli.
MULTILINE_THRESHOLD = 120
#: Maximální délka automaticky odvozeného popisku.
LABEL_MAX_LENGTH = 40
#: Znak, kterým Word označuje „sem něco doplň“.
BULLET = "\u25cf"  # znak ● z Wordu

_UPPER = "A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
_LETTER = r"[^\W\d_]"

_DATE_PATTERNS: tuple[str, ...] = (
    r"^(?:pondělí|úterý|středa|čtvrtek|pátek|sobota|neděle)?\s*\d{1,2}\.\s*\d{1,2}\.\s*\d{2,4}$",
    r"^\d{1,2}\.\s*\d{1,2}\.$",
    r"^\d{4}-\d{1,2}-\d{1,2}$",
    r"^\d{1,2}/\d{1,2}/\d{2,4}$",
    r"^(?:pondělí|úterý|středa|čtvrtek|pátek|sobota|neděle)?\s*\d{1,2}\.\s*"
    r"(?:ledna|února|března|dubna|května|června|července|srpna|září|října|listopadu|prosince)"
    r"\s*\d{4}$",
)

_DATE_RE = tuple(re.compile(p, re.IGNORECASE) for p in _DATE_PATTERNS)


@dataclass(frozen=True)
class FieldRule:
    """Jedno pravidlo pro odhad klíče a popisku pole.

    Pravidlo platí, jsou-li splněny **všechny** vyplněné podmínky. Vyhodnocuje
    se v pořadí, v jakém je v ``FIELD_RULES`` — vyhrává první shoda.
    """

    key: str
    label: str
    inner: str = ""  # regex nad normalizovaným inner
    context: str = ""  # regex nad okolním textem odstavce
    veto: str = ""  # regex nad inner, který pravidlo vyřadí
    kind: str = ""  # požadovaný Placeholder.kind
    after: str = ""  # klíč pole, které musí bezprostředně předcházet
    type: str = ""  # vynucený FieldType
    case_sensitive: bool = False
    help: str = ""


# ---------------------------------------------------------------------------
# Tabulka vzorů. Pořadí je významné: první shoda vyhrává.
# ---------------------------------------------------------------------------
FIELD_RULES: tuple[FieldRule, ...] = (
    FieldRule(
        key="email",
        label="E-mail",
        inner=r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$",
    ),
    FieldRule(
        key="email",
        label="E-mail",
        inner=rf"^(?:{BULLET}|\.{{3}}|…)$",
        context=r"e-?mail|elektronick[áé] adres",
    ),
    FieldRule(
        key="psc",
        label="PSČ",
        inner=r"^\d{3}\s?\d{2}$",
    ),
    FieldRule(
        key="psc",
        label="PSČ",
        inner=rf"^(?:{BULLET}|\.{{3}}|…)$",
        context=r"\bPSČ\b|poštovní směrovací",
    ),
    FieldRule(
        key="ico",
        label="IČO",
        inner=r"^\d{8}$",
    ),
    FieldRule(
        key="ico",
        label="IČO",
        inner=r"^[\d\s]{6,12}$",
        context=r"IČO|IČ:|identifikační číslo",
    ),
    FieldRule(
        key="telefon",
        label="Telefon",
        inner=r"^\+?[\d][\d\s()\-/]{7,}$",
        veto=r"^\d{3}\s?\d{2}$|^\d{8}$",
    ),
    FieldRule(
        key="telefon",
        label="Telefon",
        inner=rf"^(?:{BULLET}|\.{{3}}|…)$",
        context=r"telefon|tel\.|mobil",
    ),
    FieldRule(
        key="zeme",
        label="Země",
        inner=r"^(?:Česká|Slovenská|Německá|Rakouská|Polská)\s+republika"
        r"|^(?:Česko|Slovensko|Polsko|Rakousko|Německo|Maďarsko)\b"
        r"|království$",
    ),
    FieldRule(
        key="domena",
        label="Doménové jméno",
        inner=rf"^(?:{BULLET}|\.{{3}}|…|\[?doména\]?)$",
        context=r"doménov|domény|domén[uay]\b|doménového jména",
    ),
    FieldRule(
        key="domena",
        label="Doménové jméno",
        inner=r"^(?:www\.)?[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+$",
        veto=r"@",
    ),
    FieldRule(
        key="datum",
        label="Datum",
        kind="sdt",
        type="date",
    ),
    FieldRule(
        key="datum",
        label="Datum",
        inner="|".join(f"(?:{p})" for p in _DATE_PATTERNS),
        type="date",
    ),
    FieldRule(
        key="lhuta",
        label="Lhůta (počet dnů)",
        inner=rf"^(?:{BULLET}|\.{{3}}|…)$",
        context=r"lhůt|pracovních dn|kalendářních dn",
    ),
    FieldRule(
        key="datum",
        label="Datum",
        inner=rf"^(?:{BULLET}|\.{{3}}|…)$",
        context=r"\bdne\b|\bdatum\b|\bdatu\b",
        type="date",
    ),
    FieldRule(
        key="spisova_znacka",
        label="Spisová značka",
        inner=r"^\d+\s*[A-Z][a-zA-Z]*\s*\d+\s*/\s*\d{2,4}$",
    ),
    FieldRule(
        key="spisova_znacka",
        label="Spisová značka",
        context=r"spisov[áé] značk|sp\.\s*zn|č\.\s*j\.|naše značka",
    ),
    FieldRule(
        key="lhuta",
        label="Lhůta (počet dnů)",
        inner=r"^\d{1,3}$",
        context=r"lhůt|\bdn[ůí]\b|pracovních dn|kalendářních dn",
    ),
    FieldRule(
        key="lhuta",
        label="Lhůta",
        inner=r"^\d{1,3}\s*(?:pracovních\s+|kalendářních\s+)?dn[ůí]$",
    ),
    FieldRule(
        key="drzitel",
        label="Držitel doménového jména",
        context=r"držitel|registrant",
        veto=r"^\d+$",
    ),
    FieldRule(
        key="jmeno",
        label="Jméno a příjmení",
        inner=rf"^[{_UPPER}]{_LETTER}+(?:\s+[{_UPPER}]{_LETTER}+){{1,3}}$",
        case_sensitive=True,
    ),
    FieldRule(
        key="jmeno",
        label="Jméno a příjmení",
        inner=rf"^(?:{BULLET}|\.{{3}}|…)$",
        context=r"\bjmén[oa]\b|vážen[ýá]|oslovení|bytem",
    ),
    FieldRule(
        key="mesto",
        label="Město",
        inner=rf"^[{_UPPER}]{_LETTER}+(?:[\s\-]{_LETTER}+)*(?:\s\d{{1,2}})?$",
        after="psc",
        case_sensitive=True,
    ),
    FieldRule(
        key="mesto",
        label="Město",
        context=r"\bměst[oěa]\b|\bobec\b|\bobci\b",
        veto=r"\d",
    ),
    FieldRule(
        key="ulice",
        label="Ulice a číslo popisné",
        inner=r"^\D+\s\d+[a-zA-Z]?(?:\s*/\s*\d+[a-zA-Z]?)?$",
    ),
    FieldRule(
        key="ulice",
        label="Ulice a číslo popisné",
        context=r"\bulic[eiy]\b|se sídlem|\badres[ay]\b",
        veto=r"^\d+$",
    ),
)


# ---------------------------------------------------------------------------
# Pomocné funkce
# ---------------------------------------------------------------------------
def _normalize_space(text: str) -> str:
    return " ".join(str(text or "").replace("\u00a0", " ").split())


def _fold(text: str) -> str:
    """Bez diakritiky a malými písmeny — pro porovnávání kontextu."""

    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _looks_like_date(text: str) -> bool:
    value = _normalize_space(text)
    return any(rx.match(value) for rx in _DATE_RE)


def _shorten(text: str, limit: int) -> str:
    value = _normalize_space(text)
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def _label_from_inner(inner: str) -> str:
    """Popisek pro neznámý placeholder — z jeho vlastního textu."""

    value = _normalize_space(inner).replace(BULLET, "doplnit")
    value = value.strip(" .,;:–-")
    value = _normalize_space(value)
    if not value:
        return "Doplnit"
    return _shorten(value, LABEL_MAX_LENGTH)


_DECORATION_RE = re.compile(r"[\[\]{}»«\s]+")


def _context_is_redundant(context: str, inner: str) -> bool:
    """Kontext, který je jen samotný placeholder, uživateli nic neřekne."""

    bare_context = _DECORATION_RE.sub("", _normalize_space(context))
    bare_inner = _DECORATION_RE.sub("", _normalize_space(inner))
    return bool(bare_inner) and bare_context == bare_inner


def _plural_places(count: int) -> str:
    if count == 1:
        return "1 místo"
    if 2 <= count <= 4:
        return f"{count} místa"
    return f"{count} míst"


def _compile(pattern: str, *, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(pattern, flags)


def _rule_matches(rule: FieldRule, inner: str, context: str, kind: str, previous_key: str) -> bool:
    if rule.kind and rule.kind != kind:
        return False
    if rule.after and rule.after != previous_key:
        return False
    if rule.veto and _compile(rule.veto, case_sensitive=rule.case_sensitive).search(inner):
        return False
    if rule.inner and not _compile(rule.inner, case_sensitive=rule.case_sensitive).search(inner):
        return False
    if rule.context:
        # kontext porovnáváme bez diakritiky a bez ohledu na velikost písmen
        # (i vzor se „odháčkuje“, takže „držitel“ najde i „Drzitelem“)
        if not re.search(_fold(rule.context), _fold(context), re.IGNORECASE):
            return False
    if not (rule.kind or rule.after or rule.inner or rule.context):
        return False
    return True


def guess_rule(
    inner: str, context: str, kind: str = "", previous_key: str = ""
) -> FieldRule | None:
    """Najde první pravidlo, které na placeholder sedí (nebo ``None``)."""

    value = _normalize_space(inner)
    ctx = _normalize_space(context)
    for rule in FIELD_RULES:
        if _rule_matches(rule, value, ctx, kind, previous_key):
            return rule
    return None


#: Výplňové značky bez vlastního významu („[●]“, „[...]“, „[…]“). Stejný
#: vnitřek u nich neznamená stejný údaj — o významu rozhoduje až kontext.
_GENERIC_INNER_RE = re.compile(rf"^(?:{BULLET}|\.{{3}}|…)$")


def _is_generic_inner(inner: str) -> bool:
    return bool(_GENERIC_INNER_RE.match(_normalize_space(inner)))


def _group_key(placeholder: Placeholder, rule: "FieldRule | None" = None) -> str:
    """Klíč slučování: shodný vnitřek = jedno pole.

    Výplňová značka („[●]“) je výjimka: v jednom dopise bývá pro doménu,
    lhůtu i e-mail zároveň. Slučovat ji podle vnitřku by znamenalo zapsat
    doménu i do lhůty, proto se slučuje až podle rozpoznaného pravidla.
    """

    normalized = _normalize_space(placeholder.inner)
    if not normalized:
        # prázdný vnitřek nelze rozumně sloučit — každý zůstane samostatně
        return "\x00" + placeholder.id
    if _is_generic_inner(normalized):
        return "\x00" + placeholder.id if rule is None else "\x01" + rule.key
    return normalized


def _field_type(placeholders: Sequence[Placeholder], rule: FieldRule | None) -> str:
    options = _options_of(placeholders)
    if len(options) >= 2:
        return "choice"
    if rule is not None and rule.type:
        return rule.type
    first = placeholders[0]
    if first.kind == "sdt" or _looks_like_date(first.inner):
        return "date"
    if len(_normalize_space(first.inner)) > MULTILINE_THRESHOLD:
        return "multiline"
    return "text"


def _options_of(placeholders: Sequence[Placeholder]) -> list[str]:
    for ph in placeholders:
        options = [o for o in (str(x).strip() for x in ph.options) if o]
        if len(options) >= 2:
            return options
    return []


def _unique(base: str, used: set[str]) -> tuple[str, int]:
    """Vrátí unikátní klíč a pořadí duplicity (1 = první výskyt)."""

    if base not in used:
        used.add(base)
        return base, 1
    index = 2
    while f"{base}_{index}" in used:
        index += 1
    key = f"{base}_{index}"
    used.add(key)
    return key, index


# ---------------------------------------------------------------------------
# Veřejné API
# ---------------------------------------------------------------------------
def suggest_fields(scan: ScanResult) -> list[FieldSpec]:
    """Z výsledku analýzy šablony navrhne formulářová pole.

    * placeholdery se shodným ``inner`` tvoří jedno pole,
    * typ se odvodí z variant / druhu / délky,
    * klíč a popisek se hádají podle ``FIELD_RULES``,
    * pořadí odpovídá pořadí v dokumentu (``Placeholder.order``).
    """

    placeholders = sorted(
        [p for p in getattr(scan, "placeholders", []) if p is not None],
        key=lambda p: (p.order, p.part, p.id),
    )

    # Pravidlo se musí vyhodnotit pro KAŽDÝ placeholder zvlášť a teprve podle
    # něj se slučuje — jinak by se pravidla „● + kontext“ nikdy neuplatnila.
    groups: dict[str, list[Placeholder]] = {}
    rules: dict[str, FieldRule | None] = {}
    order: list[str] = []
    previous_key = ""
    for ph in placeholders:
        rule = guess_rule(ph.inner, ph.context, ph.kind, previous_key)
        previous_key = rule.key if rule is not None else ""
        key = _group_key(ph, rule)
        if key not in groups:
            groups[key] = []
            rules[key] = rule
            order.append(key)
        groups[key].append(ph)

    fields: list[FieldSpec] = []
    used_keys: set[str] = set()
    unknown_index = 0

    for position, group_key in enumerate(order):
        members = groups[group_key]
        first = members[0]
        context = next((m.context for m in members if m.context), "")
        rule = rules[group_key]

        ftype = _field_type(members, rule)
        options = _options_of(members) if ftype == "choice" else []

        if rule is not None:
            base_key = rule.key
            base_label = rule.label
        elif options:
            # Nerozpoznaná volba mezi variantami ("[A / B]"). Klíč „pole_3“ by
            # uživateli nic neřekl, „varianta“ aspoň napoví, oč jde.
            base_key = "varianta"
            base_label = _label_from_inner(first.inner)
        else:
            unknown_index += 1
            base_key = f"pole_{unknown_index}"
            base_label = _label_from_inner(first.inner)

        key, duplicate = _unique(base_key, used_keys)
        label = base_label if duplicate == 1 else f"{base_label} ({duplicate})"

        help_parts: list[str] = []
        if rule is not None and rule.help:
            help_parts.append(rule.help)
        if len(members) > 1:
            help_parts.append(f"Vyplní {_plural_places(len(members))} v dokumentu.")
        if context and not _context_is_redundant(context, first.inner):
            help_parts.append("Kontext: " + _shorten(context, 120))

        fields.append(
            FieldSpec(
                key=key,
                label=label,
                type=ftype,
                options=options,
                default="",
                required=True,
                help=" ".join(help_parts).strip(),
                placeholder_ids=[m.id for m in members],
                order=position,
            )
        )

    return fields


def suggest_optional_paragraphs(scan: ScanResult) -> list[OptionalParagraph]:
    """Zatím se volitelné odstavce nehádají — uživatel si je označí sám."""

    del scan
    return []


def apply_fields(
    fields: Sequence[FieldSpec], values: Mapping[str, str]
) -> dict[str, str]:
    """Z hodnot podle klíče pole udělá hodnoty podle id placeholderu.

    Prázdné hodnoty se vynechávají: o nevyplněných placeholderech pak
    rozhoduje ``fill_docx(keep_unfilled=...)`` — buď zůstanou v dokumentu,
    nebo se smažou. Kdybychom posílali prázdný řetězec, vždy bychom je
    vymazali a přepínač by neměl smysl.
    """

    out: dict[str, str] = {}
    for spec in fields or ():
        raw = values.get(spec.key)
        value = spec.default if raw is None else raw
        value = "" if value is None else str(value)
        if not value.strip():
            continue
        for pid in spec.placeholder_ids:
            out[pid] = value
    return out
