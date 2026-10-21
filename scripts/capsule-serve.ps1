<#
.SYNOPSIS
    Keep `capsule serve` running, logging to data\logs\serve.log.

.DESCRIPTION
    This is what the "Capsule Backend" scheduled task runs at logon (installed by
    scripts\capsule-autostart.ps1). It is safe to run by hand too.

    Why a wrapper instead of pointing the task at uv directly:
      - `capsule serve` resolves data\, .env and data\.capsule-token relative to the
        WORKING DIRECTORY. The wrapper pins it to the repo root, so the task can never
        write a second, empty archive somewhere else (the BUG-003 shape).
      - A background process has no console, so output goes to a log. The previous
        run's log is kept as serve.prev.log. NOTE: the log contains the auth token
        (serve prints it on start); it lives under data\, which is git-ignored.
      - A crash is restarted after a short pause. Repeated fast failures (e.g. the
        port is already taken) stop the loop instead of spinning forever.
#>
[CmdletBinding()]
param(
    # Full path to uv.exe. The installer passes the path it resolved; by hand, PATH is used.
    [string]$Uv = 'uv',
    [int]$MaxFastFailures = 5,
    [int]$FastFailureSeconds = 60,
    [int]$RestartDelaySeconds = 10
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

$logDir = Join-Path $repo 'data\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'serve.log'
if (Test-Path -LiteralPath $log) {
    Move-Item -LiteralPath $log -Destination (Join-Path $logDir 'serve.prev.log') -Force
}

function Write-Log([string]$message) {
    Add-Content -LiteralPath $log -Value ('[{0:yyyy-MM-dd HH:mm:ss}] [launcher] {1}' -f (Get-Date), $message)
}

# Redirected stdout is block-buffered by default; without this the log lags badly.
$env:PYTHONUNBUFFERED = '1'
# ...and encoded in the console codepage, which mangles the banner's em-dash.
$env:PYTHONIOENCODING = 'utf-8'

$fastFailures = 0
while ($true) {
    Write-Log "starting: $Uv run capsule serve (cwd $repo)"
    $started = Get-Date
    # cmd does the redirection so stdout and stderr land in ONE file in order.
    # /s: strip exactly the outer quote pair, so the quoted paths inside survive.
    $cmdLine = '/d /s /c ""{0}" run capsule serve >> "{1}" 2>&1"' -f $Uv, $log
    $proc = Start-Process -FilePath 'cmd.exe' -ArgumentList $cmdLine -NoNewWindow -Wait -PassThru
    $code = $proc.ExitCode
    $ranSeconds = [int]((Get-Date) - $started).TotalSeconds
    Write-Log "capsule serve exited with code $code after ${ranSeconds}s"

    if ($code -eq 0) {
        Write-Log 'clean exit; not restarting'
        exit 0
    }
    if ($ranSeconds -lt $FastFailureSeconds) { $fastFailures++ } else { $fastFailures = 0 }
    if ($fastFailures -ge $MaxFastFailures) {
        Write-Log "giving up after $fastFailures fast failures in a row (port taken? missing key? see above)"
        exit 1
    }
    Write-Log "restarting in ${RestartDelaySeconds}s"
    Start-Sleep -Seconds $RestartDelaySeconds
}
