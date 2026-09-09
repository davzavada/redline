# Generátor dopisů — architektura a kontrakty

Lokální desktopová aplikace pro přípravu dopisů (výzvy k nápravě, předžalobní výzvy)
z uživatelem nahraných šablon ve formátu `.docx`.

## Základní pravidla (platí pro celý projekt)

1. **Žádné runtime závislosti třetích stran.** Aplikace běží na čisté standardní
   knihovně Pythonu (`zipfile`, `xml.parsers.expat`, `tkinter`, `json`, `pathlib`, …).
   `python-docx`, `pytest` a `pyinstaller` jsou pouze *vývojové* závislosti
   (`requirements-dev.txt`).
2. **Bez administrátorských práv.** Vše se ukládá do uživatelského profilu, nic do
   `Program Files`, žádný zápis do registru, žádná instalace, žádné síťové porty.
3. **Cílová platforma**: Windows 10/11 (jeden `.exe` z PyInstalleru). Kód ale musí
   běžet i na Linuxu, aby šly spouštět testy v CI a při vývoji.
4. **Python 3.10+**. Každý modul začíná `from __future__ import annotations`.
5. **Jazyk uživatelského rozhraní je čeština.** Názvy identifikátorů v kódu
   a docstringy jsou anglicky/česky dle uvážení, ale všechny texty viditelné
   uživateli jsou české.
6. **Věrnost dokumentu je nejvyšší priorita.** Vygenerovaný `.docx` se musí od
   šablony lišit *pouze* v místech, která jsme skutečně změnili. Žádné
   přeserializování celého XML, žádné přejmenování namespace prefixů.

## Adresářová struktura

```
domain-letter-generator/
  ARCHITECTURE.md            <- tento soubor
  README.md
  requirements-dev.txt
  build.bat                  <- lokální build na Windows
  DomainLetterGenerator.spec <- PyInstaller
  assets/app.ico
  tools/make_icon.py
  dlg/
    __init__.py
    __main__.py              <- `python -m dlg`
    version.py               <- __version__, APP_NAME, APP_ID
    config.py                <- cesty, Settings
    models.py                <- dataklasy (Placeholder, FieldSpec, TemplateMeta, ...)
    store.py                 <- knihovna šablon, historie hodnot
    mapping.py               <- automatický návrh polí z placeholderů
    naming.py                <- vzor názvu výstupního souboru
    docx_engine/
      __init__.py            <- veřejné API: scan_docx, fill_docx, extract_text
      xmlsplice.py           <- bajtově přesné úpravy XML
      parts.py               <- práce s .docx jako ZIP, výběr částí
      scan.py                <- detekce placeholderů
      fill.py                <- doplnění hodnot
    ui/
      __init__.py
      theme.py
      widgets.py
      templates_view.py
      mapping_view.py
      generate_view.py
      settings_view.py
    app.py                   <- okno aplikace, navigace
  tests/
    conftest.py
    fixtures.py              <- generátory testovacích .docx (python-docx + ruční XML)
    test_xmlsplice.py
    test_scan.py
    test_fill.py
    test_store.py
    test_mapping.py
    test_integration.py
  samples_local/             <- necommitované ukázky (v .gitignore)
```

## Datový model (`dlg/models.py`)

Všechny dataklasy mají `to_dict()` / `from_dict()` pro JSON serializaci
(žádné `dataclasses.asdict` u tříd s vnořenými objekty — psát explicitně).

```python
PlaceholderKind = Literal["bracket", "highlight", "mustache", "sdt"]
FieldType       = Literal["text", "multiline", "choice", "date"]

@dataclass(frozen=True)
class Placeholder:
    id: str            # deterministický, stabilní pro nezměněný soubor
    part: str          # "word/document.xml", "word/header3.xml", ...
    kind: PlaceholderKind
    raw: str           # viditelný text včetně oddělovačů, např. "[Jan Novák]"
    inner: str         # text bez oddělovačů, např. "Jan Novák"
    options: tuple[str, ...]  # varianty ze split(" / "), jinak ()
    context: str       # okolní text odstavce pro náhled (max ~200 znaků)
    paragraph_id: str  # id odstavce, ve kterém placeholder leží
    order: int         # pořadí v dokumentu (napříč částmi), 0-based

@dataclass(frozen=True)
class ParagraphInfo:
    id: str
    part: str
    order: int
    text: str
    style: str | None
    in_table: bool

@dataclass
class ScanResult:
    placeholders: list[Placeholder]
    paragraphs: list[ParagraphInfo]

@dataclass
class FieldSpec:
    key: str                 # strojový klíč, [a-z0-9_]+, unikátní v šabloně
    label: str               # české označení pro formulář
    type: FieldType = "text"
    options: list[str] = []  # pro type == "choice"
    default: str = ""
    required: bool = True
    help: str = ""
    placeholder_ids: list[str] = []   # jeden údaj může plnit více placeholderů
    order: int = 0

@dataclass
class OptionalParagraph:
    paragraph_id: str
    label: str
    included_by_default: bool = True

@dataclass
class TemplateMeta:
    id: str                  # slug + krátký hash
    name: str
    description: str = ""
    tags: list[str] = []
    source_filename: str = ""
    imported_at: str = ""    # ISO 8601
    updated_at: str = ""
    fields: list[FieldSpec] = []
    optional_paragraphs: list[OptionalParagraph] = []
    output_pattern: str = "{datum}_{nazev}"
    schema_version: int = 1

@dataclass
class FillReport:
    filled: list[str]           # placeholder_id
    unfilled: list[str]         # placeholder_id ponechané beze změny
    dropped_paragraphs: list[str]
    warnings: list[str]
```

## Kontrakt: `dlg.docx_engine`

```python
def scan_docx(path_or_bytes: Path | bytes) -> ScanResult: ...

def fill_docx(
    src: Path | bytes,
    values: Mapping[str, str],          # klíč = Placeholder.id
    *,
    drop_paragraphs: Iterable[str] = (),# ParagraphInfo.id
    clear_highlight: bool = True,
    keep_unfilled: bool = True,         # False => nevyplněné placeholdery se smažou
) -> tuple[bytes, FillReport]: ...      # bytes = celý nový .docx

def extract_text(path_or_bytes: Path | bytes) -> str: ...
    # prostý text hlavního dokumentu pro náhled (odstavce oddělené \n)
```

### Jak se detekují placeholdery (`scan.py`)

Prohledávané části ZIPu: `word/document.xml`, `word/header*.xml`, `word/footer*.xml`,
`word/footnotes.xml`, `word/endnotes.xml`. **Nikdy** `word/glossary/*`.

Pro každý odstavec (`w:p`, včetně odstavců v tabulkách a v textových polích) se
poskládá text ze všech `w:t` v pořadí dokumentu a hledá se:

1. **`sdt`** — odstavec/běh uvnitř `w:sdt`, jehož `w:sdtPr` obsahuje `w:date`
   nebo `w:showingPlcHdr`. Celý text `sdtContent` je jeden placeholder
   (`kind="sdt"`). Má přednost před vším ostatním ve svém rozsahu.
2. **`{{klic}}`** — regex `\{\{\s*([^{}]+?)\s*\}\}`.
3. **`[...]`** — regex `\[([^\[\]]{1,400})\]`. Vnitřek nesmí být prázdný.
4. **zvýraznění** — souvislé úseky znaků, jejichž běh má `w:highlight`
   s hodnotou jinou než `none`. Použije se jen tam, kde úsek **není** pokryt
   žádným nálezem z bodů 1–3.

Překryvy se řeší v tomto pořadí priority (1 > 2 > 3 > 4); kandidát, který se
překrývá s již přijatým nálezem, se zahodí.

`options` vzniká rozdělením `inner` na `" / "` (mezera-lomítko-mezera) — díky
tomu zůstane „LEGO Holding A/S“ vcelku. Rozdělí se jen tehdy, vzniknou-li
aspoň dvě neprázdné části.

`Placeholder.id` = `"ph_" + sha1(f"{part}|{para_index}|{start}|{end}|{raw}").hexdigest()[:12]`
`ParagraphInfo.id` = `"pg_" + sha1(f"{part}|{para_index}|{text[:120]}").hexdigest()[:12]`

### Jak se doplňují hodnoty (`fill.py`)

Úpravy se provádějí jako **bajtové splice operace nad původním XML** (viz
`xmlsplice.py`), nikdy přeserializováním stromu. Postup pro jeden placeholder:

* Nová hodnota se zapíše do prvního `w:t` rozsahu; ze zbývajících `w:t`
  v rozsahu se odpovídající část textu odstraní.
* Text se XML-escapuje (`&`, `<`, `>`).
* `\n` v hodnotě se převede na `</w:t><w:br/><w:t xml:space="preserve">`.
* Pokud výsledný text `w:t` začíná nebo končí bílým znakem, doplní se
  `xml:space="preserve"` do počáteční značky.
* Při `clear_highlight=True` se ze všech dotčených běhů odstraní element
  `w:highlight` (i z `w:rPr` v `w:pPr`, pokud tam je).
* Leží-li placeholder uvnitř `w:sdt`, sdt se „rozbalí“ — odstraní se
  `<w:sdt>…<w:sdtContent>` a odpovídající `</w:sdtContent></w:sdt>`, takže
  hodnota zůstane statická a Word ji nepřepíše.

Vypuštění odstavce = smazání celého rozsahu `<w:p …>…</w:p>`. Je-li to jediný
odstavec v buňce tabulky (`w:tc`), odstavec se místo smazání vyprázdní
(Word vyžaduje aspoň jeden `w:p` v buňce) a do `FillReport.warnings` se přidá
poznámka.

Editace se sbírají do seznamu, kontroluje se, že se nepřekrývají, a aplikují
se najednou (od konce dokumentu k začátku).

Výstupní ZIP se skládá znovu z původního: nedotčené položky se kopírují
bajt po bajtu, upravené se zapisují s `ZIP_DEFLATED`. Pořadí položek se
zachovává.

## Kontrakt: `dlg.config`

```python
def app_home() -> Path
    # $DLG_HOME  |  %LOCALAPPDATA%\DomainLetterGenerator  |  ~/.local/share/domain-letter-generator

def templates_dir() -> Path      # app_home()/templates
def default_output_dir() -> Path # Dokumenty/Generátor dopisů (Windows) nebo ~/Generátor dopisů

@dataclass
class Settings:
    output_dir: str
    open_after_generate: bool = True
    clear_highlight: bool = True
    keep_unfilled: bool = True
    last_template_id: str = ""
    profile: dict[str, str] = {}   # výchozí hodnoty podle klíče pole (napříč šablonami)

def load_settings() -> Settings
def save_settings(s: Settings) -> None
```

Zápis JSON vždy atomicky (zápis do `*.tmp` + `os.replace`), kódování UTF-8,
`ensure_ascii=False`, `indent=2`.

## Kontrakt: `dlg.store`

```python
class TemplateStore:
    def __init__(self, root: Path | None = None) -> None       # root = templates_dir()
    def list(self) -> list[TemplateMeta]                        # seřazeno podle name
    def get(self, template_id: str) -> TemplateMeta
    def docx_path(self, template_id: str) -> Path
    def read_docx(self, template_id: str) -> bytes
    def import_docx(self, src: Path, name: str, *, description: str = "",
                    tags: Sequence[str] = ()) -> TemplateMeta   # zkopíruje soubor, scan, auto-mapping
    def save_meta(self, meta: TemplateMeta) -> None
    def duplicate(self, template_id: str, new_name: str) -> TemplateMeta
    def rename(self, template_id: str, new_name: str) -> TemplateMeta
    def delete(self, template_id: str) -> None
    def scan(self, template_id: str) -> ScanResult              # s cache podle mtime

class ValueHistory:
    def __init__(self, path: Path | None = None) -> None        # app_home()/history.json
    def suggestions(self, key: str) -> list[str]                # nejnovější první, max 10
    def remember(self, values: Mapping[str, str]) -> None
```

Uložení šablony: `templates/<id>/template.docx` + `templates/<id>/meta.json`.
`id` = slug názvu (ASCII, `[a-z0-9-]`, max 40 znaků) + `-` + 6 hex znaků.

## Kontrakt: `dlg.mapping`

```python
def suggest_fields(scan: ScanResult) -> list[FieldSpec]
def suggest_optional_paragraphs(scan: ScanResult) -> list[OptionalParagraph]  # zatím []
def apply_fields(fields: Sequence[FieldSpec], values: Mapping[str, str]) -> dict[str, str]
    # klíč pole -> hodnota  ==>  placeholder_id -> hodnota
```

`suggest_fields` slučuje placeholdery se shodným `inner` do jednoho pole,
odvozuje `type` (`" / "` → `choice`, `sdt`/datum → `date`, delší text → `multiline`)
a hádá `key`/`label` podle jednoduché tabulky vzorů (držitel, ulice, PSČ, město,
země, doména, e-mail, telefon, lhůta, datum). Tabulka je v modulu jako data,
snadno rozšiřitelná. Neznámé placeholdery dostanou `key = "pole_1"`, `pole_2`, …
a `label` odvozený z `inner` (zkrácený). Pořadí odpovídá pořadí v dokumentu.

## Kontrakt: `dlg.naming`

```python
def render_pattern(pattern: str, values: Mapping[str, str], *, template_name: str,
                   today: date) -> str
    # {datum} = YYYY-MM-DD, {nazev} = template_name, {klic} = hodnota pole
    # výsledek je bezpečný název souboru (bez / \ : * ? " < > |), bez přípony
def unique_path(directory: Path, stem: str, suffix: str = ".docx") -> Path
    # přidá " (2)", " (3)", … pokud soubor existuje
```

## Uživatelské rozhraní (`dlg/ui`, `dlg/app.py`)

Jedno okno, vlevo svislá navigace, vpravo obsah. Pohledy:

* **Šablony** — seznam, tlačítka *Nahrát šablonu…*, *Upravit pole*, *Duplikovat*,
  *Přejmenovat*, *Smazat*, *Otevřít složku*.
* **Pole šablony** — tabulka rozpoznaných polí (popisek, klíč, typ, varianty,
  výchozí hodnota, povinné) + seznam odstavců, které lze označit jako volitelné.
* **Generovat** — výběr šablony, dynamický formulář, náhled výsledného textu,
  název výstupního souboru, tlačítka *Generovat*, *Generovat a otevřít*.
* **Nastavení** — výstupní složka, chování po generování, odstranění zvýraznění,
  profil (výchozí hodnoty jako podpis, e-mail, telefon).

Technické požadavky UI: čistý `tkinter`/`ttk`, žádné blokující operace v hlavním
vlákně nad ~200 ms (generování je rychlé, ale import velké šablony běží ve vlákně
s `after()` callbackem), DPI awareness na Windows
(`ctypes.windll.shcore.SetProcessDpiAwareness(1)` v `try/except`), otevření
souboru přes `os.startfile` na Windows / `xdg-open` jinde.

## Rozšíření do budoucna (zatím se neimplementuje)

Vyhledání držitele domény (RDAP / CZ.NIC whois) — bude to samostatný modul
`dlg/lookup/` s rozhraním `lookup(domain: str) -> dict[str, str]`, jehož výstup
se namapuje na klíče polí. Nic z toho teď nevzniká; formulář se vyplňuje ručně.
