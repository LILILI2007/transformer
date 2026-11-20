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
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'bridge\package\ModLingo.Bridge.dll'))) { throw 'Build the companion with scripts/build_bridge.ps1 first.' }
& $buildPython -m PyInstaller --noconfirm --clean --windowed --onedir --name ModLingo --add-data 'examples;examples' --add-data 'bridge/package;bridge/package' run_modlingo.py
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
$reportRoot = Join-Path $projectRoot 'dist\ModLingo\reports'
New-Item -ItemType Directory -Path $reportRoot -Force | Out-Null
foreach ($name in @('game-probe-summary-0.6.13.json','performance-0.6.13.json','regression-0.6.13.json','coverage-0.6.13.json','coverage-0.6.13.csv')) {
    $sourceReport = Join-Path $projectRoot "artifacts\$name"
    if (Test-Path -LiteralPath $sourceReport) { Copy-Item -LiteralPath $sourceReport -Destination $reportRoot -Force }
}
& $buildPython scripts/collect_notices.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
& $buildPython -m pip freeze | Set-Content -LiteralPath (Join-Path $releaseRoot 'build-environment.txt') -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $releaseRoot 'build-environment.txt') -Destination (Join-Path $projectRoot 'dist\ModLingo\build-environment.txt') -Force
Compress-Archive -Path (Join-Path $projectRoot 'dist\ModLingo') -DestinationPath (Join-Path $releaseRoot 'ModLingo-Windows-x64.zip') -Force
Get-FileHash -LiteralPath (Join-Path $releaseRoot 'ModLingo-Windows-x64.zip') -Algorithm SHA256 | ForEach-Object { $_.Hash.ToLower() + '  ModLingo-Windows-x64.zip' } | Set-Content -LiteralPath (Join-Path $releaseRoot 'SHA256SUMS.txt') -Encoding ASCII
Write-Output 'Built: release/ModLingo-Windows-x64.zip'
