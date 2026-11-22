---
name: trader-in-news
description: >-
  India News Monitor. Watches NSE's official corporate filings for every
  stock you hold: announcements (results, board meeting outcomes,
  acquisitions, order wins, fund raises, rating changes, director and auditor
  changes, pledges, legal matters), corporate actions (dividends, splits,
  bonuses, buybacks with ex-dates and record dates), financial results and
  board meetings. Matches each filing to your NSE symbols, tags it, flags red
  flags like an auditor resigning, links the PDF, and alerts on each filing
  once. Use when the user says "any news on my stocks", "did Infosys announce
  anything", "upcoming dividends", "ex-date", "record date", "corporate
  announcements", "results today", or "watch my Indian portfolio".
argument-hint: "[symbol]"
---

# trader-in-news

The Indian edition of trader-news. In India the first and most reliable news
about a listed company is its own filing with the exchange, which it must
make within minutes or hours of a material event (SEBI LODR Regulation 30).
This skill reads those filings from NSE's public RSS feeds.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py" check                          # NEW filings for holdings + targets + watchlist
python3 "${CLAUDE_SKILL_DIR}/news.py" check --symbols INFY TCS       # just these
python3 "${CLAUDE_SKILL_DIR}/news.py" latest                         # everything on the feeds now, tagged
python3 "${CLAUDE_SKILL_DIR}/news.py" latest --tag results --tag dividend
python3 "${CLAUDE_SKILL_DIR}/news.py" actions                        # dividends / splits / bonuses for your symbols
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/portfolio.json`, `trader/targets.json` | the symbols to watch |
| `trader/config.json` | optional `"watchlist": ["SYMBOL", ...]` for stocks you do not hold |
| `trader/cache/EQUITY_L.csv` | NSE's symbol list, to turn company names into symbols (shared with trader-in-alerts) |
| `trader/state/news.json` | filings already seen, so each alerts once |

## Sources and limits

NSE's RSS feeds on `nsearchives.nseindia.com/content/RSS/`:
`Online_announcements.xml`, `Corporate_action.xml`, `Financial_Results.xml`,
`Board_Meetings.xml`. They hold only the most recent items. On a busy day
the announcements feed covers just the last few hours, so for full coverage
run `check` every 30 minutes in market hours, or at least once each evening
(a Claude scheduled task). BSE-only companies are not covered. A company name
the tool cannot match to a symbol shows as `?`.

## Tags

results, dividend, bonus, split, buyback, fund raise, merger, acquisition,
disposal, order win, rating, management change, auditor, pledge, insider,
legal, board meeting, investor meet, press release, other. These are
**marked "check this"**: management change, auditor, pledge and legal.
Auditor resignations and rising promoter pledges are classic Indian red
flags. Tell the user plainly when one appears in a stock they hold.

## Steps

1. Run `check`. For each new filing, give the user one line: symbol, what it
   is, and the PDF link. Put "check this" items first.
2. If the user asks what a filing says, open the PDF link and summarise it
   (the feed only carries the subject line). Do not guess from the subject.
3. For income events, run `actions`. Explain the ex-date: India settles T+1,
   so the shares must be bought by the day before the ex-date to get the
   dividend. After a split or bonus, the lots in trader/portfolio.json need
   new share counts (trader-in-tax).
4. A filing is information. If the user wants to act, the normal path runs:
   size, `/trader-in-risk`, their yes in `/trader-in-broker`.

## Example

On the evening of 22 Nov 2026, `check --symbols APLAPOLLO TATASTEEL` found
two APL Apollo filings (an acquisition, and the outcome of a board meeting)
and six Tata Steel filings (board outcomes, a sale or disposal, a
diversification update), each with its PDF. A second `check` a minute later
found nothing new.

## Safety

- Education and tooling, not financial advice. A filing is what the company told the exchange.
- Nothing in this pack places an order except trader-in-broker (or trader-broker), and only after
  trader-in-risk passed that exact order today and the user typed an explicit yes for that one order.
- Never act on a headline alone; read the filing.
