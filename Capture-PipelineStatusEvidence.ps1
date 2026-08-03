<#
.SYNOPSIS
    Captures the Windows-side acceptance evidence for scripts/pipeline_status.py.

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

    D9 Invoke-Capture now returns exactly ONE [pscustomobject] with an integer ExitCode. A
       PowerShell function returns everything on its success stream, so routing display output to
       the host is necessary but not sufficient -- the contract has to be a single object, not a
       positional value that happens to be last.

    D10 An EMPTY runs/T2 satisfied the read-only differential trivially and proved nothing about
       behaviour on real artifacts. That is now refused rather than reported as evidence.

    D11 INTERPRETER IDENTITY, raised by the 2026-08-01 rev-5 review as the important unverified
       contract. Every printed command starts with the bare literal `python`, which is why none
       needs a call operator -- but bare `python` is a PATH lookup. On Windows it can resolve to
       the Microsoft Store alias stub under WindowsApps, which opens the Store rather than
       running Python, or to an interpreter without this repository's dependencies. The script
       now resolves it, refuses the Store alias by path, proves it can import
       scripts.pipeline_status from the repo, and records the path and version. The printed command
       is only as good as what that name resolves to.

    D12 FOUND BY RUNNING IT. The captured file merges stdout and stderr (D1/D6), so it is not
       valid JSON whenever the command also writes a human-readable line to stderr -- which every
       refusal path does by design. `ConvertFrom-Json` on the whole file threw `Invalid JSON
       primitive: seed` at the refusal step, which reads like a broken CLI contract and was this
       script misreading its own evidence. Get-CapturedJson extracts the one top-level object;
       un-merging the streams would trade a real evidence property for parsing convenience.

    D13 FOUND BY RUNNING IT, and the more serious of the two. The refusal step passed the seed
       `nurse gift\` to prove the TRAILING-BACKSLASH refusal. It cannot: the marshalling defect
       under test destroys that input before the tool sees it. Measured here --
           PS value `nurse gift\`   -> child receives  nurse gift"
           PS value `nurse gift\\`  -> child receives  nurse gift\
       -- so the tool was refusing a value containing a DOUBLE QUOTE while the script recorded the
       result as proof about backslashes. It exited 2 for the wrong reason and the gate could not
       tell, because it asserted only the exit code. Now the seed is doubled so the intended value
       actually arrives, and every refusal assertion names the REASON it expects.

    Also: StrictMode Latest throws on a missing JSON property, so Get-JsonProperty reports which
    property was absent instead; origin/<branch> existence is checked before it is dereferenced;
    stages 5 and 11 are recorded by name in the summary; the trailing-backslash REFUSAL is
    captured as evidence in its own right; and -FullSuite / -ConnectivityScan close B5 and B6 in
    the same sitting when asked.

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

$script:Captures = @()

function Invoke-Capture {
    <# D1/D6. Native stderr must not become a terminating error, and the success stream must
       carry EXACTLY one object out of here.

       A PowerShell function returns everything written to the success stream, so a bare
       `... | Tee-Object` would combine the captured lines with the intended exit code and the
       caller's `$Rc` would be an array whose last element happens to be an integer. Display
       output goes to the host; one [pscustomobject] comes back.

       D12 REVISED. The streams were merged here, and the merged text was then parsed as JSON.
       That threw `Invalid JSON primitive: seed` the first time a refusal ran, because every
       refusal path writes a human sentence to stderr and the document to stdout. A first fix
       extracted the JSON object back out of the merged text; this replaces that, because
       recovering structure from a stream you deliberately destroyed is a worse contract than
       not destroying it. stdout is captured on its own and is what gets parsed.

       The cost is real and is stated rather than hidden: `2>&1` preserved the INTERLEAVING of
       the two streams, which is what makes a traceback readable in order. The transcript below
       reconstructs both halves but cannot restore that ordering. Machine-readable evidence has
       to win here -- a gate that cannot parse its own output proves nothing at all. #>
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $errFile = "$File.stderr.tmp"
    try {
        $started = Get-Date
        $stdout  = @(& python @Arguments 2>$errFile)
        $code    = $LASTEXITCODE
        if ($null -eq $code) { $code = -1 }        # no output and no exit code is still a result
        $elapsed = ((Get-Date) - $started).TotalSeconds

        $stderrText = ""
        if (Test-Path -LiteralPath $errFile) {
            $raw = Get-Content -Raw -LiteralPath $errFile
            if ($null -ne $raw) { $stderrText = $raw }
            Remove-Item -LiteralPath $errFile -Force
        }
        $stdoutText = ($stdout -join "`n")

        # The human transcript keeps the same filename it always had, so the evidence directory
        # does not change shape. Both halves are labelled because they are no longer interleaved.
        @(
            "=== STDOUT ==="
            $stdoutText
            "=== STDERR ==="
            $stderrText
            "=== EXIT: $code ==="
        ) | Set-Content -Encoding UTF8 -LiteralPath $File
        $stdout | Out-Host
        if ($stderrText) { Write-Host $stderrText }

        $result = [pscustomobject]@{
            ExitCode  = [int]$code
            File      = $File
            Command   = "python $($Arguments -join ' ')"
            Arguments = $Arguments
            Stdout    = $stdoutText
            Stderr    = $stderrText
            Duration  = [math]::Round($elapsed, 3)
            LineCount = $stdout.Count
        }
        $script:Captures += $result
        return $result
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

function ConvertFrom-CapturedStdout {
    <# Parse the JSON document from a capture's OWN stdout. Never from the transcript file, and
       never from anything stderr touched -- that conflation is exactly D12. #>
    param([Parameter(Mandatory = $true)]$Capture)
    if ([string]::IsNullOrWhiteSpace($Capture.Stdout)) {
        throw "No stdout to parse from '$($Capture.Command)'. Exit code was $($Capture.ExitCode)."
    }
    try { return ($Capture.Stdout | ConvertFrom-Json) }
    catch { throw "stdout of '$($Capture.Command)' is not valid JSON: $($_.Exception.Message)" }
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

# D11. INTERPRETER IDENTITY. Every printed command starts with the bare literal `python`, which
# is why none of them needs PowerShell's call operator -- but bare `python` is a PATH lookup, and
# on Windows it can resolve to the Microsoft Store alias stub in WindowsApps, which opens the
# Store instead of running anything. It can also resolve to an interpreter without this
# repository's dependencies. The printed command is only as good as what that name resolves to,
# so the resolution is evidence, not an assumption.
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $PythonCmd) {
    throw "`python` does not resolve on PATH. Every printed command begins with it, so none of them would run."
}
$PythonPath = $PythonCmd.Source
if ($PythonPath -like "*\WindowsApps\*") {
    throw "`python` resolves to the Microsoft Store alias at $PythonPath, which opens the Store rather than running Python. The printed commands would not work for the owner."
}
$PythonVersion = (& python --version 2>&1 | Out-String).Trim()
& python -c "import scripts.pipeline_status" | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "The resolved interpreter at $PythonPath cannot import scripts.pipeline_status from $Repo."
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
# PYTHONIOENCODING is the AUTHORITATIVE mechanism here and the only one set. PYTHONUTF8=1 and
# -X utf8 were rejected deliberately: UTF-8 mode also changes the FILESYSTEM encoding, and this
# tool's entire job is listing filenames -- changing how they decode would alter the measurement.
# Narrower is correct: stdio only.
# Scope: this is a PROCESS environment variable. It affects this script and the children it
# spawns, and dies with the script -- it cannot leak into any later shell. It is applied to every
# captured child deliberately, so all evidence in one run shares one encoding.
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
    python_version             = $PythonVersion
    python_resolved_path       = $PythonPath
    python_resolution          = "bare 'python' via PATH -- the form every printed command uses"
    evidence_directory         = $EvidenceDir
} | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "metadata.json")

# ---------------------------------------------------------------------------------------------
# LAYER 1 -- SHELL MARSHALLING, CHARACTERISED SEPARATELY FROM APPLICATION POLICY.
#
# D13 and D14 were both the same error: proving an application's refusal REASON by sending a value
# through a shell that rewrites that value first. The two things are different measurements and
# they are now recorded as different artifacts.
#
# This step measures ONLY what PowerShell delivers to a native child -- no policy, no refusal, no
# opinion. An argv probe prints what it received; the table says PRESERVED or TRANSFORMED. The
# escaped forms the refusal steps rely on stop being a claim in a comment and become a recorded
# measurement that a re-auditor can read without rerunning anything.
#
# Layer 2, application validation against an EXACT argv with no shell in the path, is not
# duplicated here: tests/test_pipeline_status.py already does it via
# subprocess.run([sys.executable, "-m", "scripts.pipeline_status", ...]). Repeating it in PowerShell
# would only re-introduce the marshalling this step exists to isolate.
# ---------------------------------------------------------------------------------------------
$ProbePath = Join-Path $EvidenceDir "argv_probe.py"
Set-Content -Encoding UTF8 -LiteralPath $ProbePath -Value @(
    "import json, sys"
    "sys.stdout.buffer.write(json.dumps(sys.argv[1:], ensure_ascii=False).encode('utf-8'))"
)
$MarshallingCases = @(
    @{ label = "the real seed";                     value = $Seed },
    @{ label = "trailing backslash, single (trap)"; value = "nurse gift\" },
    @{ label = "trailing backslash, doubled";       value = "nurse gift\\" },
    @{ label = "double quote, bare (trap)";         value = 'nurse"quote"' },
    @{ label = "double quote, escaped";             value = 'nurse\"quote\"' }
)
$Marshalling = @()
foreach ($case in $MarshallingCases) {
    # stdout only, for the same reason as D12: the probe's argv echo is the measurement, and
    # anything on stderr is a separate event that must not be glued into it.
    $probeErr = Join-Path $EvidenceDir "argv_probe.stderr.tmp"
    $received = ((& python $ProbePath --seed $case.value 2>$probeErr) -join "").Trim()
    if (Test-Path -LiteralPath $probeErr) { Remove-Item -LiteralPath $probeErr -Force }
    $argv = $null
    try { $argv = ($received | ConvertFrom-Json)[1] } catch { $argv = "<unparseable: $received>" }
    $Marshalling += [pscustomobject]@{
        label            = $case.label
        powershell_value = $case.value
        child_received   = $argv
        classification   = $(if ($argv -eq $case.value) { "PRESERVED" } else { "TRANSFORMED" })
    }
}
$Marshalling | ConvertTo-Json -Depth 6 |
    Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "shell-marshalling.json")
$Marshalling | Format-Table -AutoSize | Out-Host

# The owner's real seed must survive intact, or every printed command is about a different search.
$SeedCase = $Marshalling | Where-Object { $_.label -eq "the real seed" }
if ($SeedCase.classification -ne "PRESERVED") {
    throw "The owner's seed does not survive marshalling to a native child: sent '$($SeedCase.powershell_value)', child received '$($SeedCase.child_received)'."
}

$BeforeSnapshot = @(Get-TreeSnapshot -Root $RunsT2)
# B7. An EMPTY runs/T2 would satisfy the read-only differential trivially and prove nothing about
# the tool's behaviour on real artifacts. Refuse to call that business evidence.
if ($BeforeSnapshot.Count -eq 0) {
    throw "runs/T2 exists but contains no files. An empty workspace is not acceptance evidence."
}
$BeforeSnapshot | ConvertTo-Json -Depth 6 |
    Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "runs-T2-before.json")

$TextOutput      = Join-Path $EvidenceDir "pipeline-status-text.txt"
$JsonOutput      = Join-Path $EvidenceDir "pipeline-status.json"
$NoSeedText      = Join-Path $EvidenceDir "pipeline-status-no-seed.txt"
$NoSeedJson      = Join-Path $EvidenceDir "pipeline-status-no-seed.json"
$AdversarialText = Join-Path $EvidenceDir "pipeline-status-adversarial-seed.txt"
$TestOutput      = Join-Path $EvidenceDir "pipeline-status-tests.txt"
$BoundaryOutput  = Join-Path $EvidenceDir "boundary-tests.txt"

$TextRc = (Invoke-Capture -File $TextOutput -Arguments @("-m", "scripts.pipeline_status", "--seed", $Seed)).ExitCode
if ($TextRc -ne 0) { throw "Text pipeline-status command failed with exit code $TextRc." }

$JsonRun = Invoke-Capture -File $JsonOutput -Arguments @("-m", "scripts.pipeline_status", "--seed", $Seed, "--json")
$JsonRc  = $JsonRun.ExitCode
if ($JsonRc -ne 0) { throw "JSON pipeline-status command failed with exit code $JsonRc." }
$JsonDoc = ConvertFrom-CapturedStdout -Capture $JsonRun
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
# A non-empty runs/T2 is necessary but not sufficient: if neither stage 5 nor stage 11 has any
# artifact, this run says nothing about the two stages the multi-output defect actually affected.
# Recorded rather than thrown -- an early-pipeline workspace is a legitimate state, just not
# evidence for these stages.
$Stages5And11Exercised = (
    $null -ne (Get-JsonProperty -Object $Stage5  -Name "artifact") -or
    $null -ne (Get-JsonProperty -Object $Stage11 -Name "artifact"))
if (-not $Stages5And11Exercised) {
    Write-Warning "runs/T2 has no artifacts for stage 5 or stage 11. The read-only proof still holds, but this run does not evidence the two stages the defect affected."
}

# D3 -- C5 evidence: with NO seed there must be no pasteable engine command anywhere.
$NoSeedTextRun = Invoke-Capture -File $NoSeedText -Arguments @("-m", "scripts.pipeline_status")
$NoSeedTextRc  = $NoSeedTextRun.ExitCode
if ($NoSeedTextRc -ne 0) { throw "No-seed pipeline-status command failed with exit code $NoSeedTextRc." }
$NoSeedJsonRun = Invoke-Capture -File $NoSeedJson -Arguments @("-m", "scripts.pipeline_status", "--json")
$NoSeedJsonRc  = $NoSeedJsonRun.ExitCode
if ($NoSeedJsonRc -ne 0) { throw "No-seed JSON command failed with exit code $NoSeedJsonRc." }
# Both streams: a placeholder must not appear anywhere the owner can see it.
$NoSeedText_Content = $NoSeedTextRun.Stdout + "`n" + $NoSeedTextRun.Stderr
if ($NoSeedText_Content -match "<seed-keyword>") {
    throw "C5 REGRESSION: a placeholder command was printed without a real seed."
}
$NoSeedDoc     = ConvertFrom-CapturedStdout -Capture $NoSeedJsonRun
$NoSeedCommand = Get-JsonProperty -Object $NoSeedDoc -Name "next_command"
$NoSeedNeeds   = Get-JsonProperty -Object $NoSeedDoc -Name "next_command_needs_seed"
if ($null -ne $NoSeedCommand -and $NoSeedNeeds) {
    throw "C5 REGRESSION: next_command is non-null while next_command_needs_seed is true."
}

# D4 -- C4 evidence end-to-end: an adversarial seed against the real workspace.
$AdversarialSeed = "nurse'; Write-Host PWNED; # `$5 > sentinel.txt"
$AdvRc = (Invoke-Capture -File $AdversarialText -Arguments @("-m", "scripts.pipeline_status", "--seed", $AdversarialSeed)).ExitCode
if ($AdvRc -ne 0) { throw "Adversarial-seed command failed with exit code $AdvRc." }
if (Test-Path -LiteralPath (Join-Path $Repo "sentinel.txt")) {
    throw "C4 REGRESSION: rendering an adversarial seed created sentinel.txt."
}

# Evidence for the option-B decision taken under the 2026-08-01 pre-Windows review: a value the
# tool cannot pass through PowerShell unchanged must be REFUSED, not emitted with a caveat.
# D13. The seed below is written with TWO trailing backslashes, and that is not a typo.
#
# This step is supposed to prove the TRAILING-BACKSLASH refusal end to end. Passing `nurse gift\`
# never did: the marshalling bug under test destroys the input before the tool sees it. Measured
# through this very shell --
#     PS value `nurse gift\`   -> child receives  nurse gift"      (backslash became a QUOTE)
#     PS value `nurse gift\\`  -> child receives  nurse gift\      (the value actually intended)
# -- so the single-backslash form made the tool refuse a value containing a double quote and the
# script recorded it as proof about backslashes. It exited 2 for the WRONG REASON, and an exit
# code alone could never tell the difference. The doubled form is what delivers `nurse gift\`.
#
# Hence the reason assertions below. A gate that checks only the exit code passes whenever
# anything at all goes wrong, which is the failure mode this script exists to refuse.
$RefusedText = Join-Path $EvidenceDir "pipeline-status-refused-seed.txt"
$RefusedJson = Join-Path $EvidenceDir "pipeline-status-refused-seed.json"
$RefusedRun = Invoke-Capture -File $RefusedText -Arguments @("-m", "scripts.pipeline_status", "--seed", "nurse gift\\")
$RefusedRc  = $RefusedRun.ExitCode
if ($RefusedRc -ne 2) {
    throw "A quote-requiring trailing-backslash seed must exit 2, got $RefusedRc."
}
# The refusal SENTENCE goes to stderr and the document to stdout; read both, since the assertion
# below is about which reason fired, not about which stream carried it.
$RefusedTextContent = $RefusedRun.Stdout + "`n" + $RefusedRun.Stderr
if ($RefusedTextContent -match "python -m research") {
    throw "A refused seed emitted an engine command. It must emit none."
}
if ($RefusedTextContent -notmatch "ends in a backslash") {
    throw "The trailing-backslash seed was refused for a DIFFERENT reason than the backslash. Exit 2 alone is not the proof this step claims. See $RefusedText."
}
$RefusedJsonRun = Invoke-Capture -File $RefusedJson -Arguments @("-m", "scripts.pipeline_status", "--json", "--seed", "nurse gift\\")
$RefusedJsonRc  = $RefusedJsonRun.ExitCode
if ($RefusedJsonRc -ne 2) { throw "Refused seed in --json mode must exit 2, got $RefusedJsonRc." }
$RefusedDoc = ConvertFrom-CapturedStdout -Capture $RefusedJsonRun
if ((Get-JsonProperty -Object $RefusedDoc -Name "error") -ne "UNSUPPORTED_VALUE") {
    throw "Refused seed did not report a structured UNSUPPORTED_VALUE error."
}
if ((Get-JsonProperty -Object $RefusedDoc -Name "detail") -notmatch "ends in a backslash") {
    throw "The structured refusal named a different cause than the backslash."
}
if ($null -ne (Get-JsonProperty -Object $RefusedDoc -Name "next_command")) {
    throw "A refused seed left next_command non-null."
}

# The third refusal, added 2026-08-01 after the first Windows run of the test suite found that a
# double quote is dropped on the way to the child. Proved here in the shell it is about, and
# distinguished BY REASON from the backslash case above -- the two are one exit code apart and
# nothing else.
#
# D14, and it is D13 again one step lower. The first version of THIS step passed a plain
# 'nurse"quote"', which cannot work for the same reason: PowerShell drops the quotes on the way
# to the child, the tool receives `nursequote` -- an ordinary, perfectly valid seed -- and returns
# 0. The gate threw `A double-quote seed must exit 2, got 0`, which was the gate being right.
#
# The general rule, worth stating once: A VALUE THE TOOL REFUSES BECAUSE THIS SHELL CANNOT DELIVER
# IT INTACT CANNOT BE DELIVERED TO THE TOOL BY THIS SHELL. Testing such a refusal end-to-end needs
# the escaped form the shell CAN carry. Measured, both cases:
#     nurse gift\\      -> child receives  nurse gift\      (trailing backslash: double it)
#     nurse\"quote\"    -> child receives  nurse"quote"     (embedded quote: backslash-escape it)
$QuoteRefusedText = Join-Path $EvidenceDir "pipeline-status-refused-quote-seed.txt"
$QuoteRefusedRun = Invoke-Capture -File $QuoteRefusedText -Arguments @("-m", "scripts.pipeline_status", "--seed", 'nurse\"quote\"')
$QuoteRefusedRc  = $QuoteRefusedRun.ExitCode
if ($QuoteRefusedRc -ne 2) { throw "A double-quote seed must exit 2, got $QuoteRefusedRc." }
$QuoteRefusedContent = $QuoteRefusedRun.Stdout + "`n" + $QuoteRefusedRun.Stderr
if ($QuoteRefusedContent -notmatch "contains a double quote") {
    throw "The double-quote seed was refused for a different reason. See $QuoteRefusedText."
}
if ($QuoteRefusedContent -match "python -m research") {
    throw "A refused double-quote seed emitted an engine command. It must emit none."
}

# ---------------------------------------------------------------------------------------------
# D16. THE READ-ONLY CLAIM IS SCOPED HERE, and this is the last pipeline-status command.
#
# The snapshot pair used to be "before the first command" and "after everything", with the full
# suite, the boundary tests and the connectivity scan in between -- and the throw said "The
# pipeline-status run CHANGED real runs/T2". Run 3 fired exactly that, on a workspace where the
# module had changed nothing: 220 files before and after, ZERO content differences, ZERO size
# differences, and ten files under phase6/6E rewritten byte-identically with new mtimes by
# something in the 4781-test suite.
#
# The assertion was true and its stated cause was false. That is the defect this whole branch
# exists to remove -- DEFECT A presented a wrong value as a right one, D13 recorded a refusal for
# the wrong reason, and this blamed the wrong actor. An evidence script may not name a cause its
# measurement cannot support.
#
# So the claim is measured where the claim actually is. BEFORE -> MIDDLE brackets ONLY the
# pipeline-status commands and still THROWS: that is the read-only proof, and it is now stricter,
# because nothing else can dilute it. MIDDLE -> AFTER brackets everything else and is reported as
# its own separate finding about the SUITE, never as a fact about this module.
# ---------------------------------------------------------------------------------------------
$MiddleSnapshot = @(Get-TreeSnapshot -Root $RunsT2)
$MiddleSnapshot | ConvertTo-Json -Depth 6 |
    Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "runs-T2-after-pipeline-status.json")

$FocusedRc = (Invoke-Capture -File $TestOutput -Arguments @("-m", "unittest", "discover", "-s", "tests", "-p", "test*pipeline*status*.py", "-v")).ExitCode
if ($FocusedRc -ne 0) { throw "Pipeline-status focused tests failed with exit code $FocusedRc." }

# D2 -- the name alone is not proof. A skipped test prints its name too.
#
# D18, found the first time any run reached this check at all. It used to scan the -v transcript
# for the test name and "... ok" ON ONE LINE. unittest emits that line in pieces, so anything else
# reaching stderr mid-test lands between them -- here a ResourceWarning from a leaked file handle
# inside the test itself, which split the line and made a PASSING test unreadable as passed. The
# script threw "did not run to a passing result" about a test that had just run and passed.
#
# Reading a result out of interleaved console text was the wrong instrument. Run the one test on
# its own and let unittest report it: exit code, an explicit "Ran 1 test", and OK. A skip still
# exits 0, so the skip check stays and is asserted separately -- that is D2 and it has not moved.
#
# It also runs HERE rather than after the full suite, so the central proof fails in seconds
# instead of nineteen minutes.
$RequiredTest = "test_windows_powershell_renderer_preserves_exact_seed"
$RequiredNode = "tests.test_pipeline_status.TestWindowsPowerShellExecution.$RequiredTest"
$RequiredRun  = Invoke-Capture -File (Join-Path $EvidenceDir "required-windows-test.txt") `
                               -Arguments @("-m", "unittest", $RequiredNode, "-v")
$RequiredText = $RequiredRun.Stdout + "`n" + $RequiredRun.Stderr
$WasSkipped   = $RequiredText -match "skipped"
$RanOne       = $RequiredText -match "Ran 1 test"
$RanAndPassed = ($RequiredRun.ExitCode -eq 0) -and $RanOne -and (-not $WasSkipped) -and
                ($RequiredText -match "(?m)^OK\s*$")
if ($WasSkipped) {
    throw "$RequiredTest SKIPPED on Windows. The shell-execution proof is missing and acceptance stays HOLD."
}
if (-not $RanOne) {
    throw "$RequiredNode did not resolve to exactly one test. The node moved or was renamed."
}
if (-not $RanAndPassed) {
    throw "$RequiredTest did not run to a passing result (exit $($RequiredRun.ExitCode)). Acceptance stays HOLD."
}

$BoundaryRc = (Invoke-Capture -File $BoundaryOutput -Arguments @("-m", "unittest", "tests.test_amazon_boundary", "tests.test_network_policy", "tests.test_connectivity_policy", "-v")).ExitCode
if ($BoundaryRc -ne 0) { throw "Boundary tests failed with exit code $BoundaryRc." }

$ConnectivityRc = $null
if ($ConnectivityScan) {                                   # B6
    $ConnectivityOutput = Join-Path $EvidenceDir "connectivity-scan.txt"
    $ConnectivityRc = (Invoke-Capture -File $ConnectivityOutput -Arguments @("scripts/connectivity_scan.py")).ExitCode
    if ($ConnectivityRc -ne 0) { throw "Connectivity scan failed with exit code $ConnectivityRc." }
}

$FullSuiteRc = $null
if ($FullSuite) {                                          # B5 -- roughly 19 minutes
    Write-Host "Running the full suite. This takes ~19 minutes." -ForegroundColor Yellow
    $FullSuiteOutput = Join-Path $EvidenceDir "full-suite.txt"
    $FullSuiteRc = (Invoke-Capture -File $FullSuiteOutput -Arguments @("-m", "unittest", "discover", "-s", "tests")).ExitCode
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
$MiddleComparable = & $Flatten $MiddleSnapshot
$AfterComparable  = & $Flatten $AfterSnapshot

function Compare-Snapshot {
    # Compare the joined text FIRST. Compare-Object refuses an empty collection, so calling it on
    # an empty workspace -- or on one side becoming empty -- would throw an argument-binding error
    # that reads like a script bug instead of like the evidence result it actually is.
    param([string[]]$Reference, [string[]]$Difference)
    $changed = (($Reference -join "`n") -ne ($Difference -join "`n"))
    $diff = @()
    if ($changed -and $Reference.Count -gt 0 -and $Difference.Count -gt 0) {
        $diff = @(Compare-Object -ReferenceObject $Reference -DifferenceObject $Difference)
    }
    if ($changed -and $diff.Count -eq 0) {
        $diff = @("one side is empty: reference=$($Reference.Count) difference=$($Difference.Count)")
    }
    return [pscustomobject]@{ Changed = $changed; Diff = $diff }
}

# THE claim: the pipeline-status commands, and nothing else, touched nothing.
$ToolDelta = Compare-Snapshot -Reference $BeforeComparable -Difference $MiddleComparable
$ToolDelta.Diff | Out-String |
    Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "runs-T2-diff.txt")
$TreeChanged = $ToolDelta.Changed
$TreeDiff    = $ToolDelta.Diff

# A SEPARATE fact about everything that ran after them. Attributed to the suite, because that is
# what it measures. Recorded loudly and never folded into the sentence above.
$SuiteDelta = Compare-Snapshot -Reference $MiddleComparable -Difference $AfterComparable
$SuiteDelta.Diff | Out-String |
    Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "runs-T2-diff-after-tests.txt")
if ($SuiteDelta.Changed) {
    Write-Warning "The TEST SUITE changed real runs/T2 (not scripts.pipeline_status, which was already proved clean above). This is a finding about the suite writing into the owner's real workspace, and it matters here because pipeline_status derives STALE from mtimes. See runs-T2-diff-after-tests.txt."
}
if ($TreeChanged) {
    throw "The pipeline-status COMMANDS changed real runs/T2, measured across those commands alone. Review runs-T2-diff.txt in $EvidenceDir."
}

# D15. `scripts/connectivity_scan.py` WRITES CONNECTED-RESEARCH-NETWORK-SCAN.json, which is a
# TRACKED file. So -ConnectivityScan made the clean-tree assertion below fail for doing exactly
# what the flag asks for: the two switches were mutually exclusive and nothing said so. Run 3
# would have thrown here had it not thrown earlier.
#
# Declared, not excused. The scan's own output is allowed and is recorded by name; anything else
# still fails. A blanket "ignore modified files" would have removed the check instead of scoping
# it, and the check is the one that catches an evidence run mutating the repository.
$DeclaredSideEffects = @()
if ($ConnectivityScan) { $DeclaredSideEffects += "CONNECTED-RESEARCH-NETWORK-SCAN.json" }

$StatusAfter = @(git status --porcelain=v1 --untracked-files=all)
$StatusAfter | Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "git-status-after.txt")
$Undeclared = @($StatusAfter | Where-Object {
    $path = ($_ -replace '^.{2,3}\s*', '').Trim('"')
    $DeclaredSideEffects -notcontains $path
})
if ($Undeclared.Count -gt 0) {
    git status
    throw "Repository working tree changed during evidence capture, beyond the declared side effects ($($DeclaredSideEffects -join ', ')): $($Undeclared -join '; ')"
}
$DeclaredSideEffectsSeen = @($StatusAfter | ForEach-Object { ($_ -replace '^.{2,3}\s*', '').Trim('"') })

# The verdict turns on what THIS script proves about THIS module: the tool ran read-only, the
# Windows execution proof passed, and the capture left nothing behind but its declared side
# effects. $SuiteDelta is deliberately NOT a term here -- it is a true finding about other code,
# and folding it in would repeat the attribution error D16 exists to remove.
$Verdict = if ($RanAndPassed -and -not $TreeChanged -and $Undeclared.Count -eq 0) {
    "WINDOWS_EVIDENCE_COMPLETE_PENDING_INDEPENDENT_REAUDIT"
} else {
    "INCOMPLETE"
}

# The review's D12 contract: stdout, stderr and exit code preserved as separate fields per command.
$script:Captures | ConvertTo-Json -Depth 6 |
    Set-Content -Encoding UTF8 (Join-Path $EvidenceDir "captures.json")

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
    refused_seed_exit_code         = $RefusedRc
    refused_seed_json_exit_code    = $RefusedJsonRc
    focused_test_exit_code         = $FocusedRc
    boundary_test_exit_code        = $BoundaryRc
    runs_T2_changed_by_pipeline_status = $TreeChanged
    runs_T2_changed_scope          = "Measured across the pipeline-status commands ALONE, which is the only span this module is responsible for. The old field spanned the whole run including the full suite, so it reported a change the module had not made."
    runs_T2_changed_by_test_suite  = $SuiteDelta.Changed
    runs_T2_test_suite_note        = "A separate finding about OTHER code writing into the owner's real workspace. Run 3, 2026-08-01: 220 files before and after, zero content and zero size differences, ten files under phase6/6E rewritten byte-identically with new mtimes. It is not a pipeline_status defect, and it is not nothing either -- this module derives STALE from mtimes, so a suite that advances them can change what the owner is told to run next."
    shell_marshalling              = $Marshalling
    shell_marshalling_note         = "Layer 1 only: what PowerShell delivers to a native child, with no application policy involved. Application validation against an exact argv is proved in tests/test_pipeline_status.py, not here, so the two measurements cannot be confused for one another."
    declared_side_effects          = $DeclaredSideEffects
    declared_side_effects_observed = $DeclaredSideEffectsSeen
    stages_5_and_11_exercised      = $Stages5And11Exercised
    stages_note                    = "false means the workspace held no artifacts for the two multi-output stages the defect affected, so this run does not evidence them even though it is read-only-clean."
    stage_5_state                  = (Get-JsonProperty -Object $Stage5  -Name "state")
    stage_5_artifact               = (Get-JsonProperty -Object $Stage5  -Name "artifact")
    stage_11_state                 = (Get-JsonProperty -Object $Stage11 -Name "state")
    stage_11_artifact              = (Get-JsonProperty -Object $Stage11 -Name "artifact")
    connectivity_scan_exit_code    = $ConnectivityRc
    full_suite_exit_code           = $FullSuiteRc
    full_suite_note                = "null means not requested. A nonzero code is NOT by itself a regression: test_199e_no_acceptance_tag_yet is known-stale and fails on main too. Compare against the audited baseline."
    repository_changed_undeclared  = ($Undeclared.Count -gt 0)
    powershell_execution_test_ran_and_passed = $RanAndPassed
    c5_no_placeholder_command      = $true
    c4_no_redirection_side_effect  = $true
    acceptance_note                = "Evidence only. Merge and acceptance tag remain unauthorized until an independent re-audit returns ACCEPTED."
} | ConvertTo-Json -Depth 6 | Tee-Object -FilePath (Join-Path $EvidenceDir "summary.json")

Write-Host ""
Write-Host "WINDOWS PIPELINE-STATUS CAPTURE COMPLETE: $Verdict" -ForegroundColor Green
Write-Host "Evidence: $EvidenceDir"
Write-Host "Do not merge or tag. Commit only reviewed report/proof updates to the remediation branch."
