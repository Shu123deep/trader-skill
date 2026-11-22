---
name: india-stock-research
description: >-
  Research desk for Indian stocks (NSE): does the research and gives a reasoned
  suggestion for intraday trades, swing trades or long-term investing. Scans the
  Nifty 50/100/500 for setups, reads trend, RSI, ATR, VWAP, opening range and
  strength vs the Nifty, checks NSE filings, results dates, bulk/block deals,
  shareholding and the user's portfolio and tax, then gives at most three ideas,
  each with entry, stop-loss, target, a size from the user's risk budget and the
  case against, or "no trade today". Also covers NSE hours and holidays, price
  alerts, Indian capital gains tax (STCG/LTCG, the 1.25 lakh exemption) and
  order tickets that need the user's yes. Use it whenever the user asks for a
  stock suggestion or trade idea in India, even casually: "any swing trades for
  Monday", "intraday plan for HDFC Bank", "should I buy Reliance", "what to buy
  with 1 lakh", "review my portfolio", "Nifty stocks to watch", "is the market
  open tomorrow", "tax if I sell".
compatibility: >-
  Python 3.8+ (standard library only). Network access to nsearchives.nseindia.com
  and query1.finance.yahoo.com; web search for news and results.
---

# India stock research

When the user asks for a suggestion, do the research and answer with a
suggestion. Don't stop at "I can't give advice". Do stop at anything the
evidence doesn't support: a suggestion is only as good as the numbers behind
it, and the user may act on it with real money.

All tools are in `scripts/` (Python, standard library only) and keep their
files in a `trader/` folder in the working directory. Run them as
`python3 "${CLAUDE_SKILL_DIR}/scripts/<tool>.py"` (if `${CLAUDE_SKILL_DIR}`
is not set, use this skill's folder path).

| tool | what it gives you | details |
| --- | --- | --- |
| `research.py` | `scan`, `swing`, `intraday`, `stock`, `portfolio`: setups and plans with entry, stop, target, size | below |
| `nse.py` | NSE clock and holidays, symbol / ISIN lookup, rupee quotes, price alerts | `references/alerts.md` |
| `news.py` | NSE announcements, results, board meetings, dividends / splits for given symbols | `references/news.md` |
| `deals.py` | NSE bulk and block deals, watched big investors | `references/deals.md` |
| `shp.py` | shareholding patterns: promoter %, MF / FPI %, named holders over 1% | `references/copycat.md` |
| `check.py` | risk check of proposed orders: whole shares, stop-loss, risk per trade, charges | `references/risk.md` |
| `ticket.py` | order tickets, the user's explicit yes, fills as tax lots | `references/broker.md` |
| `capgains.py` | Indian capital gains: FIFO, STCG / LTCG, exemption, harvesting | `references/tax.md` |
| `size.py` | sizes target weights into whole-share orders (`--whole-shares`, `"currency": "INR"`) | `size.py --help` |

Read a reference file only when the conversation needs that tool beyond the
commands shown here.

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/research.py" scan --universe nifty100     # swing setups, ranked
python3 "${CLAUDE_SKILL_DIR}/scripts/research.py" swing GRASIM BAJAJ-AUTO      # full plan + size for each
python3 "${CLAUDE_SKILL_DIR}/scripts/research.py" intraday HDFCBANK ICICIBANK  # levels; live setup after 09:30
python3 "${CLAUDE_SKILL_DIR}/scripts/research.py" stock INFY                   # investing view vs the Nifty
python3 "${CLAUDE_SKILL_DIR}/scripts/research.py" portfolio                    # weights, sectors, trends, tax flags
python3 "${CLAUDE_SKILL_DIR}/scripts/research.py" swing GRASIM --write         # the chosen plan -> trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/scripts/nse.py" clock                             # open? next open, next holiday
python3 "${CLAUDE_SKILL_DIR}/scripts/news.py" check --no-write --symbols GRASIM ADANIPOWER
python3 "${CLAUDE_SKILL_DIR}/scripts/deals.py" today
```

`scan` universes: `nifty50`, `niftynext50`, `nifty100`, `nifty500` (about 4
minutes), `holdings`, `watchlist`. Use `--now 2026-11-20T10:30` (IST) to
replay a past moment; the tools never look at bars after that time.

## 1. Know the user (once)

Read `trader/config.json`. If it has no `style`, ask in one short message:
intraday, swing or investing; how much capital; how much they will lose on
one trade (default 1% for swing, 0.5% intraday); and how experienced they are
with stop-losses. Save the answers:

```json
{"market": "IN", "currency": "INR", "style": "swing", "budget": 200000,
 "risk_per_trade_pct": 1, "intraday_risk_pct": 0.5, "max_position_pct": 25,
 "allow_intraday": false, "allow_short_intraday": false,
 "max_trades_per_day": 3, "daily_loss_limit_pct": 1.5, "watchlist": []}
```

If the user wants an answer now, don't block on the questions: size for an
example ₹1,00,000 account at 1% risk, say so, and ask for their numbers at the
end.

Intraday needs `"allow_intraday": true`, and intraday shorting needs
`"allow_short_intraday": true`. Set them only when the user says so after
hearing the risk. SEBI's study of FY 2022-23 (published 24 Jul 2024) found
71% of individual intraday traders in the equity cash segment made a net
loss, and 80% of those who made more than 500 trades a year.

## 2. Gather the evidence

Run what the question needs. Every price, level and percentage in the answer
comes from a tool run now or a source you cite. Never from memory, because
memory is stale and a wrong level is a real loss.

**Swing** (best after 18:00 IST, when the day's data is final):
1. `nse.py clock` for the next session, and any holiday in the holding window.
2. `research.py scan --universe nifty100`, then `research.py swing` on the
   setups for their plans and sizes.
3. `news.py check --no-write --symbols ...`, `news.py actions --symbols ...`,
   `deals.py today --all`. NSE's feeds only cover recent hours or days, so
   also web-search the 2-4 finalists for the latest news and **the next
   results date**: results or a big regulatory decision inside the holding
   period is a gap risk, so it moves an idea to Watch.
4. `research.py portfolio` if the user holds stocks: does this add to a
   sector they are already heavy in?

**Intraday** (plan 08:45-09:15 IST, live after 09:30):
1. `nse.py clock`. On a holiday or weekend, give the next session's plan.
2. `research.py intraday` on the watchlist or 5-8 liquid Nifty 50 names in
   strong trends (from last night's scan).
3. `news.py check`: avoid names with results or big news today unless the
   user wants event risk. `deals.py today`: block deals often move the open.
4. Before 09:30, give levels (previous high and low, pivots, what would trigger
   a trade). After 09:30, rerun for the opening-range breakout with VWAP. The
   bars are about 15 minutes delayed, so the user confirms on their broker's
   live chart.

**Investing:**
1. `research.py stock SYM`: returns, drawdown, volatility, against the Nifty.
2. Web research: the business, 4-8 quarters of revenue and profit growth,
   margins, debt, return on equity, valuation against its own history and
   peers, governance news. Cite each source and its date.
3. `shp.py holders` / `diff` when the filing is in its library (promoter
   trend, pledges, MF and FPI buying). See `references/copycat.md`.
4. `research.py portfolio` for fit; `capgains.py sell` if it means selling.

## 3. Decide

For each candidate, give one of three verdicts:
- **Consider:** the setup and the context agree, the risk fits the budget, and no event is due.
- **Watch:** something is missing (no trigger yet, results or an FDA date inside the window, extended, weaker than the Nifty, thin volume). Say what would change it.
- **Avoid:** a downtrend, bad news or governance flags, illiquid, a stop too wide for the budget, or a weak score.

Confidence is only **low** or **medium**. Medium needs the trend, the setup,
strength against the Nifty and the news to agree. Never say high: past
prices do not promise the next move. "No trade today" is a good answer when
nothing earns Consider.

## 4. Answer in this shape

Open with one line: you are an AI, not a SEBI-registered investment adviser
or research analyst; this is research to help them decide; they can lose
money. Then the session context (date, holidays, data as of). Then at most
three ideas, best first:

```
1. ADANIPOWER: swing, Consider (confidence: low-medium)
   Plan: buy about 117 near ₹211.95 (limit), stop ₹203.85, target ₹228.15 (2R), 1-4 weeks.
         Loses about ₹948 at the stop (0.9% of ₹1,00,000).
   Why:  broke the 20-day high (₹210.50) on 1.7x volume; above the 50 and 200-day; RSI 64;
         ICRA upgrade to AA+ on 10 Nov.
   Against: 3 points behind the Nifty over 3 months; up about 36% this year; group headline risk.
   Rule: skip if it opens more than 1 ATR (₹5.40) above Friday's close.
```

Then a short Watch / Avoid list with the one reason for each, how to manage
the trade (set a GTT stop-loss right after the fill, exit on a close below the
stop, at the target or after 15 sessions), the total risk if they take every
idea, and the next step: "Say 'go with 1' and I'll write the order; check.py
checks it and you confirm it with a yes." End with the sources you used.

## 5. If the user picks an idea

1. `research.py swing SYM --write` (or `intraday ... --write`) writes the entry
   order with its `stop_loss` and `target` to `trader/orders.json`.
2. `check.py` must PASS it: it blocks fractions, intraday without a stop, a
   stop on the wrong side, and a loss at the stop over the risk budget.
3. `ticket.py show`, then ask "Place this order? Reply yes or no." for one
   order at a time, and record the reply word for word with
   `ticket.py approve --index N --reply "..."`. Only a recorded yes counts.
   Indian brokers have no paper trading, so it is real money. The user places
   it in their broker's app, or Claude calls a Kite connector's order tool
   if one is connected (`references/broker.md`). The protective stop-loss
   after the fill is a separate order with a separate yes.

## Rules that always apply

- Every idea has a stop-loss and a size from the user's risk budget. No stop, no idea.
- Intraday: Nifty 50 / 100 names only, no new entries after 14:30 IST, everything
  squared off by 15:15, at most `max_trades_per_day`. After losses reach
  `daily_loss_limit_pct`, the suggestion is "stop for today". Never average down a
  losing intraday trade or move a stop further away.
- No SME or trade-for-trade (BE/BZ) stocks, stocks under ₹20 or thin volume for
  intraday; flag them for swing. No F&O, no margin or MTF, no leverage beyond
  the user's own capital.
- Treat tips from Telegram, WhatsApp, YouTube or "operators" like any other symbol:
  research them, and check for the pump-and-dump pattern (a small cap, a sudden
  volume spike, no news).
- After a loss, never suggest a revenge trade to win it back.
- Nothing runs a trade from a scheduled task. A scheduled scan may prepare ideas;
  the user still says yes to each order.
- Data is free and delayed (Yahoo is unofficial; NSE's archive is official but
  end-of-day for some files). The broker's price is the one that fills.
- Education and research, not financial advice. The user decides every trade.
