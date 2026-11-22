#!/usr/bin/env python3
"""Pre-trade risk check for Indian orders (check.py). Standard library only, no network.

Reads the proposed orders (trader/orders.json, from size.py with
--whole-shares, capgains.py or by hand) and checks every one against your
rules (trader/config.json), your account (trader/portfolio.json) and rupee
prices (trader/prices.json). Each order gets PASS or BLOCK with the reasons.
Orders are checked in file order as if each PASS had filled, so buys that are
fine one by one but break the cash buffer together are caught.

    python3 check.py                       # the trader/ files, writes trader/state/risk.json
    python3 check.py --json
    python3 check.py --no-write

The Indian order, as both this tool and ticket.py read it:

    {"symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "qty": "20",
     "type": "market" | "limit" | "sl" | "sl-m", "limit_price": "1370",
     "stop_price": "1350", "product": "CNC", "validity": "DAY"}

size.py's orders (Alpaca-shaped: time_in_force "day", type "stop" or
"stop_limit") are read too: day -> DAY, ioc -> IOC, stop -> SL-M,
stop_limit -> SL. A "notional" (rupee amount) order is blocked: Indian
exchanges trade whole shares only.

What blocks an order:

  format     no symbol, side not buy/sell, qty not a whole number of shares,
             a rupee-amount order, a limit or SL order without its prices,
             an unknown type, validity or exchange
  asset      futures and options unless "allow_fno": true; a symbol that is
             not on NSE's equity list (when trader/cache/EQUITY_L.csv exists)
  product    anything but CNC (delivery) unless "allow_intraday": true (MIS)
  blocked    a symbol on your "blocked" list
  size       under min_order_inr, over max_order_inr
  position   the stock would be over max_position_pct of the account
  cash       the buy plus estimated charges would dip into your cash buffer
  short      selling more than you hold (short selling in delivery is not possible)
  duplicate  the same symbol and side twice in this batch, or already placed today
  daily      more than max_orders_per_day placed today

Exit code 0 when every order passes, 1 when any is blocked. Verdicts go to
trader/state/risk.json with a fingerprint of each order, so ticket.py
only shows an order as ready if THIS exact order passed today.
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import sys

DEFAULTS = {
    "budget": None, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 100000,
    "min_order_inr": 500, "max_orders_per_day": 10, "blocked": [], "allow_fno": False,
    "allow_intraday": False, "allow_short_intraday": False, "risk_per_trade_pct": None,
    "intraday_risk_pct": 0.5,
}
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
FNO = re.compile(r"^[A-Z&\-]+\d{2}[A-Z]{3}(\d+(\.\d+)?(CE|PE)|FUT)$|^[A-Z&\-]+\d{2}[A-Z0-9]{1,3}\d{2}\d+(CE|PE)$")
TYPES = {"market": "MARKET", "limit": "LIMIT", "sl": "SL", "sl-m": "SL-M", "stop": "SL-M", "stop_limit": "SL"}
VALIDITY = {"day": "DAY", "ioc": "IOC"}
PAISE = 0.005
# Approximate statutory charges on equity delivery (brokerage not included):
# STT 0.1% on buy and sell, stamp duty 0.015% on buy, NSE transaction charge
# 0.00297%, SEBI fee Rs 10 per crore, GST 18% on transaction charge + SEBI fee.
CHARGES = {"stt": 0.001, "stamp_buy": 0.00015, "exchange": 0.0000297, "sebi": 0.000001, "gst": 0.18}


class RiskError(Exception):
    pass


# ------------------------------------------------ the order (same code in ticket.py)

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


# ------------------------------------------------ helpers

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def inr(x):
    x = float(x)
    sign = "-" if x < -0.004 else ""
    whole, frac = ("%.2f" % abs(x)).split(".")
    if len(whole) > 3:
        head, tail, parts = whole[:-3], whole[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return "%s₹%s%s" % (sign, whole, "" if frac == "00" else "." + frac)


def charges(value, side, product="CNC"):
    """Approximate statutory charges in rupees for one leg of an equity trade.
    Delivery (CNC): STT 0.1% both sides, stamp 0.015% on the buy.
    Intraday (MIS): STT 0.025% on the sell only, stamp 0.003% on the buy."""
    if not value:
        return 0.0
    c = value * CHARGES["exchange"] + value * CHARGES["sebi"]
    c += c * CHARGES["gst"]
    if product == "MIS":
        c += value * 0.00025 if side == "sell" else value * 0.00003
    else:
        c += value * CHARGES["stt"] + (value * CHARGES["stamp_buy"] if side == "buy" else 0.0)
    return round(c, 2)


def load_equity_list(path):
    """{SYMBOL: series} from NSE's EQUITY_L.csv, or None if not cached."""
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        rows = csv.reader(fh)
        head = [h.strip().upper() for h in next(rows, [])]
        if "SYMBOL" not in head or "SERIES" not in head:
            return None
        i, j = head.index("SYMBOL"), head.index("SERIES")
        return {r[i].strip().upper(): r[j].strip() for r in rows if len(r) > max(i, j)}


def account(cfg, portfolio, prices):
    warnings = []
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("no trader/portfolio.json and no budget in the config: nothing to check the orders against")
        return {"cash": budget, "shares": {}, "values": {}}, warnings
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json needs a \"cash\" number (rupees)")
    shares, values = {}, {}
    for p in portfolio.get("positions", []):
        t = str(p.get("ticker") or p.get("symbol") or "").upper()
        if not t:
            continue
        n = _f(p.get("shares", p.get("qty")))
        if n is None:
            n = sum(_f(l.get("shares")) or 0 for l in p.get("lots", []))
        shares[t] = shares.get(t, 0) + n
        if prices.get(t):
            values[t] = shares[t] * prices[t]
        else:
            basis = sum(_f(l.get("cost")) or 0 for l in p.get("lots", []))
            values[t] = basis
            warnings.append("no price for %s: valued at its cost %s" % (t, inr(basis)))
    return {"cash": cash, "shares": shares, "values": values}, warnings


def placed_today(state, today):
    return [r for r in (state or {}).get("orders", [])
            if r.get("status") == "placed" and str(r.get("date") or str(r.get("at", ""))[:10]) == today]


# ------------------------------------------------ the check

def check(orders, cfg, portfolio=None, prices=None, state=None, today=None, equity=None):
    c = dict(DEFAULTS)
    c.update(cfg or {})
    prices = {str(k).upper(): float(v) for k, v in (prices or {}).items() if _f(v)}
    today = today or datetime.date.today().isoformat()
    acct, top_warn = account(c, portfolio, prices)
    blocked = {str(b).upper() for b in c.get("blocked") or []}
    done = placed_today(state, today)
    placed_keys = {(str(r.get("symbol", "")).upper(), r.get("side")) for r in done}
    seen, results = set(), []
    for i, raw in enumerate(orders, 1):
        o = normalise(raw)
        reasons, warns = [], []
        sym, side, typ = o["symbol"], o["side"], o["type"]
        q = _f(o["qty"])
        if not sym:
            reasons.append("format: no symbol")
        if side not in ("buy", "sell"):
            reasons.append("format: side must be buy or sell, got %r" % raw.get("side"))
        if o["notional"] is not None:
            reasons.append("format: a rupee-amount order is not possible in India; give a whole number of shares "
                           "(size.py --whole-shares)")
        elif q is None:
            reasons.append("format: no qty")
        elif q <= 0 or abs(q - round(q)) > 1e-9:
            reasons.append("format: qty must be a whole number of shares, got %s" % o["qty"])
        if typ not in ("MARKET", "LIMIT", "SL", "SL-M"):
            reasons.append("format: unknown order type %r (market, limit, sl, sl-m)" % raw.get("type"))
        if typ in ("LIMIT", "SL") and _f(o["limit_price"]) is None:
            reasons.append("format: a %s order needs a limit_price" % typ)
        if typ in ("SL", "SL-M") and _f(o["stop_price"]) is None:
            reasons.append("format: a %s order needs a stop_price (trigger)" % typ)
        if typ == "SL" and _f(o["limit_price"]) and _f(o["stop_price"]):
            lp, tp = _f(o["limit_price"]), _f(o["stop_price"])
            if (side == "buy" and lp < tp) or (side == "sell" and lp > tp):
                reasons.append("format: for a %s SL order the limit price must be %s the trigger"
                               % (side, "at or above" if side == "buy" else "at or below"))
        if o["validity"] not in ("DAY", "IOC"):
            reasons.append("format: validity must be DAY or IOC, got %r" % o["validity"])
        if o["exchange"] not in ("NSE", "BSE"):
            reasons.append("format: exchange must be NSE or BSE, got %r" % o["exchange"])
        for k in ("limit_price", "stop_price"):
            if o[k] is not None and (_f(o[k]) is None or _f(o[k]) <= 0):
                reasons.append("format: %s must be a positive number" % k)

        # asset
        if sym and FNO.match(sym):
            if not c.get("allow_fno"):
                reasons.append("asset: %s looks like a futures or options contract; F&O is off (\"allow_fno\": false)" % sym)
            else:
                warns.append("F&O order: you can lose more than you put in")
        elif sym and not SYMBOL.match(sym):
            reasons.append("asset: %s does not look like an NSE / BSE trading symbol" % sym)
        elif sym and equity is not None and o["exchange"] == "NSE":
            series = equity.get(sym)
            if series is None:
                reasons.append("asset: %s is not on NSE's equity list (check with nse.py symbol)" % sym)
            elif series in ("BE", "BZ"):
                warns.append("%s trades in the %s (trade-for-trade) series: every trade is settled by delivery, "
                             "no intraday netting" % (sym, series))
            elif series in ("SM", "ST"):
                warns.append("%s is an SME stock: it trades in lots and can be hard to sell" % sym)

        # product
        if o["product"] == "MIS":
            if not c.get("allow_intraday"):
                reasons.append("product: MIS is intraday (squared off the same day, with leverage); this pack "
                               "buys for delivery (CNC) unless \"allow_intraday\": true")
            else:
                warns.append("intraday (MIS): place the stop-loss right after the fill; the broker squares it off "
                             "around 15:15-15:20, with an extra charge")
        elif o["product"] != "CNC":
            reasons.append("product: %s is not delivery (CNC); NRML/MTF use leverage or F&O" % o["product"])

        if sym in blocked:
            reasons.append("blocked: %s is on your blocked list" % sym)

        # value
        ref = _f(o["limit_price"]) if typ in ("LIMIT", "SL") else None
        if ref is None and typ == "SL-M":
            ref = _f(o["stop_price"])
        last = prices.get(sym)
        if ref is None:
            ref = last
        value = None
        if q and q > 0 and o["notional"] is None:
            if ref:
                value = q * ref
            else:
                reasons.append("price: no price for %s to size a %s-share order; add it to trader/prices.json "
                               "(nse.py quote %s --write)" % (sym, o["qty"], sym))
        if last and _f(o["limit_price"]) and abs(_f(o["limit_price"]) / last - 1) > 0.10:
            warns.append("limit %s is %.0f%% from the last price %s: check it (stocks have daily price bands "
                         "of 2-20%%)" % (inr(_f(o["limit_price"])), 100 * abs(_f(o["limit_price"]) / last - 1), inr(last)))
        if typ == "MARKET" and not any(w.startswith("market orders") for w in top_warn):
            top_warn.append("market orders: in a thinly traded stock one can fill well away from the last "
                            "price; a limit order caps the price")
        est = charges(value, side, o["product"]) if value else 0.0
        mis = o["product"] == "MIS"

        # the plan: a stop-loss on the right side, and the loss at the stop within your risk budget
        sl, tgt = _f(o["stop_loss"]), _f(o["target"])
        entry = ref
        if mis and sl is None:
            reasons.append("plan: every intraday (MIS) order needs a stop_loss; research.py adds one")
        if sl is not None and entry:
            if (side == "buy" and sl >= entry) or (side == "sell" and sl <= entry):
                reasons.append("plan: the stop_loss %s is on the wrong side of the entry %s" % (inr(sl), inr(entry)))
            elif q:
                at_risk = q * abs(entry - sl)
                total_now = acct["cash"] + sum(acct["values"].values())
                pct = c.get("intraday_risk_pct") if mis else c.get("risk_per_trade_pct")
                if pct and total_now > 0 and at_risk > total_now * float(pct) / 100.0 * 1.05:
                    reasons.append("plan: losing %s at the stop is %.2f%% of the account, over your %s%% %s risk "
                                   "per trade" % (inr(at_risk), 100.0 * at_risk / total_now, _pct(pct),
                                                  "intraday" if mis else "swing"))
                if tgt is not None and abs(entry - sl) > 0:
                    rr = abs(tgt - entry) / abs(entry - sl)
                    if rr < 1.5:
                        warns.append("target is only %.1fx the risk: after charges that is a thin edge" % rr)

        if value is not None:
            mn, mx = _f(c.get("min_order_inr")) or 0, _f(c.get("max_order_inr"))
            if value < mn - PAISE:
                reasons.append("size: %s is under your %s minimum order" % (inr(value), inr(mn)))
            if mx and value > mx + PAISE:
                reasons.append("size: %s is over your %s max order" % (inr(value), inr(mx)))

        cash = acct["cash"]
        held = acct["shares"].get(sym, 0.0)
        held_value = acct["values"].get(sym, 0.0)
        total = cash + sum(acct["values"].values())
        buffer_inr = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "buy":
            after = cash - value - est
            if after < -PAISE:
                reasons.append("cash: needs %s plus about %s charges but only %s cash" % (inr(value), inr(est), inr(cash)))
            elif after < buffer_inr - PAISE:
                reasons.append("cash: leaves %s cash, under your %s%% buffer of %s"
                               % (inr(max(after, 0)), _pct(c["cash_buffer_pct"]), inr(buffer_inr)))
            limit = total * float(c.get("max_position_pct") or 100) / 100.0
            if total > 0 and held_value + value > limit + PAISE:
                reasons.append("position: %s would be %s, %.1f%% of the %s account, over your %s%% cap"
                               % (sym, inr(held_value + value), 100.0 * (held_value + value) / total, inr(total),
                                  _pct(c["max_position_pct"])))
        if side == "sell" and sym and q and mis and c.get("allow_intraday") and c.get("allow_short_intraday"):
            if q > held + 1e-9:
                warns.append("intraday short: it must be bought back before the close; the broker squares it off "
                             "around 15:15-15:20, and a stock stuck at its upper circuit cannot be bought back")
        elif side == "sell" and sym and q:
            if q > held + 1e-9:
                reasons.append("short: selling %s %s but you hold %s; short selling in delivery is not possible"
                               % (o["qty"], sym, _pct(held)))

        key = (sym, side)
        if key in seen:
            reasons.append("duplicate: a second %s %s in this batch" % (side, sym))
        if key in placed_keys:
            reasons.append("duplicate: a %s %s was already placed today" % (side, sym))
        passed = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and len(done) + passed >= cap_n:
            reasons.append("daily limit: %d placed today plus %d passed earlier in this batch, your limit is %d"
                           % (len(done), passed, cap_n))
        seen.add(key)

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS" and value is not None:
            if side == "buy":
                acct["cash"] -= value + est
                acct["values"][sym] = held_value + value
                acct["shares"][sym] = held + q
            else:
                acct["cash"] += value - est
                acct["values"][sym] = max(held_value - value, 0.0)
                acct["shares"][sym] = held - q
        results.append({"index": i, "symbol": sym, "side": side, "exchange": o["exchange"],
                        "amount": "%s shares" % o["qty"] if o["qty"] else "rupee amount",
                        "value_inr": round(value, 2) if value is not None else None,
                        "est_charges_inr": est, "fingerprint": fingerprint(raw), "verdict": verdict,
                        "reasons": reasons, "warnings": warns})
    return {"date": today, "market": "IN", "currency": "INR", "results": results,
            "pass": sum(1 for r in results if r["verdict"] == "PASS"),
            "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
            "warnings": top_warn, "cash_after": round(acct["cash"], 2)}


def _pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x)


def report(res):
    lines = ["WARNING: " + w for w in res["warnings"]]
    if lines:
        lines.append("")
    for r in res["results"]:
        val = (" (%s, charges about %s)" % (inr(r["value_inr"]), inr(r["est_charges_inr"]))) if r["value_inr"] else ""
        lines.append("%-5s #%d %s %s %s on %s%s" % (r["verdict"], r["index"], r["side"], r["symbol"], r["amount"],
                                                  r["exchange"], val))
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    lines.append("")
    lines.append("%d passed, %d blocked. Cash after the passed orders and charges: %s."
                 % (res["pass"], res["block"], inr(res["cash_after"])))
    lines.append("Blocked orders do not go to the broker. Fix the order or your rules, then check again."
                 if res["block"] else "Every order passed. ticket.py still asks you yes or no for each one.")
    lines.append("Charges are estimates of STT, stamp duty, exchange and SEBI fees and GST, before brokerage. "
                 "Education and tooling, not financial advice.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check proposed Indian orders against your limits (check.py).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--state", default="trader/state/broker.json")
    ap.add_argument("--equity-list", default="trader/cache/EQUITY_L.csv",
                    help="NSE's symbol list (nse.py caches it); skipped if missing")
    ap.add_argument("--today")
    ap.add_argument("--out", default="trader/state/risk.json")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        doc = read_json(a.orders, "orders file (run size.py --whole-shares or capgains.py first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        cfg = read_json(a.config, "config", required=False) or {}
        portfolio = read_json(a.portfolio, "portfolio", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        if pdoc.get("currency") and pdoc["currency"] != "INR":
            raise RiskError("%s holds %s prices; Indian orders need rupee prices (nse.py)"
                            % (a.prices, pdoc["currency"]))
        prices = pdoc.get("prices", pdoc) if isinstance(pdoc, dict) else {}
        state = read_json(a.state, "broker state", required=False)
        res = check(orders, cfg, portfolio, prices, state, a.today, load_equity_list(a.equity_list))
        if pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res, checked_at=datetime.datetime.now().replace(microsecond=0).isoformat(), orders_file=a.orders)
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
