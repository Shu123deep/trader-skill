---
name: trader-in-alerts
description: >-
  India Price Alerts and NSE Clock. Rings when an NSE stock hits a price
  target, a stop, a percent move or the opening bell, each ringing once so you
  are not spammed. Knows NSE hours (09:15 to 15:30 IST, pre-open 09:00) and the
  2026 NSE trading holidays from the official circular, looks up NSE symbols,
  company names and ISINs, fetches free delayed rupee quotes (Yahoo .NS / .BO)
  or NSE's official end-of-day bhavcopy with delivery percentage, and writes
  trader/prices.json for the other skills. Use when the user says "alert me
  when Reliance hits", "set a stop on HDFC Bank", "is the market open today",
  "is tomorrow a market holiday", "what is the NSE symbol for", "get me
  prices", or to refresh prices before sizing, risk or tax for Indian stocks.
argument-hint: "[symbol] [price]"
---

# trader-in-alerts

The Indian edition of trader-alerts. It watches NSE prices so the user does
not have to. An alert is information, never an order: when a stop rings,
nothing is sold. If the user wants to act, the normal path runs (size, check
with trader-in-risk, their yes in trader-in-broker).

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/nse.py" clock                            # NSE open or closed, next open, next holiday
python3 "${CLAUDE_SKILL_DIR}/nse.py" symbol reliance industries       # symbol, series, ISIN, name
python3 "${CLAUDE_SKILL_DIR}/nse.py" quote RELIANCE TCS --write       # -> trader/prices.json (INR)
python3 "${CLAUDE_SKILL_DIR}/nse.py" quote --holdings --write         # portfolio + targets symbols
python3 "${CLAUDE_SKILL_DIR}/nse.py" quote INFY --source bhavcopy     # NSE's official close + delivery %
python3 "${CLAUDE_SKILL_DIR}/nse.py" add --symbol RELIANCE --above 1600
python3 "${CLAUDE_SKILL_DIR}/nse.py" add --symbol HDFCBANK --stop 800 --note "my stop"
python3 "${CLAUDE_SKILL_DIR}/nse.py" add --symbol TCS --pct 3
python3 "${CLAUDE_SKILL_DIR}/nse.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/nse.py" check                            # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/nse.py" list
python3 "${CLAUDE_SKILL_DIR}/nse.py" reset reliance-above-1600
```

## Symbols

Use NSE trading symbols: `RELIANCE`, `HDFCBANK`, `M&M`, `BAJAJ-AUTO`. When
the user names a company ("Mahindra", "Reliance"), run `symbol` first and
confirm the right one: several companies share words (Reliance Industries vs
Reliance Chemotex). For a stock listed only on BSE, use Yahoo's BSE code with
`.BO`, for example `500325.BO`. The bhavcopy source covers NSE only.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | latest quotes in rupees, merged in, with `"currency": "INR"` |
| `trader/cache/EQUITY_L.csv` | NSE's list of equity symbols, refreshed weekly |
| `trader/config.json` | optional `"holidays"` for years the tool does not know yet |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` | once per day at most |
| `opening_bell` | the NSE is open | once per trading day |

## Quotes

- `yahoo` (default): `query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS`,
  an unofficial free JSON endpoint, intraday, about 15 minutes delayed. The
  tool refuses a quote that is not in INR, so a US ticker cannot slip in.
- `bhavcopy`: NSE's own end-of-day file
  (`nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv`),
  every NSE stock in one download, with the delivery percentage. It is
  published in the evening, usually after 18:00 IST. **On a holiday NSE
  serves the previous trading day's file under the new date**, so the tool
  reads the date inside the file and never mistakes old prices for new ones.
- Free quotes are delayed. Fine for alerts and for checking sizes, not for
  timing an order. The broker's price is the one that fills.
- NSE's archive server silently stalls requests whose User-Agent contains a
  URL, so the tool identifies itself plainly as `trader-skill-india/1.0`.

## The NSE clock

Normal market 09:15 to 15:30 IST, pre-open 09:00 to 09:08, closed on weekends
and on the 2026 trading holidays in NSE circular NSE/FAOP/71777 (12 Dec 2025):
Jan 26, Mar 3, Mar 26, Mar 31, Apr 3, Apr 14, May 1, May 28, Jun 26, Sep 14,
Oct 2, Oct 20, Nov 10, Nov 24, Dec 25, plus Jan 15 (Maharashtra municipal
elections, NSE/FAOP/72262). India has no daylight saving time.

NSE publishes the next year's list each December. Until the tool is updated,
`clock` says the year is unknown; add the dates from NSE's circular to
`trader/config.json` as `{"holidays": {"2027-01-26": "Republic Day"}}`.
Muhurat trading (the special Diwali evening session) is announced separately
each year and is not modelled.

## Steps

1. Ask what to watch, in the user's words ("tell me if Infosys falls under
   ₹1,000"), find the symbol, and add the rule. Confirm it back: symbol,
   type, level in rupees.
2. Run `check` now, and on a schedule if the user wants (every 15 minutes in
   market hours is plenty). Each run fetches only the symbols in the rules
   and rings each alert once.
3. Tell the user what rang in one line each. If they want to act, send them
   to sizing and `/trader-in-risk`. Never act for them.

## Example

`add --symbol RELIANCE --above 1000` and `add --symbol TCS --pct 1`. On
Friday 20 Nov 2026 Reliance closed at ₹1,377.50 and TCS rose 1.43% to
₹2,376.20, so a `check` on Monday morning rings both once, and a second
`check` five minutes later prints "Nothing hit". `clock` on Monday evening
reports that Tuesday 24 Nov is an NSE holiday (Guru Nanak Jayanti).

## What it never does

It never places, cancels or changes an order, never claims a quote is real
time, and never predicts where a price is going.

## Safety

- Education and tooling, not financial advice. An alert is a fact about a price, not a signal to trade.
- Nothing in this pack places an order except trader-in-broker (or trader-broker), and only after
  trader-in-risk passed that exact order today and the user typed an explicit yes for that one order.
- No F&O, margin, intraday (MIS) or short selling unless the config allows it and the user confirms.
- Broker logins and API keys stay with the broker or its connector, never in the chat.
