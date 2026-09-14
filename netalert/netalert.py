"""NetAlert - monitors internet connectivity and sounds a speaker alarm on outages.

Runs headless under pythonw.exe. Writes drop/recovery history to netalert.log
and its process id to netalert.pid so stop_netalert.bat can kill it.
"""
import json
import os
import select
import socket
import sys
import time
import traceback
from datetime import datetime

CHECK_HOSTS = [("1.1.1.1", 53), ("8.8.8.8", 53)]
CHECK_INTERVAL = 2      # seconds between checks while online
RETRY_DELAY = 0.5       # seconds before re-checking after a failed check
ALARM_REPEAT = 2        # seconds between alarm bursts while offline
FAILS_TO_OFFLINE = 2    # consecutive failed rounds before declaring an outage
CONNECT_TIMEOUT = 1

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# The speaker to alarm on, regardless of which output Windows treats as
# default (connected earbuds must not steal the alarm). Set in config.json;
# empty means "use whatever the default output is". Filled in by load_config().
ALARM_DEVICE = ""
LOG_FILE = os.path.join(BASE_DIR, "netalert.log")
LOG_MAX_BYTES = 512 * 1024   # roll over at half a megabyte
PID_FILE = os.path.join(BASE_DIR, "netalert.pid")
# The lock lives outside the project folder on purpose: two copies
# installed in different directories must still refuse to both run,
# otherwise a redeploy leaves two alarms sounding at once.
LOCK_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or BASE_DIR, "pc-alerts")
LOCK_FILE = os.path.join(LOCK_DIR, "netalert.lock")

_LOCK = None   # held open for the process lifetime


class Monitor:
    """Connection state machine: debounces failures, alarms while offline."""

    def __init__(self, check, on_down, on_alarm, on_up, fails_to_offline=FAILS_TO_OFFLINE):
        self.check = check
        self.on_down = on_down
        self.on_alarm = on_alarm
        self.on_up = on_up
        self.fails_to_offline = fails_to_offline
        self.online = True
        self.fails = 0

    def tick(self):
        up = self.check()
        if self.online:
            if up:
                self.fails = 0
            else:
                self.fails += 1
                if self.fails >= self.fails_to_offline:
                    self.online = False
                    self.on_down()
        else:
            if up:
                self.online = True
                self.fails = 0
                self.on_up()
            else:
                self.on_alarm()
        return self.online


def _norm(name):
    return "".join(name.lower().split())


def pick_output_device(devices, target):
    """Pick the sounddevice index matching the target device name.

    devices: dicts with index/name/hostapi_name/max_output_channels.
    Matches on substring either way because MME truncates names to 31
    chars; prefers WASAPI entries (full names, better latency).
    """
    t = _norm(target)
    if not t:
        return None
    best, best_score = None, -1
    for d in devices:
        if d.get("max_output_channels", 0) < 1:
            continue
        n = _norm(d.get("name") or "")
        if not n or (n not in t and t not in n):
            continue
        score = len(n) + (1000 if "wasapi" in d.get("hostapi_name", "").lower() else 0)
        if score > best_score:
            best, best_score = d["index"], score
    return best


def resolve_alarm_device(refresh=False):
    """Index of the alarm speaker, or None. refresh=True re-enumerates the
    hardware so speakers plugged in after startup are picked up."""
    import sounddevice as sd
    if refresh:
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
    hostapis = sd.query_hostapis()
    devices = [
        {"index": i, "name": d["name"],
         "hostapi_name": hostapis[d["hostapi"]]["name"],
         "max_output_channels": d["max_output_channels"]}
        for i, d in enumerate(sd.query_devices())
    ]
    return pick_output_device(devices, ALARM_DEVICE)


def _speaker_endpoint():
    """Endpoint volume of the alarm speaker; falls back to the default output."""
    import warnings
    from pycaw.pycaw import AudioUtilities
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for d in AudioUtilities.GetAllDevices():
            if (_norm(d.FriendlyName or "") == _norm(ALARM_DEVICE)
                    and getattr(d.state, "name", "") == "Active"):
                try:
                    return d.EndpointVolume
                except Exception:
                    break      # device busy//vanishing - use the default output
    return AudioUtilities.GetSpeakers().EndpointVolume


class VolumeGuard:
    """Forces speakers audible while alarming; restores the user's volume after."""

    def __init__(self, get_endpoint=None, log=None, level=1.0):
        self.get_endpoint = get_endpoint or _speaker_endpoint
        self.log = log or globals()["log"]
        self.level = level
        self.saved = None  # (volume_scalar, mute) captured at outage start

    def force(self):
        try:
            vol = self.get_endpoint()
            if self.saved is None:
                self.saved = (vol.GetMasterVolumeLevelScalar(), vol.GetMute())
            vol.SetMute(0, None)
            vol.SetMasterVolumeLevelScalar(self.level, None)
        except Exception as e:
            self.log(f"volume control failed: {e}")

    def restore(self):
        if self.saved is None:
            return
        volume, mute = self.saved
        self.saved = None
        try:
            vol = self.get_endpoint()
            vol.SetMasterVolumeLevelScalar(volume, None)
            vol.SetMute(mute, None)
        except Exception as e:
            self.log(f"volume restore failed: {e}")


def internet_up(hosts=None):
    """True if any probe host answers.

    Hosts are probed at the same time so one dead host cannot add its timeout
    to the detection delay. This uses non-blocking sockets rather than threads:
    the check runs every couple of seconds forever, and spawning threads that
    often is both wasteful and the first thing to fail when the machine is
    under memory pressure (which has killed this monitor before).
    """
    targets = list(hosts or CHECK_HOSTS)
    pending = []
    try:
        for host, port in targets:
            sock = socket.socket()
            sock.setblocking(False)
            try:
                sock.connect_ex((host, port))
                pending.append(sock)
            except OSError:
                sock.close()

        deadline = time.monotonic() + CONNECT_TIMEOUT
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                _, writable, failed = select.select([], pending, pending, remaining)
            except OSError:
                return False
            for sock in writable:
                if sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
                    return True
                if sock in pending:
                    pending.remove(sock)
            for sock in failed:
                if sock in pending:
                    pending.remove(sock)
        return False
    finally:
        for sock in pending:
            try:
                sock.close()
            except OSError:
                pass


def next_delay(monitor):
    """Seconds to wait before the next check, based on monitor state."""
    if not monitor.online:
        return ALARM_REPEAT
    if monitor.fails > 0:
        return RETRY_DELAY  # suspected outage: confirm fast
    return CHECK_INTERVAL


def load_config():
    """Read config.json. Missing or broken config must never stop the alarm."""
    global ALARM_DEVICE
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            ALARM_DEVICE = json.load(f).get("alarm_device", "") or ""
    except FileNotFoundError:
        log("config.json not found; alarm will use the default output")
    except (ValueError, OSError) as e:
        log(f"config.json unreadable ({e}); alarm will use the default output")
    return ALARM_DEVICE


def log(message):
    """Append to the log, rolling it over before it can grow without bound.

    This process runs for months at a time, so an unrotated log is a slow
    disk leak. One previous generation is kept: enough to investigate a
    missed outage, bounded enough to forget about.
    """
    try:
        if os.path.getsize(LOG_FILE) >= LOG_MAX_BYTES:
            os.replace(LOG_FILE, LOG_FILE + ".1")
    except OSError:
        pass            # no log yet, or it is busy: appending still works
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{stamp}  {message}\n")


def play_tone(freq, ms, device):
    import numpy as np
    import sounddevice as sd
    # WASAPI shared mode only accepts the device's own mix rate
    info = sd.query_devices(kind="output") if device is None else sd.query_devices(device)
    rate = int(info["default_samplerate"])
    n = int(rate * ms / 1000)
    t = np.arange(n) / rate
    wave = 0.9 * np.sin(2 * np.pi * freq * t)
    fade = min(n // 10, 200)  # short fade in/out to avoid clicks
    env = np.ones(n)
    env[:fade] = np.linspace(0, 1, fade)
    env[n - fade:] = np.linspace(1, 0, fade)
    sd.play((wave * env).astype(np.float32), rate, device=device, blocking=True)


def beep_fallback(tones):
    """Last resort: PC beep on whatever the default output is."""
    import winsound
    for freq, ms in tones:
        winsound.Beep(freq, ms)


class AlarmOutput:
    """Plays tones on the configured speaker, surviving device hotplug.

    The speaker may be absent at startup (driver not ready, cable unplugged,
    remote session) and appear later, so a missing or failing device triggers
    a hardware re-scan before falling back to the default output.
    """

    def __init__(self, resolve=None, play=None, fallback=None, log=None):
        self.resolve = resolve or resolve_alarm_device
        self.play_tone = play or play_tone
        self.fallback = fallback or beep_fallback
        self.log = log or (lambda m: globals()["log"](m))
        self.device = None

    def _attempt(self, tones, device):
        try:
            for freq, ms in tones:
                self.play_tone(freq, ms, device)
            return True
        except Exception as e:
            self.log(f"playback on device {device} failed ({e})")
            return False

    def play(self, tones):
        """Sound the tones, trying hardest-to-hear outputs first."""
        if self.device is None:
            self.device = self.resolve(refresh=False)
        if self.device is not None and self._attempt(tones, self.device):
            return True

        self.device = None
        found = self.resolve(refresh=True)   # speakers may have appeared/changed
        if found is not None:
            self.log(f"alarm output now on: {ALARM_DEVICE} (device {found})")
            if self._attempt(tones, found):
                self.device = found
                return True

        # Speaker unreachable (unplugged, or an RDP session hides it):
        # better a loud tone on the default output than no alarm at all.
        if self._attempt(tones, None):
            return True
        self.fallback(tones)
        return False


ALARM_TONES = [(950, 250), (600, 250)] * 2
RECOVERY_TONES = [(523, 150), (659, 150), (784, 150), (1047, 150)]


def alarm_burst(output):
    output.play(ALARM_TONES)


def recovery_chime(output):
    output.play(RECOVERY_TONES)


def monitor_loop(monitor, guard, sleep=time.sleep, stop_after=None):
    """Run the monitor until stopped, always handing back the user's volume.

    An outage raises the speaker to full and unmutes it, and only a recovery
    puts it back. Without the finally below, a process killed mid-outage -
    the installer force-stops it, and Windows has killed it for memory before
    - would leave the machine at full volume permanently.
    """
    ticks = 0
    try:
        while stop_after is None or ticks < stop_after:
            try:
                monitor.tick()
            except Exception:
                log("check failed: "
                    + " | ".join(traceback.format_exc(limit=3).splitlines()[-3:]))
            ticks += 1
            sleep(next_delay(monitor))
    finally:
        guard.restore()
    return ticks


def acquire_single_instance(path=None):
    """Take an exclusive OS lock, or return False if another copy holds it.

    A lock beats comparing process ids: Windows reuses pids, so a stale pid
    file could match an unrelated python process and wrongly convince the
    watchdog that the monitor is alive. The OS releases this lock however the
    process dies, including a hard kill or being killed for memory.
    """
    global _LOCK
    import msvcrt
    target = path or LOCK_FILE
    os.makedirs(os.path.dirname(target), exist_ok=True)
    handle = open(target, "a+")
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return False
    _LOCK = handle
    return True


def parse_args(argv):
    """(hosts, test_mode) for a command line. Debug form:

        netalert.py --hosts 10.255.255.1:53

    test_mode is True only for a well-formed --hosts run. That form skips the
    single-instance lock on purpose, so the tool can be verified without a
    monitoring gap - which makes it important that a malformed one does not
    also skip it and quietly start a second, unregistered monitor.
    """
    if len(argv) == 3 and argv[1] == "--hosts":
        try:
            hosts = [(h.split(":")[0], int(h.split(":")[1]))
                     for h in argv[2].split(",")]
        except (ValueError, IndexError):
            return None, False
        if hosts:
            return hosts, True
    return None, False


def main():
    hosts, test_mode = parse_args(sys.argv)
    if not test_mode and not acquire_single_instance():
        return          # another copy is live; the watchdog calls us often

    if not test_mode:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))

    load_config()
    # Test runs are marked: they share this log with the live monitor, and an
    # unmarked line makes the outage history impossible to read back.
    log(f"NetAlert started (pid {os.getpid()})"
        + (" [test mode]" if test_mode else ""))
    output = AlarmOutput()
    try:
        device = output.resolve(refresh=False)
        output.device = device
    except Exception as e:
        device = None
        log(f"alarm device lookup failed ({e})")
    if device is not None:
        log(f"alarm output locked to: {ALARM_DEVICE} (device {device})")
    else:
        log(f"'{ALARM_DEVICE}' not present yet; will re-scan when the alarm fires")

    guard = VolumeGuard()
    monitor = Monitor(
        check=lambda: internet_up(hosts),
        on_down=lambda: (log("INTERNET DOWN - alarm sounding"), guard.force(), alarm_burst(output)),
        on_alarm=lambda: (guard.force(), alarm_burst(output)),
        on_up=lambda: (log("Internet is back online"), recovery_chime(output), guard.restore()),
    )
    try:
        monitor_loop(monitor, guard)
    except BaseException:
        # pythonw has no console, so an unlogged crash is an invisible one.
        log("FATAL: " + " | ".join(traceback.format_exc().splitlines()[-4:]))
        raise
    finally:
        if not test_mode:
            try:
                os.remove(PID_FILE)
            except OSError:
                pass
        log("NetAlert stopped")


if __name__ == "__main__":
    main()
