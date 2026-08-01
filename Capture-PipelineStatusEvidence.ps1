<#
.SYNOPSIS
    Captures the Windows-side acceptance evidence for core/pipeline_status.py.

.DESCRIPTION
    Read-only. Runs nothing that writes to runs/T2 and proves it with a SHA256 tree snapshot
    either side. Derived from WINDOWS_PIPELINE_STATUS_ACCEPTANCE_CAPTURE.ps1 with four defects
    corrected and three evidence gaps filled -- see CORRECTIONS below.

    Produces the two blocking artifacts the 2026-08-01 remediation review named:
      1. a real runs/T2 execution, read-only-proved
      2. proof that the rendered command survives Windows PowerShell with adversarial seeds

.NOTES
    CORRECTIONS to the reviewer's draft:

    D1 BLOCKING. `native.exe 2>&1 | Tee-Object` under $ErrorActionPreference='Stop' raises
       NativeCommandError on the first stderr line in Windows PowerShell 5.1. `python -m
       unittest` writes ALL of its output to stderr, so both test steps would have thrown
       before producing any evidence. Native calls now run through Invoke-Capture, which drops
       to 'Continue' for the call and checks $LASTEXITCODE itself.

    D2 BLOCKING-FOR-CORRECTNESS. The draft looked for the test NAME in -v output. A SKIPPED
       test still prints its name:
           test_windows_powershell_renderer_preserves_exact_seed (...) ... skipped '...'
       so `powershell_execution_test_seen` reported $true when nothing had executed -- a false
       pass on the one gate the script exists to enforce. It now requires `... ok` and fails
       hard on `skipped`.

    D3 The draft only captured the WITH-seed rendering, so the C5 fix (no command is printed
       without a real seed) was never evidenced on Windows. Added, text and JSON.

    D4 The draft never exercised an adversarial seed against the real workspace, so C4 had no
       end-to-end Windows evidence outside the unit test. Added.

    D5 The draft hardcoded -ExpectedHead 5104904. A pinned hash self-invalidates the moment the
       branch gains a commit -- including the commit that adds this script -- so the default is
       now empty and HEAD is verified against origin/<branch> plus descent from the audited
       baseline. Pass -ExpectedHead <sha> to pin exactly for a formal capture.

    D6 Tee-Object output had to be routed to Out-Host. Without it every captured line becomes
       part of Invoke-Capture's return value and each exit-code variable is an array of output.

    D7 Compare-Object refuses an empty collection, so an empty runs/T2 -- or one side becoming
       empty -- threw an argument-binding error that reads like a script bug rather than like
       the evidence result it is. Text comparison first, Compare-Object only for the detail.

    D8 Python's stdout falls back to the ANSI code page when REDIRECTED rather than attached to
       a console, which is exactly what Tee-Object does. A non-ASCII seed would have raised
       UnicodeEncodeError and aborted the capture on an encoding detail. PYTHONIOENCODING=utf-8.

    Also: StrictMode Latest throws on a missing JSON property, so Get-JsonProperty reports which
    property was absent instead; origin/<branch> existence is checked before it is dereferenced;
    stages 5 and 11 are recorded by name in the summary; and -FullSuite / -ConnectivityScan close
    B5 and B6 in the same sitting when asked.

    NOT CHECKED HERE, deliberately: nothing in this script authorizes a merge or an acceptance
    tag, and it refuses to run if one already points at HEAD.
#>
param(
    [string]$Repo = "D:\Claude\Amazon\AMZ-FBM-Toolkit-v2_4_0-RC2\AMZ-FBM-Toolkit-v2_3_4-RC1",
    # B5/B6 from the 2026-08-01 gate review. Off by default: the full suite is ~19 minutes and
    # most runs do not need it. On, so both can be closed in the same sitting when they do.
    [switch]$FullSuite,
    [switch]$ConnectivityScan,
    # Deliberately NOT defaulted to a hash. A hardcoded candidate hash self-invalidates the
    # moment the branch gains a commit -- including the commit that adds this script -- and a
    # stale default is worse than none, because it either blocks a valid run or gets edited out
    # of the way. Left empty, HEAD is verified against origin/<branch> and against the audited
    # baseline instead. Pass -ExpectedHead <sha> to pin an exact candidate for a formal capture.
    [string]$ExpectedHead = "",
    [string]$ExpectedMain = "211f2f8",
    [string]$AuditedBaseline = "518b516"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-TreeSnapshot {
    param([Parameter(Mandatory = $true)][string]$Root)
    Get-ChildItem -LiteralPath $Root -Recurse -File |
        Sort-Object FullName |
        ForEach-Object {
            [pscustomobject]@{
                RelativePath          = $_.FullName.Substring($Root.Length).TrimStart('\')
                Length                = $_.Length
                LastWriteTimeUtcTicks = $_.LastWriteTimeUtc.Ticks
                SHA256                = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
            }
        }
}

function Get-JsonProperty {
    <# StrictMode Latest throws on a missing property, which turns a clear evidence failure into
       an opaque one. Ask first, then report what was actually missing. #>
    param(
        [Parameter(Mandatory = $true)]$Object,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ($null -eq $Object -or -not $Object.psobject.Properties.Name.Contains($Name)) {
        throw "Expected JSON property '$Name' is absent. The CLI contract changed or the run failed."
    }
    return $Object.$Name
}

function Invoke-Capture {
    <# D1. Native stderr must not become a terminating error. Returns the real exit code. #>
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # Out-Host, not a bare Tee-Object: without it every captured line becomes part of this
        # function's return value and the caller's exit-code variable is an array of output.
        & python @Arguments 2>&1 | Tee-Object -FilePath $File | Out-Host
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

Set-Location $Repo
git fetch origin --prune --tags

$Branch      = (git branch --show-current).Trim()
$Head        = (git rev-parse HEAD).Trim()
$Main        = (git rev-parse main).Trim()
$OriginMain  = (git rev-parse origin/main).Trim()
$WantMain    = (git rev-parse $ExpectedMain).Trim()
$StatusBefore = @(git status --porcelain=v1 --untracked-files=all)

if ([string]::IsNullOrWhiteSpace($Branch) -or $Branch -eq "main") {
    throw "Run this only from the remediation branch, never main or a detached HEAD."
}
if ([string]::IsNullOrWhiteSpace($ExpectedHead)) {
    # Unpinned: the candidate must at least be published (so a re-auditor can fetch exactly what
    # was measured) and must descend from the audited baseline. Both are checked; neither is a
    # substitute for the other, and together they cannot certify an arbitrary branch.
    git show-ref --verify --quiet "refs/remotes/origin/$Branch"
    if ($LASTEXITCODE -ne 0) {
        throw "origin/$Branch does not exist. Push the branch before capturing evidence about it."
    }
    $OriginBranch = (git rev-parse "origin/$Branch").Trim()
    if ($Head -ne $OriginBranch) {
        throw "HEAD $Head is not pushed: origin/$Branch is $OriginBranch. Evidence must describe a fetchable commit."
    }
    git merge-base --is-ancestor $AuditedBaseline HEAD
    if ($LASTEXITCODE -ne 0) {
        throw "HEAD does not descend from the audited baseline $AuditedBaseline."
    }
}
else {
    $WantHead = (git rev-parse $ExpectedHead).Trim()
    if ($Head -ne $WantHead) {
        throw "HEAD $Head does not resolve to the pinned candidate $ExpectedHead ($WantHead)."
    }
}
if ($Main -ne $WantMain -or $OriginMain -ne $WantMain) {
    throw "main or origin/main does not match the expected accepted main $ExpectedMain."
}
if ($StatusBefore.Count -gt 0) {
    git status
    throw "Working tree is dirty before evidence capture."
}
if (@(git tag --points-at HEAD | Where-Object { $_ -match "accepted" }).Count -gt 0) {
    throw "An acceptance tag already points at the candidate. Nothing here authorizes one."
}

$RunsT2 = Join-Path $Repo "runs\T2"
if (-not (Test-Path -LiteralPath $RunsT2)) {
    throw "Real runs/T2 workspace is missing on this Windows repository."
}
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShellExe)) {
    throw "Windows PowerShell powershell.exe was not found."
}

$Seed = Read-Host "Enter the real T2 seed keyword"
if ([string]::IsNullOrWhiteSpace($Seed)) { throw "A real seed keyword is required." }

$Stamp       = Get-Date -Format "yyyyMMdd-HHmmss"
$EvidenceDir = Join-Path $env:TEMP "AMZ-FBM-pipeline-status-$Stamp"
New-Item -ItemType Directory -Path $EvidenceDir | Out-Null
$Env:PYTHONDONTWRITEBYTECODE = "1"     # keeps __pycache__ out of the git-status gate
# D8. Python's stdout falls back to the ANSI code page when it is REDIRECTED rather than attached
# to a console -- which is exactly what Tee-Object does. A seed containing a non-ASCII character,
# e.g. "cafe" with an acute accent, would then raise UnicodeEncodeError and abort the capture on
# an encoding detail rather than on anything about the tool.
$Env:PYTHONIOENCODING = "utf-8"

[ordered]@{
    captured_at                = (Get-Date).ToString("o")
    repository                 = $Repo
    branch                     = $Branch
    head                       = $Head
    main                       = $Main
    origin_main                = $OriginMain
    powershell_exe             = $PowerShellExe
    windows_powershell_version = (& $PowerShellExe -NoLogo -NoProfile -Command '$PSVersionTable.PSVersion.ToString()').Trim()
    audited_baseline           = (git rev-parse $AuditedBaseline).Trim()
    expected_main              = $WantMain
    python_version             = (python --version 2>&1 | Out-String).Trim()
    evidence_directory         = $EvidenceDir
} | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "metadata.json")

$BeforeSnapshot = @(Get-TreeSnapshot -Root $RunsT2)
$BeforeSnapshot | ConvertTo-Json -Depth 6 |
    Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "runs-T2-before.json")

$TextOutput      = Join-Path $EvidenceDir "pipeline-status-text.txt"
$JsonOutput      = Join-Path $EvidenceDir "pipeline-status.json"
$NoSeedText      = Join-Path $EvidenceDir "pipeline-status-no-seed.txt"
$NoSeedJson      = Join-Path $EvidenceDir "pipeline-status-no-seed.json"
$AdversarialText = Join-Path $EvidenceDir "pipeline-status-adversarial-seed.txt"
$TestOutput      = Join-Path $EvidenceDir "pipeline-status-tests.txt"
$BoundaryOutput  = Join-Path $EvidenceDir "boundary-tests.txt"

$TextRc = Invoke-Capture -File $TextOutput -Arguments @("-m", "core.pipeline_status", "--seed", $Seed)
if ($TextRc -ne 0) { throw "Text pipeline-status command failed with exit code $TextRc." }

$JsonRc = Invoke-Capture -File $JsonOutput -Arguments @("-m", "core.pipeline_status", "--seed", $Seed, "--json")
if ($JsonRc -ne 0) { throw "JSON pipeline-status command failed with exit code $JsonRc." }
try { $JsonDoc = Get-Content -Raw -LiteralPath $JsonOutput | ConvertFrom-Json }
catch { throw "The --json output is not valid JSON: $($_.Exception.Message)" }
$ActualShell = Get-JsonProperty -Object $JsonDoc -Name "target_shell"
if ($ActualShell -ne "Windows PowerShell") {
    throw "JSON target_shell is '$ActualShell', expected 'Windows PowerShell'."
}

# The two stages the multi-output staleness defect actually affected. Recorded by name so the
# re-auditor reads a result rather than re-deriving one from the stage array.
$Stages = Get-JsonProperty -Object $JsonDoc -Name "stages"
$Stage5  = $Stages | Where-Object { $_.n -eq 5 }
$Stage11 = $Stages | Where-Object { $_.n -eq 11 }
if ($null -eq $Stage5 -or $null -eq $Stage11) {
    throw "Stages 5 and 11 were not both present in the JSON output."
}

# D3 -- C5 evidence: with NO seed there must be no pasteable engine command anywhere.
$NoSeedTextRc = Invoke-Capture -File $NoSeedText -Arguments @("-m", "core.pipeline_status")
if ($NoSeedTextRc -ne 0) { throw "No-seed pipeline-status command failed with exit code $NoSeedTextRc." }
$NoSeedJsonRc = Invoke-Capture -File $NoSeedJson -Arguments @("-m", "core.pipeline_status", "--json")
if ($NoSeedJsonRc -ne 0) { throw "No-seed JSON command failed with exit code $NoSeedJsonRc." }
$NoSeedText_Content = Get-Content -Raw -LiteralPath $NoSeedText
if ($NoSeedText_Content -match "<seed-keyword>") {
    throw "C5 REGRESSION: a placeholder command was printed without a real seed."
}
$NoSeedDoc     = Get-Content -Raw -LiteralPath $NoSeedJson | ConvertFrom-Json
$NoSeedCommand = Get-JsonProperty -Object $NoSeedDoc -Name "next_command"
$NoSeedNeeds   = Get-JsonProperty -Object $NoSeedDoc -Name "next_command_needs_seed"
if ($null -ne $NoSeedCommand -and $NoSeedNeeds) {
    throw "C5 REGRESSION: next_command is non-null while next_command_needs_seed is true."
}

# D4 -- C4 evidence end-to-end: an adversarial seed against the real workspace.
$AdversarialSeed = "nurse'; Write-Host PWNED; # `$5 > sentinel.txt"
$AdvRc = Invoke-Capture -File $AdversarialText -Arguments @("-m", "core.pipeline_status", "--seed", $AdversarialSeed)
if ($AdvRc -ne 0) { throw "Adversarial-seed command failed with exit code $AdvRc." }
if (Test-Path -LiteralPath (Join-Path $Repo "sentinel.txt")) {
    throw "C4 REGRESSION: rendering an adversarial seed created sentinel.txt."
}

$FocusedRc = Invoke-Capture -File $TestOutput -Arguments @("-m", "unittest", "discover", "-s", "tests", "-p", "test*pipeline*status*.py", "-v")
if ($FocusedRc -ne 0) { throw "Pipeline-status focused tests failed with exit code $FocusedRc." }

$BoundaryRc = Invoke-Capture -File $BoundaryOutput -Arguments @("-m", "unittest", "tests.test_amazon_boundary", "tests.test_network_policy", "tests.test_connectivity_policy", "-v")
if ($BoundaryRc -ne 0) { throw "Boundary tests failed with exit code $BoundaryRc." }

$ConnectivityRc = $null
if ($ConnectivityScan) {                                   # B6
    $ConnectivityOutput = Join-Path $EvidenceDir "connectivity-scan.txt"
    $ConnectivityRc = Invoke-Capture -File $ConnectivityOutput -Arguments @("scripts/connectivity_scan.py")
    if ($ConnectivityRc -ne 0) { throw "Connectivity scan failed with exit code $ConnectivityRc." }
}

$FullSuiteRc = $null
if ($FullSuite) {                                          # B5 -- roughly 19 minutes
    Write-Host "Running the full suite. This takes ~19 minutes." -ForegroundColor Yellow
    $FullSuiteOutput = Join-Path $EvidenceDir "full-suite.txt"
    $FullSuiteRc = Invoke-Capture -File $FullSuiteOutput -Arguments @("-m", "unittest", "discover", "-s", "tests")
    # NOT thrown on: the known-stale test_199e_no_acceptance_tag_yet fails on main too, so a
    # nonzero code here is not by itself a regression. It is recorded for the differential and
    # the re-auditor compares it against the same run on the audited baseline.
    if ($FullSuiteRc -ne 0) {
        Write-Warning "Full suite exit code $FullSuiteRc. Compare against the baseline run before calling it a regression. See $FullSuiteOutput."
    }
}

$AfterSnapshot = @(Get-TreeSnapshot -Root $RunsT2)
$AfterSnapshot | ConvertTo-Json -Depth 6 |
    Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "runs-T2-after.json")

$Flatten = { param($rows) @($rows | ForEach-Object {
    "$($_.RelativePath)`t$($_.Length)`t$($_.LastWriteTimeUtcTicks)`t$($_.SHA256)" }) }
$BeforeComparable = & $Flatten $BeforeSnapshot
$AfterComparable  = & $Flatten $AfterSnapshot
# Compare the joined text FIRST. Compare-Object refuses an empty collection, so calling it on an
# empty workspace -- or on one side becoming empty -- would throw an argument-binding error that
# reads like a script bug instead of like the evidence result it actually is.
$TreeChanged = (($BeforeComparable -join "`n") -ne ($AfterComparable -join "`n"))
$TreeDiff = @()
if ($TreeChanged -and $BeforeComparable.Count -gt 0 -and $AfterComparable.Count -gt 0) {
    $TreeDiff = @(Compare-Object -ReferenceObject $BeforeComparable -DifferenceObject $AfterComparable)
}
if ($TreeChanged -and $TreeDiff.Count -eq 0) {
    $TreeDiff = @("one side is empty: before=$($BeforeComparable.Count) after=$($AfterComparable.Count)")
}
$TreeDiff | Out-String | Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "runs-T2-diff.txt")
if ($TreeChanged) {
    throw "The pipeline-status run CHANGED real runs/T2. Review $EvidenceDir."
}

$StatusAfter = @(git status --porcelain=v1 --untracked-files=all)
$StatusAfter | Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "git-status-after.txt")
if ($StatusAfter.Count -gt 0) {
    git status
    throw "Repository working tree changed during evidence capture."
}

# D2 -- the name alone is not proof. A skipped test prints its name too.
$RequiredTest = "test_windows_powershell_renderer_preserves_exact_seed"
$FocusedText  = Get-Content -Raw -LiteralPath $TestOutput
$RanAndPassed = $FocusedText -match ([regex]::Escape($RequiredTest) + "[^\r\n]*\.\.\.\s*ok")
$WasSkipped   = $FocusedText -match ([regex]::Escape($RequiredTest) + "[^\r\n]*skipped")
if ($WasSkipped) {
    throw "$RequiredTest SKIPPED on Windows. The shell-execution proof is missing and acceptance stays HOLD."
}
if (-not $RanAndPassed) {
    throw "$RequiredTest did not run to a passing result. Acceptance stays HOLD."
}

$Verdict = if ($RanAndPassed -and -not $TreeChanged -and $StatusAfter.Count -eq 0) {
    "WINDOWS_EVIDENCE_COMPLETE_PENDING_INDEPENDENT_REAUDIT"
} else {
    "INCOMPLETE"
}

[ordered]@{
    verdict                        = $Verdict
    evidence_dir                   = $EvidenceDir
    branch                         = $Branch
    head                           = $Head
    main_unchanged                 = ($Main -eq $WantMain -and $OriginMain -eq $WantMain)
    text_exit_code                 = $TextRc
    json_exit_code                 = $JsonRc
    no_seed_text_exit_code         = $NoSeedTextRc
    no_seed_json_exit_code         = $NoSeedJsonRc
    adversarial_seed_exit_code     = $AdvRc
    focused_test_exit_code         = $FocusedRc
    boundary_test_exit_code        = $BoundaryRc
    runs_T2_changed                = $TreeChanged
    stage_5_state                  = (Get-JsonProperty -Object $Stage5  -Name "state")
    stage_5_artifact               = (Get-JsonProperty -Object $Stage5  -Name "artifact")
    stage_11_state                 = (Get-JsonProperty -Object $Stage11 -Name "state")
    stage_11_artifact              = (Get-JsonProperty -Object $Stage11 -Name "artifact")
    connectivity_scan_exit_code    = $ConnectivityRc
    full_suite_exit_code           = $FullSuiteRc
    full_suite_note                = "null means not requested. A nonzero code is NOT by itself a regression: test_199e_no_acceptance_tag_yet is known-stale and fails on main too. Compare against the audited baseline."
    repository_changed             = ($StatusAfter.Count -gt 0)
    powershell_execution_test_ran_and_passed = $RanAndPassed
    c5_no_placeholder_command      = $true
    c4_no_redirection_side_effect  = $true
    acceptance_note                = "Evidence only. Merge and acceptance tag remain unauthorized until an independent re-audit returns ACCEPTED."
} | ConvertTo-Json -Depth 6 | Tee-Object -FilePath (Join-Path $EvidenceDir "summary.json")

Write-Host ""
Write-Host "WINDOWS PIPELINE-STATUS CAPTURE COMPLETE: $Verdict" -ForegroundColor Green
Write-Host "Evidence: $EvidenceDir"
Write-Host "Do not merge or tag. Commit only reviewed report/proof updates to the remediation branch."
