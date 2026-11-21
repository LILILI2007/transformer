$ErrorActionPreference = 'Stop'
try {
    Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
    . (Join-Path $PSScriptRoot 'project_environment.ps1')
    $sourcePythonPath = Use-ModLingoEnvironment
    & $sourcePythonPath -X utf8 -m modlingo @args
    exit $LASTEXITCODE
} catch {
    Write-Error -Message $_ -ErrorAction Continue
    exit 1
}
