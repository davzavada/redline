# Generátor dopisů

Program pro Windows, který z vlastních šablon ve Wordu vyrábí hotové dopisy —
výzvy k nápravě, předžalobní výzvy, průvodní dopisy. Šablony si do něj nahrajete
sami, program v nich najde místa k vyplnění a nabídne je jako formulář.

Nic se neinstaluje, nepotřebuje práva správce a nikam se nepřipojuje.
Všechno běží jen na vašem počítači.

---

## K čemu to je

Když se stejný dopis píše podesáté a mění se v něm jen jméno držitele domény,
adresa, doména a lhůta, dělá se to obvykle tak, že se otevře poslední verze,
přepíší se v ní žluté kusy textu a uloží se pod novým jménem. Občas se přitom
něco přehlédne — zůstane cizí jméno, žluté zvýraznění nebo obě varianty věty
najednou.

Tenhle program dělá totéž, ale pořádně: šablona zůstane nedotčená, vyplňuje se
formulář a výsledkem je nový soubor `.docx` bez zvýraznění a bez zbytků.

---

## Jak to funguje

1. **Nahrajete šablonu.** V pohledu *Šablony* zvolíte *Nahrát šablonu…* a vyberete
   svůj `.docx`. Program si udělá vlastní kopii — s originálem už nic nedělá.
2. **Program šablonu přečte.** Najde v ní místa k vyplnění: text v hranatých
   závorkách, značky `{{klic}}`, žlutě zvýrazněné úseky a datumová pole Wordu.
3. **Nabídne je jako formulář.** Každé nalezené místo dostane popisek. Tam, kde
   šablona nabízí varianty oddělené lomítkem (`zrušili registraci / převedli
   doménu`), se místo textového pole objeví rozklikávací výběr.
4. **Vyplníte a vygenerujete.** Program doplní hodnoty, odstraní žluté
   zvýraznění a uloží nový `.docx` do zvolené složky. Formátování, číslování,
   hlavička, zápatí i poznámky pod čarou zůstanou přesně takové, jaké byly —
   program mění jen ta místa, která jste vyplnili.

Šablona v knihovně zůstává pořád stejná, dá se použít znovu a znovu.

### Uložit rovnou i PDF

V patičce pohledu *Generovat* je volba **Uložit vedle dopisu i PDF**. Program
si PDF nevyrábí sám — nechá ho udělat Word, který na počítači stejně máte
(přes jeho vlastní „Uložit jako → PDF“), takže výsledek vypadá přesně tak, jak
dopis vidíte ve Wordu: hlavičkový papír, fonty, zarovnání i číslování sedí.
Když Word není, zkusí se LibreOffice; když není ani ten, volba se vůbec
nenabídne.

Dopis `.docx` se ukládá vždycky — když se převod nepovede, program to řekne
a hotový dopis zůstane, jak byl.

---

## Jak si vyrobit šablonu

Šablona je obyčejný dokument Wordu. Stačí v něm místa k vyplnění označit jedním
ze čtyř způsobů:

| Zápis v dokumentu | K čemu se hodí |
| --- | --- |
| `[Jan Novák]`, `[110 00]`, `[●]` | běžné jednorázové údaje |
| `{{drzitel}}`, `{{domena}}` | když má stejný údaj vyjít na více místech |
| žlutě zvýrazněný text | rychlé značení bez psaní závorek |
| datumové pole Wordu (*Vývojář → Ovládací prvek obsahu – datum*) | datum dopisu |

Hranaté závorky a žluté zvýraznění se dají kombinovat — je zvykem psát
`[Jan Novák]` a celé to navíc zvýraznit žlutě. Program si s tím poradí, každé
místo najde jen jednou.

### Varianty k výběru

Když v dokumentu napíšete varianty oddělené **mezerou, lomítkem a mezerou**,
program z nich udělá rozklikávací výběr:

```
[Česká republika / Slovenská republika]
[zrušili registraci Doménového jména / převedli Doménové jméno na společnost …]
```

Mezery kolem lomítka jsou důležité: díky nim se `LEGO Holding A/S` nebo `s/r/o`
nerozpadne na kusy. Rozdělí se jen to, co má kolem lomítka mezery.

### Na co si dát pozor

* **Nerozdělujte značku formátováním.** Když je v `[Jan Novák]` půlka jména
  tučně a půlka ne, Word text rozseká na několik částí; program to zvládne, ale
  bezpečnější je označit celé místo stejným formátem.
* **Hranatá závorka musí být párová** a nesmí být prázdná — `[]` se ignoruje.
* **Nepoužívejte hranaté závorky pro něco jiného** (odkazy, poznámky), program
  by je nabídl k vyplnění.
* Prohledává se hlavní text, tabulky, textová pole, hlavičky, zápatí i poznámky
  pod čarou. Neprohledávají se stavební bloky (glossary).

---

## Instalace

1. Otevřete stránku **Releases** projektu na GitHubu.
2. Stáhněte **`GeneratorDopisu-onedir.zip`**.
3. Rozbalte celou složku, kam chcete — na plochu, do Dokumentů, na flashku.
4. Spusťte `GeneratorDopisu.exe` uvnitř ní (a udělejte si na něj zástupce).

To je celé. Nic se neinstaluje, nic se nezapisuje do registru, program
nepotřebuje práva správce ani povolení IT oddělení. Odinstaluje se smazáním
složky.

### Proč složka, a ne jeden soubor

U každého vydání je i **`GeneratorDopisu.exe`** — celý program v jediném
souboru. Hodí se na flashku, ale **startuje výrazně pomaleji**: při každém
spuštění se celý svůj obsah (Python, Tcl/Tk, stovky souborů) rozbalí do dočasné
složky, teprve pak se program spustí a po zavření se to zase smaže. Čeká se
přitom hlavně na antivirus, který každý rozbalený soubor kontroluje. Ze stejného
důvodu u jednosouborové varianty častěji vyskočí falešný poplach — je to typický
vzor, který používají i některé škodlivé programy.

Varianta ve složce má soubory rovnou na disku, takže tenhle krok odpadá úplně
a okno naskočí prakticky okamžitě.

### Kdyby si stěžoval antivirus

Program není podepsaný certifikátem (ten stojí několik tisíc korun ročně),
takže ho Windows SmartScreen může napoprvé označit za neznámou aplikaci:
klepněte na *Další informace → Přesto spustit*.

---

## Kde se ukládají data

Šablony a nastavení leží v profilu uživatele:

```
%LOCALAPPDATA%\DomainLetterGenerator\
    settings.json          nastavení programu
    history.json           naposledy použité hodnoty (napovídání ve formuláři)
    templates\             knihovna šablon
        <sablona>\template.docx
        <sablona>\meta.json
```

Cestu vložíte do adresního řádku Průzkumníka tak, jak je — `%LOCALAPPDATA%`
si Windows doplní samy.

Hotové dopisy se ukládají do **Dokumenty\Generátor dopisů**, dokud si
v *Nastavení* nezvolíte jinou složku.

### Záloha

Zkopírujte celou složku `%LOCALAPPDATA%\DomainLetterGenerator`. Obnova = zkopírovat
ji zpátky. Přenos na jiný počítač funguje stejně.

Chcete-li mít program úplně přenosný (třeba na šifrované flashce), nastavte před
spuštěním proměnnou prostředí `DLG_HOME` na cestu, kam se mají data ukládat.

---

## Co zatím neumí

**Automatické dohledání držitele domény.** Údaje o držiteli se zatím vyplňují
ručně. Napojení na registr CZ.NIC a na protokol RDAP je v plánu jako samostatný
modul `dlg/lookup`; jeho výstup se namapuje rovnou na pole formuláře, takže se
podstatná část dopisu vyplní sama. Do té doby si držitele vyhledejte obvyklým
způsobem a hodnoty zadejte do formuláře.

---

## Pro vývojáře

Aplikace **nemá žádné runtime závislosti třetích stran** — běží na čisté
standardní knihovně Pythonu 3.10+ (`zipfile`, `xml`, `tkinter`, `json`).
`pytest`, `python-docx` a `pyinstaller` jsou pouze vývojové závislosti.
Závazný popis architektury a kontraktů modulů je v [`ARCHITECTURE.md`](ARCHITECTURE.md).

### Spuštění ze zdrojových kódů

```bash
python -m dlg
```

### Testy

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Testy uživatelského rozhraní potřebují běžící X server; na Linuxu se spouštějí
přes `xvfb-run -a python -m pytest -q`. Testy, které pracují se skutečnou
ukázkovou šablonou v `samples_local/`, se bez ní samy přeskočí (klientské
dokumenty se do repozitáře necommitují).

### Sestavení .exe (Windows)

```
build.bat
```

Skript si vytvoří `.venv`, doinstaluje do něj PyInstaller a postaví obě
varianty. Výsledek:

```
dist\GeneratorDopisu-onedir\GeneratorDopisu.exe   varianta ve složce (tuhle používejte)
dist\GeneratorDopisu.exe                          jeden soubor (na flashku)
```

Ručně se totéž dělá dvěma běhy PyInstalleru nad jedním spec souborem — varianta
se přepíná proměnnou prostředí `DLG_BUILD_MODE` (bez ní se staví `onedir`):

```
python -m PyInstaller --noconfirm --workpath build\onedir  --distpath dist DomainLetterGenerator.spec
set DLG_BUILD_MODE=onefile
python -m PyInstaller --noconfirm --workpath build\onefile --distpath dist DomainLetterGenerator.spec
```

### Přesun do vlastního repozitáře

Složka `domain-letter-generator/` je soběstačná — kód nikam ven nesahá, jediná
runtime závislost je standardní knihovna Pythonu. Při přesunu stačí:

1. Vzít celou složku jako nový kořen repozitáře.
2. Přenést i `.github/workflows/domain-letter-generator.yml` (leží o patro výš).
3. Ve workflow zrušit `defaults.run.working-directory: domain-letter-generator`,
   filtr `paths:` a předávání zdrojáku artefaktem mezi joby `test` a `build` —
   to obchází soubor s dvojtečkou v názvu, který je jen v tomhle repozitáři,
   a v samostatném repozitáři se Windows job může normálně naklonovat.

### Ikona

`assets/app.ico` se generuje skriptem, ne ručně:

```bash
python tools/make_icon.py          # vygeneruje a zkontroluje
python tools/make_icon.py --check  # jen zkontroluje hlavičky hotového souboru
```

Skript používá jen standardní knihovnu (žádný Pillow) a vyrábí vrstvy
16/32/48/64 px jako BMP a 256 px jako PNG.

### Vydání nové verze

1. Zvednout `__version__` v `dlg/version.py` (číslo se propíše do vlastností
   `.exe`).
2. Otagovat commit jako `dlg-v<verze>`, například `dlg-v0.2.0`, a tag pushnout.
3. Workflow `.github/workflows/domain-letter-generator.yml` pustí testy,
   postaví obě varianty na Windows a založí GitHub Release s oběma soubory.
