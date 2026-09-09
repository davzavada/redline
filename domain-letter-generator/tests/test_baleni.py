"""Testy balení do .exe (``DomainLetterGenerator.spec``).

Spec se nedá naimportovat jako modul — PyInstaller mu do jmenného prostoru
dodává ``SPECPATH`` a ``workpath``. Testy z něj proto berou jen to, co se dá
ověřit staticky, plus jeden test, který spustí celý průchod aplikací
s vyloučenými moduly zablokovanými.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC = PROJECT_ROOT / "DomainLetterGenerator.spec"


def _spec_source() -> str:
    return SPEC.read_text(encoding="utf-8")


def _spec_literal(name: str):
    """Vytáhne ze specu hodnotu přiřazenou do ``name`` (musí to být literál)."""

    tree = ast.parse(_spec_source(), filename=str(SPEC))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"ve specu chybí {name}")


def _spec_function(name: str, *konstanty: str):
    """Vyzobne ze specu jednu funkci a vrátí ji spustitelnou.

    ``konstanty`` jsou jména modulových literálů, na které funkce sahá — bez
    nich by spadla na ``NameError``.
    """

    tree = ast.parse(_spec_source(), filename=str(SPEC))
    namespace: dict = {jmeno: _spec_literal(jmeno) for jmeno in konstanty}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            exec(compile(ast.Module([node], []), str(SPEC), "exec"), namespace)  # noqa: S102
            return namespace[name]
    raise AssertionError(f"ve specu chybí funkce {name}")


# ---------------------------------------------------------------------------
# režim buildu
# ---------------------------------------------------------------------------
def test_vychozi_je_varianta_ve_slozce() -> None:
    """Onedir startuje řádově rychleji — musí být výchozí.

    Jednosouborová varianta při každém spuštění rozbaluje celý obsah .exe do
    %TEMP% a čeká na antivirus; to jsou ty vteřiny čekání po dvojkliku.
    """

    source = _spec_source()
    assert 'os.environ.get("DLG_BUILD_MODE", "onedir")' in source
    assert 'or "onedir"' in source


# ---------------------------------------------------------------------------
# odlehčení balíčku
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "cesta",
    [
        "_tcl_data/tzdata/Europe/Prague",
        "tcl/tzdata/Pacific/Kiritimati",
        "tcl/tcl8.6/tzdata/UTC",
        "_tk_data/demos/widget",
        "tk8.6/demos/images/earth.gif",
        "_tk_data/images/logo.gif",
        r"_tcl_data\tzdata\Europe\Prague",  # oddělovače jako na Windows
    ],
)
def test_balast_se_z_balicku_vyhodi(cesta: str) -> None:
    je_balast = _spec_function("_je_balast", "DATA_BALAST")
    assert je_balast(cesta) is True


@pytest.mark.parametrize(
    "cesta",
    [
        "_tcl_data/init.tcl",
        "_tcl_data/encoding/cp1250.enc",
        "_tk_data/ttk/vista.tcl",
        "_tk_data/msgs/cs.msg",
        "tcl/tcl8.6/http-2.9.5/http.tcl",
        "assets/app.ico",
        "base_library.zip",
        "python3.dll",
    ],
)
def test_potrebne_soubory_v_balicku_zustanou(cesta: str) -> None:
    je_balast = _spec_function("_je_balast", "DATA_BALAST")
    assert je_balast(cesta) is False


# ---------------------------------------------------------------------------
# vyloučené moduly
# ---------------------------------------------------------------------------
def _importy_balicku() -> set[str]:
    """Vrchní jména modulů, které si balík ``dlg`` odkudkoli importuje."""

    nalezene: set[str] = set()
    for path in sorted((PROJECT_ROOT / "dlg").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                nalezene.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                nalezene.add((node.module or "").split(".")[0])
    nalezene.discard("dlg")
    return nalezene


def test_nevylucuje_se_nic_co_aplikace_importuje() -> None:
    """Pojistka proti tomu, aby se do EXCLUDES dostal potřebný modul.

    Vyloučený modul se pozná až na Windows a až po spuštění .exe, kdy se
    aplikace tiše nespustí — staticky se to musí chytit tady.
    """

    excludes = set(_spec_literal("EXCLUDES"))
    kolize = excludes & _importy_balicku()
    assert not kolize, f"spec vylučuje moduly, které aplikace importuje: {sorted(kolize)}"


def test_vyvojove_zavislosti_se_do_exe_nedostanou() -> None:
    """python-docx a pytest jsou jen pro testy — do dopisového generátoru nepatří."""

    excludes = set(_spec_literal("EXCLUDES"))
    for jmeno in ("docx", "lxml", "pytest", "_pytest", "setuptools"):
        assert jmeno in excludes, f"{jmeno} chybí v EXCLUDES"


#: Skript, který projde aplikací s vyloučenými moduly zablokovanými.
_ZKOUSKA = """
import builtins, os, sys, tempfile, pathlib

ROOT = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["DLG_HOME"] = tempfile.mkdtemp(prefix="dlg-baleni-")

BLOKOVANE = set(sys.argv[2].split(","))
skutecny_import = builtins.__import__


def hlidac(name, *args, **kwargs):
    if name.split(".")[0] in BLOKOVANE:
        raise ModuleNotFoundError("vyloučeno z balíčku: " + name)
    return skutecny_import(name, *args, **kwargs)


for jmeno in list(sys.modules):
    if jmeno.split(".")[0] in BLOKOVANE:
        del sys.modules[jmeno]
builtins.__import__ = hlidac

import fixtures
from dlg import app as app_module
from dlg.store import TemplateStore

domov = pathlib.Path(os.environ["DLG_HOME"])
telo = "".join([
    fixtures.text_paragraph("Vážený pane [JMÉNO],"),
    fixtures.text_paragraph("ve věci domény [DOMÉNA] ze dne [DATUM]."),
])
vzor = domov / "vzor.docx"
vzor.write_bytes(fixtures.make_docx(document=fixtures.document_xml(telo)))
meta = TemplateStore().import_docx(vzor, name="Vzor")

app = app_module.App()
app.show_view(app_module.NAV_GENERATE, template_id=meta.id)
app.update()
pohled = app.view(app_module.NAV_GENERATE)
pohled.set_values({spec.key: "zkouška" for spec in pohled._specs})
pohled.update_preview()
for klic in (app_module.NAV_FIELDS, app_module.NAV_SETTINGS, app_module.NAV_TEMPLATES):
    app.show_view(klic)
app.update()
app.destroy()
print("OK")
"""


@pytest.mark.gui
def test_aplikace_se_obejde_bez_vylouceneho_modulu(tmp_path: Path) -> None:
    """Projde celý průchod aplikací s vyloučenými moduly zablokovanými.

    Bez tohohle testu je seznam EXCLUDES ve specu jen dobré slovo: vyloučí-li
    se omylem něco potřebného, pozná se to až tím, že se .exe na Windows tiše
    nespustí.
    """

    if not os.environ.get("DISPLAY") and sys.platform.startswith("linux"):
        pytest.skip("bez displeje nelze postavit okno")

    excludes = set(_spec_literal("EXCLUDES"))
    stdlib = excludes & set(sys.stdlib_module_names)
    assert stdlib, "spec nevylučuje žádnou část standardní knihovny — test by nic neověřil"

    skript = tmp_path / "zkouska.py"
    skript.write_text(_ZKOUSKA, encoding="utf-8")
    vysledek = subprocess.run(
        [sys.executable, str(skript), str(PROJECT_ROOT), ",".join(sorted(stdlib))],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert vysledek.returncode == 0, (
        "aplikace bez vyloučených modulů neprojde:\n"
        f"{vysledek.stdout}\n{vysledek.stderr}"
    )
    assert "OK" in vysledek.stdout
