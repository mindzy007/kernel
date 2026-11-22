@echo off
set PYTHONDONTWRITEBYTECODE=1
REM Builds the installer (installer\Kernel-Setup-<version>.exe). For a quick test build use build-portable.bat.
REM Layout: <root>\kernel (source + assets), <root>\packaging (this file, spec, iss, version_info.txt), <root>\installer (output).
cd /d "%~dp0.."
set "K=%CD%\kernel"
set "P=%CD%\packaging"

echo [1/4] Installing tools...
python -m pip install -q --upgrade pyinstaller PySide6-Essentials || goto :fail

echo [2/4] Building monitor helper (needs .NET 11 SDK)...
dotnet publish "%K%\monitor-helper" -c Release -o "%K%\monitor-helper\out" || goto :fail

if exist "%K%\__pycache__" rmdir /s /q "%K%\__pycache__"
for /f %%v in ('python -c "import sys; sys.path.insert(0, r'%K%'); from version import VERSION; print(VERSION)"') do set VER=%%v
echo Version from version.py: %VER%
pushd "%K%"
python make_version_info.py || (popd & goto :fail)
popd
if exist "%K%\version_info.txt" move /y "%K%\version_info.txt" "%P%\version_info.txt" >nul
echo [3/4] Building Kernel.exe %VER%...
python -m PyInstaller --noconfirm --clean --onedir --windowed --uac-admin ^
  --name Kernel --icon "%K%\icon.ico" --version-file "%P%\version_info.txt" ^
  --specpath build --workpath build\work ^
  --add-data "%K%\icon.ico;." --add-data "%K%\icon.png;." ^
  --add-data "%K%\fonts;fonts" --add-data "%K%\assets;assets" ^
  --add-data "%K%\monitor-helper\out\kernel-monitor.exe;monitor-helper" ^
  "%K%\kernel.py" || goto :fail

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
%ISCC% /DAppVersion=%VER% "%P%\kernel.iss" || goto :fail
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo DONE: installer is in the "installer" folder.
pause & exit /b 0

:fail
echo.
echo BUILD FAILED - read the error above.
pause & exit /b 1
