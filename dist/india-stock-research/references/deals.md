# deals tool (scripts/deals.py)

The Indian answer to deals.py. India has no congressional trade
disclosures, but large trades by anyone must be disclosed the same evening as
bulk or block deals. This skill reads them from NSE's public archive.

`${CLAUDE_SKILL_DIR}` is the skill's folder; the tools are in its `scripts/` folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/deals.py" today                    # the day's deals, biggest first
python3 "${CLAUDE_SKILL_DIR}/scripts/deals.py" today --all              # include same-day buy+sell churn
python3 "${CLAUDE_SKILL_DIR}/scripts/deals.py" watch                    # NEW deals by your investors / in your holdings
python3 "${CLAUDE_SKILL_DIR}/scripts/deals.py" investor kacholia        # that investor's deals in the ledger, net by stock
python3 "${CLAUDE_SKILL_DIR}/scripts/deals.py" symbol GALLANTT          # every deal in one stock in the ledger
python3 "${CLAUDE_SKILL_DIR}/scripts/deals.py" investors                # who is watched
```

## What a deal is

| | rule | window |
| --- | --- | --- |
| bulk deal | one client's trades in a stock on one day add up to more than 0.5% of its listed shares | normal market, reported the same evening |
| block deal | a single trade of at least Rs 10 crore | the block windows, 08:45-09:00 and 14:05-14:20 IST |

A block trade that is also over 0.5% of the company appears in both files;
the tool keeps it once as `block+bulk`.

## Sources and limits

- `nsearchives.nseindia.com/content/equities/bulk.csv` and `block.csv`, NSE's
  public archive. They hold **only the latest trading day**, so run `watch`
  every evening (a Claude scheduled task at 19:30 IST works) and the ledger in
  `trader/state/deals.json` becomes the history. `investor` and `symbol` read
  that ledger.
- **NSE only.** BSE publishes its own deals, and some large ones happen only
  there (BSE's API refuses automated requests). A BSE-only deal does not
  appear here.
- Most bulk deals on a normal day are market makers and HFT firms buying and
  selling the same stock the same day. Those are marked `intraday` and hidden
  unless `--all`.
- Only trades over the thresholds are disclosed. Smaller buying never shows.

## The watchlist

`investors.json` in this folder lists well-known investors with the names
they file under (people and their investment companies, for example Rare
Enterprises for the Jhunjhunwala family). A client name matches when it
contains every word of one entry. Add your own in `trader/investors.json`:

```json
{"investors": [{"name": "My fund", "match": ["SOME CAPITAL LLP", "SOME PERSON NAME"]}]}
```

`watch` also alerts on any deal in a stock in `trader/portfolio.json`.

## Steps

1. For "what's happening", run `today` and summarise the five biggest deals
   and any watched investor, in one line each.
2. For monitoring, run `watch`. It prints only deals it has not seen before,
   exits 0 when there is something new, and records everything it saw.
3. For "what has X been buying", run `investor X` and show the net by stock.
   Say how far back the ledger goes.
4. If the user wants to copy a move, they decide. Size it with size.py
   (`--whole-shares`), check it with references/risk.md, and they say yes to it
   in references/broker.md.

## Example

On 20 Nov 2026 NSE listed 112 deals. 92 were same-day churn. The biggest
real one was a ₹84.37 Cr sale of Gallantt Ispat by Dinesh R Agarwal, both a
block deal and a bulk deal, bought in the block window by two companies.

## Safety

- Education and tooling, not financial advice. A disclosed deal is a fact about a past trade, not a
  recommendation, and copying it late means a different price.
- Nothing in this pack places an order except the broker tool (references/broker.md) (or ticket.py), and only after
  the risk tool (references/risk.md) passed that exact order today and the user typed an explicit yes for that one order.
- No F&O, margin or short selling.
