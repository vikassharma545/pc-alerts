# NetAlert

Sounds an alarm through the speakers when the internet drops, and a chime when
it comes back. Runs hidden in the background.

## Behaviour

- Probes Cloudflare DNS (`1.1.1.1:53`) and Google DNS (`8.8.8.8:53`) every
  **2 seconds**. Both are probed at the same time, so one dead host cannot add
  its timeout to the detection delay.
- An outage is declared only after **two consecutive failures**, so a single
  dropped packet never triggers a false alarm. A suspected failure is
  re-checked after 0.5s rather than waiting a full cycle.
- **Detection takes about 2.7 seconds**; recovery is noticed in about 4.
- While offline: a two-tone siren every 2 seconds until the connection returns.
  On recovery: a short rising chime, then silence.
- Every drop and recovery is timestamped in `netalert.log`, so you also get an
  outage history. The log rolls over at 512 KB, keeping one previous
  generation as `netalert.log.1`.
- If the monitor is killed during an outage — the installer stopping it, or
  Windows reclaiming memory — the volume it raised for the alarm is put back
  on the way out, rather than leaving the machine at full volume.

## Settings

`config.json`:

```json
{ "alarm_device": "Speakers (Realtek(R) Audio)" }
```

The speaker to alarm on, exactly as Windows Sound settings names it. Empty
means "use the default output". List the names with:

```powershell
python -c "import sounddevice as sd; print(sd.query_devices())"
```

Timings live at the top of `netalert.py` (`CHECK_INTERVAL`, `ALARM_REPEAT`,
`FAILS_TO_OFFLINE`, `CONNECT_TIMEOUT`) if you want to tune them.

## Checking it without waiting for an outage

`--hosts` points the monitor at an address of your choosing and deliberately
bypasses the single-instance lock, so it runs alongside the live monitor
without leaving you unmonitored:

```powershell
python netalert.py --hosts 127.0.0.1:9999
```

Nothing is listening on that port, so it will declare an outage and alarm
within a few seconds. Ctrl-C to stop. Test runs are tagged `[test mode]` in
the log so they cannot be mistaken for real outages later.

Only a well-formed `--hosts host:port` bypasses the lock. `--hosts` with a
missing or unparseable value is treated as a normal run, so it can never
start a second monitor that the watchdog cannot see.

## Notes

The connection check uses non-blocking sockets rather than threads. It runs
every couple of seconds forever, and spawning threads that often is both
wasteful and the first thing to fail when the machine is under memory
pressure — which killed this monitor once.
