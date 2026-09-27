# Pipeline watchdog -- brings up anything that is not running, then logs it.
#
# `run.py ops start` is idempotent: it only launches what is missing and
# refuses to start a second copy, so a tick on a healthy machine is a no-op
# that costs nothing.
#
# This runs on a REPEATING TIMER, deliberately not a logon trigger. This PC
# sleeps rather than shuts down, so logon triggers fire roughly never -- the
# same trap HotspotKeepAlive fell into. A 10-minute timer covers sleep/wake,
# mid-day crashes, and silent overnight deaths with one mechanism.
$ErrorActionPreference = 'Stop'

$repo  = 'D:\projects\website-designers'
$py    = Join-Path $repo 'venv\Scripts\python.exe'
$log   = Join-Path $repo 'pipeline\logs\watchdog.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

try {
    $out = (& $py (Join-Path $repo 'run.py') ops start 2>&1 | Out-String).Trim()
} catch {
    $out = "ERROR: $_"
}

# A healthy tick says both "started: nothing (already up)" and "ALL UP".
# Those are skipped -- logging one every 10 minutes would bury the lines that
# actually matter. Anything else (a real restart, or a service that failed to
# come up) gets written with a timestamp.
$healthyNoop = ($out -match 'started: nothing \(already up\)') -and ($out -match 'ALL UP')

if (-not $healthyNoop) {
    Add-Content -Path $log -Value "[$stamp] $($out -replace "`r`n", ' | ')" -Encoding utf8
}
