"""NSE/BSE equity session schedule: what to announce and when.

Timings reflect the rules in force from August/September 2026:
  - Closing Auction Session (CAS) replaced VWAP closing for F&O stocks
    (SEBI, effective 2026-08-03): continuous trading for those stocks ends
    at 15:15 and closing prices are set via auction by 15:35.
  - The derivatives segment now closes at 15:40 (was 15:30).
  - Pre-open order rules were revised effective 2026-09-07.
"""
from datetime import date, datetime, timedelta

TUESDAY = 1   # NSE weekly/monthly expiry (moved from Thursday on 2025-09-02)
THURSDAY = 3  # BSE Sensex weekly/monthly expiry


class Event:
    """One spoken announcement at a fixed time on a trading day."""

    def __init__(self, key, hour, minute, text):
        self.key = key
        self.hour = hour
        self.minute = minute
        self.text = text

    def at(self, day):
        return datetime(day.year, day.month, day.day, self.hour, self.minute)

    def __repr__(self):
        return f"<Event {self.key} {self.hour:02d}:{self.minute:02d}>"


# Wording is deliberately terse. These fire during live trading, so an
# announcement that takes several seconds is still playing when the next
# thing needs attention.
EVENTS = [
    Event("pre_open", 9, 0,
          "Pre-open started."),
    Event("pre_open_limit", 9, 5,
          "Market orders closed. Limit only."),
    Event("pre_open_close", 9, 10,
          "Pre-open closed. Price discovery."),
    Event("market_open", 9, 15,
          "Market open."),
    Event("cas_start", 15, 15,
          "F and O trading ended. Auction started."),
    Event("cas_collect", 15, 20,
          "Auction orders open."),
    Event("cash_close", 15, 30,
          "Cash market closed. Auction orders closed."),
    Event("cas_end", 15, 35,
          "Auction ended. Prices set."),
    Event("fno_close", 15, 40,
          "Derivatives closed."),
    Event("post_close_start", 15, 50,
          "Post close started."),
    Event("market_closed", 16, 0,
          "Post close ended. Market closed."),
    # Fires only on the last trading day before an exchange holiday; on any
    # other day announcement() returns None and the loop stays silent.
    Event("holiday_eve", 17, 0, ""),
]

EVENTS_BY_KEY = {e.key: e for e in EVENTS}


def parse_holidays(text):
    """Read the holidays file into {date: name}. Names come from the trailing
    comment and are used in the holiday-eve announcement."""
    days = {}
    for line in text.splitlines():
        stamp, _, comment = line.partition("#")
        stamp = stamp.strip()
        if not stamp:
            continue
        try:
            day = datetime.strptime(stamp, "%Y-%m-%d").date()
        except ValueError:
            continue  # ignore malformed lines rather than refusing to start
        days[day] = comment.strip()
    return days


def coverage_warning(holidays, today, min_days=60):
    """Warn when the hand-maintained holiday list is about to run out.

    Past the last listed holiday every real one looks like an ordinary
    trading day: the tool would announce an open market that is actually
    shut, and the holiday-eve reminder would never fire again. Being wrong
    while sounding confident is the failure this whole tool exists to avoid,
    so it says so out loud instead.
    """
    if not holidays:
        return ("holidays.txt lists no holidays: every day will be treated as "
                "a trading day. Add the exchange calendar.")
    last = max(holidays)
    left = (last - today).days
    if left < 0:
        return (f"holidays.txt expired on {last:%Y-%m-%d}: holidays after that "
                "date are being treated as trading days. Add the new calendar.")
    if left <= min_days:
        return (f"holidays.txt runs out on {last:%Y-%m-%d} ({left} days left). "
                "Add next year's exchange calendar.")
    return None


def next_trading_day(day, holidays, limit=15):
    day += timedelta(days=1)
    for _ in range(limit):
        if is_trading_day(day, holidays):
            return day
        day += timedelta(days=1)
    return day


def holiday_notice(day, holidays):
    """Announcement for the last working day before an exchange holiday.

    Returns None on any other day. The reminder lands on the day before the
    holiday when that is a working day, and skips back over the weekend
    otherwise - a Monday holiday is announced on the Friday before.
    """
    if not is_trading_day(day, holidays):
        return None          # the reminder belongs on the previous trading day

    closed, cursor = [], day + timedelta(days=1)
    for _ in range(15):
        if is_trading_day(cursor, holidays):
            break
        # A holiday landing on a weekend costs no trading day, so it is not
        # worth announcing.
        if cursor in holidays and cursor.weekday() < 5:
            closed.append(cursor)
        cursor += timedelta(days=1)
    if not closed:
        return None

    parts = []
    for d in closed:
        when = "tomorrow" if (d - day).days == 1 else d.strftime("%A")
        name = holidays.get(d) if isinstance(holidays, dict) else ""
        parts.append(f"{when}, {d.strftime('%d %B')}" + (f", {name}" if name else ""))
    return ("Closed " + ", and ".join(parts)
            + f". Resumes {cursor.strftime('%A %d %B')}.")


def is_trading_day(day, holidays):
    return day.weekday() < 5 and day not in holidays


def _shift_back_to_trading_day(day, holidays):
    """Expiry moves to the previous trading day when it lands on a holiday."""
    for _ in range(10):
        if is_trading_day(day, holidays):
            return day
        day -= timedelta(days=1)
    return day


def _last_weekday_of_month(day, weekday):
    last = date(day.year, day.month, 1) + timedelta(days=32)
    last = date(last.year, last.month, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _weekly_expiry_for(day, weekday, holidays):
    """The actual expiry date of the week containing `day`."""
    scheduled = day + timedelta(days=(weekday - day.weekday()))
    return _shift_back_to_trading_day(scheduled, holidays)


def expiry_labels(day, holidays, nse=True, bse=True):
    """Human labels for any expiries falling on `day` (may be empty)."""
    labels = []
    for enabled, weekday, index in ((nse, TUESDAY, "Nifty"), (bse, THURSDAY, "Sensex")):
        if not enabled:
            continue
        monthly = _shift_back_to_trading_day(
            _last_weekday_of_month(day, weekday), holidays)
        if day == monthly:
            labels.append(f"{index} monthly expiry")
        elif day == _weekly_expiry_for(day, weekday, holidays):
            labels.append(f"{index} weekly expiry")
    return labels


def announcement(event, day, holidays, expiry_nse=True, expiry_bse=True):
    """Spoken text for an event, or None when it has nothing to say today."""
    if event.key == "holiday_eve":
        return holiday_notice(day, holidays)
    text = event.text
    if event.key == "market_open":
        labels = expiry_labels(day, holidays, expiry_nse, expiry_bse)
        if labels:
            text += " " + " and ".join(labels) + "."
    return text


def next_event(after, enabled, holidays, horizon_days=30):
    """The first enabled event strictly after `after`, or None.

    `enabled` is a set of event keys. Non-trading days are skipped.
    """
    if not enabled:
        return None
    day = after.date()
    for _ in range(horizon_days):
        if not is_trading_day(day, holidays):
            day += timedelta(days=1)
            continue
        for event in EVENTS:
            if event.key not in enabled:
                continue
            when = event.at(day)
            if when > after:
                return when, event
        day += timedelta(days=1)
    return None
