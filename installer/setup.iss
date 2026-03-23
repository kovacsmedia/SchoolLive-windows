; installer/setup.iss
; Inno Setup script – SchoolLive Player Windows telepítő
;
; Fordítás: ISCC.exe setup.iss
; Kimenet: SchoolLiveSetup.exe

#define MyAppName      "SchoolLive Player"
#define MyAppVersion   GetEnv("APP_VERSION")
#define MyAppPublisher "SchoolLive"
#define MyAppURL       "https://schoollive.hu"
#define MyAppExeName   "SchoolLivePlayer.exe"
#define MyAppDataDir   "{userappdata}\SchoolLive"

[Setup]
AppId={{8A3F2C1D-4B5E-4F6A-9C0D-1E2F3A4B5C6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\SchoolLive Player
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Kimenet
OutputDir=.
OutputBaseFilename=SchoolLiveSetup
; Tömörítés
Compression=lzma2/ultra64
SolidCompression=yes
; 64 bites
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Megjelenés
WizardStyle=modern
WizardSizePercent=120
; UAC – nem kell admin a felhasználói mappába telepítéshez
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Minimum Windows 10
MinVersion=10.0.17763
; Uninstaller ikon
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

[Languages]
Name: "hungarian"; MessagesFile: "compiler:Languages\Hungarian.isl"
Name: "english";   MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";    Description: "Ikon létrehozása az {cm:DesktopName} felületen"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon";    Description: "Automatikus indítás Windows induláskor";          GroupDescription: "Indítási beállítások"; Flags: unchecked

[Files]
; Fő bináris
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; Assets (ha vannak)
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: DirExists('..\assets')

[Icons]
; Start menü
Name: "{group}\{#MyAppName}";           Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} eltávolítása"; Filename: "{uninstallexe}"

; Asztal ikon (opcionális)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

; Startup (opcionális)
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
; Telepítés után indítás (opcionális)
Filename: "{app}\{#MyAppExeName}"; \
  Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Törli az adatkönyvtárat is eltávolításkor (device_key.txt stb.)
; FIGYELEM: ez törli a provisioning adatokat is!
; Ha meg akarod tartani, vedd ki ezt a szekciót.
Type: filesandordirs; Name: "{#MyAppDataDir}"

[Code]
// Startup regisztrációja registry-be (Tasks: startupicon esetén)
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if WizardIsTaskSelected('startupicon') then begin
      RegWriteStringValue(
        HKCU,
        'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
        '{#MyAppName}',
        ExpandConstant('"{app}\{#MyAppExeName}"')
      );
    end else begin
      RegDeleteValue(
        HKCU,
        'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
        '{#MyAppName}'
      );
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then begin
    RegDeleteValue(
      HKCU,
      'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
      '{#MyAppName}'
    );
  end;
end;