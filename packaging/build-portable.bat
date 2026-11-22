@echo off
set PYTHONDONTWRITEBYTECODE=1
REM Quick build of just the portable exe (installer\Kernel-Portable-<version>.exe) for testing.
REM Skips the pip upgrade and the .NET helper build when they are already in place; use build-installer.bat for a full release.
REM Layout: <root>\kernel (source + assets), <root>\packaging (this file, spec, iss, version_info.txt), <root>\installer (output).
cd /d "%~dp0.."
set "K=%CD%\kernel"
set "P=%CD%\packaging"

python -m PyInstaller --version >nul 2>&1 || python -m pip install -q --upgrade pyinstaller PySide6-Essentials || goto :fail
if not exist "%K%\monitor-helper\out\kernel-monitor.exe" (
  echo Building monitor helper (needs .NET 11 SDK^)...
  dotnet publish "%K%\monitor-helper" -c Release -o "%K%\monitor-helper\out" || goto :fail
)

if exist "%K%\__pycache__" rmdir /s /q "%K%\__pycache__"
for /f %%v in ('python -c "import sys; sys.path.insert(0, r'%K%'); from version import VERSION; print(VERSION)"') do set VER=%%v
echo Version from version.py: %VER%
pushd "%K%"
python make_version_info.py || (popd & goto :fail)
popd
if exist "%K%\version_info.txt" move /y "%K%\version_info.txt" "%P%\version_info.txt" >nul
echo Building Kernel-Portable-%VER%.exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin ^
  --name Kernel-Portable-%VER% --icon "%K%\icon.ico" --version-file "%P%\version_info.txt" ^
  --distpath installer --workpath build\portable --specpath build\portable ^
  --add-data "%K%\icon.ico;." --add-data "%K%\icon.png;." ^
  --add-data "%K%\fonts;fonts" --add-data "%K%\assets;assets" ^
  --add-data "%K%\monitor-helper\out\kernel-monitor.exe;monitor-helper" ^
  "%K%\kernel.py" || goto :fail
if exist build rmdir /s /q build

echo.
echo DONE: installer\Kernel-Portable-%VER%.exe
exit /b 0

:fail
echo.
echo BUILD FAILED - read the error above.
pause & exit /b 1
