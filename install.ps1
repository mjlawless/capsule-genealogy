<#
.SYNOPSIS
    Capsule installer for Windows.

        powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/mjlawless/capsule-genealogy/main/install.ps1 | iex"

.DESCRIPTION
    1. Installs `uv` (which brings its own Python) if you do not have it.
    2. Downloads the latest Capsule release into %LOCALAPPDATA%\Capsule\app. An upgrade replaces
       only the program; your archive lives elsewhere and is never touched.
    3. Runs `capsule setup --no-key` (no questions; add your Anthropic key later in the
       extension, or never: Capsule archives pages without one).
    4. Starts Capsule now and at every logon (a per-user scheduled task, no admin rights).
    5. Checks that it is running and tells you where the Chrome extension folder is.

    Optional environment variables: CAPSULE_HOME (install location), CAPSULE_VERSION (a release
    tag such as v0.2.0), CAPSULE_INSTALL_SOURCE (install from a local folder; for testing),
    CAPSULE_AUTOSTART=0 (start now without registering the logon task).
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Repo = 'mjlawless/capsule-genealogy'
$Port = 8765
if ($env:CAPSULE_HOME) { $CapsuleHome = $env:CAPSULE_HOME } else { $CapsuleHome = Join-Path $env:LOCALAPPDATA 'Capsule' }
$App = Join-Path $CapsuleHome 'app'

function Fail($message) {
    Write-Host "Capsule install failed: $message" -ForegroundColor Red
    if (Get-Command Release-Lock -ErrorAction SilentlyContinue) { Release-Lock }
    exit 1
}

Write-Host "Installing Genealogy Capsule into $App"

# 1. uv ---------------------------------------------------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing uv (the tool that runs Capsule)...'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression | Out-Null
    $env:Path = (Join-Path $env:USERPROFILE '.local\bin') + ';' + $env:Path
}
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) { Fail 'uv is not on PATH after installing it' }
$uv = $uvCommand.Source

# 2. the program ------------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $CapsuleHome | Out-Null

# One install at a time per folder: two overlapping runs would swap each other's files and clean
# up each other's rollback copies (Codex review). Creating a directory is atomic; the lock is
# released on every way out (Fail, Rollback and the normal end).
$lockPath = Join-Path $CapsuleHome '.install-lock'
try {
    New-Item -ItemType Directory -Path $lockPath -ErrorAction Stop | Out-Null
} catch {
    Write-Host "Capsule install failed: another Capsule install is already running in $CapsuleHome. If none is, delete the folder $lockPath and run this again." -ForegroundColor Red
    exit 1
}
$script:lockHeld = $true
function Release-Lock { if ($script:lockHeld) { Remove-Item $lockPath -Force -ErrorAction SilentlyContinue; $script:lockHeld = $false } }
# Any error not handled below (a network timeout while downloading, a bad archive) still
# releases the lock, so the next run is not refused as "already running" (Codex review).
trap {
    Write-Host "Capsule install failed: $($_.Exception.Message)" -ForegroundColor Red
    Release-Lock
    exit 1
}

$stage = Join-Path $CapsuleHome ("app.new." + $PID)
# A staging folder left by an earlier, interrupted run is not anyone's any more (we hold the lock).
Get-ChildItem $CapsuleHome -Directory -Filter 'app.new*' -ErrorAction SilentlyContinue |
    ForEach-Object { Rename-Item $_.FullName ("app.stale." + $PID + "." + $_.Name) }
New-Item -ItemType Directory -Path $stage | Out-Null
if ($env:CAPSULE_INSTALL_SOURCE) {
    Write-Host "Copying Capsule from $env:CAPSULE_INSTALL_SOURCE"
    robocopy $env:CAPSULE_INSTALL_SOURCE $stage /E /XD .git .venv data /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail 'could not copy the source folder' }
} else {
    $tag = $env:CAPSULE_VERSION
    if (-not $tag) { $tag = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest").tag_name }
    if (-not $tag) { Fail 'could not find the latest Capsule release' }
    Write-Host "Downloading Capsule $tag..."
    $zip = Join-Path $env:TEMP "capsule-$tag-$PID.zip"
    $unzipped = Join-Path $env:TEMP "capsule-$tag-$PID"
    Invoke-WebRequest "https://github.com/$Repo/archive/refs/tags/$tag.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $unzipped -Force
    $inner = Get-ChildItem $unzipped -Directory | Select-Object -First 1
    Get-ChildItem $inner.FullName -Force | Move-Item -Destination $stage
}
if (-not (Test-Path (Join-Path $stage 'pyproject.toml'))) { Fail 'the download does not look like Capsule' }

# Upgrades: stop the Capsule that is running now, so the health check below can only ever be
# answered by the NEW one; refuse if something that is not Capsule holds the port.
function Test-Healthy {
    try { return ((Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2).status -eq 'ok') } catch { return $false }
}
function Get-Listener {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in '127.0.0.1', '::1' } | Select-Object -First 1
}
function Stop-Capsule {
    # Only THIS installation is stopped: its scheduled task (whose action lives under $App)
    # and processes started from $App. The autostart helper's `stop` would end every Capsule
    # on the machine, a developer checkout included (Codex review), so it is not used here.
    $task = Get-ScheduledTask -TaskName 'Capsule Backend' -ErrorAction SilentlyContinue
    if ($task) {
        # The logon task is shared by name; it is only ever stopped or replaced if it runs THIS
        # installation. Another one's (a developer checkout, say) keeps its archive elsewhere,
        # and taking its task over would quietly switch archives (Codex review).
        $taskArgs = ($task.Actions | ForEach-Object { $_.Arguments }) -join ' '
        $taskScript = if ($taskArgs -match '-File\s+"([^"]+)"') { $Matches[1] } else { $null }
        if ($taskArgs.Contains($App + '\')) {
            Stop-ScheduledTask -TaskName 'Capsule Backend' -ErrorAction SilentlyContinue
        } elseif ($taskScript -and -not (Test-Path -LiteralPath $taskScript)) {
            # Left behind by a copy that has since been deleted: it can start nothing and guards
            # no archive, so it is simply replaced (found on a real upgrade, v0.2.1).
            Unregister-ScheduledTask -TaskName 'Capsule Backend' -Confirm:$false
            Write-Host "Removed an old start-at-logon task left by a Capsule copy that no longer exists."
        } else {
            $other = if ($taskScript) { Split-Path -Parent (Split-Path -Parent $taskScript) } else { '(unknown folder)' }
            Fail ("another copy of Capsule, in $other, is set to start at logon, and it keeps its own archive. " +
                "To switch to this installation, retire it first by running this in PowerShell, then run the installer again:`n" +
                "  powershell -NoProfile -ExecutionPolicy Bypass -File `"$other\scripts\capsule-autostart.ps1`" uninstall")
        }
    }
    # Only Capsule's own server and launcher, and only this installation's ("$App\" with its
    # separator, so a sibling such as app-dev is not matched): a folder path alone
    # also matches an editor or Explorer window open on it, which must never be killed.
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine.Contains($App + '\') -and
            ($_.CommandLine -match 'capsule(\.exe)?"?\s+serve' -or $_.CommandLine -match 'capsule-serve\.ps1')
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    $listener = Get-Listener
    if ($listener) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
        # Only OUR installation is stopped: a Capsule run from another folder keeps its archive
        # next to it, and replacing it would quietly open an empty archive (Codex review).
        if ($proc -and $proc.CommandLine -and $proc.CommandLine.Contains($App + '\')) {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        } elseif ($proc -and $proc.CommandLine -match 'capsule.*serve') {
            Fail "another Capsule is already running from a different folder (process $($proc.ProcessId)). Stop it first; its archive is not touched."
        }
    }
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-Listener) -and -not (Test-Healthy)) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}
function Start-Capsule {
    if ($env:CAPSULE_AUTOSTART -ne '0') {
        & (Join-Path $App 'scripts\capsule-autostart.ps1') install
        return $true
    }
    $logs = Join-Path $CapsuleHome 'logs'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    # Start-Process joins its arguments into ONE command line, so a path with spaces (most
    # Windows profiles) must be quoted explicitly or it splits into several arguments.
    Start-Process -FilePath $uv -ArgumentList ('run --project "' + $App + '" capsule serve') `
        -WorkingDirectory $App -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'serve.log') -RedirectStandardError (Join-Path $logs 'serve.err.log')
    return $false
}
function Wait-Capsule {
    for ($i = 0; $i -lt 90; $i++) { if (Test-Healthy) { return $true }; Start-Sleep -Seconds 1 }
    return $false
}

# Whatever setting put it there, an archive found INSIDE the current program folder would be
# deleted with the old program. Refuse rather than guess where it belongs (Codex review).
if ((Test-Path $App) -and (Get-ChildItem $App -Recurse -Filter 'capsule.db' -ErrorAction SilentlyContinue | Select-Object -First 1)) {
    Fail "an archive (capsule.db) is stored inside $App, which an upgrade replaces. Move that folder out of $App and set CAPSULE_DATA_DIR to its new place, then run this again."
}

# Never inside the program folders: an upgrade swaps those out and deletes the old copy, so an
# archive kept there would be deleted with it (Codex review). Checked BEFORE anything moves.
$savedDataDir = Join-Path $CapsuleHome 'data-dir.txt'
if (-not $env:CAPSULE_DATA_DIR -and (Test-Path $savedDataDir)) { $env:CAPSULE_DATA_DIR = ([System.IO.File]::ReadAllText($savedDataDir)).Trim() }
if ($env:CAPSULE_DATA_DIR) {
    $env:CAPSULE_DATA_DIR = [System.IO.Path]::GetFullPath($env:CAPSULE_DATA_DIR)
    $homeFull = [System.IO.Path]::GetFullPath($CapsuleHome).TrimEnd('\') + '\'
    $archiveFull = $env:CAPSULE_DATA_DIR.TrimEnd('\') + '\'
    if ($archiveFull.StartsWith($homeFull + 'app\', [System.StringComparison]::OrdinalIgnoreCase) -or
        $archiveFull.StartsWith($homeFull + 'app.', [System.StringComparison]::OrdinalIgnoreCase)) {
        Fail "your archive folder ($env:CAPSULE_DATA_DIR) is inside Capsule's program folder, which upgrades replace. Move it somewhere else (for example $CapsuleHome\archive) and set CAPSULE_DATA_DIR to the new place."
    }
}

$previous = $null
$swapped = $false
$hadCapsule = Test-Path $App

# The CURRENT installation's archive settings, so a failed upgrade restarts it on its own archive
# rather than on the one this run was asked to use (Codex review).
$homeEnvPath = Join-Path $CapsuleHome '.env'
$prevSaved = $null
if (Test-Path $savedDataDir) { $prevSaved = [System.IO.File]::ReadAllText($savedDataDir) }
$prevHomeEnv = $null
if (Test-Path $homeEnvPath) { $prevHomeEnv = [System.IO.File]::ReadAllText($homeEnvPath) }

# From the moment anything is stopped, any failure ends here: the previous program is put back
# (or, if nothing was swapped yet, the existing one is started again), so you are never left
# without a working Capsule (Codex review).
function Rollback($message) {
    Write-Host "Capsule install failed: $message" -ForegroundColor Red
    try {
        $restoreEncoding = New-Object System.Text.UTF8Encoding $false
        if ($null -ne $prevSaved) {
            [System.IO.File]::WriteAllText($savedDataDir, $prevSaved, $restoreEncoding)
            $env:CAPSULE_DATA_DIR = $prevSaved.Trim()
        } else {
            Remove-Item $savedDataDir -ErrorAction SilentlyContinue
            Remove-Item Env:CAPSULE_DATA_DIR -ErrorAction SilentlyContinue
        }
        if ($null -ne $prevHomeEnv) {
            [System.IO.File]::WriteAllText($homeEnvPath, $prevHomeEnv, $restoreEncoding)
        } else {
            Remove-Item $homeEnvPath -ErrorAction SilentlyContinue
        }
        # Keyed on the BACKUP existing, not on the swap completing: if moving the new copy into
        # place is what failed, the old one is still set aside and must come back (Codex review).
        if ($previous -and (Test-Path $previous)) {
            Write-Host 'Putting the previous Capsule back...'
            Stop-Capsule | Out-Null
            if (Test-Path $App) { Rename-Item $App ("app.failed." + $PID) }
            Rename-Item $previous 'app'
            Start-Capsule | Out-Null
            if (Wait-Capsule) { Write-Host 'The previous Capsule is running again.' }
        } elseif ($hadCapsule -and -not $swapped -and (Test-Path $App)) {
            Write-Host 'Starting the existing Capsule again...'
            Start-Capsule | Out-Null
            if (Wait-Capsule) { Write-Host 'The existing Capsule is running again.' }
        }
    } catch {
        Write-Host "Could not restore the previous Capsule automatically: $($_.Exception.Message)" -ForegroundColor Red
    }
    Release-Lock
    exit 1
}

# Everything from stopping the running Capsule onward runs inside one try: a native command's
# failure AND a thrown error (a full disk, a scheduled task that cannot be registered) both end
# in the rollback.
try {
    if (-not (Stop-Capsule)) { Fail "port $Port is in use by another program; close it and run this again" }

    if ($hadCapsule) { $previous = Join-Path $CapsuleHome ("app.previous." + $PID); Rename-Item $App (Split-Path $previous -Leaf) }
    Rename-Item $stage 'app'
    $swapped = $true

    # A custom archive folder (CAPSULE_DATA_DIR) must reach every way Capsule starts: this run,
    # the logon task (whose working folder is the program folder) and later upgrades run without
    # the variable. Remembered beside the program, written as .env where each launcher starts.
    # UTF-8 WITHOUT a byte-order mark: a path like C:\Users\José must survive, and the .env
    # reader must not see a BOM (Windows PowerShell's -Encoding UTF8 writes one).
    $utf8 = New-Object System.Text.UTF8Encoding $false

    # The logon task starts INSIDE the program folder, so app\.env is its configuration. Carry
    # the previous one forward whole (an archive folder, a key, anything else set there), then
    # let the installer's own setting replace only its own line (Codex review).
    $carried = @()
    if ($previous -and (Test-Path (Join-Path $previous '.env'))) {
        $carried = [System.IO.File]::ReadAllLines((Join-Path $previous '.env'), $utf8)
    }
    if ($env:CAPSULE_DATA_DIR) {
        [System.IO.File]::WriteAllText($savedDataDir, $env:CAPSULE_DATA_DIR, $utf8)
        # Single-quoted and escaped for dotenv: unquoted, a ' #' in a folder name starts a comment.
        $dotenvValue = $env:CAPSULE_DATA_DIR.Replace('\', '\\').Replace("'", "\'")
        $line = "CAPSULE_DATA_DIR='$dotenvValue'"
        $carried = @($carried | Where-Object { $_ -notmatch '^\s*(export\s+)?CAPSULE_DATA_DIR\s*=' }) + $line
        # Only the archive line changes in CapsuleHome\.env too; every other setting is kept.
        $homeLines = @()
        if ($null -ne $prevHomeEnv) {
            $homeLines = @($prevHomeEnv -split "`r?`n" | Where-Object { $_ -ne '' -and $_ -notmatch '^\s*(export\s+)?CAPSULE_DATA_DIR\s*=' })
        }
        [System.IO.File]::WriteAllText($homeEnvPath, ((@($homeLines) + $line) -join "`n") + "`n", $utf8)
    }
    if ($carried.Count -gt 0) {
        [System.IO.File]::WriteAllText((Join-Path $App '.env'), (($carried -join "`n") + "`n"), $utf8)
    }

    Write-Host 'Preparing Capsule (first time: a minute or two)...'
    & $uv sync --project $App --frozen --quiet
    if ($LASTEXITCODE -ne 0) { throw "could not install Capsule's dependencies" }

    # 3. setup --------------------------------------------------------------------------------
    # From the folder the backend starts in (the logon task runs inside the program folder), so
    # setup reads the same .env and archive and shows the token the backend will use.
    Push-Location $App
    try {
        $env:CAPSULE_INSTALLER = '1'
        & $uv run --project $App capsule setup --no-key
        if ($LASTEXITCODE -ne 0) { throw 'capsule setup did not finish' }
    } finally {
        Remove-Item Env:CAPSULE_INSTALLER -ErrorAction SilentlyContinue
        Pop-Location
    }

    # 4. start it -----------------------------------------------------------------------------
    $loginItem = Start-Capsule

    # 5. check --------------------------------------------------------------------------------
    if (-not (Wait-Capsule)) { throw "Capsule did not start; see the logs in $CapsuleHome" }
} catch {
    Rollback $_.Exception.Message
}

# The new program runs; earlier copies of the PROGRAM (never the archive) can go.
# Belt and braces: a copy holding a capsule.db is kept, whatever the checks above believed.
foreach ($pattern in 'app.previous.*', 'app.stale.*', 'app.failed.*') {
    foreach ($old in Get-ChildItem $CapsuleHome -Directory -Filter $pattern) {
        if (Get-ChildItem $old.FullName -Recurse -Filter 'capsule.db' -ErrorAction SilentlyContinue | Select-Object -First 1) {
            Write-Host "Kept $($old.FullName): it contains an archive (capsule.db)."
        } else {
            Remove-Item $old.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host ''
Write-Host "Capsule is running (http://127.0.0.1:$Port)." -ForegroundColor Green
if ($loginItem) { Write-Host 'It will also start by itself whenever you log on.' }
Write-Host ''
Write-Host "Already added Capsule to Chrome (from the Chrome Web Store)? Then you're done: click its"
Write-Host 'icon and it connects by itself. Otherwise, the last step, in Google Chrome:'
Write-Host '  1. Open chrome://extensions and turn on Developer mode (top right).'
Write-Host "  2. Click 'Load unpacked' and choose this folder:"
Write-Host "       $App\extension"
Write-Host '  3. Pin the Capsule icon, open a record page, and click Capture.'
Write-Host ''
Write-Host "No Anthropic key yet? That's fine: pages are archived now and can be read later."
Release-Lock
