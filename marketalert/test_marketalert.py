"""Tests for the announcement loop and the audio layer."""
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta

import marketalert as app
import speaker as spk

_REAL_LOG = app.log

HOLIDAYS = {date(2026, 9, 14): "Ganesh Chaturthi"}
CONFIG = {"expiry_nse": True, "expiry_bse": True,
          "events": {"market_open": True, "fno_close": True}}


class FakeClock:
    """Clock that jumps forward whenever the code under test sleeps."""

    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class FakeAnnouncer:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)
        return True


class RunLoopTests(unittest.TestCase):
    def setUp(self):
        self.announcer = FakeAnnouncer()
        app.log = lambda msg: None      # keep tests off the real log file

    def run_from(self, start, stop_after=1, config=CONFIG):
        clock = FakeClock(start)
        app.run(config, HOLIDAYS, self.announcer,
                now=clock, sleep=clock.sleep, stop_after=stop_after)
        return clock

    def test_announces_market_open_at_the_right_time(self):
        clock = self.run_from(datetime(2026, 9, 11, 9, 14, 30))
        self.assertEqual(self.announcer.said, ["Market is now open."])
        self.assertGreaterEqual(clock.now, datetime(2026, 9, 11, 9, 15))

    def test_waits_rather_than_announcing_early(self):
        self.run_from(datetime(2026, 9, 11, 6, 0), stop_after=1)
        self.assertEqual(len(self.announcer.said), 1)

    def test_announces_events_in_order(self):
        self.run_from(datetime(2026, 9, 11, 9, 0), stop_after=2)
        self.assertEqual(self.announcer.said,
                         ["Market is now open.", "Derivatives market is now closed."])

    def test_skips_events_slept_through(self):
        # Starting well after the open: it must not shout a stale announcement
        self.run_from(datetime(2026, 9, 11, 12, 0), stop_after=1)
        self.assertEqual(self.announcer.said, ["Derivatives market is now closed."])

    def test_event_within_grace_window_still_fires(self):
        # Launched 30s after the open (e.g. PC finished booting): still announce
        self.run_from(datetime(2026, 9, 11, 9, 15, 30), stop_after=1)
        self.assertEqual(self.announcer.said, ["Market is now open."])

    def test_event_older_than_grace_is_not_announced_late(self):
        self.run_from(datetime(2026, 9, 11, 9, 20), stop_after=1)
        self.assertEqual(self.announcer.said, ["Derivatives market is now closed."])

    def test_expiry_day_open_mentions_expiry(self):
        self.run_from(datetime(2026, 9, 15, 9, 14), stop_after=1)
        self.assertIn("Nifty weekly expiry", self.announcer.said[0])

    def test_nothing_is_announced_on_a_holiday(self):
        clock = self.run_from(datetime(2026, 9, 14, 9, 0), stop_after=1)
        self.assertEqual(clock.now.date(), date(2026, 9, 15))  # jumped to Tuesday
        self.assertEqual(len(self.announcer.said), 1)

    def test_holiday_eve_speaks_only_before_a_holiday(self):
        config = {"expiry_nse": True, "expiry_bse": True,
                  "events": {"holiday_eve": True}}
        self.run_from(datetime(2026, 9, 11, 16, 59), stop_after=1, config=config)
        self.assertIn("Ganesh Chaturthi", self.announcer.said[0])

    def test_holiday_eve_silent_on_an_ordinary_day(self):
        # Tue 8 Sept has no holiday ahead, nor do the 9th and 10th; the first
        # thing it should ever say is on Fri 11th, the eve of the holiday.
        config = {"expiry_nse": True, "expiry_bse": True,
                  "events": {"holiday_eve": True}}
        clock = FakeClock(datetime(2026, 9, 8, 16, 59))
        app.run(config, HOLIDAYS, self.announcer,
                now=clock, sleep=clock.sleep, stop_after=1)
        self.assertEqual(clock.now.date(), date(2026, 9, 11))
        self.assertEqual(len(self.announcer.said), 1)

    def test_disabled_events_are_never_announced(self):
        config = {"expiry_nse": True, "expiry_bse": True,
                  "events": {"market_open": False, "fno_close": True}}
        self.run_from(datetime(2026, 9, 11, 9, 0), stop_after=1, config=config)
        self.assertEqual(self.announcer.said, ["Derivatives market is now closed."])


class RecordingAnnouncer(FakeAnnouncer):
    """Announcer that also records what was pre-rendered before each event."""

    def __init__(self):
        super().__init__()
        self.prepared = []

    def prepare(self, text):
        self.prepared.append(text)
        return True


class PrerenderTests(unittest.TestCase):
    """A phrase must never be synthesised at the moment it has to be spoken."""

    def setUp(self):
        app.log = lambda msg: None

    def test_upcoming_phrase_is_rendered_before_it_is_due(self):
        announcer = RecordingAnnouncer()
        clock = FakeClock(datetime(2026, 9, 11, 9, 0))
        app.run(CONFIG, HOLIDAYS, announcer, now=clock, sleep=clock.sleep,
                stop_after=1)
        self.assertIn("Market is now open.", announcer.prepared)
        # prepared strictly before it was spoken
        self.assertEqual(announcer.said, ["Market is now open."])

    def test_expiry_variant_is_prepared_not_just_the_plain_text(self):
        announcer = RecordingAnnouncer()
        clock = FakeClock(datetime(2026, 9, 15, 9, 0))   # Tuesday: Nifty expiry
        app.run(CONFIG, HOLIDAYS, announcer, now=clock, sleep=clock.sleep,
                stop_after=1)
        self.assertTrue(any("Nifty weekly expiry" in p for p in announcer.prepared),
                        f"expiry wording never pre-rendered: {announcer.prepared}")

    def test_holiday_notice_is_prepared_before_it_speaks(self):
        config = {"expiry_nse": True, "expiry_bse": True,
                  "events": {"holiday_eve": True}}
        announcer = RecordingAnnouncer()
        clock = FakeClock(datetime(2026, 9, 11, 16, 0))
        app.run(config, HOLIDAYS, announcer, now=clock, sleep=clock.sleep,
                stop_after=1)
        self.assertTrue(any("Ganesh Chaturthi" in p for p in announcer.prepared))

    def test_prerender_covers_expiry_wording_across_the_horizon(self):
        rendered = []

        class Fake:
            def prepare(self, text):
                rendered.append(text)
                return True

        app.Announcer.prerender(Fake(), {"market_open"}, HOLIDAYS,
                                {"expiry_nse": True, "expiry_bse": True},
                                horizon_days=30)
        joined = " | ".join(rendered)
        self.assertIn("Nifty weekly expiry", joined)
        self.assertIn("Sensex weekly expiry", joined)
        self.assertIn("Market is now open.", rendered)

    def test_prerender_deduplicates_repeated_phrases(self):
        rendered = []

        class Fake:
            def prepare(self, text):
                rendered.append(text)
                return True

        app.Announcer.prerender(Fake(), {"fno_close"}, HOLIDAYS,
                                {"expiry_nse": True, "expiry_bse": True},
                                horizon_days=30)
        self.assertEqual(len(rendered), 1, "same phrase rendered more than once")


class ConfigTests(unittest.TestCase):
    def test_enabled_keys_ignores_unknown_and_disabled(self):
        keys = app.enabled_keys({"events": {"market_open": True,
                                            "cas_end": False,
                                            "not_a_real_event": True}})
        self.assertEqual(keys, {"market_open"})

    def test_enabled_keys_handles_missing_section(self):
        self.assertEqual(app.enabled_keys({}), set())


class FakeEndpoint:
    def __init__(self, volume, mute=0):
        self.volume, self.mute = volume, mute

    def GetMasterVolumeLevelScalar(self):
        return self.volume

    def GetMute(self):
        return self.mute

    def SetMasterVolumeLevelScalar(self, value, ctx):
        self.volume = value

    def SetMute(self, value, ctx):
        self.mute = value


class VolumeFloorTests(unittest.TestCase):
    """Low system volume must not silence an announcement."""

    def floor_for(self, volume, mute=0, floor=0.7):
        endpoint = FakeEndpoint(volume, mute)
        return endpoint, spk.VolumeFloor(lambda: endpoint, floor=floor,
                                         log=lambda m: None)

    def test_raises_volume_when_too_low(self):
        endpoint, guard = self.floor_for(0.14)
        guard.raise_if_needed()
        self.assertAlmostEqual(endpoint.volume, 0.7)

    def test_unmutes_a_muted_speaker(self):
        endpoint, guard = self.floor_for(0.9, mute=1)
        guard.raise_if_needed()
        self.assertEqual(endpoint.mute, 0)

    def test_restores_the_users_level_afterwards(self):
        endpoint, guard = self.floor_for(0.14, mute=1)
        guard.raise_if_needed()
        guard.restore()
        self.assertAlmostEqual(endpoint.volume, 0.14)
        self.assertEqual(endpoint.mute, 1)

    def test_leaves_an_already_audible_volume_alone(self):
        endpoint, guard = self.floor_for(0.85)
        guard.raise_if_needed()
        guard.restore()
        self.assertAlmostEqual(endpoint.volume, 0.85)

    def test_never_lowers_a_loud_volume(self):
        endpoint, guard = self.floor_for(0.95, mute=1)
        guard.raise_if_needed()
        self.assertAlmostEqual(endpoint.volume, 0.95)

    def test_audio_failure_does_not_raise(self):
        def boom():
            raise OSError("no endpoint")
        guard = spk.VolumeFloor(boom, floor=0.7, log=lambda m: None)
        guard.raise_if_needed()
        guard.restore()


class SpeakerRoutingTests(unittest.TestCase):
    def setUp(self):
        self.played = []
        self.resolves = []

    def build(self, resolved, play=None):
        def resolve(refresh=False):
            self.resolves.append(refresh)
            return resolved[min(len(self.resolves) - 1, len(resolved) - 1)]
        return spk.Speaker(
            "Speakers", resolve=resolve,
            play=play or (lambda data, rate, dev: self.played.append(dev)),
            log=lambda m: None)

    def test_plays_on_the_configured_speaker(self):
        out = self.build([12])
        self.assertTrue(out.play([0.0], 48000))
        self.assertEqual(self.played, [12])

    def test_caches_the_device(self):
        out = self.build([12])
        out.play([0.0], 48000)
        out.play([0.0], 48000)
        self.assertEqual(len(self.resolves), 1)

    def test_rescans_when_speaker_missing(self):
        out = self.build([None, 7])
        self.assertTrue(out.play([0.0], 48000))
        self.assertEqual(self.played, [7])

    def test_falls_back_to_default_output(self):
        out = self.build([None])
        self.assertTrue(out.play([0.0], 48000))
        self.assertEqual(self.played, [None])


class AudioHelperTests(unittest.TestCase):
    def test_resample_changes_length_proportionally(self):
        import numpy as np
        data = np.zeros(1000, dtype=np.float32)
        self.assertEqual(len(spk.resample(data, 22050, 44100)), 2000)

    def test_resample_is_a_noop_at_the_same_rate(self):
        import numpy as np
        data = np.arange(10, dtype=np.float32)
        self.assertIs(spk.resample(data, 48000, 48000), data)

    def test_tone_has_the_expected_duration(self):
        self.assertEqual(len(spk.tone(440, 500, 48000)), 24000)

    def test_cache_path_is_stable_and_voice_specific(self):
        a = spk.cache_path("c", "hello", "Hazel")
        self.assertEqual(a, spk.cache_path("c", "hello", "Hazel"))
        self.assertNotEqual(a, spk.cache_path("c", "hello", "Zira"))

    def test_pick_output_device_prefers_wasapi_full_name(self):
        devices = [{"index": 0, "name": "Speakers (3- High Defini",
                    "hostapi_name": "MME", "max_output_channels": 2},
                   {"index": 1, "name": "Speakers (3- High Definition Audio Device)",
                    "hostapi_name": "Windows WASAPI", "max_output_channels": 2}]
        self.assertEqual(
            spk.pick_output_device(devices, "Speakers (3- High Definition Audio Device)"), 1)

    def test_pick_output_device_ignores_inputs(self):
        devices = [{"index": 0, "name": "Speakers", "hostapi_name": "MME",
                    "max_output_channels": 0}]
        self.assertIsNone(spk.pick_output_device(devices, "Speakers"))

    def test_blank_target_matches_nothing(self):
        devices = [{"index": 0, "name": "Speakers", "hostapi_name": "MME",
                    "max_output_channels": 2}]
        self.assertIsNone(spk.pick_output_device(devices, ""))


class SpeechScriptQuotingTests(unittest.TestCase):
    """Announcement text is data, not PowerShell code.

    Text reaches the synthesiser from holidays.txt, config.json and --say.
    An apostrophe in any of them used to end the PowerShell string literal,
    which both broke the announcement and let the rest of the value run as
    code.
    """

    CACHE = r"C:\voice_cache\phrase.wav"

    def test_apostrophe_in_text_is_escaped(self):
        script = spk._speech_script("New Year's Day", "", self.CACHE, 0)
        self.assertIn("$s.Speak('New Year''s Day')", script)

    def test_apostrophe_in_voice_is_escaped(self):
        script = spk._speech_script("hi", "Bob's Voice", self.CACHE, 0)
        self.assertIn("SelectVoice('Bob''s Voice')", script)

    def test_apostrophe_in_path_is_escaped(self):
        script = spk._speech_script("hi", "", r"C:\o'brien\voice.wav", 0)
        self.assertIn(r"SetOutputToWaveFile('C:\o''brien\voice.wav')", script)

    def test_injection_payload_stays_inside_the_string_literal(self):
        payload = r"x'); Remove-Item C:\important -Recurse; ('"
        script = spk._speech_script(payload, "", self.CACHE, 0)
        # every quote the payload contributes must be doubled, so no odd
        # number of quotes can terminate the literal early
        speak = script.split("$s.Speak(")[1]
        self.assertNotIn("');", speak.replace("'');", ""))

    def test_non_numeric_rate_is_rejected_not_interpolated(self):
        with self.assertRaises(ValueError):
            spk._speech_script("hi", "", self.CACHE, "3; rm -r C:")


class SpeechRenderingTests(unittest.TestCase):
    """End-to-end through the real synthesiser: quoting must actually work."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_renders_text_containing_an_apostrophe(self):
        path = os.path.join(self.dir, "apos.wav")
        spk.render_wav("Markets are closed for New Year's Day.", "", path, 0)
        data, rate = spk.load_wav(path)
        self.assertGreater(len(data), 0)

    def test_injected_command_does_not_run(self):
        marker = os.path.join(self.dir, "PWNED.txt")
        payload = ("hi'); [IO.File]::WriteAllText('"
                   + marker.replace("\\", "\\\\") + "','x'); ('")
        path = os.path.join(self.dir, "inj.wav")
        spk.render_wav(payload, "", path, 0)
        self.assertFalse(os.path.exists(marker),
                         "text from a config file executed as PowerShell")


class CachedWavTests(unittest.TestCase):
    """A damaged cache entry must be re-rendered, never trusted forever.

    wav_for() used to accept any existing file, so a render interrupted
    part-way left a stub that silenced that phrase permanently - the exact
    failure mode these tools exist to prevent.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.renders = []

    def fake_render(self, text, voice, path, rate_wpm=0):
        self.renders.append(text)
        import wave as w
        with w.open(path, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(22050)
            f.writeframes(bytes([1, 0]) * 100)
        return path

    def test_renders_on_a_cache_miss(self):
        path = spk.cached_wav(self.dir, "hello", "Hazel", render=self.fake_render)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.renders, ["hello"])

    def test_reuses_a_good_cache_entry(self):
        spk.cached_wav(self.dir, "hello", "Hazel", render=self.fake_render)
        spk.cached_wav(self.dir, "hello", "Hazel", render=self.fake_render)
        self.assertEqual(self.renders, ["hello"], "re-rendered a healthy entry")

    def test_empty_cache_entry_is_re_rendered(self):
        path = spk.cache_path(self.dir, "hello", "Hazel")
        open(path, "wb").close()                      # interrupted mid-render
        spk.cached_wav(self.dir, "hello", "Hazel", render=self.fake_render)
        self.assertEqual(self.renders, ["hello"])
        self.assertGreater(os.path.getsize(path), 0)

    def test_truncated_cache_entry_is_re_rendered(self):
        path = spk.cache_path(self.dir, "hello", "Hazel")
        with open(path, "wb") as f:
            f.write(b"RIFF" + bytes(2))                  # half a wav header
        spk.cached_wav(self.dir, "hello", "Hazel", render=self.fake_render)
        self.assertEqual(self.renders, ["hello"])
        data, _ = spk.load_wav(path)
        self.assertGreater(len(data), 0)

    def test_result_is_always_loadable(self):
        path = spk.cache_path(self.dir, "hello", "Hazel")
        open(path, "wb").close()
        data, rate = spk.load_wav(
            spk.cached_wav(self.dir, "hello", "Hazel", render=self.fake_render))
        self.assertEqual(rate, 22050)
        self.assertEqual(len(data), 100)


class LogRotationTests(unittest.TestCase):
    """An always-on tool must not grow its log without bound."""

    real_log = staticmethod(_REAL_LOG)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # other suites replace app.log wholesale, so save the real one
        self.original = (app.LOG_FILE, app.LOG_MAX_BYTES, app.log)
        app.log = type(self).real_log
        app.LOG_FILE = os.path.join(self.dir, "marketalert.log")

    def tearDown(self):
        app.LOG_FILE, app.LOG_MAX_BYTES, app.log = self.original

    def test_writes_the_message(self):
        app.log("hello")
        with open(app.LOG_FILE, encoding="utf-8") as f:
            self.assertIn("hello", f.read())

    def test_rotates_once_the_log_grows_too_large(self):
        app.LOG_MAX_BYTES = 200
        for i in range(40):
            app.log(f"line {i}")
        self.assertTrue(os.path.exists(app.LOG_FILE + ".1"))
        self.assertLessEqual(os.path.getsize(app.LOG_FILE),
                             app.LOG_MAX_BYTES * 2)

    def test_rotation_keeps_the_most_recent_lines(self):
        app.LOG_MAX_BYTES = 200
        for i in range(40):
            app.log(f"line {i}")
        with open(app.LOG_FILE, encoding="utf-8") as f:
            self.assertIn("line 39", f.read())

    def test_small_log_is_not_rotated(self):
        app.LOG_MAX_BYTES = 100000
        app.log("hello")
        self.assertFalse(os.path.exists(app.LOG_FILE + ".1"))


class ConfigValueTests(unittest.TestCase):
    """A broken value must not stop the alerts, just as a broken file does not.

    load_config() already survives a missing or unparseable config.json, but
    a single mistyped number inside a valid one used to raise while the
    Announcer was being built, killing the tool at startup.
    """

    def setUp(self):
        self.logged = []
        self.original = app.log
        app.log = self.logged.append

    def tearDown(self):
        app.log = self.original

    def test_number_is_read_normally(self):
        self.assertEqual(app.as_number(90, 50, name="floor"), 90.0)

    def test_numeric_string_is_accepted(self):
        self.assertEqual(app.as_number("90", 50, name="floor"), 90.0)

    def test_null_falls_back_to_the_default(self):
        self.assertEqual(app.as_number(None, 50, name="floor"), 50.0)

    def test_nonsense_falls_back_and_says_so(self):
        self.assertEqual(app.as_number("ninety", 50, name="floor"), 50.0)
        self.assertTrue(any("floor" in m for m in self.logged),
                        "a rejected setting must be reported")

    def test_announcer_survives_a_null_volume_floor(self):
        config = dict(app.DEFAULTS)
        config["volume_floor_percent"] = None
        app.Announcer(config)          # must not raise

    def test_announcer_survives_a_nonsense_gain(self):
        config = dict(app.DEFAULTS)
        config["voice_gain"] = "loud"
        app.Announcer(config)

    def test_announcer_survives_a_nonsense_speech_rate(self):
        config = dict(app.DEFAULTS)
        config["speech_rate"] = "fast"
        announcer = app.Announcer(config)
        self.assertEqual(announcer.rate, app.DEFAULTS["speech_rate"])

    def test_volume_floor_is_clamped_to_a_sane_range(self):
        config = dict(app.DEFAULTS)
        config["volume_floor_percent"] = 500
        self.assertLessEqual(app.Announcer(config).volume.floor, 1.0)
        config["volume_floor_percent"] = -20
        self.assertGreaterEqual(app.Announcer(config).volume.floor, 0.0)


if __name__ == "__main__":
    unittest.main()
