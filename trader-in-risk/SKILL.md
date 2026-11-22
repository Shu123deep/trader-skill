---
name: trader-in-risk
description: >-
  India Pre-Trade Risk Check. Checks every proposed Indian order before it
  can reach a broker: whole shares only (no rupee-amount orders), a real NSE
  symbol, delivery (CNC) not intraday (MIS) unless allowed, no futures and
  options unless allowed, no short selling, your max order and minimum order
  in rupees, a cap on any one stock, a cash buffer after estimated STT, stamp
  duty, exchange and SEBI charges and GST, duplicates, a daily order limit,
  limit prices far from the last price, and trade-for-trade or SME series.
  PASS or BLOCK with reasons, fingerprinted so trader-in-broker only shows
  orders that passed today. Use after trader-budget, trader-in-tax or
  trader-rebalance, or when the user says "check these orders", "is this
  trade safe", or "can I afford this".
argument-hint: "[orders file]"
---

# trader-in-risk

The Indian edition of trader-risk. Every order goes through here before
trader-in-broker will show it to the user. It never places anything.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                  # trader/orders.json -> trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write       # look without recording
```

Exit 0 when every order passes, 1 when any is blocked.

## The order

```json
{"symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "qty": "20",
 "type": "market", "product": "CNC", "validity": "DAY"}
```

`type` is `market`, `limit` (needs `limit_price`), `sl` (stop-loss limit:
`stop_price` is the trigger, plus `limit_price`) or `sl-m` (stop-loss market:
`stop_price`). Orders from trader-budget (`time_in_force: "day"`) are read
as-is. Always size Indian orders with `size.py --whole-shares`: a rupee
amount (`notional`) order is blocked, because Indian exchanges trade whole
shares only.

## The rules (trader/config.json)

```json
{"market": "IN", "currency": "INR", "budget": 100000, "cash_buffer_pct": 10,
 "max_position_pct": 25, "max_order_inr": 100000, "min_order_inr": 500,
 "max_orders_per_day": 10, "blocked": [], "allow_fno": false,
 "allow_intraday": false, "broker": "manual"}
```

| check | blocks when |
| --- | --- |
| format | no symbol, not buy/sell, a fraction of a share, a rupee-amount order, a limit or SL order missing its prices, an SL limit on the wrong side of its trigger, an unknown type, validity or exchange |
| asset | a futures or options symbol (`NIFTY26NOVFUT`, `RELIANCE26NOV1400CE`) unless `allow_fno`; a symbol not on NSE's equity list (when `trader/cache/EQUITY_L.csv` exists, which trader-in-alerts creates) |
| product | MIS (intraday, leveraged, squared off the same day) unless `allow_intraday`; NRML or MTF always |
| blocked | the symbol is on your `blocked` list |
| size | under `min_order_inr` or over `max_order_inr` |
| position | the stock would be over `max_position_pct` of the account |
| cash | the buy plus estimated charges would leave less than the buffer |
| short | selling more than you hold (delivery shorting is not possible in India) |
| duplicate | the same symbol and side twice in the batch, or already placed today |
| daily limit | more than `max_orders_per_day` placed today |

Warnings (shown, not blocking): a limit price more than 10% from the last
price (daily price bands are 2-20%), trade-for-trade (BE/BZ) and SME series,
and once per batch that market orders in thin stocks can fill far from the
last price.

## Charges

The cash check includes approximate statutory charges on delivery trades:
STT 0.1% on buy and sell, stamp duty 0.015% on buys, NSE transaction charge
0.00297%, SEBI fee ₹10 per crore, and 18% GST on the transaction and SEBI
charges. About ₹1.19 per ₹1,000 bought. Brokerage and DP charges depend on
the broker and are not included. The contract note has the real figures.

## Steps

1. Refresh prices (`/trader-in-alerts` `nse.py quote --holdings --write` and
   the symbols in the orders).
2. Run `check.py`. Tell the user each BLOCK in one line with the reason, and
   what would fix it.
3. When every order the user wants has passed, go to `/trader-in-broker`.
   It refuses any order whose fingerprint is not a PASS from today.

## Example

Four buys from trader-budget (₹1,00,000 budget, 30% cap, whole shares)
pass: 20 RELIANCE (₹27,550, charges about ₹32.68), 11 TCS, 16 INFY and 22
HDFCBANK, leaving ₹10,025 cash. A ₹5,000 INFY order is blocked (rupee amount),
as are 2.5 TCS (fraction), an MIS order, NIFTY26NOVFUT (F&O off), selling
TATASTEEL that is not held (short), and FAKECO (not an NSE symbol).

## Safety

- Education and tooling, not financial advice. PASS means the order fits your rules, not that it is a good trade.
- Nothing in this pack places an order except trader-in-broker (or trader-broker), and only after
  trader-in-risk passed that exact order today and the user typed an explicit yes for that one order.
- No F&O, margin, intraday (MIS) or short selling unless the config allows it and the user confirms.
