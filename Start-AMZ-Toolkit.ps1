# Start-AMZ-Toolkit.ps1 - Phase 7.14 Launcher Lite (start).
#
# Double-click Start-AMZ-Toolkit.bat, wait, and your browser opens on the toolkit once it is
# genuinely ready. This script only finds a supported Python and hands over to the accepted launcher
# module; the console command itself lives in ONE place (production/phase7_owner_launcher.py) so
# Start, Stop and Open can never disagree.
#
# It never opens a non-local address, never runs any other command, never registers a service, a
# scheduler or a Windows startup entry, and never performs any Amazon Seller Central action.

$ErrorActionPreference = 'Stop'

# Ensure UTF-8 execution environment on Windows so Python CLI tools print Unicode safely
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
$OutputEncoding           = [System.Text.Encoding]::UTF8

# The repository root is the folder this script lives in - resolved from the script itself, so the
# toolkit keeps working when it is moved and when the path contains spaces.
$RepoRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($RepoRoot)) { $RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }

Write-Host ''
Write-Host '  AMZ FBM Toolkit - starting' -ForegroundColor Cyan
Write-Host '  Local only. No connection to Amazon Seller Central is ever made.' -ForegroundColor DarkGray
Write-Host ''

if (-not (Test-Path (Join-Path $RepoRoot 'production\phase7_owner_launcher.py'))) {
    Write-Host '  This folder does not contain the toolkit.' -ForegroundColor Red
    Write-Host "  Looked in: $RepoRoot"
    Write-Host '  Move Start-AMZ-Toolkit.bat back into the toolkit folder and try again.'
    exit 1
}

# ---- find a supported Python (3.9+) ------------------------------------------------------------
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
    Write-Host '  A supported Python was not found on this computer.' -ForegroundColor Red
    Write-Host '  Install Python 3.9 or newer from python.org, then run Start-AMZ-Toolkit again.'
    Write-Host '  During installation, tick "Add Python to PATH".'
    exit 1
}
$PyExe = $found[0]; $PyPre = $found[1]; $PyVer = $found[2]
Write-Host "  Python $PyVer found." -ForegroundColor DarkGray
Write-Host '  Checking the toolkit and waiting until it is ready...'
Write-Host ''

Push-Location $RepoRoot
try {
    & $PyExe @PyPre '-m' 'production.phase7_owner_launcher' `
        '--host' '127.0.0.1' '--port' '8780' '--workspace-root' 'runs/T2/phase7' 'start'
    $rc = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host ''
if ($rc -eq 0) {
    Write-Host '  Toolkit address: http://127.0.0.1:8780' -ForegroundColor Green
    Write-Host '  Leave this running while you work. Use Stop-AMZ-Toolkit when you are finished.'
} else {
    Write-Host '  The toolkit did not start. The reason is printed above.' -ForegroundColor Red
    Write-Host '  Nothing on your Amazon account was touched.'
}
Write-Host ''
exit $rc
