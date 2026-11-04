; Installer Inno Setup 6 per CSV Revolution. Compilare con:  ISCC packaging\windows\installer.iss
; Registra l'app tra quelle disponibili in «Apri con» per i file .csv, senza
; sostituire l'app predefinita (Windows lascia la scelta all'utente).
#define AppName "CSV Revolution"
#define AppExe "CSV Revolution.exe"
#define AppVersion "0.20.0"
#define ProgId "CsvReader.csv"

[Setup]
AppId={{8E3F6A52-1C4B-4D97-9B0A-5D2E7F31A6C4}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\..\dist
OutputBaseFilename=CSV-Revolution-Setup
SetupIconFile=..\icons\csv_reader.ico
UninstallDisplayIcon={app}\{#AppExe}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
Compression=lzma2
SolidCompression=yes

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "Crea un'icona sul desktop"; Flags: unchecked

[Files]
Source: "..\..\dist\CSV Revolution\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; ProgID: come aprire un .csv (il percorso arriva come primo argomento)
Root: HKA; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueData: "File CSV"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueData: "{app}\{#AppExe},0"
Root: HKA; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""
; Compare in «Apri con» per l'estensione .csv
Root: HKA; Subkey: "Software\Classes\.csv\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.csv"; ValueType: string; ValueName: "Content Type"; ValueData: "text/csv"; Flags: createvalueifdoesntexist
; Registrazione dell'applicazione
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".csv"; ValueData: ""

[Run]
Filename: "{app}\{#AppExe}"; Description: "Avvia {#AppName}"; Flags: nowait postinstall skipifsilent
