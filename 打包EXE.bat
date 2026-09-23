@echo off
chcp 65001 >nul
title Build HD2 Weapon Editor EXE
cd /d "%~dp0"

echo Building the standalone EXE...
echo.

rem The data files below are the ones the GUI reads at runtime: the damage table,
rem the name map, and the build fingerprint. They are derived from the game's
rem tables but are this project's own output - plain numbers and names.
rem
rem Everything else under data/ is deliberately NOT bundled: data/raw (unpacked
rem game tables), data/strings.json and data/strings_out (the game's own string
rem resources), data/wiki, data/all_names.txt and the simulated memory images are
rem copies of game assets and are not redistributable. The GUI was verified to
rem run and generate a mod with all of them absent, so bundling them only made
rem the download bigger and shipped material it should not.
rem
rem Add a file to this list only after confirming the GUI breaks without it.

python -m PyInstaller --noconfirm --onefile --windowed ^
  --name "HD2WeaponEditor-Standalone" ^
  --paths . ^
  --add-data "gui/templates;gui/templates" ^
  --add-data "data/damage_records.json;data" ^
  --add-data "data/weapon_names.json;data" ^
  --add-data "data/build_fingerprint.json;data" ^
  --add-data "tools;tools" ^
  --add-data "mod_template;mod_template" ^
  --hidden-import flask --hidden-import webview ^
  --hidden-import gen_mod --hidden-import ljcompile ^
  --hidden-import hd2archive --hidden-import build_map --hidden-import names ^
  gui/desktop.py

echo.
echo Done. The exe is in dist\
echo.
echo Verifying no game data was bundled...
python tools\check_exe_contents.py dist\HD2WeaponEditor-Standalone.exe --fail-on-game-data
if errorlevel 1 (
  echo.
  echo BUILD IS NOT SHIPPABLE - see the report above.
  pause
  exit /b 1
)
echo.
echo The build is clean.
pause
