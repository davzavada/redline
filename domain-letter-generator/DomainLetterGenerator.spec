# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec pro „Generátor dopisů“.

Jedním souborem se dají postavit dvě varianty; přepíná se proměnnou prostředí
``DLG_BUILD_MODE``:

* ``onefile`` (výchozí) — jeden samostatný ``dist/GeneratorDopisu.exe``.
* ``onedir``            — složka ``dist/GeneratorDopisu-onedir/`` s ``GeneratorDopisu.exe``
                          vedle knihoven. Startuje rychleji a antiviry ji hlásí
                          jako podezřelou podstatně méně často.

Obě varianty se stavějí dvěma samostatnými běhy PyInstalleru (viz ``build.bat``
nebo workflow ``.github/workflows/domain-letter-generator.yml``), aby si
nešlapaly po mezivýsledcích v ``build/``::

    pyinstaller --noconfirm --workpath build/onefile --distpath dist DomainLetterGenerator.spec
    set DLG_BUILD_MODE=onedir
    pyinstaller --noconfirm --workpath build/onedir  --distpath dist DomainLetterGenerator.spec

Aplikace nemá žádné runtime závislosti třetích stran — do balíčku jde jen
standardní knihovna Pythonu (včetně tkinter) a balík ``dlg``.
"""

import os
import re
import sys

# ``SPECPATH`` a ``workpath`` dodává PyInstaller do jmenného prostoru spec souboru.
PROJECT_DIR = os.path.abspath(SPECPATH)
WORK_DIR = os.path.abspath(workpath)

APP_NAME = "GeneratorDopisu"          # název .exe (ASCII — kvůli cestám a shellu)
PRODUCT_NAME = "Generátor dopisů"     # název pro uživatele (vlastnosti souboru)
ICON_PATH = os.path.join(PROJECT_DIR, "assets", "app.ico")

BUILD_MODE = os.environ.get("DLG_BUILD_MODE", "onefile").strip().lower() or "onefile"
if BUILD_MODE not in ("onefile", "onedir"):
    raise SystemExit(
        f"Neznámý režim buildu DLG_BUILD_MODE={BUILD_MODE!r}. "
        "Povolené hodnoty jsou 'onefile' a 'onedir'."
    )


# --- verze --------------------------------------------------------------------


def _read_version() -> str:
    """Přečte ``__version__`` z ``dlg/version.py`` bez importu balíčku."""

    with open(os.path.join(PROJECT_DIR, "dlg", "version.py"), encoding="utf-8") as fh:
        source = fh.read()
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if not match:
        raise SystemExit("V dlg/version.py se nepodařilo najít __version__.")
    return match.group(1)


def _version_tuple(version: str) -> tuple:
    """Z „0.1.0“ udělá (0, 1, 0, 0); nečíselné části (rc, dev, …) se ignorují."""

    parts = []
    for chunk in re.split(r"[.\-+]", version):
        digits = re.match(r"\d+", chunk)
        if not digits:
            break
        parts.append(int(digits.group(0)))
    parts = (parts + [0, 0, 0, 0])[:4]
    return tuple(parts)


VERSION = _read_version()
VERSION_TUPLE = _version_tuple(VERSION)


def _write_version_info() -> str:
    """Vygeneruje textový version resource pro Windows a vrátí cestu k němu.

    Soubor je mezivýsledek buildu — leží ve ``workpath`` (``build/…``), takže
    se necommituje. PyInstaller ho načte přes ``eval()``; kódování UTF-8 je
    proto potřeba oznámit PEP 263 komentářem.
    """

    company = os.environ.get("DLG_COMPANY", PRODUCT_NAME)
    copyright_text = os.environ.get("DLG_COPYRIGHT", "")
    escaped = {
        "company": company.replace("'", "\\'"),
        "copyright": copyright_text.replace("'", "\\'"),
        "product": PRODUCT_NAME.replace("'", "\\'"),
    }
    text = """\
# -*- coding: utf-8 -*-
# Version resource pro Windows. Generuje ho DomainLetterGenerator.spec —
# needituj ručně, změny se přepíšou při dalším buildu.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vers},
    prodvers={vers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040504b0',
        [
          StringStruct('CompanyName', '{company}'),
          StringStruct('FileDescription', 'Generátor dopisů ze šablon .docx'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', '{app}'),
          StringStruct('LegalCopyright', '{copyright}'),
          StringStruct('OriginalFilename', '{app}.exe'),
          StringStruct('ProductName', '{product}'),
          StringStruct('ProductVersion', '{version}'),
        ],
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1029, 1200])]),
  ],
)
""".format(
        vers=VERSION_TUPLE,
        version=VERSION,
        app=APP_NAME,
        company=escaped["company"],
        copyright=escaped["copyright"],
        product=escaped["product"],
    )
    os.makedirs(WORK_DIR, exist_ok=True)
    path = os.path.join(WORK_DIR, "version_info.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


VERSION_FILE = _write_version_info()


# --- vstupní skript -----------------------------------------------------------

# Balík ``dlg`` se spouští jako ``python -m dlg``; zmrazený .exe ale potřebuje
# obyčejný skript. Kdyby se jako vstup použil přímo ``dlg/__main__.py``, ztratil
# by kontext balíčku a relativní importy (``from .app import main``) by spadly.
# Proto se vedle buildu vygeneruje tenký zavaděč.
ENTRY_SCRIPT = os.path.join(WORK_DIR, "_dlg_entry.py")
with open(ENTRY_SCRIPT, "w", encoding="utf-8") as fh:
    fh.write(
        "# -*- coding: utf-8 -*-\n"
        "# Generuje DomainLetterGenerator.spec — neupravuj.\n"
        "from __future__ import annotations\n"
        "\n"
        "from dlg.__main__ import _main\n"
        "\n"
        "raise SystemExit(_main())\n"
    )


# --- analýza ------------------------------------------------------------------

# tkinter se v kódu importuje až uvnitř funkcí (a část modulů `dlg` se načítá
# podle potřeby), takže se na něj analýza importů nemusí spolehlivě dostat.
# Vyjmenujeme ho proto natvrdo — stejně jako všechny moduly balíčku `dlg`.
TK_MODULES = [
    "tkinter",
    "tkinter.colorchooser",
    "tkinter.filedialog",
    "tkinter.font",
    "tkinter.messagebox",
    "tkinter.scrolledtext",
    "tkinter.simpledialog",
    "tkinter.ttk",
]


def _dlg_modules() -> list:
    """Vyjmenuje všechny moduly balíčku ``dlg`` podle souborů na disku."""

    modules = set()
    package_root = os.path.join(PROJECT_DIR, "dlg")
    for dirpath, dirnames, filenames in os.walk(package_root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        relative = os.path.relpath(dirpath, PROJECT_DIR).replace(os.sep, ".")
        for filename in filenames:
            if not filename.endswith(".py") or filename == "__main__.py":
                continue
            if filename == "__init__.py":
                modules.add(relative)
            else:
                modules.add(f"{relative}.{filename[:-3]}")
    return sorted(modules)


# Věci, které do dopisového generátoru nepatří. Vývojové závislosti
# (pytest, python-docx a jeho lxml) se do .exe nesmí dostat ani omylem.
EXCLUDES = [
    "IPython",
    "PIL",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "_pytest",
    "docx",
    "docutils",
    "lxml",
    "matplotlib",
    "numpy",
    "pandas",
    "pip",
    "pkg_resources",
    "pluggy",
    "pytest",
    "scipy",
    "setuptools",
    "wheel",
    "wx",
]

a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[PROJECT_DIR],
    binaries=[],
    # Ikona jde do balíčku i jako data — UI si ji může nastavit do okna.
    datas=[(ICON_PATH, "assets")],
    hiddenimports=TK_MODULES + _dlg_modules(),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)


def _collected_names(entries):
    """Základní jména modulů/knihoven v TOC (bez přípon a cest)."""

    names = set()
    for entry in entries:
        dest = str(entry[0]).replace("\\", "/")
        base = dest.rsplit("/", 1)[-1]
        names.add(base.split(".", 1)[0].lower())
        names.add(dest.split(".", 1)[0].lower())
    return names


def _assert_tkinter_bundled(analysis) -> None:
    """Bez tkinteru by se .exe spustil a hned zhasl — ať to praskne už tady."""

    pure = _collected_names(analysis.pure)
    binaries = _collected_names(analysis.binaries)
    if "tkinter" not in pure:
        raise SystemExit(
            "Do balíčku se nedostal modul tkinter. Zkontroluj, že Python, kterým "
            "build běží, má nainstalovanou podporu tcl/tk (na Windows volba "
            "„tcl/tk and IDLE“ v instalátoru)."
        )
    if "_tkinter" not in binaries:
        raise SystemExit(
            "Do balíčku se nedostala nativní část tkinteru (_tkinter). "
            "Bez knihoven Tcl/Tk by se okno aplikace nezobrazilo."
        )


_assert_tkinter_bundled(a)

pyz = PYZ(a.pure)


# --- výstupy ------------------------------------------------------------------

COMMON = dict(
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX zvyšuje šanci na falešný poplach antiviru
    console=False,      # GUI aplikace, žádné černé okno
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH,
    version=VERSION_FILE,
    uac_admin=False,    # nikdy nežádat práva správce
)

if BUILD_MODE == "onefile":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        runtime_tmpdir=None,
        **COMMON,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **COMMON,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        # Vlastní jméno složky, aby v `dist/` nekolidovala se souborem
        # GeneratorDopisu.exe z varianty onefile.
        name=f"{APP_NAME}-onedir",
    )

print(f"[dlg] režim: {BUILD_MODE}, verze: {VERSION} {VERSION_TUPLE}", file=sys.stderr)
