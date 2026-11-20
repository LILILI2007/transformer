param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$deliveries = (Resolve-Path -LiteralPath 'D:\chatgpt工作区').Path
$retained = Join-Path $deliveries 'ModLingo-local-history'
$targets = [System.Collections.Generic.List[string]]::new()
foreach ($name in @('build','dist','release')) {
    $path = Join-Path $repo $name
    if (Test-Path -LiteralPath $path) { $targets.Add($path) }
}
Get-ChildItem -LiteralPath $deliveries | Where-Object {
    ($_.Name -match '^ModLingo-v[0-9].*' -and $_.Name -notlike 'ModLingo-v0.6.13-local*') -or
    $_.Name -like 'ModLingo-Bridge-0.5.7-local*' -or
    $_.Name -match '^ModLingo-离线验收-0\.6\.(11|12)$' -or
    $_.Name -eq 'ModLingo-复测-20261018' -or $_.Name -eq 'ModLingo-限额实测'
} | ForEach-Object { $targets.Add($_.FullName) }
Get-ChildItem -LiteralPath (Join-Path $repo 'artifacts') | Where-Object {
    $_.Name -ne 'preview.png' -and $_.Name -ne 'cp-gmcm-reference.txt' -and
    $_.Name -ne 'bridge-probe-20261106-130335' -and $_.Name -notmatch '0\.6\.13'
} | ForEach-Object { $targets.Add($_.FullName) }
foreach ($path in $targets) {
    $resolved = (Resolve-Path -LiteralPath $path).Path
    if (!(($resolved.StartsWith($repo+'\',[StringComparison]::OrdinalIgnoreCase)) -or
          ($resolved.StartsWith($deliveries+'\',[StringComparison]::OrdinalIgnoreCase)))) { throw 'Path outside workspace' }
    if ((Get-Item -LiteralPath $resolved).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing linked target' }
    if ((Get-Item -LiteralPath $resolved).PSIsContainer -and (Get-ChildItem -LiteralPath $resolved -Recurse -Force -Attributes ReparsePoint -ErrorAction SilentlyContinue)) { throw 'Refusing tree with links' }
}
if (!$Apply) { $targets; exit }
New-Item -ItemType Directory -Path $retained -Force | Out-Null
$archivePath = Join-Path $retained 'private-evidence-before-cleanup-20261120.zip'
if (Test-Path -LiteralPath $archivePath) { throw 'Archive already exists; inspect previous cleanup first' }
Add-Type -AssemblyName System.IO.Compression
$archive = [IO.Compression.ZipFile]::Open($archivePath,[IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($target in $targets) {
        $item = Get-Item -LiteralPath $target
        $files = if ($item.PSIsContainer) { Get-ChildItem -LiteralPath $target -Recurse -File } else { @($item) }
        foreach ($file in $files) {
            if ($file.Name -match '\.(json|jsonl|sqlite3?|db|csv|md|txt|log)(-wal|-shm)?$') {
                $prefix = if ($file.FullName.StartsWith($repo+'\')) { 'repository/' } else { 'deliveries/' }
                $base = if ($prefix -eq 'repository/') { $repo } else { $deliveries }
                $entry = $prefix + $file.FullName.Substring($base.Length+1).Replace('\','/')
                [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive,$file.FullName,$entry,[IO.Compression.CompressionLevel]::Optimal) | Out-Null
            }
        }
    }
} finally { $archive.Dispose() }
$verify = [IO.Compression.ZipFile]::OpenRead($archivePath)
try { $entries = $verify.Entries.Count; if (!$entries) { throw 'Empty evidence archive' } } finally { $verify.Dispose() }
$record = [ordered]@{ date='2026-11-20'; archive_sha256=(Get-FileHash -LiteralPath $archivePath).Hash; archived_entries=$entries; removed_paths=@($targets); preserved_latest='ModLingo-v0.6.13-local'; player_directories_touched=$false }
$record | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $retained 'cleanup-record.json') -Encoding utf8
foreach ($target in $targets) { Remove-Item -LiteralPath $target -Recurse -Force }
Write-Output "Cleaned $($targets.Count) obsolete paths; retained $entries evidence files in private local archive."
