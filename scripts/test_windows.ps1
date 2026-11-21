param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'project_environment.ps1')
$testPythonPath = Use-ModLingoEnvironment -PythonPath $PythonPath
& $testPythonPath -X utf8 scripts/run_regression.py
if ($LASTEXITCODE -ne 0) { throw 'Regression checks failed.' }
