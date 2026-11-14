@echo off
REM Quick build of just the portable exe (installer\Kernel-Portable-<version>.exe) for testing.
REM Skips the pip upgrade and the .NET helper build when they are already in place; use build-installer.bat for a full release.
cd /d "%~dp0"

python -m PyInstaller --version >nul 2>&1 || python -m pip install -q --upgrade pyinstaller PySide6-Essentials || goto :fail
if not exist monitor-helper\out\kernel-monitor.exe (
  echo Building monitor helper (needs .NET 8 SDK^)...
  dotnet publish monitor-helper -c Release -o monitor-helper\out || goto :fail
)

if exist __pycache__ rmdir /s /q __pycache__
for /f %%v in ('python -c "from version import VERSION; print(VERSION)"') do set VER=%%v
echo Version from version.py: %VER%
python make_version_info.py || goto :fail
echo Building Kernel-Portable-%VER%.exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin ^
  --name Kernel-Portable-%VER% --icon "%CD%\icon.ico" --version-file "%CD%\version_info.txt" ^
  --distpath installer --workpath build\portable --specpath build\portable ^
  --add-data "%CD%\icon.ico;." --add-data "%CD%\icon.png;." ^
  --add-data "%CD%\fonts;fonts" --add-data "%CD%\assets;assets" ^
  --add-data "%CD%\monitor-helper\out\kernel-monitor.exe;monitor-helper" ^
  "%CD%\kernel.py" || goto :fail

echo.
echo DONE: installer\Kernel-Portable-%VER%.exe
exit /b 0

:fail
echo.
echo BUILD FAILED - read the error above.
pause & exit /b 1
