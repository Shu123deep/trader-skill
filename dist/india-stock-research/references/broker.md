# broker tool (scripts/ticket.py)

The Indian edition of ticket.py, and the only Indian skill that can
lead to a real order. An order reaches the market only after:

1. **the risk tool (references/risk.md) passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.
3. **The user places it, or approves the tool call.** In manual mode the user
   enters it in their broker's app. With a connector, the order tool must be
   set to ask every time.

`${CLAUDE_SKILL_DIR}` is the skill's folder; the tools are in its `scripts/` folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/ticket.py" show                       # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/scripts/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/scripts/ticket.py" decline --index 2
python3 "${CLAUDE_SKILL_DIR}/scripts/ticket.py" result --index 1 --status placed --broker-id 251123000012345
python3 "${CLAUDE_SKILL_DIR}/scripts/ticket.py" fill --symbol RELIANCE --side buy --qty 20 --price 1377.50 --charges 52.68
python3 "${CLAUDE_SKILL_DIR}/scripts/ticket.py" log
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
   BLOCKED ticket goes back to references/risk.md.
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
   the tax tool (references/tax.md) expects). Then the next ticket.

## What it never does

- Never places or approves an order without all the steps above, and never two orders on one yes.
- Never runs from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats broker passwords, PINs, TOTP codes, API keys or access tokens.
- Never sets product MIS, NRML or MTF, and never trades F&O, unless the config allows it and the user confirms.

## Safety

- Education and tooling, not financial advice. No order here is a recommendation, and nothing promises returns.
- Real money: Indian brokers have no paper trading, so start small.
- Nothing in this pack places an order except the broker tool (references/broker.md) (or ticket.py), and only after
  the risk tool (references/risk.md) passed that exact order today and the user typed an explicit yes for that one order.
- Broker logins stay with the broker or its connector, never in the chat.
