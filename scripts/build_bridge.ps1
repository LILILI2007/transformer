param([string]$GamePath, [string]$DotNetPath)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $GamePath) { $GamePath = $env:MODLINGO_GAME_PATH }
if (-not $GamePath) {
    . (Join-Path $PSScriptRoot 'project_environment.ps1')
    $discoveryPython = Use-ModLingoEnvironment
    Push-Location -LiteralPath $projectRoot
    try {
        $foundGames = @(& $discoveryPython -X utf8 -c 'from modlingo.discovery import discover_games; print("\n".join(str(p) for p in discover_games() if (p/"StardewModdingAPI.dll").is_file()))')
        if ($LASTEXITCODE -ne 0) { throw 'Game discovery failed.' }
    } finally { Pop-Location }
    $foundGames = @($foundGames | Where-Object { $_ })
    if ($foundGames.Count -ne 1) { throw 'Specify -GamePath or MODLINGO_GAME_PATH when no unique SMAPI installation is found.' }
    $GamePath = $foundGames[0]
}
if (-not (Test-Path -LiteralPath (Join-Path $GamePath 'StardewModdingAPI.dll'))) { throw 'GamePath must contain an installed SMAPI game.' }
if (-not $DotNetPath) { $DotNetPath = $env:MODLINGO_DOTNET }
if (-not $DotNetPath) {
    $dotNetCommand = Get-Command dotnet -CommandType Application -ErrorAction SilentlyContinue
    if ($dotNetCommand) { $DotNetPath = $dotNetCommand.Source }
}
if (-not $DotNetPath -or -not (Test-Path -LiteralPath $DotNetPath -PathType Leaf)) { throw 'Install .NET 6 SDK or set MODLINGO_DOTNET.' }
$installedSdks = & $DotNetPath --list-sdks
if ($LASTEXITCODE -ne 0 -or -not ($installedSdks -match '^6\.')) { throw '.NET 6 SDK is required; the runtime alone is insufficient.' }
& $DotNetPath build (Join-Path $projectRoot 'bridge\ModLingo.Bridge.csproj') -c Release "-p:GamePath=$GamePath"
if ($LASTEXITCODE -ne 0) { throw 'Bridge build failed.' }
$package = Join-Path $projectRoot 'bridge\package'
New-Item -ItemType Directory -Path $package -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'bridge\bin\Release\net6.0\ModLingo.Bridge.dll') -Destination $package -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'bridge\manifest.json') -Destination $package -Force
Write-Output 'Built own bridge assembly; game and SMAPI assemblies are not redistributed.'
