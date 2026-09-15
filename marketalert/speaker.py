"""Voice output: renders announcements to WAV once, then plays them on the
chosen speaker regardless of which device Windows treats as default.

Rendering uses the System.Speech synthesiser via PowerShell (reliable and
already present on Windows); playback goes through sounddevice so a specific
output device can be targeted, with graceful fallbacks when it is unavailable.
"""
import hashlib
import os
import subprocess
import wave

CHIME = [(880, 120), (1175, 160)]   # short rising two-note attention chime


def _norm(name):
    return "".join((name or "").lower().split())


def pick_output_device(devices, target):
    """Index of the output device matching `target`, or None.

    Matches on substring either way because the MME host API truncates device
    names to 31 characters; prefers WASAPI entries for their full names.
    """
    t = _norm(target)
    if not t:
        return None
    best, best_score = None, -1
    for d in devices:
        if d.get("max_output_channels", 0) < 1:
            continue
        n = _norm(d.get("name"))
        if not n or (n not in t and t not in n):
            continue
        score = len(n) + (1000 if "wasapi" in _norm(d.get("hostapi_name")) else 0)
        if score > best_score:
            best, best_score = d["index"], score
    return best


def resolve_device(target, refresh=False):
    """Find the target speaker. refresh=True re-enumerates the hardware so
    devices connected after startup are picked up."""
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
    return pick_output_device(devices, target)


def resample(data, src_rate, dst_rate):
    """Linear resample - adequate for speech and tones, no extra dependency."""
    import numpy as np
    if src_rate == dst_rate or len(data) == 0:
        return data
    n = int(round(len(data) * dst_rate / src_rate))
    src_x = np.arange(len(data))
    dst_x = np.linspace(0, len(data) - 1, n)
    return np.interp(dst_x, src_x, data).astype(np.float32)


def boost(data, target_rms=0.30, peak=0.97, max_gain=12.0):
    """Make speech carry across a room.

    Synthesised speech has a low average level even when its peaks are high,
    so plain normalisation barely helps. This lifts the average level and uses
    a soft limiter (tanh) on the peaks, which raises loudness a lot more than
    peak normalisation without the harshness of hard clipping.
    """
    import numpy as np
    data = np.asarray(data, dtype=np.float32)
    if data.size == 0:
        return data
    rms = float(np.sqrt(np.mean(np.square(data))))
    if rms <= 0:
        return data
    gain = min(target_rms / rms, max_gain)
    out = np.tanh(data * gain)
    top = float(np.max(np.abs(out)))
    if top > 0:
        out *= peak / top
    return out.astype(np.float32)


def load_wav(path):
    """Read a PCM wav file as mono float32 in [-1, 1] plus its sample rate."""
    import numpy as np
    with wave.open(path, "rb") as w:
        channels, width, rate, frames = (w.getnchannels(), w.getsampwidth(),
                                         w.getframerate(), w.getnframes())
        raw = w.readframes(frames)
    if width != 2:
        raise ValueError(f"unsupported sample width: {width * 8} bit")
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def tone(freq, ms, rate):
    import numpy as np
    n = int(rate * ms / 1000)
    t = np.arange(n) / rate
    wave_ = 0.85 * np.sin(2 * np.pi * freq * t)
    fade = min(n // 10, 400)          # fade edges so tones do not click
    env = np.ones(n)
    env[:fade] = np.linspace(0, 1, fade)
    env[n - fade:] = np.linspace(1, 0, fade)
    return (wave_ * env).astype(np.float32)


def _ps_quote(value):
    """Escape a value for a PowerShell single-quoted string literal.

    Announcement text is data, not code: it comes from holidays.txt, from
    config.json and from --say. A single apostrophe ("New Year's Day") used
    to close the literal early, which killed the announcement and let the
    remainder of the value run as PowerShell.
    """
    return str(value).replace("'", "''")


def _speech_script(text, voice, path, rate_wpm=0):
    """The PowerShell one-liner that renders `text` to `path`."""
    return (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        + (f"try {{ $s.SelectVoice('{_ps_quote(voice)}') }} catch {{}}; "
           if voice else "")
        # Rate is numeric and unquoted, so it is coerced rather than escaped:
        # a non-numeric value in config.json raises here instead of reaching
        # the shell.
        + f"$s.Rate = {int(rate_wpm)}; "
        f"$s.SetOutputToWaveFile('{_ps_quote(path)}'); "
        f"$s.Speak('{_ps_quote(text)}'); "
        "$s.Dispose()"
    )


RENDER_TIMEOUT = 60      # a wedged synthesiser must not stall the whole loop


def render_wav(text, voice, path, rate_wpm=0):
    """Synthesise `text` to a wav file. Returns the path.

    Written to a temporary file and moved into place only once complete, so a
    render interrupted half-way (the installer force-stops this process, or
    the PC loses power) cannot leave a truncated file in the cache. A
    truncated file would otherwise be treated as a valid cache entry forever
    and silence that phrase for good.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = f"{path}.{os.getpid()}.part"
    script = _speech_script(text, voice, partial, rate_wpm)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=RENDER_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 or not os.path.exists(partial):
            raise RuntimeError(
                f"speech synthesis failed: {result.stderr.strip()[:200]}")
        if os.path.getsize(partial) == 0:
            raise RuntimeError("speech synthesis produced an empty file")
        os.replace(partial, path)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"speech synthesis timed out after {RENDER_TIMEOUT}s")
    finally:
        if os.path.exists(partial):
            try:
                os.remove(partial)
            except OSError:
                pass
    return path


def cache_path(folder, text, voice, rate_wpm=0):
    """Stable filename for a phrase, so re-rendering is skipped on restart.

    The rate is part of the key: without it, changing speech_rate reused the
    wav rendered at the old speed and the setting appeared to do nothing.
    """
    digest = hashlib.sha1(
        f"{voice}|{rate_wpm}|{text}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(folder, f"{digest}.wav")


def _is_playable(path):
    """True if `path` is a wav this program can actually read back."""
    try:
        if os.path.getsize(path) == 0:
            return False
        data, _ = load_wav(path)
        return len(data) > 0
    except Exception:
        return False


def cached_wav(folder, text, voice, rate_wpm=0, render=None):
    """Path to a usable cached rendering of `text`, synthesising if needed.

    Existence alone is not proof of a good cache entry: a file damaged by an
    interrupted render would otherwise be trusted forever and silence that
    phrase for good, so an unreadable entry is discarded and rebuilt.
    """
    render = render or render_wav
    path = cache_path(folder, text, voice, rate_wpm)
    if os.path.exists(path):
        if _is_playable(path):
            return path
        try:
            os.remove(path)
        except OSError:
            pass
    render(text, voice, path, rate_wpm)
    return path


class VolumeFloor:
    """Raises the speaker to a minimum audible level, then puts it back.

    Unlike an emergency alarm this never forces maximum volume: routine
    announcements should be audible without being startling.
    """

    def __init__(self, get_endpoint, floor=0.5, log=print):
        self.get_endpoint = get_endpoint
        self.floor = floor
        self.log = log
        self.saved = None

    def raise_if_needed(self):
        try:
            vol = self.get_endpoint()
            level, muted = vol.GetMasterVolumeLevelScalar(), vol.GetMute()
            if level >= self.floor and not muted:
                return                      # already audible: leave it alone
            if self.saved is None:
                self.saved = (level, muted)
            vol.SetMute(0, None)
            vol.SetMasterVolumeLevelScalar(max(level, self.floor), None)
        except Exception as e:
            self.log(f"volume adjust failed: {e}")

    def restore(self):
        if self.saved is None:
            return
        level, muted = self.saved
        self.saved = None
        try:
            vol = self.get_endpoint()
            vol.SetMasterVolumeLevelScalar(level, None)
            vol.SetMute(muted, None)
        except Exception as e:
            self.log(f"volume restore failed: {e}")


class Speaker:
    """Plays audio on the configured speaker, tolerating device changes."""

    def __init__(self, target, resolve=None, play=None, log=print):
        self.target = target
        self.resolve = resolve or (lambda refresh=False: resolve_device(self.target, refresh))
        self._play = play or self._play_array
        self.log = log
        self.device = None

    def _play_array(self, data, rate, device):
        import sounddevice as sd
        info = sd.query_devices(kind="output") if device is None else sd.query_devices(device)
        dst_rate = int(info["default_samplerate"])
        sd.play(resample(data, rate, dst_rate), dst_rate, device=device, blocking=True)

    def _attempt(self, data, rate, device):
        try:
            self._play(data, rate, device)
            return True
        except Exception as e:
            self.log(f"playback on device {device} failed: {e}")
            return False

    def play(self, data, rate):
        """Try the configured speaker, re-scan hardware, then the default output."""
        if self.device is None:
            self.device = self.resolve(refresh=False)
        if self.device is not None and self._attempt(data, rate, self.device):
            return True

        self.device = None
        found = self.resolve(refresh=True)
        if found is not None:
            self.log(f"speaker output now on device {found}")
            if self._attempt(data, rate, found):
                self.device = found
                return True

        # Configured speaker unreachable (unplugged, or hidden inside an RDP
        # session): being heard somewhere beats staying silent.
        return self._attempt(data, rate, None)
