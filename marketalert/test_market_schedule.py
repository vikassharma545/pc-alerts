"""Tests for the NSE/BSE session schedule."""
import unittest
from datetime import date, datetime

from market_schedule import (EVENTS_BY_KEY, announcement, expiry_labels,
                             holiday_notice, is_trading_day, next_event,
                             parse_holidays)

# Real 2026 dates used below:
#   2026-09-11 Fri, 2026-09-12 Sat, 2026-09-14 Mon = Ganesh Chaturthi (holiday),
#   2026-09-15 Tue, 2026-09-29 Tue = last Tuesday of September.
HOLIDAYS = {date(2026, 9, 14), date(2026, 10, 2), date(2026, 12, 25)}
ALL = set(EVENTS_BY_KEY)


class ParseHolidaysTests(unittest.TestCase):
    def test_parses_dates_and_ignores_comments_and_junk(self):
        text = ("# header\n"
                "2026-01-26  # Republic Day\n"
                "\n"
                "not-a-date\n"
                "2026-12-25\n")
        self.assertEqual(set(parse_holidays(text)),
                         {date(2026, 1, 26), date(2026, 12, 25)})

    def test_captures_holiday_names_for_the_spoken_notice(self):
        parsed = parse_holidays("2026-01-26  # Republic Day")
        self.assertEqual(parsed[date(2026, 1, 26)], "Republic Day")


class TradingDayTests(unittest.TestCase):
    def test_weekday_is_a_trading_day(self):
        self.assertTrue(is_trading_day(date(2026, 9, 11), HOLIDAYS))

    def test_weekend_is_not(self):
        self.assertFalse(is_trading_day(date(2026, 9, 12), HOLIDAYS))
        self.assertFalse(is_trading_day(date(2026, 9, 13), HOLIDAYS))

    def test_holiday_is_not(self):
        self.assertFalse(is_trading_day(date(2026, 9, 14), HOLIDAYS))


class NextEventTests(unittest.TestCase):
    def test_finds_next_event_same_day(self):
        when, event = next_event(datetime(2026, 9, 11, 9, 16), ALL, HOLIDAYS)
        self.assertEqual(event.key, "cas_start")
        self.assertEqual(when, datetime(2026, 9, 11, 15, 15))

    def test_rolls_over_to_next_trading_day_after_close(self):
        # Friday evening -> skips Sat, Sun, and Monday's holiday -> Tuesday
        when, event = next_event(datetime(2026, 9, 11, 20, 0), ALL, HOLIDAYS)
        self.assertEqual(when.date(), date(2026, 9, 15))
        self.assertEqual(event.key, "pre_open")

    def test_respects_enabled_subset(self):
        when, event = next_event(datetime(2026, 9, 11, 8, 0),
                                 {"market_open", "fno_close"}, HOLIDAYS)
        self.assertEqual(event.key, "market_open")
        self.assertEqual(when, datetime(2026, 9, 11, 9, 15))

    def test_event_exactly_now_is_not_returned_again(self):
        when, event = next_event(datetime(2026, 9, 11, 9, 15), ALL, HOLIDAYS)
        self.assertNotEqual(when, datetime(2026, 9, 11, 9, 15))
        self.assertEqual(event.key, "cas_start")

    def test_no_enabled_events_returns_none(self):
        self.assertIsNone(next_event(datetime(2026, 9, 11, 9, 0), set(), HOLIDAYS))

    def test_events_come_in_chronological_order(self):
        cursor = datetime(2026, 9, 11, 0, 0)
        seen = []
        for _ in range(6):
            when, event = next_event(cursor, ALL, HOLIDAYS)
            seen.append(event.key)
            cursor = when
        self.assertEqual(seen, ["pre_open", "pre_open_limit", "pre_open_close",
                                "market_open", "cas_start", "cas_collect"])


class ExpiryTests(unittest.TestCase):
    def test_tuesday_is_nse_weekly_expiry(self):
        self.assertIn("Nifty weekly expiry",
                      expiry_labels(date(2026, 9, 15), HOLIDAYS))

    def test_thursday_is_bse_weekly_expiry(self):
        self.assertIn("Sensex weekly expiry",
                      expiry_labels(date(2026, 9, 17), HOLIDAYS))

    def test_ordinary_day_has_no_expiry(self):
        self.assertEqual(expiry_labels(date(2026, 9, 11), HOLIDAYS), [])

    def test_last_tuesday_is_monthly_not_weekly(self):
        labels = expiry_labels(date(2026, 9, 29), HOLIDAYS)
        self.assertIn("Nifty monthly expiry", labels)
        self.assertNotIn("Nifty weekly expiry", labels)

    def test_expiry_shifts_back_when_tuesday_is_a_holiday(self):
        # 2026-12-29 is a Tuesday; make it a holiday and expiry moves to Monday
        holidays = {date(2026, 12, 29), date(2026, 12, 25)}
        self.assertIn("Nifty monthly expiry",
                      expiry_labels(date(2026, 12, 28), holidays))
        self.assertEqual(expiry_labels(date(2026, 12, 29), holidays), [])

    def test_can_disable_one_exchange(self):
        labels = expiry_labels(date(2026, 9, 17), HOLIDAYS, nse=True, bse=False)
        self.assertEqual(labels, [])


class AnnouncementTests(unittest.TestCase):
    def test_plain_event_text_unchanged(self):
        text = announcement(EVENTS_BY_KEY["cas_start"], date(2026, 9, 11), HOLIDAYS)
        self.assertEqual(text, EVENTS_BY_KEY["cas_start"].text)

    def test_open_on_expiry_day_mentions_expiry(self):
        text = announcement(EVENTS_BY_KEY["market_open"], date(2026, 9, 15), HOLIDAYS)
        self.assertIn("Market is now open", text)
        self.assertIn("Nifty weekly expiry", text)

    def test_open_on_ordinary_day_has_no_expiry_note(self):
        text = announcement(EVENTS_BY_KEY["market_open"], date(2026, 9, 11), HOLIDAYS)
        self.assertNotIn("expiry", text)

    def test_cas_timings_match_the_2026_rules(self):
        self.assertEqual((EVENTS_BY_KEY["cas_start"].hour,
                          EVENTS_BY_KEY["cas_start"].minute), (15, 15))
        self.assertEqual((EVENTS_BY_KEY["cas_end"].hour,
                          EVENTS_BY_KEY["cas_end"].minute), (15, 35))
        self.assertEqual((EVENTS_BY_KEY["fno_close"].hour,
                          EVENTS_BY_KEY["fno_close"].minute), (15, 40))


class HolidayNoticeTests(unittest.TestCase):
    NAMED = {date(2026, 9, 14): "Ganesh Chaturthi",
             date(2026, 10, 2): "Mahatma Gandhi Jayanti"}

    def test_friday_before_a_monday_holiday_announces_it(self):
        text = holiday_notice(date(2026, 9, 11), self.NAMED)
        self.assertIn("Ganesh Chaturthi", text)
        self.assertIn("14 September", text)
        self.assertIn("Tuesday 15 September", text)   # when trading resumes

    def test_day_directly_before_holiday_says_tomorrow(self):
        # 2026-10-01 is a Thursday; 2026-10-02 is a holiday
        text = holiday_notice(date(2026, 10, 1), self.NAMED)
        self.assertIn("tomorrow", text)

    def test_ordinary_weekday_gets_no_notice(self):
        self.assertIsNone(holiday_notice(date(2026, 9, 9), self.NAMED))

    def test_plain_weekend_is_not_announced_as_a_holiday(self):
        # Friday 2026-09-04: only Sat/Sun follow, which is routine
        self.assertIsNone(holiday_notice(date(2026, 9, 4), self.NAMED))

    def test_announcement_returns_none_when_no_holiday_follows(self):
        text = announcement(EVENTS_BY_KEY["holiday_eve"], date(2026, 9, 9), self.NAMED)
        self.assertIsNone(text)

    def test_announcement_returns_notice_on_holiday_eve(self):
        text = announcement(EVENTS_BY_KEY["holiday_eve"], date(2026, 9, 11), self.NAMED)
        self.assertIn("Ganesh Chaturthi", text)

    def test_reminder_is_the_day_before_when_that_is_a_working_day(self):
        # Holiday Tue 20 Oct -> reminded Mon 19 Oct
        oct20 = {date(2026, 10, 20): "Dussehra"}
        self.assertIsNotNone(holiday_notice(date(2026, 10, 19), oct20))
        self.assertIsNone(holiday_notice(date(2026, 10, 18), oct20))   # Sunday
        self.assertIsNone(holiday_notice(date(2026, 10, 16), oct20))   # Friday

    def test_reminder_skips_back_to_friday_over_a_weekend(self):
        # Holiday Mon 14 Sept: the 13th is Sunday and 12th Saturday, so the
        # reminder must land on Friday the 11th.
        self.assertIsNone(holiday_notice(date(2026, 9, 13), self.NAMED))
        self.assertIsNone(holiday_notice(date(2026, 9, 12), self.NAMED))
        self.assertIsNotNone(holiday_notice(date(2026, 9, 11), self.NAMED))

    def test_reminder_day_is_always_a_trading_day(self):
        import market_schedule as sched
        from datetime import timedelta
        for holiday in (date(2026, 1, 26), date(2026, 9, 14), date(2026, 10, 20),
                        date(2026, 12, 25), date(2026, 4, 3)):
            named = {holiday: "Test Holiday"}
            day, found = holiday - timedelta(days=1), None
            for _ in range(7):
                if holiday_notice(day, named):
                    found = day
                    break
                day -= timedelta(days=1)
            self.assertIsNotNone(found, f"no reminder before {holiday}")
            self.assertTrue(sched.is_trading_day(found, named))

    def test_weekend_holiday_is_not_announced(self):
        # A holiday on a Saturday costs no trading day
        saturday = {date(2027, 1, 2): "Weekend Holiday"}
        self.assertIsNone(holiday_notice(date(2027, 1, 1), saturday))

    def test_holiday_eve_fires_at_five_pm(self):
        event = EVENTS_BY_KEY["holiday_eve"]
        self.assertEqual((event.hour, event.minute), (17, 0))

    def test_no_events_at_all_on_the_holiday_itself(self):
        self.assertFalse(is_trading_day(date(2026, 9, 14), self.NAMED))
        when, _ = next_event(datetime(2026, 9, 14, 0, 1), ALL, self.NAMED)
        self.assertEqual(when.date(), date(2026, 9, 15))


if __name__ == "__main__":
    unittest.main()
