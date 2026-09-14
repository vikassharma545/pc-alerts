# MarketAlert

Speaks NSE/BSE equity session events through the speakers. Runs hidden in the
background and starts automatically when you log in.

## Reminders

Enabled now (edit `config.json` to change):

| Time  | Announcement |
|-------|--------------|
| 09:15 | Market is now open *(+ "Today is Nifty/Sensex weekly/monthly expiry" when applicable)* |
| 15:15 | Continuous trading ended for F&O stocks. Closing auction session has started |
| 15:20 | Closing auction order collection window is now open |
| 15:30 | Non-F&O stocks have closed. Closing auction order collection is closed |
| 15:35 | Closing auction session has ended. Closing prices are set |
| 15:40 | Derivatives market is now closed |
| 17:00 | *(holiday eve only)* Which holiday is coming and when trading resumes |

Available but switched off — flip to `true` in `config.json` to enable:
`pre_open` (09:00), `pre_open_limit` (09:05), `pre_open_close` (09:10),
`post_close_start` (15:50), `market_closed` (16:00).

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
- `marketalert.log` — what was announced and when
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

## Known gaps

- **Muhurat trading** (Sunday 2026-11-08) is not scheduled — NSE publishes those
  timings by circular closer to the date. Add it manually once known.
- Holidays are a static list; update `holidays.txt` when NSE publishes 2027.

## Tests

```
python -m unittest discover -p "test_*.py"
```
