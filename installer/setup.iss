; installer/setup.iss
; Inno Setup script – SchoolLive Player Windows telepítő
;
; Fordítás: ISCC.exe installer/setup.iss
; Kimenet: installer/SchoolLiveSetup.exe
;
; Repo struktúra feltételezése:
;   dist/SchoolLivePlayer.exe  – PyInstaller kimenet
;   schoollive-logo.png        – a gyökérben

#ifndef APP_VERSION
  #define APP_VERSION "1.0.0"
#endif

#define MyAppName      "SchoolLive Player"
#define MyAppVersion   APP_VERSION
#define MyAppPublisher "SchoolLive"
#define MyAppURL       "https://schoollive.hu"
#define MyAppExeName   "SchoolLivePlayer.exe"

[Setup]
AppId={{8A3F2C1D-4B5E-4F6A-9C0D-1E2F3A4B5C6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\SchoolLive Player
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=.
OutputBaseFilename=SchoolLiveSetup
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
WizardSizePercent=120
; Nem kell admin jog
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Windows 10+
MinVersion=10.0.17763
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; Setup ikon (opcionális – ha nincs icon.ico, vedd ki)
; SetupIconFile=..\schoollive-logo.ico

[Languages]
Name: "hungarian"; MessagesFile: "compiler:Languages\Hungarian.isl"
Name: "english";   MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; \
  Description: "Ikon létrehozása az asztalon"; \
  GroupDescription: "További ikonok:"; \
  Flags: unchecked

Name: "startupicon"; \
  Description: "Automatikus indítás Windows induláskor"; \
  GroupDescription: "Indítási beállítások:"; \
  Flags: unchecked

[Files]
; PyInstaller által generált standalone exe
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}";              Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} eltávolítása"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; \
  Description: "SchoolLive Player indítása"; \
  Flags: nowait postinstall skipifsilent

[Code]
// Startup registry bejegyzés kezelése
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if WizardIsTaskSelected('startupicon') then
      RegWriteStringValue(
        HKCU,
        'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
        '{#MyAppName}',
        ExpandConstant('"{app}\{#MyAppExeName}"')
      )
    else
      RegDeleteValue(
        HKCU,
        'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
        '{#MyAppName}'
      );
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(
      HKCU,
      'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
      '{#MyAppName}'
    );
end;