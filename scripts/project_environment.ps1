# Dot-source from project tools to select the independent Anaconda environment.
function Use-ModLingoEnvironment {
    param([string]$PythonPath)
    if (-not $PythonPath) { $PythonPath = $env:MODLINGO_PYTHON }
    if (-not $PythonPath) {
        $condaRootPath = $env:MODLINGO_CONDA_ROOT
        if (-not $condaRootPath) { $condaRootPath = 'D:\Anaconda3' }
        $PythonPath = Join-Path $condaRootPath 'envs\modlingo\python.exe'
    }
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw 'The modlingo environment is missing. Create it using environment.yml in Anaconda, or set MODLINGO_PYTHON.'
    }
    $pythonExecutablePath = (Resolve-Path -LiteralPath $PythonPath).Path
    $pythonEnvironmentPath = Split-Path -Parent $pythonExecutablePath
    $projectWorkspacePath = Split-Path -Parent $PSScriptRoot
    $oldVenvPath = Join-Path $projectWorkspacePath '.venv-build'
    $remainingPaths = $env:PATH -split ';' | Where-Object {
        $_ -and $_ -notmatch '(?i)(^|[\\/])Miniconda3([\\/]|$)' -and
        -not $_.StartsWith($oldVenvPath, [StringComparison]::OrdinalIgnoreCase)
    }
    $env:PATH = (@($pythonEnvironmentPath, (Join-Path $pythonEnvironmentPath 'Library\mingw-w64\bin'),
        (Join-Path $pythonEnvironmentPath 'Library\usr\bin'), (Join-Path $pythonEnvironmentPath 'Library\bin'),
        (Join-Path $pythonEnvironmentPath 'Scripts')) + $remainingPaths) -join ';'
    Get-ChildItem Env: | Where-Object {
        $_.Name -match '^(CONDA|_CONDA|_CE_|VIRTUAL_ENV|PYTHONHOME|PYTHONPATH|TCL_LIBRARY|TK_LIBRARY)' -or
        ($_.Name -in @('SSL_CERT_FILE','REQUESTS_CA_BUNDLE','CURL_CA_BUNDLE') -and $_.Value -match '(?i)Miniconda3')
    } | ForEach-Object { Remove-Item -LiteralPath ('Env:' + $_.Name) }
    $env:CONDA_PREFIX = $pythonEnvironmentPath
    $env:CONDA_DEFAULT_ENV = 'modlingo'
    $env:PYTHONNOUSERSITE = '1'
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    $certificatePath = Join-Path $pythonEnvironmentPath 'Library\ssl\cacert.pem'
    if (Test-Path -LiteralPath $certificatePath) { $env:SSL_CERT_FILE = $certificatePath }
    return $pythonExecutablePath
}
