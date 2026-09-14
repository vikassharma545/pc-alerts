# pc-alerts

Two small always-on Windows tools that make noise when something needs your
attention. Both run hidden in the background, start at login, and are restarted
automatically if they ever die.

| Tool | What it does |
|------|--------------|
| **[NetAlert](netalert/)** | Watches the internet connection and sounds a siren within ~3 seconds of it dropping, then a chime when it returns. |
| **[MarketAlert](marketalert/)** | Speaks NSE/BSE trading session events — market open, the closing auction windows, expiry days, and a reminder the day before an exchange holiday. |

Both share the same audio behaviour:

- **Plays on the speaker you name**, regardless of which output Windows treats
  as default — connected earbuds cannot silently steal an alert. The Windows
  default device is never changed.
- **Low or muted volume cannot silence them.** Volume is raised for the alert
  and restored to exactly what it was afterwards.
- **Falls back rather than going quiet:** if the named speaker is unreachable
  (unplugged, or hidden inside a Remote Desktop session) the alert re-scans the
  hardware and then plays on whatever output does exist.

## Install on a new PC

Requires **Windows** and **Python 3.9+** ([python.org](https://python.org), tick
*Add python.exe to PATH* during install).

```powershell
git clone https://github.com/<you>/pc-alerts.git
cd pc-alerts
powershell -ExecutionPolicy Bypass -File install.ps1
```

That installs dependencies, creates each tool's `config.json`, registers
autostart plus a restart watchdog, and starts both. It is safe to re-run.

Then name your speaker so alerts do not follow your earbuds:

```powershell
python -c "import sounddevice as sd; print(sd.query_devices())"
```

Put the exact device name into `netalert\config.json` (`alarm_device`) and
`marketalert\config.json` (`speaker_device`), then re-run `install.ps1`.
Leaving them empty is fine — alerts play on the default output.

## Everyday use

| | NetAlert | MarketAlert |
|---|---|---|
| Start | `netalert\start_netalert.vbs` | `marketalert\start_marketalert.vbs` |
| Stop | `netalert\stop_netalert.bat` | `marketalert\stop_marketalert.bat` |
| History | `netalert\netalert.log` | `marketalert\marketalert.log` |
| Settings | `netalert\config.json` | `marketalert\config.json`, `holidays.txt` |

To stop a tool permanently, delete its shortcut from the Startup folder
(`shell:startup`) and its watchdog task:

```powershell
Unregister-ScheduledTask -TaskName "NetAlert Watchdog" -Confirm:$false
```

## How they stay alive

Being silent is the one way these tools fail badly — you assume you are covered
when you are not. Three layers guard against that:

1. A failed check is logged and survived, never fatal.
2. A crash writes a traceback to the log (running under `pythonw` there is no
   console to print to, so an unlogged crash would be invisible).
3. A **watchdog task relaunches each tool every 5 minutes** if it is not
   running. An exclusive file lock makes that a no-op when it is already alive,
   so duplicates are impossible.

Layer 3 exists because of a real incident: the PC ran out of virtual memory,
Windows killed NetAlert, and it stayed dead for two days unnoticed.

## Tests

```powershell
cd netalert     ; python -m unittest discover -p "test_*.py"
cd ..\marketalert ; python -m unittest discover -p "test_*.py"
```

154 tests covering outage detection and debouncing, the connectivity probe
itself, audio device fallback, volume handling (including handing the volume
back when the monitor is killed mid-outage), speech rendering and its cache,
PowerShell quoting of announcement text, the market session calendar,
expiry-day and holiday rules, holiday-calendar expiry, scheduling, log
rotation, argument parsing, config values that are not numbers, and
single-instance locking.

## What is not committed

`config.json`, `*.log`, `*.log.1`, `pythonw.txt`, `*.pid`, `*.lock` and
`voice_cache/` are machine-specific or personal and are ignored by git.
Everything needed to rebuild them is created by `install.ps1` and the first
run.

Each log rotates at 512 KB, keeping one previous generation as `*.log.1`, so
an always-on tool cannot fill the disk.
