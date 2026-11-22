#!/usr/bin/env python3
"""Order tickets and the decision log for Indian orders (ticket.py). Standard library only.

This tool never talks to a broker and never places anything. It works around
the one moment that matters, your yes:

  show      renders each proposed order as a plain ticket plus the exact
            arguments in Zerodha Kite Connect's place_order names. An order
            is READY only if check.py passed THIS exact order today
            (matched by fingerprint) and nobody has decided on it yet.
  approve   records your explicit yes for ONE order, word for word. It
            refuses "yes to all", "ok", "sure", a yes that names another
            stock or another number, and any order that is not READY.
  decline   records a no.
  result    records what happened: placed (with the broker's order id),
            denied or failed.
  fill      adds a filled order to trader/portfolio.json as a tax lot.
  log       prints today's decisions.

    python3 ticket.py show
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status placed --broker-id 251123000012345
    python3 ticket.py fill --symbol RELIANCE --side buy --qty 20 --price 1377.50 --charges 32.68
    python3 ticket.py log

"broker" in trader/config.json:
  manual (default)  you place the order yourself in your broker's app or site,
                    from the ticket; then record the result here
  kite              a Zerodha Kite connector with an order tool; Claude calls it
                    with exactly the ticket's arguments after your yes
  ibkr              Interactive Brokers' connector drafts it; you approve in IBKR

Everything is written to trader/state/broker.json (append only).
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

TYPES = {"market": "MARKET", "limit": "LIMIT", "sl": "SL", "sl-m": "SL-M", "stop": "SL-M", "stop_limit": "SL"}
VALIDITY = {"day": "DAY", "ioc": "IOC"}
YES_WORDS = ("yes", "y", "haan", "ha")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each", "sab", "sabhi"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later", "hold",
               "unless", "if", "nahi", "ruko"}
BROKERS = {"manual": "your broker (you place it)", "kite": "Zerodha Kite", "ibkr": "Interactive Brokers"}


class TicketError(Exception):
    pass


# ------------------------------------------------ the order (same code in check.py)

def _num_str(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else ("%.2f" % f).rstrip("0").rstrip(".")


def normalise(o):
    """Any accepted order shape -> the canonical Indian order (strings for numbers)."""
    typ_in = str(o.get("type") or o.get("order_type") or "market").strip().lower()
    val_in = str(o.get("validity") or o.get("time_in_force") or "day").strip().lower()
    out = {
        "symbol": str(o.get("symbol") or o.get("tradingsymbol") or "").strip().upper(),
        "exchange": str(o.get("exchange") or "NSE").strip().upper(),
        "side": str(o.get("side") or o.get("transaction_type") or "").strip().lower(),
        "qty": _num_str(o.get("qty", o.get("quantity"))),
        "notional": _num_str(o.get("notional")),
        "type": TYPES.get(typ_in, typ_in.upper()),
        "limit_price": _num_str(o.get("limit_price", o.get("price"))),
        "stop_price": _num_str(o.get("stop_price", o.get("trigger_price"))),
        "product": str(o.get("product") or "CNC").strip().upper(),
        "validity": VALIDITY.get(val_in, val_in.upper()),
        "stop_loss": _num_str(o.get("stop_loss")),
        "target": _num_str(o.get("target")),
    }
    if out["type"] in ("MARKET", "SL-M"):
        out["limit_price"] = None
    return out


def fingerprint(order):
    core = {k: v for k, v in normalise(order).items() if v is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------ files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def inr(v):
    f = float(v)
    whole, frac = ("%.2f" % abs(f)).split(".")
    if len(whole) > 3:
        head, tail, parts = whole[:-3], whole[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return "%s₹%s%s" % ("-" if f < 0 else "", whole, "" if frac == "00" else "." + frac)


# ------------------------------------------------ rendering

def kite_args(order):
    """Arguments in Kite Connect's place_order names. Check your connector's own schema."""
    o = normalise(order)
    out = {"variety": "regular", "exchange": o["exchange"], "tradingsymbol": o["symbol"],
           "transaction_type": o["side"].upper(), "quantity": int(float(o["qty"])) if o["qty"] else None,
           "product": o["product"], "order_type": o["type"], "validity": o["validity"]}
    if o["limit_price"]:
        out["price"] = float(o["limit_price"])
    if o["stop_price"] and o["type"] in ("SL", "SL-M"):
        out["trigger_price"] = float(o["stop_price"])
    return out


def stop_order(order):
    """The protective order to place right after the entry fills, or None."""
    o = normalise(order)
    if not o["stop_loss"] or not o["qty"]:
        return None
    return {"symbol": o["symbol"], "exchange": o["exchange"], "side": "sell" if o["side"] == "buy" else "buy",
            "qty": o["qty"], "type": "sl-m", "stop_price": o["stop_loss"], "product": o["product"], "validity": "DAY"}


def plain(order):
    """BUY 20 shares of RELIANCE on NSE, market order, delivery (CNC), good for today"""
    o = normalise(order)
    q = o["qty"] or "?"
    what = "%s share%s of %s on %s" % (q, "" if q == "1" else "s", o["symbol"] or "?", o["exchange"])
    t = o["type"]
    if t == "LIMIT":
        how = "limit order at %s" % inr(o["limit_price"])
    elif t == "SL":
        how = "stop-loss: trigger %s, limit %s" % (inr(o["stop_price"]), inr(o["limit_price"]))
    elif t == "SL-M":
        how = "stop-loss market: trigger %s" % inr(o["stop_price"])
    else:
        how = "market order"
    prod = {"CNC": "delivery (CNC)", "MIS": "INTRADAY (MIS)"}.get(o["product"], o["product"])
    val = {"DAY": "good for today", "IOC": "immediate or cancel"}.get(o["validity"], o["validity"])
    return "%s %s, %s, %s, %s" % (o["side"].upper(), what, how, prod, val)


def amount_numbers(order):
    o = normalise(order)
    nums = []
    for k in ("qty", "limit_price", "stop_price"):
        try:
            if o[k] is not None:
                nums.append(float(o[k]))
        except ValueError:
            pass
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, done, kar do)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = normalise(order)["symbol"]
    tokens = set(re.findall(r"[A-Z0-9][A-Z0-9&\-]{1,19}", raw.upper()))
    named = tokens & ({s.upper() for s in batch_symbols} - {sym})
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)
            if not re.search(re.escape(n), sym)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (", ".join(_num_str(n) for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


# ------------------------------------------------ state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "check.py has not checked these orders (run check.py)"
    if risk.get("market") != "IN":
        return None, "the risk check was not made by check.py; run check.py"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by check.py: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        if rec.get("status") == "approved":
            return False, "APPROVED by you at %s, waiting to be placed" % rec.get("at"), rv
        return False, "DONE: %s at %s" % (rec.get("status"), rec.get("at")), rv
    return True, "READY: check.py PASS, waiting for your yes", rv


def broker_of(cfg):
    b = str(cfg.get("broker", "manual")).lower()
    return b if b in BROKERS else "manual"


def render(order, idx, total, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    broker = broker_of(cfg)
    lines = ["ORDER %d of %d, REAL MONEY, %s" % (idx, total, BROKERS[broker]), "  " + plain(order)]
    if rv and rv.get("value_inr"):
        lines.append("  about %s plus about %s statutory charges (before brokerage)"
                     % (inr(rv["value_inr"]), inr(rv.get("est_charges_inr") or 0)))
    o = normalise(order)
    if o["stop_loss"]:
        lines.append("  plan: stop-loss %s%s" % (inr(o["stop_loss"]), (", target " + inr(o["target"])) if o["target"] else ""))
        if o["product"] == "MIS":
            lines.append("  right after the fill: %s; square off by 15:15 IST" % plain(stop_order(order)))
        else:
            lines.append("  right after the fill: set a GTT (or a daily SL order) to sell at %s; GTTs last up to "
                         "a year at most brokers, a DAY order expires tonight" % inr(o["stop_loss"]))
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    if not (rv and rv.get("verdict") == "PASS"):
        pass
    elif broker == "kite":
        lines.append("  After your yes, Claude calls the Kite connector's order tool with exactly:")
        for l in json.dumps(kite_args(order), indent=2).splitlines():
            lines.append("    " + l)
        if stop_order(order) and normalise(order)["product"] == "MIS":
            lines.append("  and, once it fills (a second yes), the stop-loss:")
            for l in json.dumps(kite_args(stop_order(order)), indent=2).splitlines():
                lines.append("    " + l)
    elif broker == "ibkr":
        lines.append("  Claude drafts this with the Interactive Brokers connector; it goes to the market only")
        lines.append("  after you approve it in your IBKR account.")
    else:
        lines.append("  After your yes, place it yourself in your broker's app exactly as above, then")
        lines.append("  tell Claude the order id so it can record it.")
    if ready:
        lines.append("  Reply \"yes\" to approve this one order, or \"no\" to skip it.")
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order), "fingerprint": fingerprint(order),
            "broker": broker_of(cfg), "kite_args": kite_args(order), "why": order.get("why", ""),
            "value_inr": (rv or {}).get("value_inr"), "est_charges_inr": (rv or {}).get("est_charges_inr"),
            "stop_order": stop_order(order), "stop_kite_args": kite_args(stop_order(order)) if stop_order(order) else None}


# ------------------------------------------------ fills -> lots

def apply_fill(path, cfg, symbol, side, qty, price, date, charges=0.0):
    """A buy adds a lot (cost includes charges, as capgains.py expects); a sell reduces lots FIFO."""
    pf = read_json(path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % path)
        pf = {"cash": float(budget), "positions": []}
    symbol, qty, price = symbol.upper(), float(qty), float(price)
    if abs(qty - round(qty)) > 1e-9:
        raise TicketError("Indian shares trade in whole units; got %s" % qty)
    gross = round(qty * price, 2)
    pos = next((p for p in pf.setdefault("positions", []) if str(p.get("ticker", "")).upper() == symbol), None)
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": int(qty),
                     "cost": round(gross + charges, 2)})
        pos["shares"] = int(round(float(pos.get("shares", 0)) + qty))
        pf["cash"] = round(float(pf.get("cash", 0)) - gross - charges, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s"
                              % (int(qty), symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = int(round(float(lot["shares"]) - take))
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 0]
        pos["shares"] = int(round(float(pos["shares"]) - qty))
        if pos["shares"] <= 0:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + gross - charges, 2)
    write_json(path, pf)
    return pf


# ------------------------------------------------ commands

def _load(a):
    doc = read_json(a.orders, "orders file (run size.py --whole-shares or capgains.py first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    return orders, cfg, risk, load_state(a.state), a.today or datetime.date.today().isoformat()


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2, ensure_ascii=False))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne order at a time: a yes covers one ticket, never the batch. Education and tooling, "
          "not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [normalise(o)["symbol"] for o in orders])
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    o = normalise(order)
    state.setdefault("orders", []).append({
        "at": now_iso(), "date": today, "index": a.index, "fingerprint": fingerprint(order), "symbol": o["symbol"],
        "side": o["side"], "plain": plain(order), "status": "approved", "reply": a.reply, "broker": broker_of(cfg)})
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    b = broker_of(cfg)
    if b == "kite":
        print("Now call the Kite connector's order tool with exactly:")
        print(json.dumps(kite_args(order), indent=2))
        print("Keep its permission on ask (never always allow). Then: ticket.py result --index %d --status "
              "placed|denied|failed --broker-id ID" % a.index)
    elif b == "ibkr":
        print("Now draft it with the Interactive Brokers connector; approve it in your IBKR account.")
    else:
        print("Place it yourself in your broker's app exactly as the ticket says, then: ticket.py result "
              "--index %d --status placed --broker-id <order id>" % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today = _load(a)
    order = _pick(orders, a.index)
    o = normalise(order)
    state.setdefault("orders", []).append({
        "at": now_iso(), "date": today, "index": a.index, "fingerprint": fingerprint(order), "symbol": o["symbol"],
        "side": o["side"], "plain": plain(order), "status": "declined", "reply": a.reply or "no"})
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was sent." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    new = {"at": now_iso(), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status}
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    state["orders"].append(new)
    write_json(a.state, state)
    print("Recorded order %d as %s." % (a.index, a.status))
    if a.status == "placed":
        print("When it fills (T+1 settlement for delivery), record it for capgains.py: ticket.py fill --symbol %s "
              "--side %s --qty Q --price P --charges C" % (rec["symbol"], rec["side"]))
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    date = a.date or datetime.date.today().isoformat()
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date, a.charges or 0.0)
    print("Recorded %s %s %s at %s on %s%s. Cash now %s." % (
        a.side, int(float(a.qty)), a.symbol.upper(), inr(a.price), date,
        (" with %s charges" % inr(a.charges)) if a.charges else "", inr(pf["cash"])))
    return 0


def cmd_log(a):
    state = load_state(a.state)
    today = a.today or datetime.date.today().isoformat()
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        print("%s  #%s %-9s %s%s%s" % (r.get("at"), r.get("index"), r.get("status"), r.get("plain"), reply, extra))
    print("%d placed%s." % (sum(1 for r in rows if r.get("status") == "placed"), "" if a.all else " today"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order tickets and your yes, for Indian orders (ticket.py).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        p.add_argument("--today")

    p = sub.add_parser("show")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--status", required=True, choices=["placed", "denied", "failed"])
    p.add_argument("--broker-id")
    p.add_argument("--note")
    p = sub.add_parser("fill")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True, type=float)
    p.add_argument("--charges", type=float, help="brokerage + statutory charges in rupees (from the contract note)")
    p.add_argument("--date")
    p = sub.add_parser("log")
    p.add_argument("--state", default="trader/state/broker.json")
    p.add_argument("--today")
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "fill": cmd_fill, "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
