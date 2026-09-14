"""Tests for the announcement loop and the audio layer."""
import unittest
from datetime import date, datetime, timedelta

import marketalert as app
import speaker as spk

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


if __name__ == "__main__":
    unittest.main()
