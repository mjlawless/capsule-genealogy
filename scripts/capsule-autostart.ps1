<#
.SYNOPSIS
    Install, control and inspect the "Capsule Backend" scheduled task, which starts
    `capsule serve` in the background at logon.

.EXAMPLE
    .\scripts\capsule-autostart.ps1 install     # register the task (and start it if the port is free)
    .\scripts\capsule-autostart.ps1 status      # task state, who owns the port, /health, log path
    .\scripts\capsule-autostart.ps1 restart     # e.g. after pulling new code
    .\scripts\capsule-autostart.ps1 stop
    .\scripts\capsule-autostart.ps1 start
    .\scripts\capsule-autostart.ps1 uninstall

.NOTES
    Runs as you, only while you are logged on, with no admin rights needed. The task
    runs scripts\capsule-serve.ps1, which pins the working directory to this repo and
    logs to data\logs\serve.log. See README.md, "Keep the backend running".

    This is the owner's bridge until the productization tray app (docs/productization-plan.md,
    P2.2) replaces it.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'start', 'stop', 'restart', 'status')]
    [string]$Action = 'status',
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'

$TaskName = 'Capsule Backend'
$repo = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot 'capsule-serve.ps1'
$log = Join-Path $repo 'data\logs\serve.log'

function Get-Task { Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }

function Get-LoopbackListener {
    # Only the loopback listener matters: that is what the extension talks to. A
    # 0.0.0.0 listener on the same port belongs to some other program.
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in '127.0.0.1', '::1' } |
        Select-Object -First 1
}

function Get-CapsuleProcesses {
    # The launcher, and every process in the cmd -> uv -> python chain it spawns, as well
    # as a hand-started `uv run capsule serve`.
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match 'capsule-serve\.ps1|capsule(\.exe)?"?\s+serve' -and $_.ProcessId -ne $PID }
}

function Test-Health {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        return ($r.status -eq 'ok')
    } catch { return $false }
}

function Stop-Capsule {
    if (Get-Task) { Stop-ScheduledTask -TaskName $TaskName }
    # Stopping the task ends the launcher, but its children can outlive it. Kill the
    # launcher first so nothing restarts the server, then everything else.
    $procs = @(Get-CapsuleProcesses | Sort-Object { $_.CommandLine -notmatch 'capsule-serve\.ps1' })
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Host "stopped $($procs.Count) process(es)"
}

function Wait-Healthy([int]$seconds = 45) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Health) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Start-Capsule {
    if (-not (Get-Task)) { throw "task '$TaskName' is not installed; run: .\scripts\capsule-autostart.ps1 install" }
    $listener = Get-LoopbackListener
    if ($listener) {
        throw "something is already listening on 127.0.0.1:$Port (pid $($listener.OwningProcess)); run 'stop' or 'restart' instead"
    }
    Start-ScheduledTask -TaskName $TaskName
    if (Wait-Healthy) { Write-Host "Capsule backend is up on http://127.0.0.1:$Port" }
    else { Write-Warning "no healthy response yet; check the log: $log" }
}

function Show-Status {
    $task = Get-Task
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host ("task:     {0} (last run {1}, result 0x{2:X})" -f $task.State, $info.LastRunTime, $info.LastTaskResult)
    } else {
        Write-Host 'task:     not installed'
    }
    $listener = Get-LoopbackListener
    if ($listener) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        $isCapsule = $owner.CommandLine -match 'capsule(\.exe)?"?\s+serve'
        Write-Host ("port:     127.0.0.1:{0} held by pid {1} ({2})" -f $Port, $listener.OwningProcess,
            $(if ($isCapsule) { 'capsule serve' } else { "NOT Capsule: $($owner.CommandLine)" }))
    } else {
        Write-Host "port:     nothing listening on 127.0.0.1:$Port"
    }
    Write-Host ("health:   {0}" -f $(if (Test-Health) { 'ok' } else { 'no answer' }))
    Write-Host "log:      $log"
}

switch ($Action) {
    'install' {
        $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
        if (-not $uv) { throw 'uv was not found on PATH; install it first (https://docs.astral.sh/uv/)' }
        # No API-key check here (v0.2.1): Capsule runs without a key (captures are archived and
        # read later), and the key lives in the OS credential store, not .env. The old check
        # also failed the whole first install: Select-String on a missing .env threw under
        # $ErrorActionPreference = 'Stop' despite -ErrorAction SilentlyContinue, and a fresh
        # install has no .env.

        $psArgs = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Uv "{1}"' -f $launcher, $uv
        $taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs -WorkingDirectory $repo
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
        $trigger.Delay = 'PT15S'  # let the network settle after logon
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -MultipleInstances IgnoreNew `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
            -StartWhenAvailable

        Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger -Principal $principal `
            -Settings $settings -Description "Runs 'capsule serve' from $repo at logon (scripts\capsule-autostart.ps1)." `
            -Force | Out-Null
        Write-Host "installed task '$TaskName' (runs at logon, uv = $uv)"

        $listener = Get-LoopbackListener
        if ($listener) {
            Write-Host "127.0.0.1:$Port is already in use (pid $($listener.OwningProcess)), so the task was not started now."
            Write-Host "To hand over to the task: .\scripts\capsule-autostart.ps1 restart"
        } else {
            Start-Capsule
        }
    }
    'uninstall' {
        if (Get-Task) {
            Stop-Capsule
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Host "removed task '$TaskName'"
        } else {
            Write-Host "task '$TaskName' is not installed"
        }
    }
    'start'   { Start-Capsule }
    'stop'    { Stop-Capsule }
    'restart' { Stop-Capsule; Start-Sleep -Seconds 2; Start-Capsule }
    'status'  { Show-Status }
}
