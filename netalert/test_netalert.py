"""Tests for the NetAlert connection state machine and volume guard."""
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import netalert
from netalert import AlarmOutput, Monitor, VolumeGuard, next_delay, pick_output_device


class FakeCheck:
    """Returns scripted True/False results in order."""

    def __init__(self, results):
        self.results = list(results)

    def __call__(self):
        return self.results.pop(0)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.events = []

    def make(self, results, fails_to_offline=2):
        return Monitor(
            check=FakeCheck(results),
            on_down=lambda: self.events.append("down"),
            on_alarm=lambda: self.events.append("alarm"),
            on_up=lambda: self.events.append("up"),
            fails_to_offline=fails_to_offline,
        )

    def test_stays_quiet_while_online(self):
        m = self.make([True, True, True])
        for _ in range(3):
            m.tick()
        self.assertEqual(self.events, [])
        self.assertTrue(m.online)

    def test_single_blip_does_not_alarm(self):
        m = self.make([True, False, True, True])
        for _ in range(4):
            m.tick()
        self.assertEqual(self.events, [])
        self.assertTrue(m.online)

    def test_two_consecutive_failures_triggers_down(self):
        m = self.make([False, False])
        m.tick()
        self.assertTrue(m.online)
        m.tick()
        self.assertFalse(m.online)
        self.assertEqual(self.events, ["down"])

    def test_alarm_repeats_while_offline(self):
        m = self.make([False, False, False, False])
        for _ in range(4):
            m.tick()
        self.assertEqual(self.events, ["down", "alarm", "alarm"])

    def test_recovery_fires_up_once_then_quiet(self):
        m = self.make([False, False, True, True, True])
        for _ in range(5):
            m.tick()
        self.assertEqual(self.events, ["down", "up"])
        self.assertTrue(m.online)

    def test_new_outage_after_recovery_alarms_again(self):
        m = self.make([False, False, True, False, False])
        for _ in range(5):
            m.tick()
        self.assertEqual(self.events, ["down", "up", "down"])


class FakeEndpoint:
    def __init__(self, volume=0.14, mute=0):
        self.volume = volume
        self.mute = mute

    def GetMasterVolumeLevelScalar(self):
        return self.volume

    def GetMute(self):
        return self.mute

    def SetMasterVolumeLevelScalar(self, value, ctx):
        self.volume = value

    def SetMute(self, value, ctx):
        self.mute = value


class VolumeGuardTests(unittest.TestCase):
    def setUp(self):
        self.endpoint = FakeEndpoint(volume=0.14, mute=1)
        self.errors = []
        self.guard = VolumeGuard(
            get_endpoint=lambda: self.endpoint,
            log=self.errors.append,
        )

    def test_force_unmutes_and_maxes_volume(self):
        self.guard.force()
        self.assertEqual(self.endpoint.volume, 1.0)
        self.assertEqual(self.endpoint.mute, 0)

    def test_restore_returns_original_volume_and_mute(self):
        self.guard.force()
        self.guard.restore()
        self.assertEqual(self.endpoint.volume, 0.14)
        self.assertEqual(self.endpoint.mute, 1)

    def test_repeated_force_keeps_first_saved_state(self):
        self.guard.force()
        self.endpoint.volume = 0.5  # user fiddles mid-outage
        self.guard.force()
        self.guard.restore()
        self.assertEqual(self.endpoint.volume, 0.14)

    def test_restore_without_force_is_noop(self):
        self.guard.restore()
        self.assertEqual(self.endpoint.volume, 0.14)
        self.assertEqual(self.endpoint.mute, 1)

    def test_endpoint_failure_logs_instead_of_crashing(self):
        def boom():
            raise OSError("no audio device")

        guard = VolumeGuard(get_endpoint=boom, log=self.errors.append)
        guard.force()
        self.assertEqual(len(self.errors), 1)
        guard.restore()  # nothing saved: no-op, no extra error
        self.assertEqual(len(self.errors), 1)
        guard.saved = (0.5, 0)
        guard.restore()
        self.assertEqual(len(self.errors), 2)


def dev(index, name, hostapi="Windows WASAPI", outputs=2):
    return {"index": index, "name": name, "hostapi_name": hostapi,
            "max_output_channels": outputs}


class PickOutputDeviceTests(unittest.TestCase):
    TARGET = "Headphones (3- High Definition Audio Device)"

    def test_exact_match(self):
        devices = [dev(0, "Headphones (OnePlus Buds 3)"), dev(1, self.TARGET)]
        self.assertEqual(pick_output_device(devices, self.TARGET), 1)

    def test_truncated_mme_name_matches(self):
        # MME truncates device names to 31 characters
        devices = [dev(0, "Headphones (OnePlus Buds 3)", hostapi="MME"),
                   dev(1, "Headphones (3- High Definition ", hostapi="MME")]
        self.assertEqual(pick_output_device(devices, self.TARGET), 1)

    def test_prefers_wasapi_over_truncated_mme(self):
        devices = [dev(0, "Headphones (3- High Definition ", hostapi="MME"),
                   dev(1, self.TARGET, hostapi="Windows WASAPI")]
        self.assertEqual(pick_output_device(devices, self.TARGET), 1)

    def test_ignores_input_only_devices(self):
        devices = [dev(0, self.TARGET, outputs=0)]
        self.assertIsNone(pick_output_device(devices, self.TARGET))

    def test_no_match_returns_none(self):
        devices = [dev(0, "Headphones (OnePlus Buds 3)"),
                   dev(1, "Digital Output (3- High Definition Audio Device)")]
        self.assertIsNone(pick_output_device(devices, self.TARGET))


class NextDelayTests(unittest.TestCase):
    """Detection speed: a suspected outage must be re-checked fast."""

    def make(self, online, fails):
        m = Monitor(check=lambda: True, on_down=lambda: None,
                    on_alarm=lambda: None, on_up=lambda: None)
        m.online, m.fails = online, fails
        return m

    def test_steady_online_uses_normal_interval(self):
        self.assertEqual(next_delay(self.make(True, 0)), netalert.CHECK_INTERVAL)

    def test_suspected_outage_retries_quickly(self):
        d = next_delay(self.make(True, 1))
        self.assertEqual(d, netalert.RETRY_DELAY)
        self.assertLess(d, netalert.CHECK_INTERVAL)

    def test_offline_waits_alarm_repeat(self):
        self.assertEqual(next_delay(self.make(False, 5)), netalert.ALARM_REPEAT)

    def test_worst_case_detection_under_five_seconds(self):
        # first failed probe + retry gap + confirming probe
        worst = (netalert.CONNECT_TIMEOUT + netalert.RETRY_DELAY
                 + netalert.CONNECT_TIMEOUT)
        self.assertLess(worst, 5)


class AlarmOutputTests(unittest.TestCase):
    """The speaker may vanish or appear at any time; the alarm must still sound."""

    def setUp(self):
        self.played = []
        self.fell_back = []
        self.logs = []
        self.resolves = []
        self.tones = [(950, 250)]

    def build(self, resolved, play=None):
        """resolved: list of values returned by successive resolve() calls."""
        def resolve(refresh=False):
            self.resolves.append(refresh)
            return resolved[min(len(self.resolves) - 1, len(resolved) - 1)]

        return AlarmOutput(
            resolve=resolve,
            play=play or (lambda f, ms, dev: self.played.append((f, dev))),
            fallback=self.fell_back.append,
            log=self.logs.append,
        )

    def test_plays_on_resolved_device(self):
        out = self.build([12])
        self.assertTrue(out.play(self.tones))
        self.assertEqual(self.played, [(950, 12)])
        self.assertEqual(self.fell_back, [])

    def test_caches_device_across_alarms(self):
        out = self.build([12])
        out.play(self.tones)
        out.play(self.tones)
        self.assertEqual(len(self.resolves), 1)  # resolved once, then cached

    def test_device_absent_at_start_is_found_on_rescan(self):
        out = self.build([None, 7])  # missing at first, appears on refresh
        self.assertTrue(out.play(self.tones))
        self.assertEqual(self.played, [(950, 7)])
        self.assertEqual(self.resolves, [False, True])
        self.assertTrue(any("now on" in m for m in self.logs))

    def test_playback_failure_triggers_rescan_and_retry(self):
        attempts = []

        def flaky(freq, ms, dev):
            attempts.append(dev)
            if dev == 12:
                raise OSError("device gone")
            self.played.append((freq, dev))

        out = self.build([12, 7], play=flaky)
        self.assertTrue(out.play(self.tones))
        self.assertEqual(attempts, [12, 7])
        self.assertEqual(self.played, [(950, 7)])

    def test_uses_default_output_when_speaker_unreachable(self):
        # e.g. an RDP session hides the sound card: still make noise somewhere
        out = self.build([None])
        self.assertTrue(out.play(self.tones))
        self.assertEqual(self.played, [(950, None)])  # None = default output
        self.assertEqual(self.fell_back, [])

    def test_default_output_not_cached_as_the_speaker(self):
        out = self.build([None])
        out.play(self.tones)
        self.assertIsNone(out.device)  # keeps re-looking for the real speaker

    def test_beeps_only_when_every_output_fails(self):
        def always_fails(freq, ms, dev):
            raise OSError("no audio")

        out = self.build([12, 7], play=always_fails)
        self.assertFalse(out.play(self.tones))
        self.assertEqual(self.fell_back, [self.tones])


class ConfigTests(unittest.TestCase):
    """The device name must live in config, not code - other PCs differ."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.original = (netalert.CONFIG_FILE, netalert.ALARM_DEVICE, netalert.log)
        netalert.log = lambda msg: None      # keep tests out of the real log

    def tearDown(self):
        netalert.CONFIG_FILE, netalert.ALARM_DEVICE, netalert.log = self.original

    def write(self, text):
        netalert.CONFIG_FILE = os.path.join(self.dir, "config.json")
        with open(netalert.CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(text)

    def test_reads_the_device_name(self):
        self.write('{"alarm_device": "My Speakers"}')
        self.assertEqual(netalert.load_config(), "My Speakers")

    def test_missing_config_falls_back_to_default_output(self):
        netalert.CONFIG_FILE = os.path.join(self.dir, "absent.json")
        netalert.ALARM_DEVICE = ""
        self.assertEqual(netalert.load_config(), "")

    def test_broken_config_does_not_crash(self):
        self.write("{not json")
        netalert.ALARM_DEVICE = ""
        self.assertEqual(netalert.load_config(), "")

    def test_empty_device_matches_nothing(self):
        # otherwise an unset device name would match the first speaker found
        devices = [dev(0, "Some Speakers")]
        self.assertIsNone(pick_output_device(devices, ""))


class SingleInstanceLockTests(unittest.TestCase):
    """The watchdog relaunches often; only one monitor may ever run."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.lock = os.path.join(self.dir, "test.lock")

    def tearDown(self):
        netalert._LOCK = None

    def test_first_caller_gets_the_lock(self):
        self.assertTrue(netalert.acquire_single_instance(self.lock))

    def test_second_process_is_refused(self):
        self.assertTrue(netalert.acquire_single_instance(self.lock))
        code = (f"import netalert, sys; "
                f"sys.exit(0 if netalert.acquire_single_instance(r'{self.lock}') else 3)")
        result = subprocess.run([sys.executable, "-c", code],
                                cwd=os.path.dirname(os.path.abspath(netalert.__file__)),
                                capture_output=True)
        self.assertEqual(result.returncode, 3, "a second instance acquired the lock")

    def test_lock_is_released_when_the_holder_dies(self):
        code = (f"import netalert, time; "
                f"netalert.acquire_single_instance(r'{self.lock}'); time.sleep(30)")
        holder = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.abspath(netalert.__file__)))
        try:
            deadline = time.time() + 10
            while time.time() < deadline:
                if not netalert.acquire_single_instance(self.lock):
                    break              # holder has it
                netalert._LOCK.close()
                netalert._LOCK = None
                time.sleep(0.2)
            else:
                self.fail("child never took the lock")
        finally:
            holder.kill()
            holder.wait()
        # After a hard kill the OS must free it, so the watchdog can restart us
        deadline = time.time() + 10
        while time.time() < deadline:
            if netalert.acquire_single_instance(self.lock):
                return
            time.sleep(0.2)
        self.fail("lock was not released after the holder was killed")


class MonitorLoopTests(unittest.TestCase):
    """However the monitor stops, the user gets their volume back.

    force() raises the speaker to full and unmutes it for the duration of an
    outage. If the process is killed mid-outage - the installer force-stops
    it, or Windows kills it for memory, which has happened - the loop must
    still hand back the level the user had chosen.
    """

    def setUp(self):
        self.original_log = netalert.log
        netalert.log = lambda msg: None

    def tearDown(self):
        netalert.log = self.original_log

    def build(self, results):
        endpoint = FakeEndpoint(volume=0.14, mute=1)
        guard = VolumeGuard(get_endpoint=lambda: endpoint, log=lambda m: None)
        monitor = Monitor(
            check=FakeCheck(results),
            on_down=guard.force,
            on_alarm=guard.force,
            on_up=lambda: None,
            fails_to_offline=2,
        )
        return endpoint, guard, monitor

    def test_volume_is_restored_when_the_loop_stops(self):
        endpoint, guard, monitor = self.build([False] * 4)
        netalert.monitor_loop(monitor, guard, sleep=lambda s: None, stop_after=4)
        self.assertEqual(endpoint.volume, 0.14)
        self.assertEqual(endpoint.mute, 1)

    def test_volume_is_restored_when_the_loop_is_killed(self):
        endpoint, guard, monitor = self.build([False] * 10)

        def die(seconds):
            if endpoint.volume == 1.0:      # outage under way
                raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            netalert.monitor_loop(monitor, guard, sleep=die)
        self.assertEqual(endpoint.volume, 0.14)
        self.assertEqual(endpoint.mute, 1)

    def test_volume_is_forced_up_while_the_outage_lasts(self):
        endpoint, guard, monitor = self.build([False] * 4)
        levels = []
        netalert.monitor_loop(monitor, guard,
                              sleep=lambda s: levels.append(endpoint.volume),
                              stop_after=3)
        self.assertIn(1.0, levels, "alarm never forced the speaker audible")

    def test_a_failed_check_is_survived_not_fatal(self):
        calls = []

        def explodes():
            calls.append(1)
            raise OSError("network stack gone")

        endpoint = FakeEndpoint()
        guard = VolumeGuard(get_endpoint=lambda: endpoint, log=lambda m: None)
        monitor = Monitor(check=explodes, on_down=lambda: None,
                          on_alarm=lambda: None, on_up=lambda: None)
        netalert.monitor_loop(monitor, guard, sleep=lambda s: None, stop_after=3)
        self.assertEqual(len(calls), 3, "loop stopped at the first failed check")

    def test_nothing_is_restored_if_no_outage_happened(self):
        endpoint, guard, monitor = self.build([True] * 3)
        netalert.monitor_loop(monitor, guard, sleep=lambda s: None, stop_after=3)
        self.assertEqual(endpoint.volume, 0.14)
        self.assertEqual(endpoint.mute, 1)


class ArgumentTests(unittest.TestCase):
    """--hosts is the debug entry point and deliberately skips the lock.

    Only a well-formed --hosts invocation may do that: a malformed one used
    to fall through to the real host list while still skipping the lock and
    the pid file, quietly producing a second unregistered monitor.
    """

    def test_no_arguments_is_a_normal_run(self):
        hosts, test_mode = netalert.parse_args(["netalert.py"])
        self.assertIsNone(hosts)
        self.assertFalse(test_mode)

    def test_hosts_flag_parses_a_single_host(self):
        hosts, test_mode = netalert.parse_args(
            ["netalert.py", "--hosts", "10.255.255.1:53"])
        self.assertEqual(hosts, [("10.255.255.1", 53)])
        self.assertTrue(test_mode)

    def test_hosts_flag_parses_several_hosts(self):
        hosts, _ = netalert.parse_args(
            ["netalert.py", "--hosts", "1.1.1.1:53,8.8.8.8:53"])
        self.assertEqual(hosts, [("1.1.1.1", 53), ("8.8.8.8", 53)])

    def test_hosts_without_a_value_is_not_a_test_run(self):
        # otherwise it skips the lock AND monitors the real hosts: a second
        # invisible monitor that the watchdog cannot see
        hosts, test_mode = netalert.parse_args(["netalert.py", "--hosts"])
        self.assertIsNone(hosts)
        self.assertFalse(test_mode)

    def test_unparseable_host_is_not_a_test_run(self):
        hosts, test_mode = netalert.parse_args(["netalert.py", "--hosts", "junk"])
        self.assertIsNone(hosts)
        self.assertFalse(test_mode)


class InternetUpTests(unittest.TestCase):
    """The probe itself - previously only the state machine around it was tested."""

    def setUp(self):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]

    def tearDown(self):
        self.server.close()

    def test_reports_up_when_a_host_answers(self):
        self.assertTrue(netalert.internet_up([("127.0.0.1", self.port)]))

    def test_reports_down_when_nothing_answers(self):
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        self.assertFalse(netalert.internet_up([("127.0.0.1", port)]))

    def test_one_live_host_is_enough(self):
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        dead_port = dead.getsockname()[1]
        dead.close()
        self.assertTrue(netalert.internet_up(
            [("127.0.0.1", dead_port), ("127.0.0.1", self.port)]))

    def test_a_dead_host_does_not_add_its_timeout(self):
        # probes run concurrently: an unroutable host must not serialise
        started = time.monotonic()
        netalert.internet_up([("10.255.255.1", 53), ("127.0.0.1", self.port)])
        self.assertLess(time.monotonic() - started, netalert.CONNECT_TIMEOUT + 1)

    def test_every_probe_socket_is_closed(self):
        made = []
        real = socket.socket

        class Tracked(real):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                made.append(self)

        socket.socket = Tracked
        try:
            netalert.internet_up([("127.0.0.1", self.port),
                                  ("10.255.255.1", 53)])
        finally:
            socket.socket = real
        leaked = [s for s in made if s.fileno() != -1]
        for s in leaked:
            s.close()
        self.assertEqual(leaked, [], f"{len(leaked)} probe socket(s) left open")


class LogRotationTests(unittest.TestCase):
    """An always-on tool must not grow its log without bound."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.original = (netalert.LOG_FILE, netalert.LOG_MAX_BYTES)
        netalert.LOG_FILE = os.path.join(self.dir, "netalert.log")

    def tearDown(self):
        netalert.LOG_FILE, netalert.LOG_MAX_BYTES = self.original

    def test_writes_the_message(self):
        netalert.log("hello")
        with open(netalert.LOG_FILE, encoding="utf-8") as f:
            self.assertIn("hello", f.read())

    def test_rotates_once_the_log_grows_too_large(self):
        netalert.LOG_MAX_BYTES = 200
        for i in range(40):
            netalert.log(f"line {i}")
        self.assertTrue(os.path.exists(netalert.LOG_FILE + ".1"))
        self.assertLessEqual(os.path.getsize(netalert.LOG_FILE),
                             netalert.LOG_MAX_BYTES * 2)

    def test_rotation_keeps_the_most_recent_lines(self):
        netalert.LOG_MAX_BYTES = 200
        for i in range(40):
            netalert.log(f"line {i}")
        with open(netalert.LOG_FILE, encoding="utf-8") as f:
            self.assertIn("line 39", f.read())

    def test_small_log_is_not_rotated(self):
        netalert.LOG_MAX_BYTES = 100000
        netalert.log("hello")
        self.assertFalse(os.path.exists(netalert.LOG_FILE + ".1"))


if __name__ == "__main__":
    unittest.main()
