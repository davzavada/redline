"""Testy pro dlg.mapping — automatický návrh polí z placeholderů."""

from __future__ import annotations

import pytest

from dlg import mapping
from dlg.mapping import apply_fields, suggest_fields, suggest_optional_paragraphs
from dlg.models import FieldSpec, Placeholder, ScanResult

BULLET = "●"


def ph(
    inner: str,
    *,
    order: int = 0,
    kind: str = "bracket",
    context: str = "",
    options: tuple[str, ...] = (),
    part: str = "word/document.xml",
    paragraph_id: str = "",
    pid: str = "",
) -> Placeholder:
    """Placeholder pro testy — id je odvozené z pořadí, ať je stabilní."""

    if not options and " / " in inner:
        parts = [p for p in inner.split(" / ") if p.strip()]
        if len(parts) >= 2:
            options = tuple(parts)
    return Placeholder(
        id=pid or f"ph_{order:03d}",
        part=part,
        kind=kind,
        raw=f"[{inner}]" if kind == "bracket" else inner,
        inner=inner,
        options=options,
        context=context or inner,
        paragraph_id=paragraph_id or f"pg_{order:03d}",
        order=order,
    )


def scan_of(*placeholders: Placeholder) -> ScanResult:
    return ScanResult(placeholders=list(placeholders), paragraphs=[])


def keys_of(fields):
    return [f.key for f in fields]


# ---------------------------------------------------------------------------
# Slučování shodných placeholderů
# ---------------------------------------------------------------------------
def test_same_inner_becomes_single_field():
    fields = suggest_fields(
        scan_of(
            ph("Jan Novák", order=0),
            ph("Nová 1", order=1),
            ph("Jan Novák", order=2, part="word/header1.xml"),
        )
    )

    assert len(fields) == 2
    jmeno = fields[0]
    assert jmeno.placeholder_ids == ["ph_000", "ph_002"]
    assert "2 místa" in jmeno.help


def test_merging_ignores_whitespace_differences():
    fields = suggest_fields(
        scan_of(ph("Jan  Novák", order=0), ph("Jan Novák ", order=1))
    )

    assert len(fields) == 1
    assert len(fields[0].placeholder_ids) == 2


def test_order_follows_document_order():
    fields = suggest_fields(
        scan_of(
            ph("info@example.com", order=5),
            ph("Jan Novák", order=1),
            ph("110 00", order=3),
        )
    )

    assert keys_of(fields) == ["jmeno", "psc", "email"]
    assert [f.order for f in fields] == [0, 1, 2]


def test_empty_inner_is_not_merged():
    fields = suggest_fields(
        scan_of(ph("", order=0, kind="highlight"), ph("  ", order=1, kind="highlight"))
    )

    assert len(fields) == 2


# ---------------------------------------------------------------------------
# Odvození typu
# ---------------------------------------------------------------------------
def test_two_options_make_choice():
    fields = suggest_fields(scan_of(ph("Česká republika / Slovenská republika")))

    assert fields[0].type == "choice"
    assert fields[0].options == ["Česká republika", "Slovenská republika"]


def test_long_choice_stays_choice():
    inner = (
        "zrušili registraci Doménového jména / převedli Doménové jméno na "
        "společnost LEGO Holding A/S a poskytli klientovi veškerou nezbytnou "
        "součinnost k provedení takového převodu"
    )
    fields = suggest_fields(scan_of(ph(inner)))

    assert fields[0].type == "choice"
    assert len(fields[0].options) == 2
    assert fields[0].options[1].startswith("převedli")


def test_sdt_is_date():
    fields = suggest_fields(
        scan_of(ph("neděle 6. září 2026", kind="sdt"))
    )

    assert fields[0].type == "date"
    assert fields[0].key == "datum"


@pytest.mark.parametrize(
    "text", ["1. 1. 2026", "01.01.2026", "2026-01-01", "9. září 2026", "1.1.26"]
)
def test_date_like_text_is_date(text):
    fields = suggest_fields(scan_of(ph(text)))

    assert fields[0].type == "date"


def test_long_text_is_multiline():
    inner = "A" * (mapping.MULTILINE_THRESHOLD + 5)
    fields = suggest_fields(scan_of(ph(inner)))

    assert fields[0].type == "multiline"


def test_short_text_is_text():
    fields = suggest_fields(scan_of(ph("Jan Novák")))

    assert fields[0].type == "text"


# ---------------------------------------------------------------------------
# Hádání klíčů
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "inner,context,expected",
    [
        ("Jan Novák", "", "jmeno"),
        ("Nová 1", "", "ulice"),
        ("110 00", "", "psc"),
        ("Česká republika / Slovenská republika", "", "zeme"),
        ("lego-shop.cz", "", "domena"),
        ("www.lego-shop.cz", "", "domena"),
        ("info@example.com", "", "email"),
        ("+420 236 045 001", "", "telefon"),
        ("12345678", "", "ico"),
        ("6. 9. 2026", "", "datum"),
        (BULLET, "jste držitelem doménového jména [●]", "domena"),
        (BULLET, "ve lhůtě [●] pracovních dnů od doručení", "lhuta"),
        (BULLET, "naše spisová značka [●]", "spisova_znacka"),
        (BULLET, "napište nám na e-mail [●]", "email"),
        (BULLET, "bytem [●]", "jmeno"),
        ("10", "ve lhůtě 10 pracovních dnů od doručení", "lhuta"),
        ("Praha", "se sídlem v obci Praha", "mesto"),
    ],
)
def test_key_guessing(inner, context, expected):
    fields = suggest_fields(scan_of(ph(inner, context=context)))

    assert fields[0].key == expected
    assert fields[0].label
    assert fields[0].label[0].isupper() or fields[0].label[0].isalpha()


def test_city_after_postal_code():
    """PSČ a hned za ním město — klasický adresní blok."""

    fields = suggest_fields(
        scan_of(
            ph("Nová 1", order=0),
            ph("110 00", order=1),
            ph("Praha 4", order=2),
        )
    )

    assert keys_of(fields) == ["ulice", "psc", "mesto"]


def test_address_block_like_the_real_template():
    fields = suggest_fields(
        scan_of(
            ph("Jan Novák", order=0),
            ph("Nová 1", order=1),
            ph("110 00", order=2),
            ph("Česká republika / Slovenská republika", order=3),
            ph(BULLET, order=4, context="jste držitelem doménového jména [●]"),
        )
    )

    assert keys_of(fields) == ["jmeno", "ulice", "psc", "zeme", "domena"]
    assert fields[3].type == "choice"
    assert fields[4].label == "Doménové jméno"


def test_holder_context_wins_over_plain_name():
    fields = suggest_fields(
        scan_of(ph("Jan Novák", context="držitelem doménového jména je Jan Novák"))
    )

    assert fields[0].key == "drzitel"


def test_email_is_not_taken_for_domain():
    fields = suggest_fields(scan_of(ph("david.zavada@example.com")))

    assert fields[0].key == "email"


# ---------------------------------------------------------------------------
# Neznámé placeholdery
# ---------------------------------------------------------------------------
def test_unknown_fields_are_numbered():
    fields = suggest_fields(
        scan_of(
            ph("§ 8 odst. 2 zákona", order=0),
            ph("!!!", order=1, kind="highlight"),
        )
    )

    assert keys_of(fields) == ["pole_1", "pole_2"]


def test_unknown_label_comes_from_inner():
    fields = suggest_fields(scan_of(ph("§ 8 odst. 2 zákona č. 89/2012 Sb.")))

    assert fields[0].key == "pole_1"
    assert fields[0].label.startswith("§ 8 odst. 2")


def test_unknown_label_is_shortened():
    fields = suggest_fields(scan_of(ph("slovo " * 40, kind="highlight")))

    assert len(fields[0].label) <= mapping.LABEL_MAX_LENGTH
    assert fields[0].label.endswith("…")


def test_bullet_label_is_czech():
    fields = suggest_fields(scan_of(ph(BULLET, context="nic užitečného kolem")))

    assert fields[0].key == "pole_1"
    assert "doplnit" in fields[0].label


def test_unknown_choice_gets_variant_key():
    """Nerozpoznaná volba mezi variantami má klíč „varianta“, ne „pole_1“."""

    fields = suggest_fields(
        scan_of(ph("zrušili registraci Doménového jména / převedli Doménové jméno"))
    )

    assert fields[0].key == "varianta"
    assert fields[0].type == "choice"
    assert len(fields[0].options) == 2


def test_unknown_choices_are_numbered_separately_from_plain_fields():
    fields = suggest_fields(
        scan_of(
            ph("§ 8 odst. 2 zákona", order=0),
            ph("ano / ne", order=1),
            ph("vlevo / vpravo", order=2),
            ph("§ 12 odst. 4 zákona", order=3),
        )
    )

    assert keys_of(fields) == ["pole_1", "varianta", "varianta_2", "pole_2"]


def test_keys_are_unique():
    fields = suggest_fields(
        scan_of(
            ph("Jan Novák", order=0),
            ph("Petr Svoboda", order=1),
            ph("Eva Malá", order=2),
        )
    )

    assert keys_of(fields) == ["jmeno", "jmeno_2", "jmeno_3"]
    assert [f.label for f in fields] == [
        "Jméno a příjmení",
        "Jméno a příjmení (2)",
        "Jméno a příjmení (3)",
    ]
    assert len(set(keys_of(fields))) == 3


def test_field_keys_match_models_pattern():
    import re

    fields = suggest_fields(
        scan_of(
            ph("Jan Novák", order=0),
            ph("§ 8 odst. 2", order=1),
            ph("110 00", order=2),
        )
    )

    for spec in fields:
        assert re.fullmatch(r"[a-z0-9_]+", spec.key), spec.key


# ---------------------------------------------------------------------------
# Ostatní API
# ---------------------------------------------------------------------------
def test_suggest_optional_paragraphs_is_empty_for_now():
    assert suggest_optional_paragraphs(scan_of(ph("Jan Novák"))) == []


def test_apply_fields_maps_keys_to_placeholder_ids():
    fields = [
        FieldSpec(key="jmeno", label="Jméno", placeholder_ids=["ph_1", "ph_2"]),
        FieldSpec(key="mesto", label="Město", placeholder_ids=["ph_3"], default="Praha"),
    ]

    result = apply_fields(fields, {"jmeno": "Jan Novák"})

    assert result == {"ph_1": "Jan Novák", "ph_2": "Jan Novák", "ph_3": "Praha"}


def test_apply_fields_skips_empty_values():
    fields = [
        FieldSpec(key="jmeno", label="Jméno", placeholder_ids=["ph_1"]),
        FieldSpec(key="mesto", label="Město", placeholder_ids=["ph_2"]),
    ]

    result = apply_fields(fields, {"jmeno": "Jan Novák", "mesto": "   "})

    assert result == {"ph_1": "Jan Novák"}


def test_apply_fields_on_empty_input():
    assert apply_fields([], {}) == {}


def test_suggest_fields_of_empty_scan():
    assert suggest_fields(ScanResult()) == []


def test_help_skips_redundant_context():
    fields = suggest_fields(scan_of(ph("Jan Novák", context="»[Jan Novák]«")))

    assert "Kontext" not in fields[0].help


def test_help_mentions_useful_context():
    fields = suggest_fields(
        scan_of(ph("Jan Novák", context="Vážený pane »[Jan Novák]«, obracíme se na Vás"))
    )

    assert fields[0].help.startswith("Kontext:")
    assert "Vážený pane" in fields[0].help


# ---------------------------------------------------------------------------
# výplňová značka „[●]“ znamená pokaždé něco jiného
# ---------------------------------------------------------------------------
def test_vice_vyplnovych_znacek_nesplyne_do_jednoho_pole() -> None:
    scan = scan_of(
        ph(BULLET, order=0, context="Doménové jméno [●] bylo registrováno"),
        ph(BULLET, order=1, context="ve lhůtě [●] pracovních dnů"),
        ph(BULLET, order=2, context="na e-mailu [●]"),
    )

    fields = suggest_fields(scan)
    assert [f.key for f in fields] == ["domena", "lhuta", "email"]
    assert [f.placeholder_ids for f in fields] == [["ph_000"], ["ph_001"], ["ph_002"]]

    hodnoty = apply_fields(fields, {"domena": "lego-shop.cz", "lhuta": "15", "email": "a@b.cz"})
    assert hodnoty == {"ph_000": "lego-shop.cz", "ph_001": "15", "ph_002": "a@b.cz"}


def test_vyplnove_znacky_se_stejnym_vyznamem_se_sluci() -> None:
    scan = scan_of(
        ph(BULLET, order=0, context="Doménové jméno [●] bylo registrováno"),
        ph(BULLET, order=1, context="převod doménového jména [●] na klienta"),
    )

    (pole,) = suggest_fields(scan)
    assert pole.key == "domena"
    assert pole.placeholder_ids == ["ph_000", "ph_001"]


def test_vyplnova_znacka_bez_kontextu_zustane_vlastnim_polem() -> None:
    scan = scan_of(
        ph(BULLET, order=0, context="… [●] …"),
        ph(BULLET, order=1, context="jiná věta [●] bez nápovědy"),
    )

    fields = suggest_fields(scan)
    assert len(fields) == 2
    assert [f.key for f in fields] == ["pole_1", "pole_2"]


def test_obsahove_placeholdery_se_dal_sluci_podle_vnitrku() -> None:
    scan = scan_of(
        ph("Jan Novák", order=0, context="Vážený pane [Jan Novák],"),
        ph("Jan Novák", order=1, context="držitel [Jan Novák] byl vyzván"),
    )

    (pole,) = suggest_fields(scan)
    assert pole.placeholder_ids == ["ph_000", "ph_001"]
