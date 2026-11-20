param([string]$GamePath = 'E:\SteamLibrary\steamapps\common\Stardew Valley')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $GamePath 'StardewModdingAPI.dll'))) { throw 'GamePath must contain an installed SMAPI game.' }
dotnet build (Join-Path $projectRoot 'bridge\ModLingo.Bridge.csproj') -c Release "-p:GamePath=$GamePath"
if ($LASTEXITCODE -ne 0) { throw 'Bridge build failed.' }
$package = Join-Path $projectRoot 'bridge\package'
New-Item -ItemType Directory -Path $package -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'bridge\bin\Release\net6.0\ModLingo.Bridge.dll') -Destination $package -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'bridge\manifest.json') -Destination $package -Force
Write-Output 'Built own bridge assembly; game and SMAPI assemblies are not redistributed.'
