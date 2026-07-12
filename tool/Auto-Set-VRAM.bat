@echo off
rem ============================================================================
rem  BigMap Optimizer -- VRAM auto-config launcher
rem  "The Man, The Mythos, The Legend : KeilerHirsch"   (GPLv3)
rem
rem  Double-click this once. It detects your graphics-card memory (NVIDIA / AMD
rem  / Intel) and writes the companion mod's settings file, so FS25's texture
rem  budget matches your card automatically -- no editing. Run it again if you
rem  change graphics cards.
rem ============================================================================
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
title BigMap Optimizer - Auto VRAM

set "HERE=%~dp0"
set "SCRIPT=%HERE%configure_companion.py"

if not exist "%SCRIPT%" (
  echo   [X] configure_companion.py is missing next to this launcher.
  echo       Keep the whole BigMap Optimizer tool folder together.
  echo(
  pause
  exit /b 1
)

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo   [X] Python 3 was not found on this PC.
  echo       Install it from  https://www.python.org/downloads/
  echo       and tick "Add python.exe to PATH" during setup, then try again.
  echo(
  pause
  exit /b 1
)

%PY% "%SCRIPT%"

echo(
pause
endlocal
