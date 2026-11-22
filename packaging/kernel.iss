#ifndef AppVersion
#define AppVersion "26.11.2.1"
#endif

; The PawnIO driver (CPU temperature, power and clocks need it) ships inside the installer when the official
; installer is saved as redist\PawnIO_setup.exe (in the project root) (download it from https://pawnio.eu, "Download"). Its maintainer
; allows redistributing that installer unmodified. Without the file this script still builds, just without the option.
#if FileExists(AddBackslash(SourcePath) + "..\redist\PawnIO_setup.exe")
#define HavePawnIO
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
OutputDir=..\installer
OutputBaseFilename=Kernel-Setup-{#AppVersion}
SetupIconFile=..\kernel\icon.ico
UninstallDisplayIcon={app}\Kernel.exe
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes

[Files]
Source: "..\dist\Kernel\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
#ifdef HavePawnIO
Source: "..\redist\PawnIO_setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Tasks: pawnio; Check: not PawnIOInstalled
#endif

[Icons]
Name: "{autoprograms}\Kernel"; Filename: "{app}\Kernel.exe"; Tasks: startmenuicon
Name: "{autodesktop}\Kernel"; Filename: "{app}\Kernel.exe"; Tasks: desktopicon

[Tasks]
Name: "startmenuicon"; Description: "Create a Start Menu shortcut"
Name: "desktopicon"; Description: "Create a desktop shortcut"
#ifdef HavePawnIO
Name: "pawnio"; Description: "Install the PawnIO driver (needed for CPU temperature readings)"; Check: not PawnIOInstalled
#endif

[Run]
#ifdef HavePawnIO
Filename: "{tmp}\PawnIO_setup.exe"; Parameters: "-install -silent"; StatusMsg: "Installing the PawnIO driver..."; Flags: waituntilterminated runhidden; Tasks: pawnio; Check: not PawnIOInstalled
#endif
Filename: "{app}\Kernel.exe"; Description: "Launch Kernel"; Flags: shellexec nowait postinstall skipifsilent
; Optional and unticked: a checkbox on the last page that opens the Ko-fi page in the browser.
Filename: "https://ko-fi.com/mindzy"; Description: "Support Kernel's development on Ko-fi (optional)"; Flags: shellexec nowait postinstall skipifsilent unchecked

[Code]
{ True when PawnIO is properly installed on this PC: its own uninstall entry exists. Its driver service key can
  linger after an uninstall, so it is not used. Other programs use PawnIO too, so it is skipped when present and
  is never removed when Kernel is uninstalled. }
function PawnIOInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM64, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO');
end;

{ Inno draws the welcome/finished headings in Verdana and everything else in the system font, so the text visibly
  changes typeface from page to page. Setting it all to Segoe UI before the wizard is shown keeps one font throughout. }
procedure InitializeWizard;
begin
  WizardForm.Font.Name := 'Segoe UI';
  WizardForm.WelcomeLabel1.Font.Name := 'Segoe UI';
  WizardForm.FinishedHeadingLabel.Font.Name := 'Segoe UI';
end;
