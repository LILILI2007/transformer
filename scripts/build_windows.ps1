$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$buildPython = Join-Path $projectRoot '.venv-build\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    python -m venv .venv-build
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create the build environment.' }
}
& $buildPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Cannot install build dependencies.' }
& $buildPython -m PyInstaller --noconfirm --clean --windowed --onedir --name ModLingo --add-data 'examples;examples' run_modlingo.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
$releaseRoot = Join-Path $projectRoot 'release'
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination (Join-Path $projectRoot 'dist\ModLingo\README.md') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'LICENSE') -Destination (Join-Path $projectRoot 'dist\ModLingo\LICENSE') -Force
$documentationRoot = Join-Path $projectRoot 'dist\ModLingo\docs'
New-Item -ItemType Directory -Path $documentationRoot -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'docs') -Filter '*.md' | Copy-Item -Destination $documentationRoot -Force
$previewRoot = Join-Path $projectRoot 'dist\ModLingo\artifacts'
New-Item -ItemType Directory -Path $previewRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'artifacts\preview.png') -Destination $previewRoot -Force
& $buildPython scripts/collect_notices.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
& $buildPython -m pip freeze | Set-Content -LiteralPath (Join-Path $releaseRoot 'build-environment.txt') -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $releaseRoot 'build-environment.txt') -Destination (Join-Path $projectRoot 'dist\ModLingo\build-environment.txt') -Force
Compress-Archive -Path (Join-Path $projectRoot 'dist\ModLingo') -DestinationPath (Join-Path $releaseRoot 'ModLingo-Windows-x64.zip') -Force
Get-FileHash -LiteralPath (Join-Path $releaseRoot 'ModLingo-Windows-x64.zip') -Algorithm SHA256 | ForEach-Object { $_.Hash.ToLower() + '  ModLingo-Windows-x64.zip' } | Set-Content -LiteralPath (Join-Path $releaseRoot 'SHA256SUMS.txt') -Encoding ASCII
Write-Output 'Built: release/ModLingo-Windows-x64.zip'
