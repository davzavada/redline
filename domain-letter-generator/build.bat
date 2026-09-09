@echo off
rem ---------------------------------------------------------------------------
rem  Generator dopisu - lokalni sestaveni .exe na Windows.
rem
rem  Staci soubor spustit dvojklikem. Nic nepotrebuje pravo spravce:
rem  virtualni prostredi i vysledek vznikaji primo v teto slozce.
rem
rem  Hlasky jsou zamerne bez diakritiky - prikazova radka Windows umi ruzne
rem  kodovani a takhle jsou citelne vzdy.
rem ---------------------------------------------------------------------------

setlocal EnableExtensions
cd /d "%~dp0"

echo ===========================================================
echo  Generator dopisu - sestaveni Windows aplikace
echo ===========================================================
echo.

rem --- 1) najdi Python -------------------------------------------------------
set "PYCMD="
py -3 --version >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"
if not defined PYCMD (
    python --version >nul 2>nul
    if not errorlevel 1 set "PYCMD=python"
)

if not defined PYCMD (
    echo CHYBA: Na tomto pocitaci se nepodarilo najit Python.
    echo.
    echo   1. Stahnete si Python 3.10 nebo novejsi:
    echo      https://www.python.org/downloads/windows/
    echo   2. V instalatoru zvolte "Install for me only" - nepotrebuje spravce.
    echo   3. Zaskrtnete "Add python.exe to PATH".
    echo   4. Nechte zaskrtnutou soucast "tcl/tk and IDLE" - bez ni se okno
    echo      aplikace nezobrazi.
    echo   5. Potom spustte build.bat znovu.
    echo.
    pause
    exit /b 1
)

%PYCMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo CHYBA: Nalezeny Python je prilis stary, potreba je verze 3.10 nebo novejsi.
    %PYCMD% --version
    echo Novejsi Python: https://www.python.org/downloads/windows/
    echo.
    pause
    exit /b 1
)

%PYCMD% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo CHYBA: Nalezeny Python nema soucast tcl/tk ^(tkinter^).
    echo Spustte instalator Pythonu, zvolte "Modify" a zaskrtnete
    echo "tcl/tk and IDLE". Bez toho by vysledny .exe nemel okno.
    echo.
    pause
    exit /b 1
)

echo Pouzivam tento Python:
%PYCMD% --version
echo.

rem --- 2) virtualni prostredi ------------------------------------------------
set "VPY=%CD%\.venv\Scripts\python.exe"

if not exist "%VPY%" (
    echo [1/5] Vytvarim virtualni prostredi .venv ...
    %PYCMD% -m venv ".venv"
    if errorlevel 1 (
        echo.
        echo CHYBA: Virtualni prostredi se nepodarilo vytvorit.
        echo Nejcasteji to znamena, ze do teto slozky nelze zapisovat.
        echo Zkopirujte projekt treba do slozky Dokumenty a zkuste to znovu.
        echo.
        pause
        exit /b 1
    )
) else (
    echo [1/5] Virtualni prostredi .venv uz existuje, preskakuji.
)

if not exist "%VPY%" (
    echo CHYBA: Ve slozce .venv chybi python.exe. Smazte slozku .venv a spustte build.bat znovu.
    pause
    exit /b 1
)

rem --- 3) vyvojove zavislosti ------------------------------------------------
echo [2/5] Instaluji nastroje pro build ^(PyInstaller a spol.^) ...
"%VPY%" -m pip install --disable-pip-version-check --quiet --upgrade pip
"%VPY%" -m pip install --disable-pip-version-check --quiet -r "requirements-dev.txt"
if errorlevel 1 (
    echo.
    echo CHYBA: Nepodarilo se stahnout vyvojove zavislosti.
    echo Zkontrolujte pripojeni k internetu; ve firemni siti muze prekazet proxy.
    echo.
    pause
    exit /b 1
)

rem --- 4) ikona --------------------------------------------------------------
if not exist "assets\app.ico" (
    echo [3/5] Ikona chybi, generuji ji ...
    "%VPY%" "tools\make_icon.py"
    if errorlevel 1 (
        echo CHYBA: Ikonu se nepodarilo vygenerovat.
        pause
        exit /b 1
    )
) else (
    echo [3/5] Ikona assets\app.ico je pripravena.
)

rem --- 5) build --------------------------------------------------------------
rem  Poradi je zamerne: nejdriv varianta ve slozce, protoze je to ta, kterou
rem  ma uzivatel spoustet. Jednosouborova varianta pri kazdem spusteni rozbaluje
rem  cely obsah .exe do %TEMP% a ceka na antivirus - proto startuje pomalu.
echo [4/5] Sestavuji doporucenou variantu ve slozce ^(onedir^) ...
set "DLG_BUILD_MODE=onedir"
"%VPY%" -m PyInstaller --noconfirm --clean --workpath "build\onedir" --distpath "dist" "DomainLetterGenerator.spec"
if errorlevel 1 goto :build_failed

echo [5/5] Sestavuji zalozni variantu ^(jeden soubor, na flashku^) ...
set "DLG_BUILD_MODE=onefile"
"%VPY%" -m PyInstaller --noconfirm --workpath "build\onefile" --distpath "dist" "DomainLetterGenerator.spec"
if errorlevel 1 goto :build_failed

set "DLG_BUILD_MODE="

echo.
echo ===========================================================
echo  Hotovo.
echo ===========================================================
echo.
echo   dist\GeneratorDopisu-onedir\GeneratorDopisu.exe
echo       TOHLE POUZIVEJTE. Cela slozka; program nastartuje hned.
echo       Zkopirujte celou slozku a udelejte si zastupce na .exe uvnitr.
echo.
echo   dist\GeneratorDopisu.exe
echo       jeden soubor - hodi se na flashku, ale startuje o dost pomaleji:
echo       pri kazdem spusteni se cely rozbali do docasne slozky.
echo.
dir /b "dist"
echo.
pause
exit /b 0

:build_failed
echo.
echo CHYBA: Sestaveni skoncilo chybou. Vypis vyse rekne proc.
echo Casta pricina: spustena stara verze aplikace drzi soubor
echo dist\GeneratorDopisu.exe - zavrete ji a spustte build.bat znovu.
echo.
pause
exit /b 1
