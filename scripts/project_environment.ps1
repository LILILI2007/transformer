# Dot-source from project tools. Explicit overrides always win.
function Use-ModLingoEnvironment {
    param([string]$PythonPath, [switch]$ForBuild)
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    if (-not $PythonPath) { $PythonPath = $env:MODLINGO_PYTHON }
    $projectWorkspacePath = Split-Path -Parent $PSScriptRoot
    if (-not $PythonPath -and $env:MODLINGO_CONDA_ROOT) {
        $PythonPath = Join-Path $env:MODLINGO_CONDA_ROOT 'envs\modlingo\python.exe'
    }
    if (-not $PythonPath) {
        $candidates = @((Join-Path $projectWorkspacePath '.venv\Scripts\python.exe'))
        $condaRoots = @()
        foreach ($condaExecutable in @($env:CONDA_EXE, $env:_CONDA_EXE)) {
            if ($condaExecutable) { $condaRoots += Split-Path -Parent (Split-Path -Parent $condaExecutable) }
        }
        $condaCommand = Get-Command conda -ErrorAction SilentlyContinue
        if ($condaCommand) {
            try {
                $condaInfo = (& $condaCommand info --json 2>$null) | ConvertFrom-Json
                foreach ($envDirectory in $condaInfo.envs_dirs) { $candidates += Join-Path $envDirectory 'modlingo\python.exe' }
                if ($condaInfo.root_prefix) { $condaRoots += $condaInfo.root_prefix }
            } catch { }
        }
        foreach ($rootDirectory in $condaRoots) { $candidates += Join-Path $rootDirectory 'envs\modlingo\python.exe' }
        if ($env:CONDA_PREFIX -and (Split-Path -Leaf $env:CONDA_PREFIX) -eq 'modlingo') {
            $candidates += Join-Path $env:CONDA_PREFIX 'python.exe'
        }
        if ($env:VIRTUAL_ENV) { $candidates += Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe' }
        $candidates += Join-Path $projectWorkspacePath '.venv-build\Scripts\python.exe'
        $pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
        if ($pythonCommand) { $candidates += $pythonCommand.Source }
        $PythonPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    }
    if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw 'Python environment not found. Activate modlingo or .venv, or set MODLINGO_PYTHON. See README.md.'
    }
    $pythonExecutablePath = (Resolve-Path -LiteralPath $PythonPath).Path
    $pythonEnvironmentPath = Split-Path -Parent $pythonExecutablePath
    if ((Split-Path -Leaf $pythonEnvironmentPath) -eq 'Scripts') { $pythonEnvironmentPath = Split-Path -Parent $pythonEnvironmentPath }
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
    if (Test-Path -LiteralPath (Join-Path $pythonEnvironmentPath 'conda-meta')) {
        $env:CONDA_PREFIX = $pythonEnvironmentPath
        $env:CONDA_DEFAULT_ENV = Split-Path -Leaf $pythonEnvironmentPath
    } elseif (Test-Path -LiteralPath (Join-Path $pythonEnvironmentPath 'pyvenv.cfg')) {
        $env:VIRTUAL_ENV = $pythonEnvironmentPath
    }
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    $certificatePath = Join-Path $pythonEnvironmentPath 'Library\ssl\cacert.pem'
    if (Test-Path -LiteralPath $certificatePath) { $env:SSL_CERT_FILE = $certificatePath }
    & $pythonExecutablePath -I -c 'import sys; assert sys.version_info >= (3,9), "Python 3.9+ required"; import tkinter'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.9+ and Tk are required. See README.md.' }
    if (-not $ForBuild) {
        & $pythonExecutablePath -I -c 'import httpx'
        if ($LASTEXITCODE -ne 0) { throw 'Install the project dependencies first: python -m pip install -e .; see README.md.' }
    }
    return $pythonExecutablePath
}
