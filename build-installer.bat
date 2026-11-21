@echo off
REM Builds the installer (installer\Kernel-Setup-<version>.exe). For a quick test build use build-portable.bat.
cd /d "%~dp0"

echo [1/4] Installing tools...
python -m pip install -q --upgrade pyinstaller PySide6-Essentials || goto :fail

echo [2/4] Building monitor helper (needs .NET 11 SDK)...
dotnet publish monitor-helper -c Release -o monitor-helper\out || goto :fail

if exist __pycache__ rmdir /s /q __pycache__
for /f %%v in ('python -c "from version import VERSION; print(VERSION)"') do set VER=%%v
echo Version from version.py: %VER%
python make_version_info.py || goto :fail
echo [3/4] Building Kernel.exe %VER%...
python -m PyInstaller --noconfirm --clean --onedir --windowed --uac-admin ^
  --name Kernel --icon icon.ico --version-file version_info.txt ^
  --add-data "icon.ico;." --add-data "icon.png;." ^
  --add-data "fonts;fonts" --add-data "assets;assets" ^
  --add-data "monitor-helper\out\kernel-monitor.exe;monitor-helper" ^
  kernel.py || goto :fail

if exist redist\PawnIO_setup.exe (
  echo PawnIO driver installer found: it will be included as an optional install step.
) else (
  echo NOTE: redist\PawnIO_setup.exe not found, so the installer is built WITHOUT the PawnIO option.
  echo       To include it, save the official installer from https://pawnio.eu as redist\PawnIO_setup.exe
)
echo [4/4] Building installer (needs Inno Setup)...
set ISCC=
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set ISCC="%ProgramFiles%\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%LocalAppData%\Programs\Inno Setup 7\ISCC.exe" set ISCC="%LocalAppData%\Programs\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set ISCC="%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC echo Inno Setup not found & goto :fail
%ISCC% /DAppVersion=%VER% kernel.iss || goto :fail
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo DONE: installer is in the "installer" folder.
pause & exit /b 0

:fail
echo.
echo BUILD FAILED - read the error above.
pause & exit /b 1
