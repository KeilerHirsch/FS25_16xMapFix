@echo off
rem ============================================================================
rem  BigMap Optimizer -- oversized-map drag & drop launcher
rem  "The Man, The Mythos, The Legend : KeilerHirsch"   (GPLv3)
rem
rem  Just drag one or more FS25 map .zip files onto this Fix-Map.bat icon.
rem  A fixed copy (<name>_fixed.zip) is written next to each original; your
rem  input files are never modified. Double-clicking with no file shows help.
rem ============================================================================
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
title BigMap Optimizer

set "HERE=%~dp0"
set "FIXER=%HERE%bigmap_optimizer.py"

echo(
echo   ============================================================
echo    BigMap Optimizer  --  oversized-map density downscaler
echo    drag a map .zip onto this file to shrink it to engine-safe size
echo   ============================================================

if not exist "%FIXER%" (
  echo(
  echo   [X] fs25_map_fixer.py is missing next to this launcher.
  echo       Keep Fix-Map.bat, fs25_map_fixer.py and grleconvert.exe together.
  echo(
  pause
  exit /b 1
)

rem --- find a Python 3 interpreter --------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo(
  echo   [X] Python 3 was not found on this PC.
  echo       Install it from  https://www.python.org/downloads/
  echo       and tick "Add python.exe to PATH" during setup, then try again.
  echo(
  pause
  exit /b 1
)

rem --- make sure Pillow is available (auto-install once) ----------------------
%PY% -c "import PIL" >nul 2>&1
if errorlevel 1 (
  echo(
  echo   [*] First run: installing the image library ^(Pillow^)...
  %PY% -m pip install --user --quiet Pillow
  %PY% -c "import PIL" >nul 2>&1
  if errorlevel 1 (
    echo   [X] Could not install Pillow automatically.
    echo       Run this once, then retry:   %PY% -m pip install Pillow
    echo(
    pause
    exit /b 1
  )
)

rem --- nothing dropped: show usage --------------------------------------------
if "%~1"=="" (
  echo(
  echo   Nothing to do yet. Drag one or more FS25 map .zip files onto the
  echo   Fix-Map.bat icon. Each gets a *_fixed.zip written beside it.
  echo(
  pause
  exit /b 0
)

rem --- process every dropped file ---------------------------------------------
set "FAIL=0"
:loop
if "%~1"=="" goto done
echo(
echo   ----------------------------------------------------------
echo    Processing: %~nx1
echo   ----------------------------------------------------------
%PY% "%FIXER%" "%~1"
if errorlevel 1 set "FAIL=1"
shift
goto loop

:done
echo(
if "%FAIL%"=="1" (
  echo   Finished WITH ERRORS -- read the messages above.
) else (
  echo   All done. Apply each *_fixed.zip to YOUR own legally-owned map copy.
)
echo(
pause
endlocal
