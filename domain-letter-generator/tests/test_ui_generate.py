"""Testy pohledů „Pole šablony“ (:mod:`dlg.ui.mapping_view`) a „Generovat“
(:mod:`dlg.ui.generate_view`).

Testy potřebují běžící X server — v CI se spouštějí přes ``xvfb-run``::

    xvfb-run -a python -m pytest -q tests/test_ui_generate.py

Testovací šablona se skládá přes ``python-docx`` (vývojová závislost) a
naimportuje se do dočasného ``DLG_HOME``, takže se nikde nesahá na skutečná
data uživatele.
"""

from __future__ import annotations

import time
import zipfile
from pathlib import Path
from typing import Any, Callable

import pytest

tk = pytest.importorskip("tkinter", reason="tkinter není k dispozici")

from dlg import config  # noqa: E402
from dlg.docx_engine import extract_text  # noqa: E402
from dlg.models import OptionalParagraph  # noqa: E402
from dlg.store import TemplateStore, ValueHistory  # noqa: E402
from dlg.ui import generate_view as gv  # noqa: E402
from dlg.ui import mapping_view as mv  # noqa: E402
from dlg.ui import theme, widgets  # noqa: E402

pytestmark = pytest.mark.gui

#: Skutečná implementace — fixture ``bez_otevirani`` ji v modulu nahrazuje.
REALNE_OTEVRENI = gv.open_path


# ---------------------------------------------------------------------------
# testovací šablona a prostředí
# ---------------------------------------------------------------------------
#: Placeholder, který je delší než ``mapping.MULTILINE_THRESHOLD``.
LONG_PLACEHOLDER = (
    "zde bude podrobné odůvodnění výzvy, které je natolik dlouhé, že se pro něj "
    "ve formuláři použije víceřádkové pole s dostatečným prostorem pro delší text"
)


def _build_template_docx(path: Path) -> Path:
    """Malá, ale realistická šablona: hranaté závorky, {{klic}}, varianty i datum."""

    docx = pytest.importorskip("docx", reason="python-docx je vývojová závislost")
    document = docx.Document()
    document.add_paragraph("Vážený pane [Jan Novák],")
    document.add_paragraph("obracíme se na Vás ve věci doménového jména {{domena}}.")
    document.add_paragraph("Držitel je zapsán se sídlem [Ulice 12], [110 00] [Praha].")
    document.add_paragraph("Země držitele: [Česká republika / Slovenská republika].")
    document.add_paragraph("Odůvodnění: [%s]." % LONG_PLACEHOLDER)
    document.add_paragraph("Tento odstavec je zcela volitelný a jde jej z dopisu vypustit.")
    document.add_paragraph("   ")  # prázdný odstavec — nesmí se nabízet jako volitelný
    document.add_paragraph("V Praze dne [1. 1. 2026]")
    document.save(str(path))
    return path


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Dočasné ``DLG_HOME`` — testy se nesmí dotknout skutečného profilu."""

    root = tmp_path / "dlg-home"
    monkeypatch.setenv(config.ENV_HOME, str(root))
    monkeypatch.setenv(config.ENV_OUTPUT_DIR, str(tmp_path / "vystup"))
    assert config.app_home() == root
    return root


@pytest.fixture()
def store(home: Path, tmp_path: Path) -> TemplateStore:
    library = TemplateStore(config.templates_dir())
    library.import_docx(_build_template_docx(tmp_path / "sablona.docx"), "Výzva k nápravě")
    return library


@pytest.fixture()
def history(home: Path) -> ValueHistory:
    return ValueHistory(config.history_path())


@pytest.fixture()
def settings(tmp_path: Path) -> config.Settings:
    return config.Settings(
        output_dir=str(tmp_path / "vystup"),
        open_after_generate=False,
        clear_highlight=True,
        keep_unfilled=True,
    )


@pytest.fixture()
def template_id(store: TemplateStore) -> str:
    return store.list()[0].id


@pytest.fixture()
def root():
    """Okno pro jeden test; bez displeje se test přeskočí."""

    try:
        window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - závisí na prostředí
        pytest.skip(f"Tk se nepodařilo spustit: {exc}")
    window.geometry("1200x760")
    theme.apply_theme(window)
    window.update()
    try:
        yield window
    finally:
        try:
            window.update_idletasks()
            window.destroy()
        except tk.TclError:  # pragma: no cover
            pass


@pytest.fixture(autouse=True)
def bez_dialogu(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Modální okna by test zablokovaly — nahradíme je záznamem do slovníku."""

    recorded: dict[str, list[Any]] = {"error": [], "warning": [], "info": [], "otazka": []}

    def record(name: str, answer: Any = None) -> Callable[..., Any]:
        def handler(_parent: Any, message: str = "", **kwargs: Any) -> Any:
            recorded[name].append(f"{message} {kwargs.get('detail', '')}".strip())
            return answer

        return handler

    monkeypatch.setattr(widgets, "show_error", record("error"))
    monkeypatch.setattr(widgets, "show_warning", record("warning"))
    monkeypatch.setattr(widgets, "show_info", record("info"))
    monkeypatch.setattr(widgets, "ask_yes_no", record("otazka", True))
    return recorded


@pytest.fixture(autouse=True)
def bez_otevirani(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Nikdy nespouštět ``xdg-open``/``os.startfile`` z testu."""

    opened: list[str] = []

    def fake_open(target: Any) -> bool:
        opened.append(str(target))
        return True

    monkeypatch.setattr(gv, "open_path", fake_open)
    return opened


def _pump(window: tk.Misc, predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    """Točí smyčku událostí, dokud podmínka neplatí (nebo nevyprší čas)."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window.update()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _generate_view(root: tk.Tk, store: TemplateStore, history: ValueHistory,
                   settings: config.Settings) -> gv.GenerateView:
    view = gv.GenerateView(root, store, history, lambda: settings, None)
    view.pack(fill="both", expand=True)
    root.update()
    return view


def _mapping_view(root: tk.Tk, store: TemplateStore, on_done: Any = None) -> mv.MappingView:
    view = mv.MappingView(root, store, on_done, None)
    view.pack(fill="both", expand=True)
    root.update()
    return view


def _fill_everything(view: gv.GenerateView) -> dict[str, str]:
    """Vyplní všechna pole formuláře smysluplnými hodnotami."""

    values: dict[str, str] = {}
    for spec in view.meta.ordered_fields():  # type: ignore[union-attr]
        if spec.type == "date":
            value = "09.09.2026"
        elif spec.type == "choice":
            value = spec.options[0] if spec.options else "varianta"
        elif spec.type == "multiline":
            value = "Odůvodnění na dvou\nřádcích."
        else:
            value = f"Hodnota {spec.key}"
        values[spec.key] = value
    view.set_values(values)
    view.update_preview()
    return values


# ---------------------------------------------------------------------------
# čisté funkce (bez okna)
# ---------------------------------------------------------------------------
def test_normalize_key_udela_strojovy_klic() -> None:
    assert mv.normalize_key("Držitel doménového jména") == "drzitel_domenoveho_jmena"
    assert mv.normalize_key("PSČ") == "psc"
    assert mv.normalize_key("2. adresa").startswith("pole_")
    assert mv.KEY_RE.match(mv.normalize_key("Ulice a č. p."))


def test_is_meaningful_paragraph_odmita_prazdne() -> None:
    assert mv.is_meaningful_paragraph("Vážený pane,")
    assert not mv.is_meaningful_paragraph("")
    assert not mv.is_meaningful_paragraph("     ")
    assert not mv.is_meaningful_paragraph("——")


# ---------------------------------------------------------------------------
# MappingView
# ---------------------------------------------------------------------------
def test_mapping_view_load_ukaze_pole_a_souhrn(
    root: tk.Tk, store: TemplateStore, template_id: str
) -> None:
    view = _mapping_view(root, store)
    assert view.load(template_id) is True

    meta = store.get(template_id)
    assert len(view.tree.get_children()) == len(meta.fields)
    souhrn = view.summary_label.cget("text")
    assert "placeholder" in souhrn
    assert str(len(meta.fields)) in souhrn

    # editační panel je naplněný prvním polem
    prvni = meta.ordered_fields()[0]
    assert view.selected_field() is not None
    assert view.key_field.get() == prvni.key
    assert view.label_field.get() == prvni.label
    # přehled placeholderů ukazuje i kontext z dokumentu
    prehled = view.places_text.get("1.0", "end-1c")
    assert "[Jan Novák]" in prehled

    assert view.has_unsaved_changes() is False


def test_mapping_view_typy_a_varianty_se_nactou(
    root: tk.Tk, store: TemplateStore, template_id: str
) -> None:
    view = _mapping_view(root, store)
    view.load(template_id)

    meta = store.get(template_id)
    vyber = next(f for f in meta.fields if f.type == "choice")
    assert view.select_field(vyber.key) is True
    assert view.type_field.get() == "výběr"
    assert view.options_field.get().splitlines() == vyber.options

    viceradkovy = next(f for f in meta.fields if f.type == "multiline")
    assert view.select_field(viceradkovy.key) is True
    assert view.type_field.get() == "víceřádkový"


def test_mapping_view_ulozi_upravene_pole(
    root: tk.Tk, store: TemplateStore, template_id: str
) -> None:
    hotovo: list[Any] = []
    view = _mapping_view(root, store, on_done=lambda saved: hotovo.append(saved))
    view.load(template_id)

    puvodni = store.get(template_id).ordered_fields()[0]
    view.select_field(puvodni.key)
    view.label_field.set("Držitel domény")
    view.key_field.set("drzitel_domeny")
    view.default_field.set("Jan Novák")
    view.required_var.set(False)
    assert view.has_unsaved_changes() is True

    assert view.save() is True
    assert hotovo == [True]

    ulozeno = store.get(template_id)
    zmenene = ulozeno.field_by_key("drzitel_domeny")
    assert zmenene is not None
    assert zmenene.label == "Držitel domény"
    assert zmenene.default == "Jan Novák"
    assert zmenene.required is False
    assert zmenene.placeholder_ids == puvodni.placeholder_ids
    assert view.has_unsaved_changes() is False


def test_mapping_view_hlida_unikatnost_klicu(
    root: tk.Tk, store: TemplateStore, template_id: str, bez_dialogu: dict[str, list[Any]]
) -> None:
    view = _mapping_view(root, store)
    view.load(template_id)

    pole = store.get(template_id).ordered_fields()
    view.select_field(pole[1].key)
    view.key_field.set(pole[0].key)  # duplicitní klíč

    problemy = view.validate()
    assert any("jedinečné" in p for p in problemy)
    assert view.save() is False
    assert bez_dialogu["error"], "uživatel musí dostat hlášku"
    assert "dvakrát" in bez_dialogu["error"][0]
    # v knihovně nesmí zůstat nic uloženého
    assert store.get(template_id).field_by_key(pole[1].key) is not None


def test_mapping_view_odmitne_neplatny_klic(
    root: tk.Tk, store: TemplateStore, template_id: str
) -> None:
    view = _mapping_view(root, store)
    view.load(template_id)
    view.key_field.set("Držitel!")
    problemy = view.validate()
    assert problemy and "není platný" in problemy[0]
    assert "drzitel" in problemy[0]


def test_mapping_view_slouceni_a_rozdeleni(
    root: tk.Tk, store: TemplateStore, template_id: str
) -> None:
    view = _mapping_view(root, store)
    view.load(template_id)

    pole = store.get(template_id).ordered_fields()
    prvni, druhy = pole[0].key, pole[1].key
    pocet = len(view.field_specs())

    assert view.merge_fields(prvni, druhy) is True
    po_slouceni = view.field_specs()
    assert len(po_slouceni) == pocet - 1
    slouceny = next(f for f in po_slouceni if f.key == prvni)
    assert slouceny.placeholder_ids == pole[0].placeholder_ids + pole[1].placeholder_ids
    assert view.tree.set(view.tree.get_children()[0], "mista") == "2"

    nove = view.split_field(prvni)
    assert len(nove) == 2
    rozdelena = view.field_specs()
    assert len(rozdelena) == pocet
    assert [f.placeholder_ids for f in rozdelena[:2]] == [
        pole[0].placeholder_ids,
        pole[1].placeholder_ids,
    ]
    assert len({f.key for f in rozdelena}) == len(rozdelena)


def test_mapping_view_zmena_poradi(
    root: tk.Tk, store: TemplateStore, template_id: str
) -> None:
    view = _mapping_view(root, store)
    view.load(template_id)
    puvodni = [f.key for f in view.field_specs()]

    assert view.move_field(puvodni[0], -1) is False  # první už je nahoře
    assert view.move_field(puvodni[0], +1) is True
    prehozene = [f.key for f in view.field_specs()]
    assert prehozene[:2] == [puvodni[1], puvodni[0]]
    assert [f.order for f in view.field_specs()] == list(range(len(prehozene)))

    assert view.save() is True
    assert [f.key for f in store.get(template_id).ordered_fields()] == prehozene


def test_mapping_view_volitelne_odstavce(
    root: tk.Tk, store: TemplateStore, template_id: str
) -> None:
    view = _mapping_view(root, store)
    view.load(template_id)

    nabizene = view.candidate_paragraphs()
    assert nabizene, "aspoň jeden odstavec musí jít nabídnout"
    assert all(mv.is_meaningful_paragraph(p.text) for p in nabizene)
    # prázdný odstavec ze šablony se nenabízí
    vsechny = store.scan(template_id).paragraphs
    assert len(nabizene) < len(vsechny)

    volitelny = next(p for p in nabizene if "volitelný" in p.text)
    assert view.set_paragraph_optional(
        volitelny.id, True, label="Nabídka smíru", included=False
    )
    assert view.has_unsaved_changes() is True
    assert view.save() is True

    ulozene = store.get(template_id).optional_paragraphs
    assert [p.paragraph_id for p in ulozene] == [volitelny.id]
    assert ulozene[0].label == "Nabídka smíru"
    assert ulozene[0].included_by_default is False

    # a jde to zase vypnout
    view.set_paragraph_optional(volitelny.id, False)
    assert view.save() is True
    assert store.get(template_id).optional_paragraphs == []


def test_mapping_view_vzor_nazvu_souboru(
    root: tk.Tk, store: TemplateStore, template_id: str
) -> None:
    view = _mapping_view(root, store)
    view.load(template_id)

    assert "{datum}" in view.pattern_field.help_label.cget("text")
    view.pattern_field.set("{datum}_vyzva")
    view.update()
    assert view.pattern_preview.cget("text").endswith("_vyzva.docx")

    assert view.save() is True
    assert store.get(template_id).output_pattern == "{datum}_vyzva"


def test_mapping_view_varuje_pri_odchodu_s_neulozenymi_zmenami(
    root: tk.Tk, store: TemplateStore, template_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    odchody: list[Any] = []
    view = _mapping_view(root, store, on_done=lambda saved: odchody.append(saved))
    view.load(template_id)

    view.go_back()  # nic se nezměnilo — jde se rovnou
    assert odchody == [False]

    view.label_field.set("Něco jiného")
    odpovedi: list[str] = []

    def odmitnout(_parent: Any, message: str = "", **kwargs: Any) -> bool:
        odpovedi.append(message)
        return False

    monkeypatch.setattr(widgets, "ask_yes_no", odmitnout)
    view.go_back()
    assert odchody == [False], "při odmítnutí se nikam neodchází"
    assert odpovedi and "neuložené změny" in odpovedi[0]

    monkeypatch.setattr(widgets, "ask_yes_no", lambda *a, **k: True)
    view.go_back()
    assert odchody == [False, False]


# ---------------------------------------------------------------------------
# GenerateView
# ---------------------------------------------------------------------------
def test_generate_view_postavi_formular(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str,
) -> None:
    view = _generate_view(root, store, history, settings)
    assert view.load(template_id) is True

    meta = store.get(template_id)
    assert sorted(view.values()) == sorted(f.key for f in meta.fields)

    # typ pole -> typ widgetu
    for spec in meta.fields:
        widget = view._fields[spec.key]
        if spec.type == "multiline":
            assert isinstance(widget, widgets.LabeledText)
        elif spec.type == "date":
            assert isinstance(widget, widgets.LabeledDate)
        else:
            assert isinstance(widget, widgets.LabeledCombobox)

    # výběr nabízí varianty a zároveň dovolí vlastní text
    vyber = next(f for f in meta.fields if f.type == "choice")
    combobox = view._fields[vyber.key].combobox  # type: ignore[union-attr]
    assert list(combobox.cget("values")) == vyber.options
    assert str(combobox.cget("state")) != "readonly"

    # datum je předvyplněné dneškem
    datum = next(f for f in meta.fields if f.type == "date")
    assert view.values()[datum.key] == widgets.format_date(__import__("datetime").date.today())


def test_generate_view_vychozi_hodnoty_z_profilu_a_defaultu(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str,
) -> None:
    """Historie se jen našeptává. Dosadit ji by znamenalo dopis na cizí jméno."""

    meta = store.get(template_id)
    textova = [f for f in meta.ordered_fields() if f.type == "text"]
    z_profilu, z_defaultu, z_historie = textova[0], textova[1], textova[2]

    z_defaultu.default = "Výchozí z šablony"
    store.save_meta(meta)

    settings.profile = {z_profilu.key: "Z profilu"}
    history.remember({z_historie.key: "Z historie", z_defaultu.key: "Historie prohraje"})

    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    hodnoty = view.values()

    assert hodnoty[z_profilu.key] == "Z profilu"
    assert hodnoty[z_defaultu.key] == "Výchozí z šablony"
    assert hodnoty[z_historie.key] == ""
    # našeptávač textového pole historii zná, jen ji sám nedosadí
    combobox = view._fields[z_historie.key].combobox  # type: ignore[union-attr]
    assert "Z historie" in combobox.completion_values()


def test_generate_view_neprevezme_udaje_predchoziho_klienta(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str,
) -> None:
    """Po vygenerování dopisu musí být formulář pro dalšího klienta prázdný."""

    prvni = _generate_view(root, store, history, settings)
    prvni.load(template_id)
    _fill_everything(prvni)
    prvni.generate()
    assert _pump(root, lambda: prvni.last_output_path is not None)
    prvni.destroy()

    druhy = _generate_view(root, store, history, settings)
    druhy.load(template_id)
    textova = [
        f.key for f in store.get(template_id).ordered_fields() if f.type == "text"
    ]
    assert all(druhy.values()[klic] == "" for klic in textova)
    # a aplikace netvrdí, že je hotovo
    assert druhy.validate_form(), "prázdná povinná pole musí kontrolu neprojít"


def test_generate_view_nahled_se_prepocita_se_zpozdenim(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str,
) -> None:
    view = _generate_view(root, store, history, settings)
    view.load(template_id)

    meta = store.get(template_id)
    klic = next(f.key for f in meta.ordered_fields() if f.type == "text")

    view.set_value(klic, "Zbrusu nová hodnota")
    assert view._preview_job is not None, "přepočet se plánuje, neběží hned"
    assert "Zbrusu nová hodnota" not in view.preview_text()

    # další změna předchozí plán zruší (after_cancel) a naplánuje nový
    prvni_plan = view._preview_job
    view.set_value(klic, "Zbrusu nová hodnota 2")
    assert view._preview_job != prvni_plan

    assert _pump(root, lambda: "Zbrusu nová hodnota 2" in view.preview_text())
    assert view._preview_job is None
    assert "[Jan Novák]" not in view.preview_text()

    # dosazená místa jsou v náhledu otagovaná
    rozsahy = view.preview.tag_ranges(gv.FILLED_TAG)
    assert rozsahy
    prvni = view.preview.get(str(rozsahy[0]), str(rozsahy[1]))
    assert prvni == "Zbrusu nová hodnota 2"


def test_generate_view_hlasi_nevyplnena_mista(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str,
) -> None:
    view = _generate_view(root, store, history, settings)
    view.load(template_id)

    meta = store.get(template_id)
    for spec in meta.fields:  # nepovinná pole, ať projde kontrola
        spec.required = False
    store.save_meta(meta)
    view.load(template_id)
    for klic in view.values():
        view.set_value(klic, "")
    view.update_preview()

    zbyva = view.unfilled_placeholders()
    assert len(zbyva) == len(store.scan(template_id).placeholders)
    hlaska = view.unfilled_label.cget("text")
    assert "Nevyplněných míst" in hlaska
    assert "[Jan Novák]" in hlaska


def test_generate_view_vygeneruje_soubor(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str, tmp_path: Path,
) -> None:
    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    hodnoty = _fill_everything(view)

    assert view.generate() is True
    assert view.busy is True, "po dobu běhu jsou tlačítka zakázaná"
    assert "disabled" in view.toolbar.buttons["generovat"].state()

    assert _pump(root, lambda: view.last_output_path is not None)
    assert view.busy is False
    assert "disabled" not in view.toolbar.buttons["generovat"].state()

    vystup = view.last_output_path
    assert vystup is not None and vystup.exists()
    assert vystup.suffix == ".docx"
    assert vystup.parent == Path(settings.output_dir)
    assert zipfile.is_zipfile(vystup)

    text = extract_text(vystup)
    for spec in store.get(template_id).fields:
        assert hodnoty[spec.key].splitlines()[0] in text
    assert "[Jan Novák]" not in text
    assert "{{domena}}" not in text

    # hodnoty se uložily do historie
    for klic, hodnota in hodnoty.items():
        assert hodnota.splitlines()[0] in "\n".join(history.suggestions(klic)) or \
            hodnota in history.suggestions(klic)


def test_generate_view_nazev_souboru_ze_vzoru(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str,
) -> None:
    meta = store.get(template_id)
    klic = next(f.key for f in meta.ordered_fields() if f.type == "text")
    meta.output_pattern = "vyzva_{%s}" % klic
    store.save_meta(meta)

    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    _fill_everything(view)

    view.set_value(klic, "lego-shop.cz")
    assert view.filename_var.get() == "vyzva_lego-shop.cz"

    # jakmile do pole sáhne uživatel, přestane se přepisovat
    view.filename_entry.focus_set()
    view.filename_var.set("muj-vlastni-nazev")
    view.filename_entry.event_generate("<KeyRelease>")
    root.update()
    view.set_value(klic, "jina-domena.cz")
    assert view.filename_var.get() == "muj-vlastni-nazev"

    view.generate()
    assert _pump(root, lambda: view.last_output_path is not None)
    assert view.last_output_path.stem == "muj-vlastni-nazev"  # type: ignore[union-attr]


def test_generate_view_povinna_pole_se_oznaci_cervene(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str, bez_dialogu: dict[str, list[Any]],
) -> None:
    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    _fill_everything(view)

    meta = store.get(template_id)
    prazdne = [f.key for f in meta.fields if f.required][:2]
    for klic in prazdne:
        view.set_value(klic, "")

    assert view.generate() is False
    assert view.last_output_path is None
    for klic in prazdne:
        assert view._fields[klic].has_error, f"pole {klic} nemá červenou hlášku"
    assert bez_dialogu["error"]
    assert "Formulář není vyplněný" in bez_dialogu["error"][0]
    assert not list(Path(settings.output_dir).glob("*.docx")) if Path(
        settings.output_dir
    ).exists() else True


def test_generate_view_potvrzeni_nevyplnenych_mist(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = store.get(template_id)
    for spec in meta.fields:
        spec.required = False
    store.save_meta(meta)

    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    for klic in view.values():
        view.set_value(klic, "")

    dotazy: list[str] = []

    def odmitnout(_parent: Any, message: str = "", **kwargs: Any) -> bool:
        dotazy.append(f"{message} {kwargs.get('detail', '')}")
        return False

    monkeypatch.setattr(widgets, "ask_yes_no", odmitnout)
    # generate() vrací True = „práce se rozběhla“; dopis se skládá ve vlákně
    # a na nevyplněná místa se aplikace ptá, až když je hotový
    assert view.generate() is True
    assert _pump(root, lambda: bool(dotazy))
    assert _pump(root, lambda: view.busy is False)
    assert view.last_output_path is None
    assert "nevyplněných" in dotazy[0]
    assert "[Jan Novák]" in dotazy[0]
    assert not list(Path(settings.output_dir).glob("*.docx")) if Path(
        settings.output_dir
    ).exists() else True


def test_generate_view_volitelny_odstavec_lze_vypustit(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str,
) -> None:
    scan = store.scan(template_id)
    volitelny = next(p for p in scan.paragraphs if "volitelný" in p.text)
    meta = store.get(template_id)
    meta.optional_paragraphs = [
        OptionalParagraph(paragraph_id=volitelny.id, label="Nabídka smíru")
    ]
    store.save_meta(meta)

    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    _fill_everything(view)
    assert "volitelný" in view.preview_text()
    assert view.dropped_paragraph_ids() == []

    assert view.set_paragraph_included(volitelny.id, False) is True
    view.update_preview()
    assert view.dropped_paragraph_ids() == [volitelny.id]
    assert "volitelný" not in view.preview_text()

    view.generate()
    assert _pump(root, lambda: view.last_output_path is not None)
    assert "volitelný" not in extract_text(view.last_output_path)  # type: ignore[arg-type]


def test_generate_view_otevre_soubor_i_slozku(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str, bez_otevirani: list[str],
) -> None:
    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    _fill_everything(view)

    view.generate(open_after=True)
    assert _pump(root, lambda: view.last_output_path is not None)
    assert bez_otevirani == [str(view.last_output_path)]

    assert view.open_output_dir() is True
    assert bez_otevirani[-1] == str(Path(settings.output_dir))
    assert Path(settings.output_dir).is_dir()


def test_generate_view_prepnuti_sablony_v_comboboxu(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str, tmp_path: Path,
) -> None:
    druha = store.import_docx(
        _build_template_docx(tmp_path / "druha.docx"), "Předžalobní výzva"
    )

    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    assert view.template_id == template_id

    view.refresh_templates()
    nabidka = list(view.template_combo.cget("values"))
    assert "Výzva k nápravě" in nabidka and "Předžalobní výzva" in nabidka

    view.template_var.set("Předžalobní výzva")
    view.template_combo.event_generate("<<ComboboxSelected>>")
    root.update()
    assert view.template_id == druha.id
    assert view.meta is not None and view.meta.name == "Předžalobní výzva"


def test_generate_view_bez_sablon_nespadne(
    root: tk.Tk, home: Path, history: ValueHistory, settings: config.Settings
) -> None:
    prazdny = TemplateStore(config.templates_dir())
    view = _generate_view(root, prazdny, history, settings)
    assert view.load() is False
    assert view.meta is None
    assert view.generate() is False
    assert view.preview_text() == ""


def test_open_path_nespadne_na_neexistujicim_souboru(monkeypatch: pytest.MonkeyPatch) -> None:
    """``open_path`` je poslední článek řetězu — nesmí shodit aplikaci."""

    def vybuchni(*_a: Any, **_k: Any):
        raise OSError("žádný prohlížeč")

    monkeypatch.setattr(gv.subprocess, "Popen", vybuchni)
    monkeypatch.setattr(gv.os, "name", "posix")
    assert REALNE_OTEVRENI("/nikde/neni.docx") is False


def _toplevels(widget: tk.Misc) -> list[tk.Toplevel]:
    """Všechna modální okna pod daným widgetem (Toplevel je dítě svého mastera)."""

    found: list[tk.Toplevel] = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Toplevel):
            found.append(child)
        found.extend(_toplevels(child))
    return found


def test_mapping_view_dialog_slouceni_probehne(
    root: tk.Tk, store: TemplateStore, template_id: str
) -> None:
    """Dialog „Sloučit s…“ je modální — potvrdíme ho z naplánovaného callbacku."""

    view = _mapping_view(root, store)
    view.load(template_id)
    pocet = len(view.field_specs())
    pokusy = {"n": 0, "potvrzeno": False}

    def potvrd() -> None:
        pokusy["n"] += 1
        dialogy = _toplevels(root)
        if dialogy:
            if pokusy["n"] > 100:  # pojistka: test nesmí uvíznout ve wait_window
                dialogy[0].destroy()
            else:
                pokusy["potvrzeno"] = True
                dialogy[0].event_generate("<Return>")
            return
        if pokusy["n"] < 150:
            root.after(20, potvrd)

    root.after(30, potvrd)
    view.merge_selected()
    root.update()

    assert pokusy["potvrzeno"], "dialog se vůbec neotevřel"
    assert _toplevels(root) == [], "dialog se má po potvrzení zavřít"
    assert len(view.field_specs()) == pocet - 1
    assert len(view.tree.get_children()) == pocet - 1


def test_pohledy_snesou_callback_bez_argumentu(
    root: tk.Tk, store: TemplateStore, template_id: str
) -> None:
    """``on_done`` smí být i funkce bez parametrů — app.py to tak často píše."""

    volani: list[str] = []
    view = _mapping_view(root, store, on_done=lambda: volani.append("zpet"))
    view.load(template_id)
    view.go_back()
    assert volani == ["zpet"]


def test_pohledy_snesou_status_bar_i_funkci(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str,
) -> None:
    """Stavový řádek smí být ``StatusBar``, obyčejná funkce, nebo ``None``."""

    hlasky: list[str] = []
    view = mv.MappingView(root, store, None, hlasky.append)
    view.pack(fill="both", expand=True)
    view.load(template_id)
    assert any("připravená" in h for h in hlasky)
    view.destroy()

    bar = widgets.StatusBar(root)
    bar.pack(fill="x")
    generator = gv.GenerateView(root, store, history, lambda: settings, bar)
    generator.pack(fill="both", expand=True)
    generator.load(template_id)
    _fill_everything(generator)
    assert "připravená" in bar.label.cget("text")

    generator.generate()
    assert _pump(root, lambda: generator.last_output_path is not None)
    assert str(generator.last_output_path) in bar.label.cget("text")
    assert bar.label.cget("style") == "Uspech.TLabel"


# ---------------------------------------------------------------------------
# rozepsaný formulář, smazaná šablona, název souboru a cílová složka
# ---------------------------------------------------------------------------
def test_generate_view_se_pta_pred_zahozenim_formulare(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    druha = store.import_docx(
        _build_template_docx(tmp_path / "druha.docx"), "Předžalobní výzva"
    )
    view = _generate_view(root, store, history, settings)
    view.load(template_id)

    # čerstvě načtený formulář se neptá — jsou v něm jen výchozí hodnoty
    assert view.has_unsaved_input() is False

    klic = next(f.key for f in view.meta.ordered_fields() if f.type == "text")
    view.set_value(klic, "Jan Novák")
    assert view.has_unsaved_input() is True

    dotazy: list[str] = []

    def odmitnout(_parent: Any, message: str = "", **kwargs: Any) -> bool:
        dotazy.append(message)
        return False

    monkeypatch.setattr(widgets, "ask_yes_no", odmitnout)
    view.template_var.set("Předžalobní výzva")
    view.template_combo.event_generate("<<ComboboxSelected>>")
    root.update()

    assert dotazy and "rozepsané" in dotazy[0]
    assert view.template_id == template_id
    assert view.values()[klic] == "Jan Novák"
    assert view.template_var.get() == "Výzva k nápravě"

    monkeypatch.setattr(widgets, "ask_yes_no", lambda *a, **k: True)
    view.template_var.set("Předžalobní výzva")
    view.template_combo.event_generate("<<ComboboxSelected>>")
    root.update()
    assert view.template_id == druha.id


def test_generate_view_smazana_posledni_sablona_nespadne(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str, tmp_path: Path, bez_dialogu: dict[str, list[Any]],
) -> None:
    """Po smazání naposledy použité šablony se nabídne jiná, bez chybového okna."""

    druha = store.import_docx(
        _build_template_docx(tmp_path / "druha.docx"), "Předžalobní výzva"
    )
    settings.last_template_id = template_id
    store.delete(template_id)

    view = _generate_view(root, store, history, settings)
    assert view.load() is True
    assert view.template_id == druha.id
    assert bez_dialogu["error"] == []


def test_generate_view_ctecka_v_nazvu_souboru_nezmrazi_prepocet(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str,
) -> None:
    """End ani Ctrl v poli s názvem nejsou úprava textu."""

    meta = store.get(template_id)
    klic = next(f.key for f in meta.ordered_fields() if f.type == "text")
    meta.output_pattern = "vyzva_{%s}" % klic
    store.save_meta(meta)

    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    view.set_value(klic, "stary-klient.cz")
    assert view.filename_var.get() == "vyzva_stary-klient.cz"

    view.filename_entry.focus_set()
    for sekvence in ("<KeyRelease-End>", "<KeyRelease-Control_L>", "<KeyRelease-Left>"):
        view.filename_entry.event_generate(sekvence)
    root.update_idletasks()
    root.update()
    assert view._filename_touched is False

    view.set_value(klic, "novy-klient.cz")
    assert view.filename_var.get() == "vyzva_novy-klient.cz"

    # skutečné přepsání textu se ale musí respektovat
    view.filename_var.set("muj-vlastni-nazev")
    view.filename_entry.event_generate("<KeyRelease>")
    root.update_idletasks()
    root.update()
    view.set_value(klic, "jina-domena.cz")
    assert view.filename_var.get() == "muj-vlastni-nazev"


def test_generate_view_popisek_slozky_se_osvezi(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str, tmp_path: Path,
) -> None:
    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    assert str(settings.output_dir) in view.output_label.cget("text")

    nova = tmp_path / "vystup_B"
    settings.output_dir = str(nova)
    view._filename_touched = True  # ruční název nesmí popisek zablokovat
    view.update_output_hint()
    assert str(nova) in view.output_label.cget("text")


def test_generate_view_selhany_zapis_nenecha_zmetek(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str, monkeypatch: pytest.MonkeyPatch,
    bez_dialogu: dict[str, list[Any]],
) -> None:
    """Plný disk nesmí ve složce nechat nedopsaný .docx ani anglickou hlášku."""

    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    _fill_everything(view)

    puvodni = gv.os.replace

    def selze(src: Any, dst: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(gv.os, "replace", selze)
    view.generate()
    assert _pump(root, lambda: bool(bez_dialogu["error"]))
    assert _pump(root, lambda: view.busy is False)

    hlaska = bez_dialogu["error"][-1]
    assert "Errno" not in hlaska
    assert "volného místa" in hlaska
    slozka = Path(settings.output_dir)
    assert list(slozka.glob("*")) == [], "po neúspěchu nesmí zůstat žádný soubor"

    # po nápravě se dopis uloží pod původním názvem, ne jako „… (2)“
    monkeypatch.setattr(gv.os, "replace", puvodni)
    view.generate()
    assert _pump(root, lambda: view.last_output_path is not None)
    assert " (2)" not in view.last_output_path.name  # type: ignore[union-attr]


def test_generate_view_varuje_pri_upravene_sablone(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str, tmp_path: Path,
) -> None:
    """Šablona upravená ve Wordu — uložené mapování už nemusí sedět."""

    hlasky: list[str] = []
    view = gv.GenerateView(root, store, history, lambda: settings, hlasky.append)
    view.pack(fill="both", expand=True)
    view.load(template_id)
    assert view._mapping_warning == ""

    # uživatel upraví template.docx mimo aplikaci
    _build_template_docx(tmp_path / "jina.docx")
    docx = pytest.importorskip("docx")
    dokument = docx.Document(str(tmp_path / "jina.docx"))
    dokument.paragraphs[0].text = "Úplně jiný začátek dopisu"
    dokument.save(str(store.docx_path(template_id)))
    store.invalidate_scan(template_id)

    view.load(template_id)
    assert view._mapping_warning
    assert "upravena" in view._mapping_warning
    assert any("upravena" in h for h in hlasky)


def test_mapping_view_upozorni_na_poskozene_nastaveni_poli(
    root: tk.Tk, store: TemplateStore, template_id: str,
    bez_dialogu: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vadná položka v meta.json nesmí tiše zmizet při prvním uložení."""

    import json

    cesta = store.meta_path(template_id)
    data = json.loads(cesta.read_text(encoding="utf-8"))
    zdrava = list(data["fields"])
    data["fields"] = [zdrava[0], None] + zdrava[1:]
    cesta.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    view = _mapping_view(root, store)
    assert view.load(template_id) is True
    assert len(view.field_specs()) == len(zdrava)
    assert bez_dialogu["warning"] and "nepodařilo přečíst" in bez_dialogu["warning"][0]

    # uložení se ptá, protože poškozenou část nenávratně přepíše
    dotazy: list[str] = []

    def odmitnout(_parent: Any, message: str = "", **kwargs: Any) -> bool:
        dotazy.append(message)
        return False

    monkeypatch.setattr(widgets, "ask_yes_no", odmitnout)
    assert view.save() is False
    assert dotazy and "nepodařilo přečíst" in dotazy[0]


def test_generate_view_nahled_po_psani_neblokuje_hlavni_vlakno(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str,
) -> None:
    """Přepočet po pauze v psaní musí běžet ve vlákně, ne v Tk smyčce."""

    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    klic = next(f.key for f in view.meta.ordered_fields() if f.type == "text")

    view.set_value(klic, "Novak-do-vlakna")
    assert view._preview_job is not None
    view.schedule_preview(delay=1)
    assert _pump(root, lambda: view._preview_job is None)

    # hned po odpálení plánu ještě náhled hotový není — počítá se jinde
    assert "Novak-do-vlakna" not in view.preview_text()
    assert _pump(root, lambda: "Novak-do-vlakna" in view.preview_text())


def test_generate_view_starsi_nahled_neprepise_novejsi(
    root: tk.Tk, store: TemplateStore, history: ValueHistory, settings: config.Settings,
    template_id: str,
) -> None:
    view = _generate_view(root, store, history, settings)
    view.load(template_id)
    klic = next(f.key for f in view.meta.ordered_fields() if f.type == "text")

    view.set_value(klic, "Prvni-hodnota")
    view.schedule_preview(delay=1)
    root.update()
    view.set_value(klic, "Druha-hodnota")
    view.schedule_preview(delay=1)

    assert _pump(root, lambda: "Druha-hodnota" in view.preview_text())
    root.update()
    assert "Prvni-hodnota" not in view.preview_text()
