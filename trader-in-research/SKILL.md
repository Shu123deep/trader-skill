---
name: trader-in-research
description: >-
  India Research Desk: does the research and gives a reasoned suggestion for
  Indian stocks, for intraday trading, swing trading or investing. Reads
  price history (trend, RSI, ATR, breakouts, VWAP, opening range, pivots,
  strength against the Nifty), scans the Nifty 50, 100 or 500 for setups,
  checks NSE filings, bulk and block deals, shareholding changes, the
  portfolio's sector concentration and tax position, adds web research on
  results and news, and turns it into at most three ideas, each with an
  entry, stop-loss, target, position size from the user's own risk budget,
  charges, the case against, and a confidence level. "No trade today" is a
  valid answer. Use whenever the user asks "suggest a stock", "what should I
  buy", "any intraday trade today", "swing trade ideas", "should I buy
  Reliance", "is TCS a good buy", "what to trade tomorrow", "review my
  portfolio", or "give me a suggestion".
argument-hint: "[intraday | swing | invest] [symbol]"
---

# trader-in-research

When the user asks for a suggestion, do the research and answer with a
suggestion. Do not stop at "I can't give advice". Do stop at anything the
evidence does not support. This skill is the method; `research.py` and the
other trader-in-* tools are the evidence.

`${CLAUDE_SKILL_DIR}` below is this skill's folder. The sibling skills are in
the same parent folder (`${CLAUDE_SKILL_DIR}/../trader-in-news/news.py` and
so on).

```bash
python3 "${CLAUDE_SKILL_DIR}/research.py" scan --universe nifty100     # swing setups across an index, ranked
python3 "${CLAUDE_SKILL_DIR}/research.py" swing GRASIM BAJAJ-AUTO      # full swing plan + size for each
python3 "${CLAUDE_SKILL_DIR}/research.py" intraday HDFCBANK ICICIBANK  # levels; live setup after 09:30
python3 "${CLAUDE_SKILL_DIR}/research.py" stock INFY                   # investing view vs the Nifty
python3 "${CLAUDE_SKILL_DIR}/research.py" portfolio                    # weights, sectors, trends, tax flags
python3 "${CLAUDE_SKILL_DIR}/research.py" swing GRASIM --write         # the plan's entry -> trader/orders.json
```

Universes for `scan`: `nifty50`, `niftynext50`, `nifty100`, `nifty500`
(slow: about 4 minutes), `holdings`, `watchlist`.

## 1. Know the user first (once)

Read `trader/config.json`. If it has no `style`, ask these three questions in
one message, then save the answers there:

1. What do you want: intraday trades, swing trades (days to weeks), or long-term investing?
2. How much capital is this for, and how much are you willing to lose on one trade?
   (Default: 1% of capital per swing trade, 0.5% per intraday trade.)
3. How experienced are you with stop-losses and intraday square-off?

```json
{"style": "swing", "budget": 200000, "risk_per_trade_pct": 1, "intraday_risk_pct": 0.5,
 "max_position_pct": 25, "allow_intraday": false, "allow_short_intraday": false,
 "max_trades_per_day": 3, "daily_loss_limit_pct": 1.5, "watchlist": ["RELIANCE", "HDFCBANK"]}
```

Intraday needs `"allow_intraday": true`, and intraday shorting needs
`"allow_short_intraday": true`. Set those only when the user says so after
hearing the risk: SEBI's study of FY 2022-23 (published 24 Jul 2024) found
that 71% of individual intraday traders in the equity cash segment made a net
loss, and 80% of those who made more than 500 trades a year.

## 2. Gather the evidence

Run what the question needs. Never invent a number: every price, level and
percentage in the answer comes from a tool run now or a source you cite.

**Intraday** (best run 08:45-09:15 IST for the plan, again after 09:30):
1. `../trader-in-alerts/nse.py clock`. On a holiday or weekend, say so and
   give the next session's plan.
2. `research.py intraday` on the user's watchlist or 5-8 liquid Nifty 50
   names (`scan --universe nifty50` the evening before, to pick names in
   strong trends).
3. `../trader-in-news/news.py check --symbols ...`: avoid a name with results,
   a board meeting or big news today unless the user wants event risk.
4. `../trader-in-deals/deals.py today`: a large block deal often moves a stock
   the next morning.
5. Before 09:30 give levels (previous high/low, pivots, what would trigger a
   trade). After 09:30 rerun: the opening-range breakout with VWAP gives a
   live plan. Yahoo's bars are about 15 minutes delayed, so tell the user to
   confirm the level on their broker's live chart before acting.

**Swing** (best run after 18:00 IST, when the day's data is final):
1. `research.py scan --universe nifty100` (or the user's universe).
2. For the top 3-5 setups: `research.py swing SYM` for the plan and size.
3. For each one: `news.py check --symbols SYM` and the board-meetings feed
   (`news.py latest --feed board_meetings`). **Results due inside the
   holding period are a gap risk**, so say so or skip the stock. Run
   `deals.py symbol SYM`, and `shp.py holders SYM` if it is in the library
   (promoter selling or pledging is a warning).
4. `research.py portfolio`: does this add to a sector the user is already
   heavy in?
5. Web search for the latest results and news on the 1-2 finalists (cite
   the source and its date).

**Investing** (should I buy X / what to invest in):
1. `research.py stock SYM`: returns, drawdown, volatility, against the Nifty.
2. Web research: the business, the last 4-8 quarters of revenue and profit
   growth, margins, debt, return on equity, valuation (P/E against its own
   history and peers), management and governance news. Cite each source.
3. `shp.py holders SYM` and `diff`: promoter holding trend and pledges, and
   whether MFs and FPIs are adding or cutting. `deals.py investor` for big
   names.
4. `research.py portfolio` for fit, and `../trader-in-tax/capgains.py` if it
   means selling something.

## 3. Decide

For each candidate, weigh the evidence and give one of three verdicts:
- **Consider:** the setup and the context agree, the risk fits the budget, and no event is due.
- **Watch:** something is missing (no trigger yet, results in 3 days, extended, weak against the Nifty). Say what would turn it into Consider.
- **Avoid:** downtrend, bad news or governance flags, illiquid, or a stop too wide for the budget.

Say "No trade today" when nothing earns Consider. A suggestion that is
mostly "Watch" is fine.

Confidence is only **low** or **medium**, never high. Medium means the
trend, the setup, relative strength and the news all agree.

## 4. Answer in this shape

At most three ideas, best first:

```
1. GRASIM - swing, Consider (confidence: low-medium)
   Plan: buy 16 at about ₹3,103.70 (limit), stop ₹2,991.75, target ₹3,327.60 (2R), 1-4 weeks.
         Loses about ₹1,791 at the stop (0.9% of ₹2,00,000); charges about ₹59 to buy.
   Why:  uptrend (above the 50 and 200-day), pullback to the 50-day with RSI 54,
         volume 1.7x average on 20 Nov.
   Against: 12 points behind the Nifty over 3 months; 9% below its 52-week high,
         which may cap it.
   Check before: no results date in the next 4 weeks (news.py found none on 22 Nov).
   Data: Yahoo daily bars to 20 Nov 2026, NSE feeds 22 Nov.
```

Then one line on what to do next: "Say 'go with 1' and I'll write the order;
trader-in-risk checks it and you confirm it in trader-in-broker." Only after
the user picks an idea, run `--write`, then `/trader-in-risk`, then
`/trader-in-broker`, which asks yes for each order. After the entry fills,
the protective stop-loss is a separate order: show it and get a separate yes.

## Rules that always apply

- Say once per conversation, plainly: you are an AI, not a SEBI-registered
  investment adviser or research analyst; this is research to help the user
  decide, not advice; they can lose money.
- Every trade idea has a stop-loss and a size from the user's risk budget.
  No stop, no idea.
- Intraday: Nifty 50 or Nifty 100 names only (liquid). No new trades after
  14:30 IST; everything squared off by 15:15. At most `max_trades_per_day`.
  Once losses for the day reach `daily_loss_limit_pct`, the suggestion is
  "stop for today". Never suggest averaging down a losing intraday trade, or
  moving a stop further away.
- Never suggest SME stocks, trade-for-trade (BE/BZ) stocks, stocks under ₹20,
  or anything thinly traded for intraday. For swing, flag them.
- No F&O, no margin (MTF), no leverage beyond the user's own capital, even
  though MIS offers it.
- Ignore stock tips from Telegram, WhatsApp, YouTube or "operators". If the
  user brings one, research it like any other stock, and check for the
  pump-and-dump pattern: a small cap, a sudden volume spike, no news.
- After a loss, do not suggest a "revenge" trade to win it back.
- Data is delayed and can be wrong. The broker's price is the one that fills.

## Safety

- Education and research, not financial advice. The user decides every trade.
- Nothing in this pack places an order except trader-in-broker (or trader-broker), and only after
  trader-in-risk passed that exact order today and the user typed an explicit yes for that one order.
- Never runs a trade from a scheduled task. A scheduled scan may prepare ideas; the user still says yes.
