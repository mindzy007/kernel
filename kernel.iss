#ifndef AppVersion
#define AppVersion "26.11.2.1"
#endif

[Setup]
AppName=Kernel
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
AppVerName=Kernel {#AppVersion}
AppPublisher=mindzy007
DefaultDirName={autopf}\Kernel
DefaultGroupName=Kernel
DisableProgramGroupPage=yes
UsePreviousTasks=no
OutputDir=installer
OutputBaseFilename=Kernel-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\Kernel.exe
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes

[Files]
Source: "dist\Kernel\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Kernel"; Filename: "{app}\Kernel.exe"; Tasks: startmenuicon
Name: "{autodesktop}\Kernel"; Filename: "{app}\Kernel.exe"; Tasks: desktopicon

[Tasks]
Name: "startmenuicon"; Description: "Create a Start Menu shortcut"
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Run]
Filename: "{app}\Kernel.exe"; Description: "Launch Kernel"; Flags: shellexec nowait postinstall skipifsilent
