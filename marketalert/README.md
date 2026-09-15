# MarketAlert

Speaks NSE/BSE equity session events through the speakers. Runs hidden in the
background and starts automatically when you log in.

## Reminders

Enabled now (edit `config.json` to change):

| Time  | Announcement |
|-------|--------------|
| 09:15 | "Market open." *(+ "Nifty/Sensex weekly/monthly expiry" when applicable)* |
| 15:15 | "F and O trading ended. Auction started." |
| 15:20 | "Auction orders open." |
| 15:30 | "Cash market closed. Auction orders closed." |
| 15:35 | "Auction ended. Prices set." |
| 15:40 | "Derivatives closed." |
| 17:00 | *(holiday eve only)* "Closed tomorrow, 02 October, Mahatma Gandhi Jayanti. Resumes Monday 05 October." |

Wording is deliberately terse — these fire during live trading, so a long
sentence is still playing when the next thing needs attention. Edit the
`text` of any event in `market_schedule.py` to reword it.

Available but switched off — flip to `true` in `config.json` to enable:
`pre_open` (09:00, "Pre-open started."), `pre_open_limit` (09:05, "Market
orders closed. Limit only."), `pre_open_close` (09:10, "Pre-open closed.
Price discovery."), `post_close_start` (15:50, "Post close started."),
`market_closed` (16:00, "Post close ended. Market closed.").

Nothing is announced on weekends or exchange holidays.

**Holiday reminder rule:** it speaks at 17:00 on the **previous trading day**.
That is the day before the holiday when the day before is a working day (holiday
Tue 20 Oct -> reminder Mon 19 Oct), and skips back over the weekend otherwise
(holiday Mon 14 Sep -> reminder Fri 11 Sep, because the 13th is a Sunday and the
12th a Saturday). Consecutive holidays are announced together, and a holiday
falling on a weekend is not announced since no trading day is lost.

## Timings source

Reflects the rules in force as of September 2026:

- **CAS** (Closing Auction Session) replaced VWAP closing for F&O stocks from
  2026-08-03: those stocks stop continuous trading at 15:15 and closing prices
  are set by auction by 15:35.
- The **derivatives segment closes at 15:40** (extended from 15:30).
- **Expiry:** NSE Nifty on Tuesday, BSE Sensex on Thursday (weekly), last
  Tuesday / last Thursday for monthly. Shifts to the previous trading day when
  it lands on a holiday.

## Controls

- `start_marketalert.vbs` — start it silently (also what runs at login)
- `stop_marketalert.bat` — stop it
- `marketalert.log` — what was announced and when; rolls over at 512 KB,
  keeping one previous generation as `marketalert.log.1`
- `holidays.txt` — exchange holidays, one per line; the trailing comment is the
  name spoken in the holiday reminder
- `config.json` — voice, which reminders are on, loudness

## Audio behaviour

- Plays on the speaker named in `config.json` regardless of which device
  Windows treats as default, so connected earbuds do not steal the
  announcement. If that speaker is unavailable it re-scans the hardware, then
  falls back to the default output rather than staying silent.
- Low system volume cannot silence it: volume is raised to at least
  `volume_floor_percent` (90) and unmuted for the announcement, then put back
  exactly as it was. Speech is also loudness-processed (+7.6 dB) so it carries.
- Raise `voice_gain` above 1.0 if you want it louder still.
- `speech_rate` (default `2`) sets the speaking speed on the System.Speech
  scale of -10 to 10, where 0 is normal. It is part of the cache key, so
  changing it re-renders every phrase on the next start.

## Known gaps

- **Muhurat trading** (Sunday 2026-11-08) is not scheduled — NSE publishes those
  timings by circular closer to the date. Add it manually once known.
- Holidays are a static list; update `holidays.txt` when NSE publishes 2027.
  Once it is within 60 days of running out, every startup logs a warning —
  past the last listed date each real holiday would otherwise look like an
  ordinary trading day and be announced as an open market.
- A mistyped number in `config.json` is reported in the log and replaced with
  its default, rather than stopping the tool.

## Tests

```
python -m unittest discover -p "test_*.py"
```
