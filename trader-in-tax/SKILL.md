---
name: trader-in-tax
description: >-
  India Capital Gains. Tracks cost-basis lots for Indian listed shares and
  equity mutual funds and does the capital-gains arithmetic under sections
  111A and 112A: FIFO lots (mandatory for demat), short term vs long term at
  12 months, STCG 20% and LTCG 12.5% (15% / 10% before 23 Jul 2024), the
  Rs 1.25 lakh LTCG exemption per financial year, loss set-off and 8-year
  carry forward, grandfathering for shares bought before 1 Feb 2018, and
  4% cess. Finds tax-loss harvesting candidates and tax-gain harvesting (use
  the exemption before 31 March) and writes them as proposed sell orders. Use
  when the user says "what tax will I pay if I sell", "STCG", "LTCG",
  "capital gains", "1.25 lakh exemption", "tax harvesting", "book profits
  before March", "set off my losses", or "when does this turn long term".
argument-hint: "[symbol] [shares]"
---

# trader-in-tax

The Indian edition of trader-tax. It does arithmetic on the user's lots. It
is **not tax advice**: a chartered accountant decides what applies to them.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/capgains.py" report --lots               # every lot: gain, term, long term from
python3 "${CLAUDE_SKILL_DIR}/capgains.py" sell INFY 50 --price 1098   # what selling 50 realizes + extra tax this FY
python3 "${CLAUDE_SKILL_DIR}/capgains.py" sell INFY 50 --price 1098 --expenses 40 --record   # it filled
python3 "${CLAUDE_SKILL_DIR}/capgains.py" fy                          # this FY: set-off, exemption, tax estimate
python3 "${CLAUDE_SKILL_DIR}/capgains.py" fy --year 2025              # FY 2025-26
python3 "${CLAUDE_SKILL_DIR}/capgains.py" harvest                     # losses that cut this year's tax
python3 "${CLAUDE_SKILL_DIR}/capgains.py" harvest --gains --write     # LTCG inside the exemption -> orders.json
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/portfolio.json` | positions with lots (`portfolio.example.json` in this folder) |
| `trader/prices.json` | rupee prices from trader-in-alerts; refuses a file marked in another currency |
| `trader/state/tax.json` | the sales recorded with `--record`, for the year's set-off |
| `trader/config.json` | optional `"carry_forward_losses": {"short": 0, "long": 0}` from your last ITR |
| `trader/orders.json` | with `harvest --write`: proposed sells for trader-in-risk |

A lot is `{"id", "date", "shares", "cost"}`, with `cost` the total rupees paid
including brokerage and charges, but not STT. For shares bought before 1 Feb
2018 add `"fmv_2018_01_31"`, the highest price quoted on 31 Jan 2018 per
share. After a bonus issue or split, adjust the lot's share count (the cost
stays the same).

## The rules it applies

Listed equity shares and equity-oriented funds on which STT was paid:

| | short term (111A) | long term (112A) |
| --- | --- | --- |
| held | 12 months or less | more than 12 months |
| sold on or after 23 Jul 2024 | 20% | 12.5% above the exemption |
| sold before 23 Jul 2024 | 15% | 10% above the exemption |
| exemption per FY | none | Rs 1,25,000 (Rs 1,00,000 up to FY 2023-24) |

- **FIFO only.** For shares in demat the oldest shares are treated as sold
  first (CBDT Circular 768). The user cannot choose lots, so `sell` and
  `harvest` always take the oldest first. A harvest that wants a loss lot
  behind a gain lot must sell both, and the tool shows the net.
- **Set-off:** this year's short-term losses go against short-term gains and
  then long-term gains. Long-term losses go only against long-term gains.
  Then brought-forward losses, the same way. The exemption applies to what
  long-term gain is left. Losses and the exemption are set against the
  higher-rate gains first.
- **Carry forward:** unused losses carry forward for 8 years, but only if the
  return is filed by the due date.
- **Estimate:** base tax plus 4% health and education cess. Surcharge (for
  high incomes) is not included.
- **No wash-sale rule.** India has none, unlike the US. A sell-and-rebuy
  purely to book a loss can still be challenged as a sham transaction, and
  the proposals say so.
- The exemption is **per person per financial year across every account**.
  The tool sees only the lots in this file, so ask about other demat accounts.

## Harvesting

- `harvest` (losses): for each stock, the FIFO sale that realizes the biggest
  loss, and the tax it saves this FY given the sales already recorded. A loss
  with no gains to absorb it is still useful: it carries forward.
- `harvest --gains`: long-term gains that fit in the exemption left this FY,
  in whole shares. Selling and buying back raises the cost of the user's
  holding, so less tax is due later. Two cautions the tool prints: buy back on
  the **next trading day**, not the same day (a same-day sell and buy can be
  treated as intraday), and the new shares start a fresh 12-month clock.
  Charges (brokerage, STT 0.1% each way on delivery, DP charges) eat into the
  benefit on small amounts.
- The financial year ends on **31 March**. A harvest has to settle by then.

## Steps

1. Make sure the lots are there and prices are fresh (`/trader-in-alerts`
   `nse.py quote --holdings --write`).
2. For "what if I sell", run `sell` and give the user the gain by lot, the
   term, and the extra tax this year in one short paragraph.
3. For harvesting, run `harvest` or `harvest --gains`, explain each line, and
   only with the user's go-ahead run `--write`. The orders then go through
   `/trader-in-risk` and `/trader-in-broker`, which asks yes for each one.
4. After a sale fills, record it with `sell ... --record` so the year's
   set-off stays right.

## Example

With `portfolio.example.json` and the 20 Nov 2026 closes, `sell INFY 50`
takes the 40 oldest shares (bought Aug 2021, long term, ₹16,720 gain) and 10
from Feb 2026 (short term, ₹720 loss). The loss is set off against the gain,
the ₹16,000 left is inside the ₹1.25 lakh exemption, so the estimated tax is
₹0. `harvest --gains` proposes all 100 HDFCBANK (₹32,600) and the 40 oldest
INFY (₹16,720) and stops at RELIANCE, whose oldest lot is at a loss.

## Safety

- Not tax advice and not financial advice: arithmetic on the user's lots under the rules above, which
  change with each Budget. Check with a chartered accountant before acting.
- Proposals only. Nothing is placed until trader-in-risk passes that exact order today and the user
  types an explicit yes for that one order in trader-in-broker.
- No F&O, intraday or short selling.
