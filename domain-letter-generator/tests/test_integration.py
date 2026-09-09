"""Integrační testy: celá cesta od nahrání šablony po hotový .docx.

Na rozdíl od ostatních souborů se tady nic nemockuje — jede se přes skutečné
``TemplateStore`` → ``mapping`` → ``docx_engine`` a nad **reálnou** šablonou
``samples_local/lego_cd_cs.docx``, pokud je k dispozici (v CI není, testy se
přeskočí). Doplňkově se testuje chování balíku v mezních situacích, které
zasahují víc modulů najednou: první spuštění, poškozený ``meta.json``,
soubor, který není .docx, a šablona bez jediného placeholderu.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path
from xml.dom import minidom

import pytest

from conftest import requires_sample  # noqa: F401  (fixtura sample_docx_path)
from dlg import config, mapping
from dlg.docx_engine import DocxError, extract_text, fill_docx, scan_docx
from dlg.models import TemplateMeta
from dlg.store import StoreError, TemplateNotFound, TemplateStore, ValueHistory

import fixtures

# ---------------------------------------------------------------------------
# pomůcky
# ---------------------------------------------------------------------------
#: Hodnoty, které by do dopisu vyplnil právník.
VALUES = {
    "jmeno": "Jan Novák",
    "drzitel": "Jan Novák",
    "ulice": "Krátká 12",
    "psc": "602 00",
    "mesto": "Brno",
    "zeme": "Česká republika",
    "domena": "lego-shop.cz",
    "datum": "9. září 2026",
}


def parts_of(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


def assert_valid_docx(data: bytes) -> zipfile.ZipFile:
    """ZIP musí být čitelný a všechny jeho XML části well-formed."""

    archive = parts_of(data)
    assert archive.testzip() is None
    for name in archive.namelist():
        if name.endswith((".xml", ".rels")):
            minidom.parseString(archive.read(name))  # vyhodí, když je XML rozbité
    return archive


@pytest.fixture()
def store(tmp_path: Path) -> TemplateStore:
    return TemplateStore(tmp_path / "templates")


@pytest.fixture()
def imported(store: TemplateStore, sample_docx_path: Path):
    meta = store.import_docx(sample_docx_path, "LEGO výzva CZ")
    return meta, store.scan(meta.id)


@pytest.fixture()
def generated(imported, sample_docx_path: Path):
    """Vyplněný dokument nad reálnou šablonou."""

    meta, scan = imported
    values = dict(VALUES)
    choice = next(f for f in meta.fields if f.type == "choice" and f.key != "zeme")
    values[choice.key] = next(o for o in choice.options if o.startswith("převedli"))
    data, report = fill_docx(sample_docx_path, mapping.apply_fields(meta.fields, values))
    return meta, scan, values, choice, data, report


# ---------------------------------------------------------------------------
# Celá cesta nad skutečnou šablonou
# ---------------------------------------------------------------------------
@requires_sample
def test_import_navrhne_pole_pro_pravnika(imported):
    meta, scan = imported

    keys = [f.key for f in meta.ordered_fields()]
    assert keys == ["datum", "jmeno", "ulice", "psc", "zeme", "domena", "varianta"]

    by_key = {f.key: f for f in meta.fields}
    assert by_key["datum"].type == "date"
    assert by_key["zeme"].options == ["Česká republika", "Slovenská republika"]
    assert by_key["varianta"].type == "choice"
    assert len(by_key["varianta"].options) == 2
    # každé pole musí ukazovat na existující placeholder
    for spec in meta.fields:
        assert spec.placeholder_ids
        for pid in spec.placeholder_ids:
            assert scan.placeholder(pid) is not None


@requires_sample
def test_vygenerovany_dokument_je_platny_docx(generated, sample_docx_path: Path):
    _, _, _, _, data, report = generated

    out = assert_valid_docx(data)
    src = zipfile.ZipFile(sample_docx_path)
    assert out.namelist() == src.namelist()
    assert report.filled and not report.unfilled and not report.warnings


@requires_sample
def test_hodnoty_jsou_dosazene_a_zastupne_texty_zmizely(generated):
    _, _, values, choice, data, _ = generated

    text = extract_text(data)
    for value in ("Jan Novák", "Krátká 12", "602 00", "Česká republika",
                  "lego-shop.cz", values["datum"], values[choice.key]):
        assert value in text
    for old in ("[Jan Novák]", "[Nová 1]", "[110 00]", "[●]",
                "[Česká republika / Slovenská republika]",
                "zrušili registraci Doménového jména", "Slovenská republika"):
        assert old not in text
    # ani osamocené hranaté závorky, ani prázdný pár
    assert "[" not in text and "]" not in text


@requires_sample
def test_na_vyplnenych_mistech_nezustalo_zvyrazneni(generated):
    _, _, _, _, data, _ = generated

    assert "<w:highlight" not in parts_of(data).read("word/document.xml").decode("utf-8")


@requires_sample
def test_sdt_s_datem_se_rozbalilo_na_staticky_text(generated, sample_docx_path: Path):
    _, _, values, _, data, _ = generated

    src = zipfile.ZipFile(sample_docx_path).read("word/document.xml").decode("utf-8")
    out = parts_of(data).read("word/document.xml").decode("utf-8")
    assert "<w:sdt>" in src and "<w:date" in src
    assert "<w:sdtContent" not in out
    assert "<w:date" not in out
    assert values["datum"] in out


@requires_sample
def test_zbytek_dokumentu_zustal_nedotcen(generated, sample_docx_path: Path):
    meta, scan, _, _, data, _ = generated

    src_lines = extract_text(sample_docx_path).split("\n")
    out_lines = extract_text(data).split("\n")
    assert len(src_lines) == len(out_lines)

    changed = {i for i, (a, b) in enumerate(zip(src_lines, out_lines)) if a != b}
    # změnit se smí právě tolik odstavců, kolik jich obsahuje placeholder
    assert len(changed) == len({p.paragraph_id for p in scan.placeholders})
    for index in range(len(src_lines)):
        if index not in changed:
            assert src_lines[index] == out_lines[index]

    src_xml = zipfile.ZipFile(sample_docx_path).read("word/document.xml").decode("utf-8")
    out_xml = parts_of(data).read("word/document.xml").decode("utf-8")
    for tag in ("<w:p ", "<w:tbl>", "<w:drawing>", "<w:tc>"):
        assert src_xml.count(tag) == out_xml.count(tag), tag


@requires_sample
def test_ostatni_casti_balicku_jsou_bajtove_identicke(generated, sample_docx_path: Path):
    _, _, _, _, data, _ = generated

    src = zipfile.ZipFile(sample_docx_path)
    out = parts_of(data)
    media = [n for n in src.namelist() if n.startswith("word/media/")]
    assert media, "ukázková šablona má obsahovat obrázky"
    for name in src.namelist():
        if name == "word/document.xml":
            continue
        assert src.read(name) == out.read(name), name


@requires_sample
def test_vypusteni_odstavce_nechá_dokument_platny(generated, sample_docx_path: Path):
    meta, scan, values, choice, first, _ = generated

    target = next(p for p in scan.paragraphs if p.text.startswith("Jak je Vám patrně známo"))
    values = dict(values)
    values[choice.key] = next(o for o in choice.options if o.startswith("převedli"))
    data, report = fill_docx(
        sample_docx_path,
        mapping.apply_fields(meta.fields, values),
        drop_paragraphs=[target.id],
    )

    assert_valid_docx(data)
    assert report.dropped_paragraphs == [target.id]
    lines = extract_text(data).split("\n")
    assert target.text not in lines
    assert lines == [line for line in extract_text(first).split("\n") if line != target.text]


@requires_sample
def test_generovani_je_deterministicke(generated, sample_docx_path: Path):
    meta, _, values, choice, first, _ = generated

    values = dict(values)
    values[choice.key] = next(o for o in choice.options if o.startswith("převedli"))
    again, _ = fill_docx(sample_docx_path, mapping.apply_fields(meta.fields, values))
    assert parts_of(first).read("word/document.xml") == parts_of(again).read("word/document.xml")


# ---------------------------------------------------------------------------
# První spuštění — prázdný datový domov
# ---------------------------------------------------------------------------
def test_prvni_spusteni_nic_nevyzaduje(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(config.ENV_HOME, str(tmp_path / "home"))

    assert TemplateStore().list() == []
    assert ValueHistory().suggestions("jmeno") == []
    assert config.load_settings().output_dir


# ---------------------------------------------------------------------------
# Poškozená data v knihovně šablon
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "obsah",
    [
        pytest.param("{ tohle není json", id="nevalidni-json"),
        pytest.param("", id="prazdny-soubor"),
        pytest.param("[1, 2, 3]", id="pole-misto-objektu"),
        pytest.param("null", id="json-null"),
        pytest.param('{"id": 5, "name": null, "fields": "ne-seznam"}', id="nesmyslne-typy"),
        pytest.param('{"optional_paragraphs": 7, "tags": 3}', id="nesmyslne-typy-2"),
        pytest.param("{}", id="prazdny-objekt"),
    ],
)
def test_poskozeny_meta_json_neshodi_seznam(store: TemplateStore, tmp_path: Path, obsah: str):
    zdroj = tmp_path / "vyzva.docx"
    zdroj.write_bytes(fixtures.make_docx(fixtures.document_xml(
        fixtures.text_paragraph("[Jan Novák]"))))
    dobra = store.import_docx(zdroj, "Dobrá šablona")

    rozbita = store.root / "rozbita-000000"
    rozbita.mkdir(parents=True)
    (rozbita / "template.docx").write_bytes(zdroj.read_bytes())
    (rozbita / "meta.json").write_text(obsah, encoding="utf-8")

    ids = [m.id for m in store.list()]
    assert dobra.id in ids
    for meta in store.list():
        assert isinstance(meta, TemplateMeta)
        assert meta.name.strip(), "šablona bez názvu by v seznamu byla neviditelná"


def test_sablona_bez_docx_hlasi_srozumitelnou_chybu(store: TemplateStore):
    sirotek = store.root / "bez-souboru"
    sirotek.mkdir(parents=True)
    (sirotek / "meta.json").write_text('{"id": "bez-souboru", "name": "Sirotek"}', encoding="utf-8")

    assert [m.id for m in store.list()] == ["bez-souboru"]
    with pytest.raises(TemplateNotFound, match="template.docx"):
        store.read_docx("bez-souboru")
    with pytest.raises(TemplateNotFound, match="template.docx"):
        store.scan("bez-souboru")


def test_poskozene_xml_v_ulozene_sablone_hlasi_store_error(store: TemplateStore, tmp_path: Path):
    zdroj = tmp_path / "vyzva.docx"
    zdroj.write_bytes(fixtures.make_docx(fixtures.document_xml(
        fixtures.text_paragraph("[Jan Novák]"))))
    meta = store.import_docx(zdroj, "Rozbije se")

    store.invalidate_scan()
    store.docx_path(meta.id).write_bytes(
        fixtures.replace_part(zdroj.read_bytes(), "word/document.xml", "<w:document><w:body>")
    )
    with pytest.raises(StoreError):
        store.scan(meta.id)


# ---------------------------------------------------------------------------
# Soubor, který není .docx
# ---------------------------------------------------------------------------
def _not_a_docx() -> dict[str, bytes]:
    zip_bez_dokumentu = io.BytesIO()
    with zipfile.ZipFile(zip_bez_dokumentu, "w") as archive:
        archive.writestr("hello.txt", "ahoj")
    rozbite_xml = io.BytesIO()
    with zipfile.ZipFile(rozbite_xml, "w") as archive:
        archive.writestr("word/document.xml", "<w:document><w:body><w:p></w:body>")
    return {
        "text": b"Tohle je obycejny text, ne dokument.",
        "prazdny": b"",
        "stary-doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64,
        "pdf": b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n",
        "zip-bez-dokumentu": zip_bez_dokumentu.getvalue(),
        "rozbite-xml": rozbite_xml.getvalue(),
    }


@pytest.mark.parametrize("jmeno,data", sorted(_not_a_docx().items()))
def test_import_odmitne_soubor_ktery_neni_docx(
    store: TemplateStore, tmp_path: Path, jmeno: str, data: bytes
):
    falesny = tmp_path / f"{jmeno}.docx"
    falesny.write_bytes(data)

    with pytest.raises(StoreError) as excinfo:
        store.import_docx(falesny, "Nepovedená")
    assert str(excinfo.value).strip()
    assert not list(store.root.glob("*")) or store.list() == []


@pytest.mark.parametrize("jmeno,data", sorted(_not_a_docx().items()))
def test_engine_hlasi_docx_error(jmeno: str, data: bytes):
    for funkce in (scan_docx, extract_text):
        with pytest.raises(DocxError):
            funkce(data)
    with pytest.raises(DocxError):
        fill_docx(data, {})


# ---------------------------------------------------------------------------
# Šablona bez jediného placeholderu
# ---------------------------------------------------------------------------
@pytest.fixture()
def bez_placeholderu(tmp_path: Path) -> Path:
    path = tmp_path / "bez-poli.docx"
    path.write_bytes(
        fixtures.make_docx(
            fixtures.document_xml(
                fixtures.text_paragraph("Vážení, tohle je dopis bez jediného pole.")
                + fixtures.text_paragraph("S pozdravem")
            )
        )
    )
    return path


def test_sablona_bez_placeholderu_jde_nahrat_i_vygenerovat(
    store: TemplateStore, bez_placeholderu: Path
):
    meta = store.import_docx(bez_placeholderu, "Bez polí")
    scan = store.scan(meta.id)

    assert meta.fields == []
    assert scan.placeholders == []
    assert len(scan.paragraphs) == 2
    assert mapping.suggest_fields(scan) == []
    assert mapping.apply_fields(meta.fields, {"cokoliv": "x"}) == {}

    data, report = fill_docx(store.read_docx(meta.id), {})
    assert_valid_docx(data)
    assert report.filled == [] and report.unfilled == [] and report.warnings == []
    assert data == store.read_docx(meta.id), "beze změn se má vrátit původní balíček"
    assert "S pozdravem" in extract_text(data)


def test_sablona_bez_placeholderu_snese_i_mazani_nevyplnenych(
    store: TemplateStore, bez_placeholderu: Path
):
    meta = store.import_docx(bez_placeholderu, "Bez polí")

    data, report = fill_docx(store.read_docx(meta.id), {}, keep_unfilled=False)
    assert_valid_docx(data)
    assert report.dropped_paragraphs == []


# ---------------------------------------------------------------------------
# Název výstupního souboru
# ---------------------------------------------------------------------------
@requires_sample
def test_nazev_vystupu_vznikne_z_hodnot(imported):
    from dlg import naming

    meta, _ = imported
    stem = naming.render_pattern(
        "{datum}_{nazev}_{domena}",
        {"domena": "lego-shop.cz"},
        template_name=meta.name,
        today=date(2026, 9, 9),
    )
    assert stem == "2026-09-09_LEGO výzva CZ_lego-shop.cz"
