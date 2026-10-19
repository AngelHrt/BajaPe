param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creando entorno virtual..." -ForegroundColor Cyan
    python -m venv .venv
}

Write-Host "Instalando dependencias..." -ForegroundColor Cyan
& ".venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-dev.txt

Write-Host "Generando icono..." -ForegroundColor Cyan
& ".venv\Scripts\python.exe" tools\make_icon.py

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

# PyInstaller escribe su registro en stderr; en Windows PowerShell 5.1 eso se
# interpreta como error si $ErrorActionPreference es "Stop". Lo relajamos solo
# durante la llamada para que la compilación no pare por falsos positivos.
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"

if ($OneFile) {
    Write-Host "Compilando BajaPe.exe (un solo archivo)..." -ForegroundColor Cyan
    & ".venv\Scripts\pyinstaller.exe" `
        --noconfirm --clean --onefile --windowed `
        --name BajaPe `
        --icon "assets\bajape.ico" `
        --version-file "tools\version_info.txt" `
        --add-data "assets;assets" `
        --collect-data customtkinter `
        app.py 2>&1 | ForEach-Object { Write-Host $_ }
    $pyinstallerCode = $LASTEXITCODE
    $ErrorActionPreference = $previousPreference
    if ($pyinstallerCode -ne 0) {
        throw "PyInstaller falló con código $pyinstallerCode."
    }
    Write-Host "Listo: dist\BajaPe.exe" -ForegroundColor Green
} else {
    Write-Host "Compilando BajaPe.exe (carpeta portable)..." -ForegroundColor Cyan
    & ".venv\Scripts\pyinstaller.exe" --noconfirm --clean BajaPe.spec 2>&1 | ForEach-Object { Write-Host $_ }
    $pyinstallerCode = $LASTEXITCODE
    $ErrorActionPreference = $previousPreference
    if ($pyinstallerCode -ne 0) {
        throw "PyInstaller falló con código $pyinstallerCode."
    }
    Write-Host "Listo: dist\BajaPe\BajaPe.exe" -ForegroundColor Green
    Write-Host "Puedes comprimir la carpeta dist\BajaPe para distribuirla." -ForegroundColor DarkGray
}
