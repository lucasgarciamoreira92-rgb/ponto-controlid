#define MyAppName "Atlas Ponto"
#define MyAppVersion "0.3.0"
#define MyAppPublisher "Atlas"
#define MyAppExeName "AtlasPonto.exe"

[Setup]
AppId={{A582AA9E-C0A9-4F15-A7AC-31F2D6E5B011}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Atlas Ponto
DefaultGroupName=Atlas Ponto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=AtlasPonto-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupLogging=yes

[Files]
Source: "..\dist\AtlasPonto\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Atlas Ponto"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Atlas Ponto"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Atlas Ponto"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
