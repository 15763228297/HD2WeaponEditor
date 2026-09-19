@echo off
chcp 65001 >nul
title Build HD2 Weapon Editor EXE
cd /d "%~dp0"

echo Building the standalone EXE...
echo.

python -m PyInstaller --noconfirm --onefile --windowed ^
  --name "HD2WeaponEditor-Standalone" ^
  --paths . ^
  --add-data "gui/templates;gui/templates" ^
  --add-data "data;data" ^
  --add-data "tools;tools" ^
  --add-data "mod_template;mod_template" ^
  --hidden-import flask --hidden-import webview ^
  --hidden-import gen_mod --hidden-import ljcompile ^
  --hidden-import hd2archive --hidden-import build_map --hidden-import names ^
  gui/desktop.py

echo.
echo Done. The exe is in dist\
pause
