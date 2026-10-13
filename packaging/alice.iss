; Inno Setup script for ALICE-Setup.exe: wraps PyInstaller's dist\ALICE folder
; (packaging\alice.spec) into one installer. Run by scripts\windows\build-installer.bat,
; which passes the version in: ISCC /DAppVersion=0.1.0 packaging\alice.iss
;
; Per-user install (no admin prompt): %LOCALAPPDATA%\Programs\ALICE, the same place
; Discord and VS Code's user installer go. Pilot settings and logs live separately in
; %APPDATA%\ALICE, so reinstalling or uninstalling never touches them.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Fixed forever: it's how Windows recognises a new ALICE-Setup.exe as an upgrade of
; the installed ALICE rather than a second app.
AppId={{6F4C2A1E-8B3D-4E7A-9C51-2D7E0A9B4F13}
AppName=ALICE
AppVersion={#AppVersion}
AppVerName=ALICE {#AppVersion}
AppPublisher=heyalice.help
AppPublisherURL=https://heyalice.help/
AppComments=Accidental Logistics Intelligence Coordination Engine
DefaultDirName={localappdata}\Programs\ALICE
DefaultGroupName=ALICE
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=ALICE-Setup-{#AppVersion}
SetupIconFile=alice.ico
UninstallDisplayIcon={app}\ALICE.exe
UninstallDisplayName=ALICE
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Upgrading over a running ALICE would fail to replace its files.
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\ALICE\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ALICE"; Filename: "{app}\ALICE.exe"
Name: "{autodesktop}\ALICE"; Filename: "{app}\ALICE.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ALICE.exe"; Description: "Launch ALICE"; Flags: nowait postinstall skipifsilent
