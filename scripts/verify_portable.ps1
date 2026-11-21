param(
    [string]$PackageDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\ModLingo'),
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'artifacts\portable-check')
)
$ErrorActionPreference = 'Stop'
$packageRoot = (Resolve-Path -LiteralPath $PackageDirectory).Path
$executable = Join-Path $packageRoot 'ModLingo.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw 'Missing portable executable.' }
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$stateRoot = Join-Path $outputRoot ('isolated-' + [guid]::NewGuid().ToString('N'))
foreach ($name in @('Local','Roaming','Temp')) {
    New-Item -ItemType Directory -Path (Join-Path $stateRoot $name) -Force | Out-Null
}
function Run-PortableCheck {
    param([string[]]$Arguments)
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $executable
    $startInfo.WorkingDirectory = $packageRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $startInfo.ArgumentList.Add($argument) }
    foreach ($name in @($startInfo.Environment.Keys)) {
        if ($name -match '^(CONDA|_CONDA|_CE_|VIRTUAL_ENV|PYTHON|TCL_LIBRARY|TK_LIBRARY|MODLINGO)' -or
            $name -in @('SSL_CERT_FILE','REQUESTS_CA_BUNDLE','CURL_CA_BUNDLE')) {
            $startInfo.Environment.Remove($name) | Out-Null
        }
    }
    $startInfo.Environment['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
    $startInfo.Environment['LOCALAPPDATA'] = Join-Path $stateRoot 'Local'
    $startInfo.Environment['APPDATA'] = Join-Path $stateRoot 'Roaming'
    $startInfo.Environment['TEMP'] = Join-Path $stateRoot 'Temp'
    $startInfo.Environment['TMP'] = Join-Path $stateRoot 'Temp'
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $process.Start() | Out-Null
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (-not $process.WaitForExit(1000)) {
        if ($clock.Elapsed.TotalSeconds -gt 120) { $process.Kill($true); throw 'Portable acceptance timed out.' }
    }
    $standardOutput = $stdout.GetAwaiter().GetResult()
    $standardError = $stderr.GetAwaiter().GetResult()
    if ($process.ExitCode -ne 0) {
        $standardError | Set-Content -LiteralPath (Join-Path $outputRoot 'failed-portable-check.txt') -Encoding UTF8
        throw 'Portable check failed; see local diagnostic output.'
    }
}
Run-PortableCheck -Arguments @('--smoke')
$reportPath = Join-Path $outputRoot 'portable-acceptance.json'
Run-PortableCheck -Arguments @('--acceptance','--acceptance-output',$reportPath)
$report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
if (-not $report.passed -or $report.paid_model_calls -ne 0) { throw 'Invalid acceptance result.' }
$report | Add-Member -NotePropertyName 'gui_smoke_passed' -NotePropertyValue $true -Force
$report | Add-Member -NotePropertyName 'runtime_without_dev_path' -NotePropertyValue $true -Force
$report | Add-Member -NotePropertyName 'fresh_application_state' -NotePropertyValue $true -Force
$report | Add-Member -NotePropertyName 'environment_scope' -NotePropertyValue 'same_host_isolated_process' -Force
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8
Write-Output 'Portable cold/warm/update/restore acceptance passed; external computer and game display remain unverified.'
