@echo off
pip install -q PySide6-Essentials pyinstaller || exit /b 1
set OUT=%USERPROFILE%\Desktop
python -m PyInstaller --onefile --noconsole --uac-admin --name kernel --icon "%~dp0icon.ico" --add-data "%~dp0icon.ico;." --add-data "%~dp0fonts;fonts" --add-data "%~dp0assets;assets" --distpath "%OUT%" --workpath "%TEMP%\kernel-build" --specpath "%TEMP%\kernel-build" "%~dp0kernel.py"
echo.
echo Built: %OUT%\kernel.exe
pause
