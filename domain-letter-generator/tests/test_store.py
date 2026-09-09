"""Testy pro dlg.store — knihovna šablon a historie hodnot."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from dlg import config, store
from dlg.models import FieldSpec, ParagraphInfo, Placeholder, ScanResult, TemplateMeta
from dlg.store import (
    StoreError,
    TemplateNotFound,
    TemplateStore,
    ValueHistory,
    make_template_id,
    slugify,
)

SAMPLE = Path(__file__).resolve().parent.parent / "samples_local" / "lego_cd_cs.docx"

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
    'relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
    '2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def make_docx(path: Path, paragraphs: tuple[str, ...] = ("[Jan Novák]",)) -> Path:
    """Minimální, ale platný .docx — bez závislosti na python-docx."""

    body = "".join(
        "<w:p><w:r><w:t>{}</w:t></w:r></w:p>".format(
            text.replace("&", "&amp;").replace("<", "&lt;")
        )
        for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
        '2006/main"><w:body>' + body + "</w:body></w:document>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/document.xml", document)
    return path


def fake_scan(paragraphs: tuple[str, ...] = ("[Jan Novák]",)) -> ScanResult:
    placeholders = []
    infos = []
    for index, text in enumerate(paragraphs):
        inner = text.strip("[]")
        placeholders.append(
            Placeholder(
                id=f"ph_{index:03d}",
                part="word/document.xml",
                kind="bracket",
                raw=text,
                inner=inner,
                context=text,
                paragraph_id=f"pg_{index:03d}",
                order=index,
            )
        )
        infos.append(
            ParagraphInfo(id=f"pg_{index:03d}", part="word/document.xml", order=index, text=text)
        )
    return ScanResult(placeholders=placeholders, paragraphs=infos)


@pytest.fixture
def scanner(monkeypatch):
    """Nahradí engine (vzniká souběžně) počítadlem volání."""

    calls: list[Path] = []

    def _scan(path: Path) -> ScanResult:
        calls.append(Path(path))
        return fake_scan()

    monkeypatch.setattr(store, "_scan_docx", _scan)
    return calls


@pytest.fixture
def st(tmp_path) -> TemplateStore:
    return TemplateStore(tmp_path / "templates")


@pytest.fixture
def source_docx(tmp_path) -> Path:
    return make_docx(tmp_path / "zdroj" / "vyzva.docx")


# ---------------------------------------------------------------------------
# slug a id
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name,expected",
    [
        ("Výzva k nápravě", "vyzva-k-naprave"),
        ("Předžalobní výzva — LEGO", "predzalobni-vyzva-lego"),
        ("ŽLUŤOUČKÝ kůň", "zlutoucky-kun"),
        ("a/b\\c:d", "a-b-c-d"),
        ("   ", "sablona"),
        ("", "sablona"),
        ("###", "sablona"),
        ("Dopis 2026", "dopis-2026"),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_slugify_is_ascii_and_bounded():
    slug = slugify("Ěščřžýáíé " * 20)

    assert slug.isascii()
    assert len(slug) <= store.SLUG_MAX_LENGTH
    assert not slug.startswith("-") and not slug.endswith("-")
    assert all(ch.isalnum() or ch == "-" for ch in slug)


def test_make_template_id_is_deterministic():
    first = make_template_id("Výzva k nápravě", b"obsah")
    second = make_template_id("Výzva k nápravě", b"obsah")

    assert first == second
    assert first.startswith("vyzva-k-naprave-")
    assert len(first.rsplit("-", 1)[1]) == 6
    assert first != make_template_id("Výzva k nápravě", b"jiny obsah")
    assert first != make_template_id("Jiný název", b"obsah")
    assert first != make_template_id("Výzva k nápravě", b"obsah", salt="2")


# ---------------------------------------------------------------------------
# import_docx
# ---------------------------------------------------------------------------
def test_import_docx_creates_files_and_meta(st, source_docx, scanner):
    meta = st.import_docx(source_docx, "Výzva k nápravě", description="test", tags=["LEGO", ""])

    assert meta.id.startswith("vyzva-k-naprave-")
    assert st.docx_path(meta.id).is_file()
    assert st.meta_path(meta.id).is_file()
    assert st.docx_path(meta.id).read_bytes() == source_docx.read_bytes()
    assert meta.source_filename == "vyzva.docx"
    assert meta.description == "test"
    assert meta.tags == ["LEGO"]
    assert meta.imported_at and meta.updated_at
    assert meta.output_pattern == "{datum}_{nazev}"
    assert [f.key for f in meta.fields] == ["jmeno"]
    assert meta.fields[0].placeholder_ids == ["ph_000"]
    assert scanner and scanner[0] == st.docx_path(meta.id)

    stored = json.loads(st.meta_path(meta.id).read_text(encoding="utf-8"))
    assert stored["name"] == "Výzva k nápravě"
    assert "Výzva" in st.meta_path(meta.id).read_text(encoding="utf-8")


def test_import_docx_uses_filename_when_name_is_empty(st, source_docx, scanner):
    meta = st.import_docx(source_docx, "  ")

    assert meta.name == "vyzva"


def test_import_docx_rejects_missing_file(st, tmp_path, scanner):
    with pytest.raises(StoreError) as excinfo:
        st.import_docx(tmp_path / "nic.docx", "X")

    assert "neexistuje" in str(excinfo.value)


def test_import_docx_rejects_plain_text(st, tmp_path, scanner):
    fake = tmp_path / "dopis.docx"
    fake.write_text("tohle není dokument", encoding="utf-8")

    with pytest.raises(StoreError) as excinfo:
        st.import_docx(fake, "X")

    assert "docx" in str(excinfo.value).lower()
    assert not list((tmp_path / "templates").glob("*")) if (tmp_path / "templates").exists() else True


def test_import_docx_rejects_zip_without_document_xml(st, tmp_path, scanner):
    fake = tmp_path / "archiv.docx"
    with zipfile.ZipFile(fake, "w") as zf:
        zf.writestr("cokoli.txt", "x")

    with pytest.raises(StoreError) as excinfo:
        st.import_docx(fake, "X")

    assert "word/document.xml" in str(excinfo.value)


def test_import_docx_rejects_old_doc_format(st, tmp_path, scanner):
    fake = tmp_path / "stary.doc"
    fake.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)

    with pytest.raises(StoreError) as excinfo:
        st.import_docx(fake, "X")

    assert ".doc" in str(excinfo.value)


def test_import_docx_cleans_up_when_engine_fails(st, source_docx, monkeypatch):
    def boom(path):
        raise ValueError("rozbitý dokument")

    monkeypatch.setattr(store, "_scan_docx", boom)

    with pytest.raises(StoreError) as excinfo:
        st.import_docx(source_docx, "Výzva")

    assert "zpracovat" in str(excinfo.value)
    assert st.list() == []
    assert not any(st.root.iterdir()) if st.root.exists() else True


def test_import_docx_twice_gives_two_templates(st, source_docx, scanner):
    first = st.import_docx(source_docx, "Výzva")
    second = st.import_docx(source_docx, "Výzva")

    assert first.id != second.id
    assert len(st.list()) == 2


# ---------------------------------------------------------------------------
# čtení knihovny
# ---------------------------------------------------------------------------
def test_list_is_sorted_by_name_ignoring_diacritics(st, tmp_path, scanner):
    for name in ("Žaloba", "Ápostrof", "chyba", "Bezva"):
        st.import_docx(make_docx(tmp_path / f"{slugify(name)}.docx"), name)

    assert [m.name for m in st.list()] == ["Ápostrof", "Bezva", "chyba", "Žaloba"]


def test_list_skips_broken_directories(st, source_docx, scanner):
    meta = st.import_docx(source_docx, "Výzva")
    (st.root / "rozbita").mkdir()
    (st.root / "rozbita" / "meta.json").write_text("{tohle není json", encoding="utf-8")
    (st.root / "prazdna").mkdir()

    listed = st.list()

    assert [m.id for m in listed] == [meta.id]


def test_list_of_missing_root_is_empty(tmp_path):
    assert TemplateStore(tmp_path / "neexistuje").list() == []


def test_get_and_read_docx(st, source_docx, scanner):
    meta = st.import_docx(source_docx, "Výzva")

    loaded = st.get(meta.id)

    assert loaded.id == meta.id
    assert loaded.name == "Výzva"
    assert [f.to_dict() for f in loaded.fields] == [f.to_dict() for f in meta.fields]
    assert st.read_docx(meta.id) == source_docx.read_bytes()


def test_get_unknown_raises_czech(st):
    with pytest.raises(TemplateNotFound) as excinfo:
        st.get("neznama-abc123")

    assert "nebyla nalezena" in str(excinfo.value)


@pytest.mark.parametrize("bad", ["../tajne", "a/b", "", "   ", "..", "/etc/passwd"])
def test_invalid_ids_are_refused(st, bad):
    with pytest.raises(TemplateNotFound):
        st.docx_path(bad)


def test_read_docx_without_file(st):
    (st.root / "sablona-abc123").mkdir(parents=True)

    with pytest.raises(TemplateNotFound):
        st.read_docx("sablona-abc123")


# ---------------------------------------------------------------------------
# scan a jeho cache
# ---------------------------------------------------------------------------
def test_scan_is_cached_per_file_stamp(st, source_docx, scanner):
    meta = st.import_docx(source_docx, "Výzva")
    scanner.clear()

    first = st.scan(meta.id)
    second = st.scan(meta.id)

    assert first is second
    assert len(scanner) == 1


def test_scan_reruns_when_file_changes(st, source_docx, scanner):
    meta = st.import_docx(source_docx, "Výzva")
    scanner.clear()
    st.scan(meta.id)

    target = st.docx_path(meta.id)
    make_docx(target, ("[Jan Novák]", "[Nová 1]"))
    import os

    stat = target.stat()
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    st.scan(meta.id)

    assert len(scanner) == 2


def test_scan_of_missing_template(st):
    with pytest.raises(TemplateNotFound):
        st.scan("sablona-abc123")


# ---------------------------------------------------------------------------
# zápis
# ---------------------------------------------------------------------------
def test_save_meta_updates_timestamp(st, source_docx, scanner):
    meta = st.import_docx(source_docx, "Výzva")
    meta.updated_at = ""
    meta.fields.append(FieldSpec(key="mesto", label="Město", placeholder_ids=["ph_000"]))

    st.save_meta(meta)
    reloaded = st.get(meta.id)

    assert reloaded.updated_at
    assert [f.key for f in reloaded.fields] == ["jmeno", "mesto"]


def test_save_meta_of_unknown_template(st):
    with pytest.raises(TemplateNotFound):
        st.save_meta(TemplateMeta(id="sablona-abc123", name="X"))


def test_rename_keeps_id(st, source_docx, scanner):
    meta = st.import_docx(source_docx, "Výzva")

    renamed = st.rename(meta.id, "Předžalobní výzva")

    assert renamed.id == meta.id
    assert st.get(meta.id).name == "Předžalobní výzva"
    assert st.docx_path(meta.id).is_file()


def test_rename_refuses_empty_name(st, source_docx, scanner):
    meta = st.import_docx(source_docx, "Výzva")

    with pytest.raises(StoreError):
        st.rename(meta.id, "   ")


def test_duplicate_copies_document_and_fields(st, source_docx, scanner):
    meta = st.import_docx(source_docx, "Výzva")
    meta.fields[0].default = "Jan Novák"
    st.save_meta(meta)

    copy = st.duplicate(meta.id, "Výzva (kopie)")

    assert copy.id != meta.id
    assert copy.name == "Výzva (kopie)"
    assert copy.fields[0].default == "Jan Novák"
    assert st.read_docx(copy.id) == st.read_docx(meta.id)
    assert len(st.list()) == 2


def test_delete_removes_everything(st, source_docx, scanner):
    meta = st.import_docx(source_docx, "Výzva")

    st.delete(meta.id)

    assert st.list() == []
    assert not st.template_dir(meta.id).exists()
    with pytest.raises(TemplateNotFound):
        st.delete(meta.id)


def test_store_defaults_to_config_templates_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_HOME, str(tmp_path / "home"))

    assert TemplateStore().root == tmp_path / "home" / "templates"


# ---------------------------------------------------------------------------
# Historie hodnot
# ---------------------------------------------------------------------------
def test_history_returns_newest_first(tmp_path):
    history = ValueHistory(tmp_path / "history.json")

    history.remember({"jmeno": "Jan Novák"})
    history.remember({"jmeno": "Petr Svoboda"})

    assert history.suggestions("jmeno") == ["Petr Svoboda", "Jan Novák"]


def test_history_deduplicates(tmp_path):
    history = ValueHistory(tmp_path / "history.json")

    history.remember({"jmeno": "Jan Novák"})
    history.remember({"jmeno": "Petr Svoboda"})
    history.remember({"jmeno": "Jan Novák"})

    assert history.suggestions("jmeno") == ["Jan Novák", "Petr Svoboda"]


def test_history_keeps_at_most_ten(tmp_path):
    history = ValueHistory(tmp_path / "history.json")

    for i in range(15):
        history.remember({"mesto": f"Město {i}"})

    values = history.suggestions("mesto")
    assert len(values) == 10
    assert values[0] == "Město 14"
    assert values[-1] == "Město 5"


def test_history_ignores_empty_values(tmp_path):
    history = ValueHistory(tmp_path / "history.json")

    history.remember({"jmeno": "", "mesto": "   ", "zeme": None, "ulice": "Nová 1"})

    assert history.suggestions("jmeno") == []
    assert history.suggestions("mesto") == []
    assert history.suggestions("zeme") == []
    assert history.suggestions("ulice") == ["Nová 1"]
    assert history.suggestions("cokoli") == []


def test_history_is_persistent(tmp_path):
    path = tmp_path / "history.json"
    ValueHistory(path).remember({"jmeno": "Jan Novák", "psc": "110 00"})

    again = ValueHistory(path)

    assert again.suggestions("jmeno") == ["Jan Novák"]
    assert again.suggestions("psc") == ["110 00"]
    assert "Jan Novák" in path.read_text(encoding="utf-8")


def test_history_survives_broken_file(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("rozbité", encoding="utf-8")

    history = ValueHistory(path)

    assert history.suggestions("jmeno") == []
    history.remember({"jmeno": "Jan Novák"})
    assert ValueHistory(path).suggestions("jmeno") == ["Jan Novák"]


def test_history_suggestions_are_a_copy(tmp_path):
    history = ValueHistory(tmp_path / "history.json")
    history.remember({"jmeno": "Jan Novák"})

    history.suggestions("jmeno").append("podvrh")

    assert history.suggestions("jmeno") == ["Jan Novák"]


def test_history_clear(tmp_path):
    path = tmp_path / "history.json"
    history = ValueHistory(path)
    history.remember({"jmeno": "Jan Novák", "psc": "110 00"})

    history.clear("psc")
    assert history.suggestions("psc") == []
    assert history.suggestions("jmeno") == ["Jan Novák"]

    history.clear()
    assert ValueHistory(path).suggestions("jmeno") == []


def test_history_defaults_to_app_home(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_HOME, str(tmp_path / "home"))

    assert ValueHistory().path == tmp_path / "home" / "history.json"


# ---------------------------------------------------------------------------
# Skutečná šablona + skutečný engine (pokud už existují)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not SAMPLE.is_file(), reason="ukázková šablona není k dispozici")
def test_import_real_sample_with_real_engine(st):
    pytest.importorskip("dlg.docx_engine", reason="engine se teprve píše")

    meta = st.import_docx(SAMPLE, "LEGO — výzva k nápravě")

    assert meta.id.startswith("lego-vyzva-k-naprave-")
    assert st.docx_path(meta.id).read_bytes() == SAMPLE.read_bytes()
    assert meta.fields, "v ukázkové šabloně se mají najít pole"

    keys = [f.key for f in meta.fields]
    assert len(set(keys)) == len(keys)
    assert st.scan(meta.id) is st.scan(meta.id)


# ---------------------------------------------------------------------------
# poškozený meta.json nesmí sebrat celé mapování
# ---------------------------------------------------------------------------
def _meta_soubor(st: TemplateStore, data: dict) -> str:
    directory = st.root / "vyzva-abc123"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "template.docx").write_bytes(b"PK\x03\x04")
    (directory / "meta.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    return "vyzva-abc123"


def test_vadna_polozka_v_fields_nesmaze_zdrava_pole(st: TemplateStore) -> None:
    tid = _meta_soubor(
        st,
        {
            "id": "vyzva-abc123",
            "name": "Výzva",
            "tags": ["a"],
            "fields": [
                {"key": "jmeno", "label": "Jméno", "placeholder_ids": ["ph_1"]},
                None,
                {"key": "psc", "label": "PSČ", "placeholder_ids": ["ph_2"]},
            ],
            "optional_paragraphs": [{"paragraph_id": "pg_1", "label": "Smír"}],
        },
    )

    meta = st.get(tid)
    assert [f.key for f in meta.fields] == ["jmeno", "psc"]
    assert [p.paragraph_id for p in meta.optional_paragraphs] == ["pg_1"]
    assert meta.tags == ["a"]
    assert st.load_problems[tid], "ztráta se musí dát ohlásit uživateli"
    assert any("Pole šablony" in note for note in st.load_problems[tid])


def test_vadny_klic_tags_nesmi_smazat_pole(st: TemplateStore) -> None:
    tid = _meta_soubor(
        st,
        {
            "id": "vyzva-abc123",
            "name": "Výzva",
            "tags": 7,
            "fields": [{"key": "jmeno", "label": "Jméno"}],
        },
    )

    meta = st.get(tid)
    assert [f.key for f in meta.fields] == ["jmeno"]
    assert meta.tags == []
    assert st.load_problems[tid]


def test_fields_jako_slovnik_se_zachrani(st: TemplateStore) -> None:
    tid = _meta_soubor(
        st,
        {
            "id": "vyzva-abc123",
            "name": "Výzva",
            "fields": {"jmeno": {"key": "jmeno", "label": "Jméno"}},
        },
    )

    meta = st.get(tid)
    assert [f.key for f in meta.fields] == ["jmeno"]
    assert st.list()[0].fields[0].key == "jmeno"


def test_zdrava_sablona_zadny_problem_nehlasi(
    st: TemplateStore, source_docx: Path, scanner
) -> None:
    meta = st.import_docx(source_docx, "Výzva")
    st.get(meta.id)
    assert st.load_problems.get(meta.id) is None


# ---------------------------------------------------------------------------
# ověření vazby mapování na dokument (šablona upravená ve Wordu)
# ---------------------------------------------------------------------------
def test_verify_mapping_mlci_u_nezmenene_sablony(
    st: TemplateStore, source_docx: Path
) -> None:
    meta = st.import_docx(source_docx, "Výzva")
    kontrola = st.verify_mapping(meta)
    assert kontrola.changed is False
    assert kontrola.ok
    assert kontrola.message == ""


def test_verify_mapping_odhali_prevtelene_id(st: TemplateStore, tmp_path: Path) -> None:
    """Po smazání odstavců může staré id připadnout jinému místu dokumentu."""

    zdroj = make_docx(
        tmp_path / "v1.docx",
        ("Věc", "Adresa klienta:", "Nová 1", "[Praha]", "Příslušný soud:", "[Praha]"),
    )
    meta = st.import_docx(zdroj, "Výzva")
    scan = st.scan(meta.id)
    mesto, soud = scan.placeholders[0], scan.placeholders[1]
    meta.fields = [
        FieldSpec(key="mesto", label="Město klienta", placeholder_ids=[mesto.id]),
        FieldSpec(key="soud", label="Sídlo soudu", placeholder_ids=[soud.id]),
    ]
    st.save_meta(meta)
    assert st.verify_mapping(st.get(meta.id)).ok

    # uživatel ve Wordu smaže dva řádky adresního bloku
    make_docx(st.docx_path(meta.id), ("Věc", "[Praha]", "Příslušný soud:", "[Praha]"))
    st.invalidate_scan(meta.id)

    kontrola = st.verify_mapping(st.get(meta.id))
    assert kontrola.changed is True
    assert not kontrola.ok
    assert mesto.id in kontrola.suspect_ids  # id přežilo, ale ukazuje jinam
    assert soud.id in kontrola.stale_ids
    assert "upravena" in kontrola.message
