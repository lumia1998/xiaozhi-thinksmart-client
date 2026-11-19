param([ValidateSet('Start','Stop','Status')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
$dir = Join-Path $PSScriptRoot '.local\windows-preview'
$server = Join-Path $PSScriptRoot 'windows_preview.py'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$pidFile = Join-Path $dir 'server.pid'
$url = 'http://127.0.0.1:18090'
function IsReady {
    try { return (Invoke-RestMethod "$url/api/preview-health" -TimeoutSec 2).service -eq 'thinksmart-ui-preview' }
    catch { return $false }
}
if ($Action -eq 'Status') {
    if (IsReady) { Write-Output "Ready: $url" } else { Write-Output 'Preview is not running' }
    return
}
if ($Action -eq 'Stop') {
    if (Test-Path -LiteralPath $pidFile) {
        $recorded = [int](Get-Content -LiteralPath $pidFile -Raw)
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $recorded"
        if ($process -and $process.CommandLine -and $process.CommandLine.Contains($server)) {
            Stop-Process -Id $recorded
        }
        Remove-Item -LiteralPath $pidFile
    }
    return
}
if (IsReady) { Write-Output "Ready: $url"; return }
New-Item -ItemType Directory -Force -Path $dir | Out-Null
if (!(Test-Path -LiteralPath $python)) {
    python -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python test environment' }
}
& $python -c 'import aiohttp,numpy,sys; assert sys.version_info >= (3,10)'
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r (Join-Path $PSScriptRoot 'requirements-preview.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Could not install preview dependencies' }
}
$process = Start-Process -FilePath $python -ArgumentList @('-B','-u',('"'+$server+'"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dir 'stdout.log') -RedirectStandardError (Join-Path $dir 'stderr.log')
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if ($process.HasExited) { break }
    if (IsReady) { Set-Content -LiteralPath $pidFile -Value $process.Id; Write-Output "Ready: $url"; return }
    Start-Sleep -Milliseconds 250
}
if (!$process.HasExited) { $process.Kill() }
throw "Preview failed to start. Check port 18090 and $dir\stderr.log"
