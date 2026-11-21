param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
. (Join-Path $PSScriptRoot 'project_environment.ps1')
$buildPython = Use-ModLingoEnvironment -PythonPath $PythonPath
& $buildPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Cannot install build dependencies.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'bridge\package\ModLingo.Bridge.dll'))) { throw 'Build the companion with scripts/build_bridge.ps1 first.' }
& $buildPython -m PyInstaller --noconfirm --clean --windowed --onedir --name ModLingo --add-data 'examples;examples' --add-data 'bridge/package;bridge/package' --add-data 'modlingo/resource_rules.json;modlingo' run_modlingo.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
$releaseRoot = Join-Path $projectRoot 'release'
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
foreach ($document in @('README.md','CHANGELOG.md','CONTRIBUTING.md','SECURITY.md','LICENSE')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $document) -Destination (Join-Path $projectRoot 'dist\ModLingo') -Force
}
$documentationRoot = Join-Path $projectRoot 'dist\ModLingo\docs'
New-Item -ItemType Directory -Path $documentationRoot -Force | Out-Null
Copy-Item -Path (Join-Path $projectRoot 'docs\*') -Destination $documentationRoot -Recurse -Force
$bridgeDocumentationRoot = Join-Path $projectRoot 'dist\ModLingo\bridge'
New-Item -ItemType Directory -Path $bridgeDocumentationRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'bridge\README.md') -Destination $bridgeDocumentationRoot -Force
$previewRoot = Join-Path $projectRoot 'dist\ModLingo\artifacts'
New-Item -ItemType Directory -Path $previewRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'artifacts\preview.png') -Destination $previewRoot -Force
$reportRoot = Join-Path $projectRoot 'dist\ModLingo\reports'
New-Item -ItemType Directory -Path $reportRoot -Force | Out-Null
$packageVersion = & $buildPython -c 'from modlingo import __version__; print(__version__)'
foreach ($name in @("game-probe-summary-$packageVersion.json","performance-$packageVersion.json","regression-$packageVersion.json","coverage-$packageVersion.json","coverage-$packageVersion.csv","minecart-adapter-$packageVersion.json","dependencies-$packageVersion.json")) {
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
