"""Převod hotového dopisu do PDF.

Aplikace nemá žádné runtime závislosti — do .exe jde jen standardní knihovna —
takže si PDF nevyrábí sama. Nechá ho udělat program, který uživatel na dopisy
stejně používá:

* **Microsoft Word** (Windows). Řídí se přes PowerShell a COM automatizaci,
  což je přesně Wordovo „Uložit jako → PDF“. Výsledek proto vypadá stejně jako
  to, co uživatel vidí ve Wordu — hlavičkový papír, fonty, zarovnání, číslování.
  Pro advokátní kancelář je to jediná varianta, která dává smysl: dopis jde
  klientovi a nesmí se rozsypat.
* **LibreOffice** (``soffice``). Záloha, když Word není. Funguje i na Linuxu,
  takže se na ní dá převod otestovat.

Když není ani jedno, :func:`convert` to řekne česky a hotový ``.docx`` zůstane
netknutý — PDF je nadstavba, ne podmínka.

Použití::

    from dlg import pdf

    if pdf.available():
        cesta = pdf.convert(Path("dopis.docx"))
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

__all__ = [
    "CONVERT_TIMEOUT",
    "PdfError",
    "available",
    "convert",
    "converter_name",
    "find_converter",
]


class PdfError(Exception):
    """Převod do PDF se nepovedl. Hláška je česká a určená uživateli."""


#: Kolik sekund se čeká na převod, než to vzdáme. Word se startuje pomalu
#: (u studeného startu klidně 10 s), první dopis v seanci proto trvá nejdél.
CONVERT_TIMEOUT = 180.0

#: Hláška, když na počítači není čím převádět.
NO_CONVERTER_MESSAGE = (
    "PDF se nepodařilo vytvořit — na tomto počítači není Microsoft Word "
    "ani LibreOffice, kterým by šel dopis převést.\n"
    "Dopis .docx je uložený; PDF z něj uděláte ve Wordu přes "
    "„Soubor → Uložit jako → PDF“."
)

#: Obvyklá místa, kde na Windows bydlí LibreOffice (v PATH nebývá).
_SOFFICE_WINDOWS_PATHS: tuple[str, ...] = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)

#: PowerShell skript, který převod svěří Wordu.
#:
#: Cesty se předávají proměnnými prostředí, ne v textu příkazu — jinak by se
#: na uvozovkách a diakritice v názvu dopisu („Výzva – Novák.docx“) rozsypalo
#: kdeco. ``finally`` je tam proto, že jinak by po chybě zůstal na pozadí viset
#: proces WINWORD.EXE a další pokus by se do souboru už netrefil.
_WORD_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$src = $env:DLG_PDF_SRC
$dst = $env:DLG_PDF_DST
$word = $null
$doc = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Open($src, [ref]$false, [ref]$true)
    $doc.SaveAs([ref]$dst, [ref]17)
} finally {
    if ($doc -ne $null) { $doc.Close([ref]$false) }
    if ($word -ne $null) { $word.Quit() }
}
"""


def _is_windows() -> bool:
    """Oddělené kvůli testovatelnosti (v testech se dá podstrčit)."""

    return os.name == "nt"


def _no_window() -> int:
    """Příznak, aby se při volání převodníku neblikla černá konzole."""

    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if _is_windows() else 0


def _run(command: list[str], *, timeout: float, env: dict[str, str] | None = None):
    """Spustí program a vrátí výsledek. Výjimky si řeší volající."""

    prostredi = dict(os.environ)
    if env:
        prostredi.update(env)
    return subprocess.run(  # noqa: S603 - cesty skládá aplikace, ne uživatel
        command,
        capture_output=True,
        timeout=timeout,
        env=prostredi,
        creationflags=_no_window(),
    )


# ---------------------------------------------------------------------------
# hledání převodníku
# ---------------------------------------------------------------------------
def _find_powershell() -> str:
    """Cesta k PowerShellu, pokud tu je (a jsme na Windows)."""

    if not _is_windows():
        return ""
    import shutil  # importuje se až tady, start aplikace ho nepotřebuje

    for name in ("powershell", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _word_present() -> bool:
    """Je na počítači Word? Ptáme se registru, ne spouštěním Wordu."""

    if not _is_windows():
        return False
    try:  # pragma: no cover - běží jen na Windows
        import winreg  # noqa: PLC0415 - jen na Windows

        for hive in (winreg.HKEY_CLASSES_ROOT,):
            try:
                with winreg.OpenKey(hive, r"Word.Application\CurVer"):
                    return True
            except OSError:
                continue
    except Exception:  # noqa: BLE001 - chybějící registr nesmí nic shodit
        return False
    return False


def _find_soffice() -> str:
    """Cesta k LibreOffice, nebo prázdný řetězec."""

    import shutil  # importuje se až tady, start aplikace ho nepotřebuje

    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    if _is_windows():
        for candidate in _SOFFICE_WINDOWS_PATHS:
            if Path(candidate).is_file():
                return candidate
    return ""


def find_converter() -> tuple[str, str]:
    """Čím se dá převádět. Vrací dvojici ``(druh, cesta)``.

    ``druh`` je ``"word"``, ``"soffice"``, nebo ``""``, když tu není nic.
    Word má přednost — dělá věrnější PDF a uživatel v něm šablonu psal.
    """

    if _word_present():
        shell = _find_powershell()
        if shell:
            return ("word", shell)
    soffice = _find_soffice()
    if soffice:
        return ("soffice", soffice)
    return ("", "")


def available() -> bool:
    """Dá se na tomhle počítači vůbec vyrobit PDF?"""

    return bool(find_converter()[0])


def converter_name() -> str:
    """Jméno převodníku pro uživatele (prázdné, když žádný není)."""

    return {"word": "Microsoft Word", "soffice": "LibreOffice"}.get(find_converter()[0], "")


# ---------------------------------------------------------------------------
# převod
# ---------------------------------------------------------------------------
def _convert_word(shell: str, src: Path, dst: Path, timeout: float) -> None:
    result = _run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", _WORD_SCRIPT],
        timeout=timeout,
        env={"DLG_PDF_SRC": str(src), "DLG_PDF_DST": str(dst)},
    )
    if result.returncode != 0 or not dst.is_file():
        raise PdfError(_popis_selhani("Word", result))


def _convert_soffice(soffice: str, src: Path, dst: Path, timeout: float) -> None:
    # LibreOffice neumí říct, jak se má výstup jmenovat — uloží ho vedle jako
    # <stejný název>.pdf. Necháme ho tedy pracovat do dočasné složky a hotový
    # soubor si přejmenujeme sami.
    import tempfile  # importuje se až tady, start aplikace ho nepotřebuje

    with tempfile.TemporaryDirectory(prefix="dlg-pdf-") as tmp:
        result = _run(
            [
                soffice,
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                tmp,
                str(src),
            ],
            timeout=timeout,
        )
        hotovo = Path(tmp) / (src.stem + ".pdf")
        if result.returncode != 0 or not hotovo.is_file():
            raise PdfError(_popis_selhani("LibreOffice", result))
        try:
            hotovo.replace(dst)
        except OSError:
            # přes hranici svazků `replace` nefunguje — zkopírovat a smazat
            dst.write_bytes(hotovo.read_bytes())


def _popis_selhani(program: str, result) -> str:
    """Z výstupu převodníku udělá českou hlášku pro uživatele."""

    detail = ""
    for proud in (getattr(result, "stderr", b""), getattr(result, "stdout", b"")):
        text = (proud or b"").decode("utf-8", "replace").strip()
        if text:
            detail = text.splitlines()[-1][:300]
            break
    zaklad = f"PDF se nepodařilo vytvořit — {program} převod nedokončil."
    return f"{zaklad}\n{detail}" if detail else zaklad


def convert(docx_path: Path | str, pdf_path: Path | str | None = None, *,
            timeout: float = CONVERT_TIMEOUT) -> Path:
    """Převede ``.docx`` na PDF a vrátí cestu k hotovému souboru.

    ``pdf_path`` se dá vynechat — PDF pak vznikne vedle dopisu se stejným
    názvem. Při jakémkoli problému letí :class:`PdfError` s českou hláškou;
    původní ``.docx`` zůstává vždycky netknutý.
    """

    src = Path(docx_path)
    if not src.is_file():
        raise PdfError(f"PDF se nepodařilo vytvořit — soubor „{src}“ neexistuje.")
    dst = Path(pdf_path) if pdf_path is not None else src.with_suffix(".pdf")

    druh, cesta = find_converter()
    if not druh:
        raise PdfError(NO_CONVERTER_MESSAGE)

    try:
        if druh == "word":
            _convert_word(cesta, src, dst, timeout)
        else:
            _convert_soffice(cesta, src, dst, timeout)
    except PdfError:
        raise
    except subprocess.TimeoutExpired as exc:
        raise PdfError(
            f"PDF se nepodařilo vytvořit — převod trval déle než "
            f"{int(timeout)} s a byl přerušen."
        ) from exc
    except OSError as exc:
        raise PdfError(f"PDF se nepodařilo vytvořit — {exc}.") from exc

    if not dst.is_file():  # pragma: no cover - pojistka
        raise PdfError("PDF se nepodařilo vytvořit — výsledný soubor nevznikl.")
    return dst


if __name__ == "__main__":  # pragma: no cover - ruční zkouška z příkazové řádky
    if len(sys.argv) < 2:
        raise SystemExit("Použití: python -m dlg.pdf dopis.docx")
    print(convert(Path(sys.argv[1])))
