---
name: trader-in-broker
description: >-
  India Order Executor. Shows each order that trader-in-risk passed today as
  a plain ticket (BUY 20 shares of RELIANCE on NSE, market, delivery CNC,
  good for today) with estimated charges, and records the user's explicit
  yes for that one order, word for word. It refuses "ok", "yes to all", or a
  yes naming another stock or number. Then the order is placed: by the user in
  their broker's app (the default, since Indian brokers have no paper
  trading), through a Zerodha Kite connector with an order tool using Kite
  Connect's place_order arguments, or drafted with Interactive Brokers. It
  records the result and the fill as a tax lot. Use when the user says
  "place the order", "buy it", "execute", "send to Zerodha", or after
  trader-in-risk passes.
argument-hint: "[order number]"
---

# trader-in-broker

The Indian edition of trader-broker, and the only Indian skill that can
lead to a real order. An order reaches the market only after:

1. **trader-in-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.
3. **The user places it, or approves the tool call.** In manual mode the user
   enters it in their broker's app. With a connector, the order tool must be
   set to ask every time.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                       # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" decline --index 2
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id 251123000012345
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side buy --qty 20 --price 1377.50 --charges 52.68
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that order, and keeps the log in `trader/state/broker.json`.

## There is no paper trading here

US brokers like Alpaca offer paper accounts. Indian brokers generally do not,
so **every order here is real money.** Each ticket says so. For practice,
use a small `max_order_inr` and `budget` in `trader/config.json`, or stop
after `show`.

## Placing the order: "broker" in trader/config.json

- **`manual` (default).** After the yes, the user places the order in their
  broker's app (Zerodha Kite, Groww, Upstox, Angel One, ICICI Direct...)
  exactly as the ticket says, then tells Claude the order id, and Claude runs
  `result`. Nothing leaves this machine. This works with every broker.
- **`kite`.** If the user has connected a Zerodha Kite connector (for example
  Zerodha's Kite MCP server) **that has an order tool**, the ticket prints the
  arguments in Kite Connect's `place_order` names: `variety`, `exchange`,
  `tradingsymbol`, `transaction_type`, `quantity`, `product`, `order_type`,
  `validity`, `price`, `trigger_price`. Before the first call, compare them
  with the tool's own schema; if the connector is read-only, use manual. Set
  the order tool to ask every time (Needs approval, or in Claude Code
  `"permissions": {"ask": ["mcp__kite__place_order"]}`), never always allow.
  Login happens in the connector's own Kite sign-in, never in the chat.
- **`ibkr`.** The Interactive Brokers connector drafts the order and the user
  approves it in their IBKR account. IBKR's India offering differs from its
  US one. Check that the account can trade NSE.

## Steps, for each order

1. `ticket.py show`. Only READY tickets go to the user. A NOT READY or
   BLOCKED ticket goes back to `/trader-in-risk`.
2. Show ONE ticket and ask: "Place this order? Reply yes or no." One order
   per question. Never offer "yes to all".
3. `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   "yes" and "haan" count. If it prints "Not recorded", ask again. Never turn
   a reply into a yes yourself.
4. Only after "Recorded your yes": in manual mode, tell the user to place it
   now in their app; with `kite`, call the order tool with exactly the JSON
   printed.
5. `ticket.py result --index N --status placed --broker-id <id>` (or
   `denied` / `failed --note ...`). A failed order is not retried without a
   new check and a new yes.
6. When it fills, `ticket.py fill ... --charges <from the contract note>`
   adds a lot to `trader/portfolio.json` (cost includes charges, as
   trader-in-tax expects). Then the next ticket.

## What it never does

- Never places or approves an order without all the steps above, and never two orders on one yes.
- Never runs from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats broker passwords, PINs, TOTP codes, API keys or access tokens.
- Never sets product MIS, NRML or MTF, and never trades F&O, unless the config allows it and the user confirms.

## Safety

- Education and tooling, not financial advice. No order here is a recommendation, and nothing promises returns.
- Real money: Indian brokers have no paper trading, so start small.
- Nothing in this pack places an order except trader-in-broker (or trader-broker), and only after
  trader-in-risk passed that exact order today and the user typed an explicit yes for that one order.
- Broker logins stay with the broker or its connector, never in the chat.
