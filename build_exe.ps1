$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name PokemonRadar main.py

Write-Host ""
Write-Host "Готово: $PSScriptRoot\dist\PokemonRadar.exe"
