<#
    Deploys NetAlert and MarketAlert on this PC.

    Run from the repo folder:   powershell -ExecutionPolicy Bypass -File install.ps1

    It installs dependencies, creates each tool's config from the example,
    pins the Python interpreter, registers autostart at login and a watchdog
    that restarts anything that dies, then starts both tools.

    Safe to re-run: everything it does is idempotent.
#>
param(
    [switch]$NoWatchdog,      # skip the 5-minute restart task
    [switch]$NoAutostart,     # skip the Startup-folder shortcuts
    [int]$WatchdogMinutes = 5
)

$ErrorActionPreference = 'Stop'
$root  = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = @(
    @{ Name = 'NetAlert';    Dir = Join-Path $root 'netalert';    Launcher = 'start_netalert.vbs' }
    @{ Name = 'MarketAlert'; Dir = Join-Path $root 'marketalert'; Launcher = 'start_marketalert.vbs' }
)

function Say($msg, $colour = 'Gray') { Write-Host $msg -ForegroundColor $colour }

Say "`n=== pc-alerts installer ===`n" Cyan

# --- 1. Python -------------------------------------------------------------
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Say "Python was not found on PATH." Red
    Say "Install Python 3.9+ from https://python.org (tick 'Add python.exe to PATH'), then re-run."
    exit 1
}
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { Say "pythonw.exe not found next to python.exe" Red; exit 1 }
Say "Python      : $python"
Say "Version     : $(& $python --version 2>&1)"

# --- 2. Dependencies -------------------------------------------------------
Say "`nInstalling dependencies..." Cyan
& $python -m pip install --quiet --upgrade --requirement (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Say "pip install failed" Red; exit 1 }
Say "Dependencies installed." Green

# --- 3. Per-tool setup -----------------------------------------------------
$startup = [Environment]::GetFolderPath('Startup')
$shell   = New-Object -ComObject WScript.Shell

foreach ($tool in $tools) {
    Say "`n--- $($tool.Name) ---" Cyan
    if (-not (Test-Path $tool.Dir)) { Say "missing folder $($tool.Dir), skipping" Yellow; continue }

    # config.json from the example, if absent (never overwrite local settings)
    $cfg     = Join-Path $tool.Dir 'config.json'
    $example = Join-Path $tool.Dir 'config.example.json'
    if ((Test-Path $example) -and -not (Test-Path $cfg)) {
        Copy-Item $example $cfg
        Say "created config.json (edit it to name your speaker)" Yellow
    } else {
        Say "config.json already present, left untouched"
    }

    # pin the interpreter for the silent launcher
    Set-Content -Path (Join-Path $tool.Dir 'pythonw.txt') -Value $pythonw -Encoding ASCII

    $launcher = Join-Path $tool.Dir $tool.Launcher

    if (-not $NoAutostart) {
        $lnk = $shell.CreateShortcut((Join-Path $startup "$($tool.Name).lnk"))
        $lnk.TargetPath       = $launcher
        $lnk.WorkingDirectory = $tool.Dir
        $lnk.Description      = "$($tool.Name) - starts at login"
        $lnk.Save()
        Say "autostart at login registered"
    }

    if (-not $NoWatchdog) {
        $taskName = "$($tool.Name) Watchdog"
        $action   = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "`"$launcher`""
        $trigger  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
                        -RepetitionInterval (New-TimeSpan -Minutes $WatchdogMinutes)
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Principal $principal -Force `
            -Description "Restarts $($tool.Name) if it is not running" | Out-Null
        Say "watchdog registered (every $WatchdogMinutes min)"
    }

    # start it now (the file lock makes a duplicate launch a no-op)
    & wscript.exe $launcher
    Say "started" Green
}

Start-Sleep -Seconds 12

# --- 4. Report -------------------------------------------------------------
Say "`n=== status ===" Cyan
foreach ($tool in $tools) {
    $running = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
               Where-Object { $_.CommandLine -like "*$($tool.Name.ToLower()).py*" }
    if ($running) { Say "$($tool.Name): running (pid $($running.ProcessId))" Green }
    else          { Say "$($tool.Name): NOT running - check $($tool.Dir)\$($tool.Name.ToLower()).log" Red }
}

Say @"

Next step: name your speaker so alerts are not stolen by earbuds.
  1. List your output devices:
       python -c "import sounddevice as sd; print(sd.query_devices())"
  2. Put the exact name into:
       netalert\config.json     -> "alarm_device"
       marketalert\config.json  -> "speaker_device"
  3. Restart both:  .\install.ps1

Leaving those empty is fine - alerts then play on the default output.
"@ Yellow
