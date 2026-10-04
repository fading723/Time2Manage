@echo off
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --distpath release desktop.spec
