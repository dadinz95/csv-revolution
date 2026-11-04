# Compilazione su Windows (PowerShell). Da lanciare dalla radice del progetto:
#   .\packaging\build.ps1
$ErrorActionPreference = "Stop"
if (-not (Test-Path .venv)) { py -m venv .venv }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -r requirements.txt -r requirements-dev.txt
& $py packaging\make_icons.py
& $py -m PyInstaller packaging\csv_reader.spec --noconfirm --distpath dist --workpath build
Write-Host "Eseguibile: dist\CSV Revolution\CSV Revolution.exe"

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    & $iscc packaging\windows\installer.iss
    Write-Host "Installer: dist\CSV-Revolution-Setup.exe"
} else {
    Write-Host "Inno Setup 6 non trovato: installalo da https://jrsoftware.org/isinfo.php per creare l'installer che registra «Apri con»."
}
