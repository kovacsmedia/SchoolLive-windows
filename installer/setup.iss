; installer/setup.iss
; Inno Setup 6+ szükséges: https://jrsoftware.org/isinfo.php

#define MyAppName "SchoolLive Player"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "SchoolLive"
#define MyAppURL "https://schoollive.hu"
#define MyAppExeName "SchoolLivePlayer.exe"
#define MyUpdaterExeName "SchoolLiveUpdater.exe"

[Setup]
AppId={{B7E4C2A1-3F8D-4E5B-9A2C-1D6F0E3B7C8A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\SchoolLive Player
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\dist\installer
OutputBaseFilename=SchoolLivePlayer_Setup_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes

[Languages]
Name: "hungarian"; MessagesFile: "compiler:Languages\Hungarian.isl"
Name: "english";   MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";    Description: "Asztali parancsikon";    GroupDescription: "További ikonok:"
Name: "startupentry";   Description: "Indítás Windows induláskor"; GroupDescription: "Automatikus indítás:"

[Files]
Source: "..\dist\{#MyAppExeName}";     DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\{#MyUpdaterExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}";          Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";    Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Windows induláskor automatikus indítás (opcionális)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run";
  ValueType: string; ValueName: "SchoolLivePlayer";
  ValueData: """{app}\{#MyAppExeName}""";
  Flags: uninsdeletevalue; Tasks: startupentry

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "SchoolLive Player indítása"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
