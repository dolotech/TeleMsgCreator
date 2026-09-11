# Stop the TeleMsgCreator server that belongs to THIS folder.
# Matching on the full path of python.exe keeps us from killing unrelated
# Python processes the user may have running elsewhere.

$ErrorActionPreference = 'SilentlyContinue'

$root = Split-Path -Parent $PSScriptRoot
$expected = Join-Path $root 'python.exe'

$targets = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.ExecutablePath -eq $expected }

if (-not $targets) {
    Write-Host 'No running TeleMsgCreator server was found.'
    exit 0
}

foreach ($proc in $targets) {
    Stop-Process -Id $proc.ProcessId -Force
    Write-Host ('Stopped PID ' + $proc.ProcessId)
}
exit 0
