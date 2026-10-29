@echo off
set DOTNET_CLI_TELEMETRY_OPTOUT=1
set SDK_FOUND=
for /f "tokens=1" %%s in ('dotnet --list-sdks 2^>nul') do set SDK_FOUND=1
if not defined SDK_FOUND (
    echo The .NET SDK is required to build the hardware monitor.
    exit /b 1
)
pip install -q PySide6-Essentials pyinstaller certifi || exit /b 1
dotnet publish "%~dp0monitor-helper\monitor-helper.csproj" -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:BaseIntermediateOutputPath="%TEMP%\kernel-monitor-build\obj\\" -p:MSBuildProjectExtensionsPath="%TEMP%\kernel-monitor-build\obj\\" -p:BaseOutputPath="%TEMP%\kernel-monitor-build\output\\" -o "%TEMP%\kernel-monitor-build" || exit /b 1
set OUT=%USERPROFILE%\Desktop
python -m PyInstaller --onefile --noconsole --uac-admin --name kernel --icon "%~dp0icon.ico" --add-data "%~dp0icon.ico;." --add-data "%~dp0fonts;fonts" --add-data "%~dp0assets;assets" --add-data "%~dp0monitor-helper\THIRD-PARTY-NOTICES.txt;monitor-helper" --add-data "%~dp0monitor-helper\MPL-2.0.txt;monitor-helper" --add-data "%~dp0monitor-helper\LIBREHARDWAREMONITOR-THIRD-PARTY-NOTICES.txt;monitor-helper" --add-binary "%TEMP%\kernel-monitor-build\kernel-monitor.exe;monitor-helper" --distpath "%OUT%" --workpath "%TEMP%\kernel-build" --specpath "%TEMP%\kernel-build" "%~dp0kernel.py"
if errorlevel 1 exit /b 1
echo.
echo Built: %OUT%\kernel.exe
pause
