@echo off
cd /d "%~dp0"
if exist "release\Time2Manage\Time2Manage.exe" (
    start "" "release\Time2Manage\Time2Manage.exe"
) else (
    pythonw main.py
)
