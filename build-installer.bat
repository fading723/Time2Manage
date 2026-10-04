@echo off
cd /d "%~dp0"
call build.bat
if errorlevel 1 exit /b %errorlevel%
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" installer.iss
) else (
    iscc installer.iss
)
