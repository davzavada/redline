"""Testy pro dlg.naming — název výstupního souboru."""

from __future__ import annotations

from datetime import date

import pytest

from dlg import naming
from dlg.naming import render_pattern, sanitize_filename, unique_path

DEN = date(2026, 9, 9)


# ---------------------------------------------------------------------------
# render_pattern
# ---------------------------------------------------------------------------
def test_default_pattern():
    result = render_pattern(
        "{datum}_{nazev}", {}, template_name="Výzva k nápravě", today=DEN
    )

    assert result == "2026-09-09_Výzva k nápravě"


def test_field_values_are_substituted():
    result = render_pattern(
        "{datum}_{domena}_{jmeno}",
        {"domena": "lego-shop.cz", "jmeno": "Jan Novák"},
        template_name="Výzva",
        today=DEN,
    )

    assert result == "2026-09-09_lego-shop.cz_Jan Novák"


def test_missing_key_renders_as_empty():
    result = render_pattern(
        "{datum}_{neexistuje}x", {}, template_name="Šablona", today=DEN
    )

    assert result == "2026-09-09_x"


def test_empty_pattern_falls_back_to_default():
    result = render_pattern("", {}, template_name="Výzva", today=DEN)

    assert result == "2026-09-09_Výzva"


def test_pattern_without_tokens_is_kept():
    assert render_pattern("dopis", {}, template_name="X", today=DEN) == "dopis"


def test_date_accepts_custom_format():
    result = render_pattern("{datum:%d.%m.%Y}", {}, template_name="X", today=DEN)

    assert result == "09.09.2026"


def test_whitespace_inside_braces_is_tolerated():
    result = render_pattern("{ datum }-{ nazev }", {}, template_name="X", today=DEN)

    assert result == "2026-09-09-X"


def test_today_defaults_to_current_date():
    result = render_pattern("{datum}", {}, template_name="X")

    assert result == date.today().isoformat()


def test_forbidden_windows_characters_are_removed():
    result = render_pattern(
        "{nazev}", {}, template_name='a/b\\c:d*e?f"g<h>i|j', today=DEN
    )

    for ch in naming.FORBIDDEN_CHARS:
        assert ch not in result
    assert result == "a-b-c-d-e-f-g-h-i-j"


def test_value_with_slash_stays_readable():
    result = render_pattern(
        "{nazev}", {}, template_name="LEGO Holding A/S", today=DEN
    )

    assert result == "LEGO Holding A-S"


def test_trailing_dots_and_spaces_are_trimmed():
    assert sanitize_filename("dopis... ") == "dopis"
    assert sanitize_filename("  dopis  ") == "dopis"
    assert not sanitize_filename("dopis.").endswith(".")


def test_control_characters_are_removed():
    assert sanitize_filename("do\npis\tx\x00") == "do pis x"


def test_reserved_names_are_escaped():
    for reserved in ("CON", "prn", "AUX", "nul", "COM1", "LPT9"):
        result = sanitize_filename(reserved)
        assert result.upper().split(".")[0] not in naming.RESERVED_NAMES
        assert reserved.lower() in result.lower()

    assert sanitize_filename("CON.docx").upper().split(".")[0] not in naming.RESERVED_NAMES
    assert sanitize_filename("CONTROL") == "CONTROL"


def test_length_is_capped():
    result = render_pattern("{nazev}", {}, template_name="á" * 300, today=DEN)

    assert len(result) <= naming.MAX_STEM_LENGTH


def test_empty_result_falls_back():
    assert render_pattern("{neznamy}", {}, template_name="", today=DEN) == "dopis"
    assert sanitize_filename("///") == "dopis"
    assert sanitize_filename("", fallback="vystup") == "vystup"


def test_values_none_is_safe():
    assert render_pattern("{a}b", {"a": None}, template_name="X", today=DEN) == "b"


# ---------------------------------------------------------------------------
# unique_path
# ---------------------------------------------------------------------------
def test_unique_path_returns_plain_name_when_free(tmp_path):
    assert unique_path(tmp_path, "dopis") == tmp_path / "dopis.docx"


def test_unique_path_appends_counter(tmp_path):
    (tmp_path / "dopis.docx").write_bytes(b"x")
    assert unique_path(tmp_path, "dopis") == tmp_path / "dopis (2).docx"

    (tmp_path / "dopis (2).docx").write_bytes(b"x")
    assert unique_path(tmp_path, "dopis") == tmp_path / "dopis (3).docx"


def test_unique_path_respects_suffix(tmp_path):
    assert unique_path(tmp_path, "dopis", ".pdf").name == "dopis.pdf"
    assert unique_path(tmp_path, "dopis", "pdf").name == "dopis.pdf"
    assert unique_path(tmp_path, "dopis", "").name == "dopis"


def test_unique_path_sanitizes_stem(tmp_path):
    result = unique_path(tmp_path, "a/b: c ")

    assert result.parent == tmp_path
    assert result.name == "a-b- c.docx"


def test_unique_path_gives_up_after_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(naming, "MAX_UNIQUE_ATTEMPTS", 3)
    for name in ("dopis.docx", "dopis (2).docx", "dopis (3).docx"):
        (tmp_path / name).write_bytes(b"x")

    with pytest.raises(FileExistsError) as excinfo:
        unique_path(tmp_path, "dopis")

    assert "příliš mnoho" in str(excinfo.value)
