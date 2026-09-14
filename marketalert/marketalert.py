"""MarketAlert - speaks NSE/BSE session events through the speakers.

Runs headless under pythonw.exe. Announcements are rendered to WAV once and
cached, so each event fires instantly at its exact minute.
"""
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta

import market_schedule as ms
from speaker import (CHIME, Speaker, VolumeFloor, boost, cache_path,
                     load_wav, render_wav, tone)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "marketalert.log")
PID_FILE = os.path.join(BASE_DIR, "marketalert.pid")
# The lock lives outside the project folder on purpose: two copies
# installed in different directories must still refuse to both run,
# otherwise a redeploy leaves two alarms sounding at once.
LOCK_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or BASE_DIR, "pc-alerts")
LOCK_FILE = os.path.join(LOCK_DIR, "marketalert.lock")

_LOCK = None   # held open for the process lifetime
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
HOLIDAY_FILE = os.path.join(BASE_DIR, "holidays.txt")
CACHE_DIR = os.path.join(BASE_DIR, "voice_cache")

POLL_MAX = 30      # seconds; re-checks the clock often enough to survive sleep
GRACE = 120        # an event older than this was slept through - do not announce

DEFAULTS = {
    "voice": "Microsoft Hazel Desktop",
    "speech_rate": 0,
    "speaker_device": "",
    "volume_floor_percent": 90,
    "voice_gain": 1.0,
    "chime_before_voice": True,
    "expiry_nse": True,
    "expiry_bse": True,
    "events": {},
}


def log(message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{stamp}  {message}\n")


def load_config():
    """Config with defaults filled in; a broken file must not stop the alerts."""
    config = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            config.update(json.load(f))
    except FileNotFoundError:
        log("config.json not found; using defaults")
    except (ValueError, OSError) as e:
        log(f"config.json unreadable ({e}); using defaults")
    return config


def load_holidays():
    try:
        with open(HOLIDAY_FILE, encoding="utf-8") as f:
            return ms.parse_holidays(f.read())
    except OSError as e:
        log(f"holidays.txt unreadable ({e}); assuming no holidays")
        return {}


def enabled_keys(config):
    events = config.get("events") or {}
    return {key for key, on in events.items() if on and key in ms.EVENTS_BY_KEY}


def _speaker_endpoint(target):
    """Volume endpoint of the target speaker, else the default output."""
    import warnings
    from pycaw.pycaw import AudioUtilities
    norm = "".join((target or "").lower().split())
    if norm:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for d in AudioUtilities.GetAllDevices():
                name = "".join((d.FriendlyName or "").lower().split())
                if name == norm and getattr(d.state, "name", "") == "Active":
                    return d.EndpointVolume
    return AudioUtilities.GetSpeakers().EndpointVolume


class Announcer:
    """Renders, caches and plays the spoken announcements."""

    def __init__(self, config):
        self.voice = config["voice"]
        self.rate = config["speech_rate"]
        self.chime = config["chime_before_voice"]
        self.gain = float(config.get("voice_gain", 1.0))
        self.speaker = Speaker(config["speaker_device"], log=log)
        self.volume = VolumeFloor(
            lambda: _speaker_endpoint(config["speaker_device"]),
            floor=max(0.0, min(1.0, config["volume_floor_percent"] / 100.0)),
            log=log,
        )

    def wav_for(self, text):
        """Path to the cached rendering of `text`, synthesising it if needed."""
        path = cache_path(CACHE_DIR, text, self.voice)
        if not os.path.exists(path):
            render_wav(text, self.voice, path, self.rate)
        return path

    def prepare(self, text):
        """Make sure `text` is synthesised and cached. Cheap once cached."""
        try:
            self.wav_for(text)
            return True
        except Exception as e:
            log(f"could not pre-render: {e}")
            return False

    def prerender(self, keys, holidays, config, horizon_days=45):
        """Synthesise every phrase due in the coming weeks.

        Rendering shells out to the speech engine, so doing it at the moment an
        event fires would both delay the announcement and risk losing it if the
        machine is briefly unable to start the process. Expiry-day and
        holiday-eve wording differs per date, so the whole horizon is walked
        rather than just today.
        """
        wanted, day = [], datetime.now().date()
        for _ in range(horizon_days):
            if ms.is_trading_day(day, holidays):
                for key in keys:
                    text = ms.announcement(ms.EVENTS_BY_KEY[key], day, holidays,
                                           config["expiry_nse"], config["expiry_bse"])
                    if text and text not in wanted:
                        wanted.append(text)
            day += timedelta(days=1)
        return sum(1 for text in wanted if self.prepare(text))

    def say(self, text):
        """Speak `text` on the configured speaker at an audible volume."""
        try:
            data, rate = load_wav(self.wav_for(text))
            data = boost(data, target_rms=0.30 * self.gain)
        except Exception as e:
            log(f"speech rendering failed: {e}")
            return False
        self.volume.raise_if_needed()
        try:
            if self.chime:
                for freq, ms_ in CHIME:
                    self.speaker.play(tone(freq, ms_, 48000), 48000)
            return self.speaker.play(data, rate)
        finally:
            self.volume.restore()


def acquire_single_instance(path=None):
    """Exclusive OS lock, or False if another copy holds it. Immune to the pid
    reuse that would otherwise fool the restart watchdog."""
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


def run(config, holidays, announcer, now=datetime.now, sleep=time.sleep,
        stop_after=None):
    """Announce each enabled event as it comes due. Returns events announced."""
    keys = enabled_keys(config)
    # Start slightly in the past so an event that fired seconds before launch
    # (a boot landing just after 09:15, say) is still announced rather than lost.
    cursor = now() - timedelta(seconds=GRACE)
    announced = 0
    while stop_after is None or announced < stop_after:
        upcoming = ms.next_event(cursor, keys, holidays)
        if upcoming is None:
            log("no events enabled; nothing to schedule")
            return announced
        when, event = upcoming
        current = now()
        delay = (when - current).total_seconds()
        if delay > 0:
            # Render the upcoming phrase during the idle wait, so a cache miss
            # can never delay or silence the announcement itself.
            prepare = getattr(announcer, "prepare", None)
            if prepare:
                pending = ms.announcement(event, when.date(), holidays,
                                          config["expiry_nse"], config["expiry_bse"])
                if pending:
                    prepare(pending)
            sleep(min(delay, POLL_MAX))
            continue
        cursor = when
        if -delay > GRACE:
            log(f"missed {event.key} at {when:%H:%M} (PC asleep or busy)")
            continue
        text = ms.announcement(event, when.date(), holidays,
                               config["expiry_nse"], config["expiry_bse"])
        if not text:
            continue                     # e.g. holiday_eve with no holiday ahead
        log(f"{event.key} -> {text}")
        announcer.say(text)
        announced += 1
    return announced


def main():
    if not acquire_single_instance():
        return          # another copy is live; the watchdog calls us often
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    config = load_config()
    holidays = load_holidays()
    keys = enabled_keys(config)
    log(f"MarketAlert started (pid {os.getpid()}) - {len(keys)} reminders enabled: "
        + ", ".join(sorted(keys)))

    announcer = Announcer(config)
    log(f"pre-rendered {announcer.prerender(keys, holidays, config)} phrases "
        f"with voice '{config['voice']}'")

    upcoming = ms.next_event(datetime.now(), keys, holidays)
    if upcoming:
        log(f"next: {upcoming[1].key} at {upcoming[0]:%Y-%m-%d %H:%M}")

    try:
        run(config, holidays, announcer)
    except BaseException:
        # pythonw has no console, so an unlogged crash is an invisible one.
        log("FATAL: " + " | ".join(traceback.format_exc().splitlines()[-4:]))
        raise
    finally:
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        log("MarketAlert stopped")


if __name__ == "__main__":
    if "--say" in sys.argv:          # manual check: speak a phrase and exit
        cfg = load_config()
        Announcer(cfg).say(sys.argv[sys.argv.index("--say") + 1])
    else:
        main()
