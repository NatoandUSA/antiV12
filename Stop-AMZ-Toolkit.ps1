# Stop-AMZ-Toolkit.ps1 - Phase 7.14 Launcher Lite (stop).
#
# Stops ONLY the console this launcher started. The accepted launcher module reads its own PID
# record, proves the recorded process is still that exact process, asks it to stop gracefully and
# waits a bounded time. It never searches for a process by name, never stops every python.exe and
# never touches a process it did not start.

$ErrorActionPreference = 'Stop'

$RepoRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($RepoRoot)) { $RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }

Write-Host ''
Write-Host '  AMZ FBM Toolkit - stopping' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path (Join-Path $RepoRoot 'production\phase7_owner_launcher.py'))) {
    Write-Host '  This folder does not contain the toolkit.' -ForegroundColor Red
    Write-Host "  Looked in: $RepoRoot"
    exit 1
}

function Find-ToolkitPython {
    # Windows PowerShell 5.1 turns a native command's stderr into a terminating ErrorRecord when
    # $ErrorActionPreference is 'Stop'. The version probe therefore runs with 'Continue' and never
    # redirects stderr - otherwise a harmless warning from python.exe would look like "no Python".
    $ErrorActionPreference = 'Continue'
    $candidates = @()
    $py = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($py) { $candidates += ,@($py.Source, @('-3')) }
    $python = Get-Command 'python' -ErrorAction SilentlyContinue
    if ($python) { $candidates += ,@($python.Source, @()) }
    foreach ($guess in @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        'C:\Python313\python.exe', 'C:\Python312\python.exe', 'C:\Python311\python.exe')) {
        if (Test-Path $guess) { $candidates += ,@($guess, @()) }
    }
    foreach ($c in $candidates) {
        $exe = $c[0]; $pre = $c[1]
        try {
            # No quote character may appear inside this one-liner: Windows PowerShell 5.1 strips
            # embedded double quotes when it builds a native command line, which would corrupt it.
            $probe = & $exe @pre '-c' 'import sys;print(sys.version.split()[0])'
            if ($LASTEXITCODE -eq 0 -and $probe) {
                $parts = ([string]$probe).Trim().Split('.')
                if ($parts.Length -ge 2 -and
                    ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 9))) {
                    return ,@($exe, $pre, ([string]$probe).Trim())
                }
            }
        } catch { }
    }
    return $null
}

$found = Find-ToolkitPython
if ($null -eq $found) {
    Write-Host '  A supported Python was not found, so the toolkit cannot be stopped from here.' -ForegroundColor Red
    Write-Host '  Install Python 3.9 or newer, then run Stop-AMZ-Toolkit again.'
    exit 1
}
$PyExe = $found[0]; $PyPre = $found[1]

Push-Location $RepoRoot
try {
    & $PyExe @PyPre '-m' 'production.phase7_owner_launcher' `
        '--host' '127.0.0.1' '--port' '8780' 'stop'
    $rc = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host ''
if ($rc -eq 0) {
    Write-Host '  Done. You can close this window.' -ForegroundColor Green
} else {
    Write-Host '  The toolkit was not stopped. The reason is printed above.' -ForegroundColor Red
    Write-Host '  Nothing else on this computer was stopped.'
}
Write-Host ''
exit $rc
