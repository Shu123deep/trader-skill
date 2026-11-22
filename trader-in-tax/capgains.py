#!/usr/bin/env python3
"""Indian capital gains on listed shares for trader-in-tax. Standard library only, no network.

Not tax advice: it does arithmetic on the lots you give it under the rules
below. A chartered accountant decides what applies to you.

    python3 capgains.py report                      # unrealized gains per stock and lot, short or long term
    python3 capgains.py sell RELIANCE 10 --price 1377.50          # what selling 10 would realize (FIFO)
    python3 capgains.py sell RELIANCE 10 --price 1377.50 --record # it filled: update lots + realized log
    python3 capgains.py fy                          # this financial year: set-off, exemption, estimated tax
    python3 capgains.py harvest                     # loss harvesting: losses that cut this year's tax
    python3 capgains.py harvest --gains             # gain harvesting: use the LTCG exemption left this year
    python3 capgains.py harvest --gains --write     # write the sells to trader/orders.json as proposals

Lots live in trader/portfolio.json, cost in rupees including brokerage and
charges you paid to buy (not STT):

    {"cash": 25000, "positions": [{"ticker": "RELIANCE", "shares": 20, "lots": [
        {"id": "RELIANCE-1", "date": "2025-06-02", "shares": 20, "cost": 28920.0}]}]}

For a lot bought before 1 Feb 2018 add "fmv_2018_01_31": the highest price
quoted on 31 Jan 2018, per share (grandfathering, section 112A).

The rules it applies (listed equity shares and equity-oriented funds on which
STT was paid; sections 111A, 112A and 70-74 of the Income-tax Act, as amended
by the Finance (No. 2) Act, 2024):

  lot order     FIFO, oldest first. For shares held in demat the first-in
                first-out rule applies (CBDT Circular 768); you cannot pick lots.
  term          long term if held for MORE than 12 months, otherwise short term
  rates         sold on or after 23 Jul 2024: STCG 20%, LTCG 12.5%
                sold before 23 Jul 2024:      STCG 15%, LTCG 10%
                plus surcharge if your income is high, and 4% health and
                education cess on the tax (shown separately)
  exemption     the first Rs 1,25,000 of LTCG in a financial year is exempt
                (Rs 1,00,000 before FY 2024-25); it is per person, per year,
                across all your accounts
  set-off       a short-term loss can be set off against short- or long-term
                gains; a long-term loss only against long-term gains; what is
                left carries forward for 8 years if you file your return on time
  grandfather   for shares bought before 1 Feb 2018 the cost is the higher of
                the actual cost and the lower of (31 Jan 2018 FMV, sale price)
  financial yr  1 April to 31 March ("FY 2026-27" runs 1 Apr 2026 to 31 Mar 2027)

India has no wash-sale rule. Selling for a loss and buying straight back can
still be questioned as a sham transaction; the proposals say so.
"""

import argparse
import datetime
import json
import os
import sys

EPS = 1e-9
NEW_RATES_FROM = datetime.date(2024, 7, 23)
GRANDFATHER_BEFORE = datetime.date(2018, 2, 1)
CESS = 0.04


class TaxError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TaxError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TaxError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def day(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        raise TaxError("not a date (YYYY-MM-DD): %r" % s)


def inr(x):
    """Rupees with Indian digit grouping: -12,34,567.89."""
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
    return "%s₹%s.%s" % (sign, whole, frac)


def qty(x):
    s = ("%.6f" % float(x)).rstrip("0").rstrip(".")
    return s or "0"


def fy_start(d):
    """The year the financial year containing d starts in (FY 2026-27 -> 2026)."""
    return d.year if d.month >= 4 else d.year - 1


def fy_label(start):
    return "FY %d-%02d" % (start, (start + 1) % 100)


def fy_bounds(start):
    return datetime.date(start, 4, 1), datetime.date(start + 1, 3, 31)


def rates_on(sold):
    """(stcg_rate, ltcg_rate) for a sale on this date."""
    return (0.20, 0.125) if sold >= NEW_RATES_FROM else (0.15, 0.10)


def exemption_for(start):
    return 125000.0 if start >= 2024 else 100000.0


# ---------------------------------------------------------------- lots

def lots_of(portfolio):
    out = {}
    for p in (portfolio or {}).get("positions", []):
        t = str(p.get("ticker") or p.get("symbol") or "").upper()
        if not t:
            continue
        lots = []
        for i, l in enumerate(p.get("lots", []), 1):
            sh, cost = float(l.get("shares", 0)), float(l.get("cost", 0))
            if sh <= EPS:
                continue
            fmv = l.get("fmv_2018_01_31")
            lots.append({"id": l.get("id") or "%s-%d" % (t, i), "date": day(l.get("date")), "shares": sh,
                         "cost": cost, "per_share": cost / sh,
                         "fmv": float(fmv) if fmv not in (None, "") else None})
        if not lots and float(p.get("shares", 0) or 0) > 0:
            raise TaxError("%s has shares but no lots; add its lots (date, shares, cost) to "
                           "trader/portfolio.json" % t)
        lots.sort(key=lambda l: (l["date"], l["id"]))
        out[t] = lots
    return out


def anniversary(d):
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return d.replace(year=d.year + 1, day=28)


def term(acquired, sold):
    """long if held for more than 12 months."""
    return "long" if sold > anniversary(acquired) else "short"


def basis_per_share(lot, price, sold):
    """Cost per share for the gain, with grandfathering for pre-1-Feb-2018 lots."""
    c = lot["per_share"]
    if lot["date"] < GRANDFATHER_BEFORE and lot.get("fmv") and term(lot["date"], sold) == "long":
        return max(c, min(lot["fmv"], price))
    return c


def unrealized(portfolio, prices, today):
    rows, totals, missing = [], [], []
    for t, lots in sorted(lots_of(portfolio).items()):
        p = prices.get(t)
        if not p:
            missing.append(t)
            continue
        tot = {"ticker": t, "shares": 0.0, "cost": 0.0, "value": 0.0, "short": 0.0, "long": 0.0}
        for l in lots:
            value = l["shares"] * p
            g = value - basis_per_share(l, p, today) * l["shares"]
            tm = term(l["date"], today)
            rows.append({"ticker": t, "id": l["id"], "date": l["date"].isoformat(), "shares": l["shares"],
                         "cost": round(l["cost"], 2), "price": p, "value": round(value, 2), "gain": round(g, 2),
                         "term": tm, "long_term_on": (anniversary(l["date"]) + datetime.timedelta(days=1)).isoformat(),
                         "grandfathered": bool(l["date"] < GRANDFATHER_BEFORE and l.get("fmv"))})
            tot["shares"] += l["shares"]
            tot["cost"] += l["cost"]
            tot["value"] += value
            tot[tm] += g
        tot["gain"] = round(tot["short"] + tot["long"], 2)
        tot["pct"] = round(100.0 * (tot["value"] - tot["cost"]) / tot["cost"], 2) if tot["cost"] else 0.0
        for k in ("cost", "value", "short", "long"):
            tot[k] = round(tot[k], 2)
        totals.append(tot)
    return rows, totals, missing


def simulate_sale(lots, shares, price, sold, expenses=0.0):
    """FIFO sale. expenses (brokerage etc. on the sale, not STT) cut the gain pro rata."""
    held = sum(l["shares"] for l in lots)
    if shares > held + EPS:
        raise TaxError("selling %s shares but only %s are held" % (qty(shares), qty(held)))
    used, left = [], shares
    res = {"proceeds": 0.0, "basis": 0.0, "short": 0.0, "long": 0.0}
    st_rate, lt_rate = rates_on(sold)
    for l in lots:
        if left <= EPS:
            break
        take = min(left, l["shares"])
        left -= take
        b = basis_per_share(l, price, sold) * take
        proceeds = price * take
        exp = expenses * take / shares if shares else 0.0
        gain = proceeds - exp - b
        tm = term(l["date"], sold)
        used.append({"id": l["id"], "acquired": l["date"].isoformat(), "shares": round(take, 6),
                     "basis": round(b, 2), "proceeds": round(proceeds, 2), "expenses": round(exp, 2),
                     "gain": round(gain, 2), "term": tm, "rate": st_rate if tm == "short" else lt_rate,
                     "grandfathered": bool(l["date"] < GRANDFATHER_BEFORE and l.get("fmv"))})
        res["proceeds"] += proceeds
        res["basis"] += b
        res[tm] += gain
    res = {k: round(v, 2) for k, v in res.items()}
    res["expenses"] = round(expenses, 2)
    res["gain"] = round(res["short"] + res["long"], 2)
    res["lots"] = used
    return res


# ---------------------------------------------------------------- the year's tax

def realized_in_fy(state, start):
    lo, hi = fy_bounds(start)
    out = []
    for r in (state or {}).get("realized", []):
        d = day(r.get("date"))
        if lo <= d <= hi:
            out.append(r)
    return out


def buckets_from(realized):
    """[{term, rate, amount}] gains (+) and losses (-) by term and rate."""
    b = {}
    for r in realized:
        for u in r.get("lots", []):
            k = (u["term"], float(u.get("rate") or rates_on(day(r["date"]))[0 if u["term"] == "short" else 1]))
            b[k] = b.get(k, 0.0) + float(u["gain"])
    return [{"term": k[0], "rate": k[1], "amount": v} for k, v in b.items()]


def fy_tax(realized, start, brought_forward=None, extra_sale=None, sold=None):
    """Set-off, exemption and estimated tax for one financial year.

    realized: recorded sales in the FY. extra_sale: a hypothetical sale (the
    simulate_sale result) on date `sold`, included as if it happened. Losses
    and the exemption go against the highest-rate gains first."""
    rows = list(realized)
    if extra_sale:
        rows.append({"date": sold.isoformat(), "lots": extra_sale["lots"]})
    bks = buckets_from(rows)
    st_gain = [b for b in bks if b["term"] == "short" and b["amount"] > 0]
    lt_gain = [b for b in bks if b["term"] == "long" and b["amount"] > 0]
    st_loss = -sum(b["amount"] for b in bks if b["term"] == "short" and b["amount"] < 0)
    lt_loss = -sum(b["amount"] for b in bks if b["term"] == "long" and b["amount"] < 0)
    bf = brought_forward or {}
    bf_st, bf_lt = float(bf.get("short", 0) or 0), float(bf.get("long", 0) or 0)
    notes = []

    def absorb(gains, loss):
        for g in sorted(gains, key=lambda x: -x["rate"]):
            use = min(g["amount"], loss)
            g["amount"] -= use
            loss -= use
        return loss

    # this year's losses first, then losses brought forward
    lt_loss = absorb(lt_gain, lt_loss)
    st_loss = absorb(st_gain, st_loss)
    st_loss = absorb(lt_gain, st_loss)
    bf_st_left = absorb(lt_gain, absorb(st_gain, bf_st))
    bf_lt_left = absorb(lt_gain, bf_lt)
    exemption = exemption_for(start)
    lt_before_ex = sum(g["amount"] for g in lt_gain)
    ex_used = min(exemption, lt_before_ex)
    absorb(lt_gain, ex_used)
    tax = sum(g["amount"] * g["rate"] for g in st_gain + lt_gain)
    if any(g["rate"] in (0.15, 0.10) for g in st_gain + lt_gain) and any(g["rate"] in (0.20, 0.125) for g in st_gain + lt_gain):
        notes.append("FY 2024-25 mixes old and new rates: losses and the exemption were set against the "
                     "higher-rate gains first")
    return {
        "fy": fy_label(start), "exemption": exemption,
        "stcg_taxable": round(sum(g["amount"] for g in st_gain), 2),
        "ltcg_before_exemption": round(lt_before_ex, 2), "exemption_used": round(ex_used, 2),
        "exemption_left": round(exemption - ex_used, 2),
        "ltcg_taxable": round(sum(g["amount"] for g in lt_gain), 2),
        "tax": round(tax, 2), "cess": round(tax * CESS, 2), "tax_with_cess": round(tax * (1 + CESS), 2),
        "carry_forward": {"short": round(st_loss + bf_st_left, 2), "long": round(lt_loss + bf_lt_left, 2)},
        "notes": notes,
    }


# ---------------------------------------------------------------- harvesting

def best_loss_prefix(lots, price, today):
    """Under FIFO you sell the oldest lots first. The number of shares (whole
    lots, oldest first) that realizes the biggest loss, or None."""
    best, cum, shares = None, 0.0, 0.0
    for l in lots:
        cum += (price - basis_per_share(l, price, today)) * l["shares"]
        shares += l["shares"]
        if cum < -0.005 and (best is None or cum < best[1]):
            best = (shares, cum)
    return best


def loss_candidates(portfolio, prices, today, state, brought_forward, min_loss=1000.0):
    start = fy_start(today)
    realized = realized_in_fy(state, start)
    base = fy_tax(realized, start, brought_forward)
    out = []
    for t, lots in sorted(lots_of(portfolio).items()):
        p = prices.get(t)
        if not p:
            continue
        best = best_loss_prefix(lots, p, today)
        if not best or -best[1] < min_loss:
            continue
        shares = best[0]
        sale = simulate_sale(lots, shares, p, today)
        after = fy_tax(realized, start, brought_forward, sale, today)
        out.append({"ticker": t, "price": p, "shares": shares, "lots": [u["id"] for u in sale["lots"]],
                    "all_shares": abs(shares - sum(l["shares"] for l in lots)) < EPS,
                    "loss": sale["gain"], "short": sale["short"], "long": sale["long"],
                    "tax_saved_this_fy": round(base["tax_with_cess"] - after["tax_with_cess"], 2),
                    "carry_forward_after": after["carry_forward"]})
    out.sort(key=lambda c: (-c["tax_saved_this_fy"], c["loss"]))
    return out, base


def gain_candidates(portfolio, prices, today, state, brought_forward):
    """Long-term gains that fit in the exemption left this FY, whole shares,
    oldest lots first. Selling and buying back resets your cost higher."""
    start = fy_start(today)
    realized = realized_in_fy(state, start)
    base = fy_tax(realized, start, brought_forward)
    room = base["exemption_left"]
    out = []
    for t, lots in sorted(lots_of(portfolio).items()):
        if room <= 1:
            break
        p = prices.get(t)
        if not p:
            continue
        shares, gain = 0, 0.0
        for l in lots:                       # FIFO: stop at the first lot that is not long term with a gain
            per = p - basis_per_share(l, p, today)
            if term(l["date"], today) != "long" or per <= 0:
                break
            n = int(min(l["shares"], (room - gain) // per))
            shares += n
            gain += n * per
            if n < l["shares"]:
                break
        if shares <= 0:
            continue
        sale = simulate_sale(lots, shares, p, today)
        if sale["short"] < -EPS or sale["short"] > EPS:
            continue
        room -= sale["long"]
        out.append({"ticker": t, "price": p, "shares": shares, "lots": [u["id"] for u in sale["lots"]],
                    "ltcg": sale["long"], "new_cost_if_rebought": round(shares * p, 2)})
    return out, base


def orders_from(cands, kind):
    out = []
    for c in cands:
        if kind == "loss":
            why = ("tax-loss harvest proposal: sell %s %s (FIFO lots %s) to realize %s (%s short term, %s long "
                   "term), estimated tax saved this year %s. India has no wash-sale rule, but buying straight "
                   "back can be questioned as a sham sale. Not tax advice."
                   % (qty(c["shares"]), c["ticker"], ", ".join(c["lots"]), inr(c["loss"]), inr(c["short"]),
                      inr(c["long"]), inr(c["tax_saved_this_fy"])))
        else:
            why = ("tax-gain harvest proposal: sell %s %s (FIFO lots %s) to realize %s of long-term gain inside "
                   "this year's exemption. If you buy back, do it on the next trading day, not the same day "
                   "(a same-day sell and buy can be treated as intraday), and the new shares start a fresh "
                   "12-month clock. Not tax advice." % (qty(c["shares"]), c["ticker"], ", ".join(c["lots"]),
                                                        inr(c["ltcg"])))
        out.append({"symbol": c["ticker"], "exchange": "NSE", "side": "sell", "qty": qty(c["shares"]),
                    "type": "market", "product": "CNC", "validity": "DAY", "why": why})
    return out


# ---------------------------------------------------------------- record a sale

def record_sale(portfolio, ticker, sale, sold, price, state):
    t = ticker.upper()
    taken = {u["id"]: u for u in sale["lots"]}
    for p in portfolio.get("positions", []):
        if str(p.get("ticker") or p.get("symbol") or "").upper() != t:
            continue
        new = []
        for i, l in enumerate(p.get("lots", []), 1):
            lid = l.get("id") or "%s-%d" % (t, i)
            l["id"] = lid
            if lid in taken:
                sh = float(l["shares"])
                take = taken[lid]["shares"]
                l["cost"] = round(float(l["cost"]) * (1 - take / sh), 2) if sh else 0.0
                l["shares"] = round(sh - take, 6)
            if float(l["shares"]) > EPS:
                new.append(l)
        p["lots"] = new
        p["shares"] = round(sum(float(l["shares"]) for l in new), 6)
    portfolio["positions"] = [p for p in portfolio.get("positions", []) if float(p.get("shares", 0)) > EPS]
    portfolio["cash"] = round(float(portfolio.get("cash", 0)) + sale["proceeds"] - sale.get("expenses", 0), 2)
    state.setdefault("realized", []).append({
        "date": sold.isoformat(), "symbol": t, "shares": round(sum(u["shares"] for u in sale["lots"]), 6),
        "price": price, "proceeds": sale["proceeds"], "basis": sale["basis"], "expenses": sale.get("expenses", 0),
        "gain": sale["gain"], "short": sale["short"], "long": sale["long"], "lots": sale["lots"]})
    return portfolio, state


# ---------------------------------------------------------------- CLI

NOT_ADVICE = ("Not tax advice: arithmetic on your lots under sections 111A/112A as amended in 2024. The "
              "Rs 1.25 lakh exemption is per person across all your accounts, surcharge is not included, and "
              "rules change in each Budget. Check with a chartered accountant before you act.")


def _inputs(a):
    portfolio = read_json(a.portfolio, "portfolio (lots)")
    pdoc = read_json(a.prices, "prices", required=False) or {}
    if pdoc.get("currency") and pdoc["currency"] != "INR":
        raise TaxError("%s holds %s prices; this tool works in rupees" % (a.prices, pdoc["currency"]))
    prices = {str(k).upper(): float(v) for k, v in (pdoc.get("prices", pdoc) or {}).items()
              if isinstance(v, (int, float))}
    state = read_json(a.state, "tax state", required=False) or {"realized": []}
    cfg = read_json(a.config, "config", required=False) or {}
    bf = cfg.get("carry_forward_losses") or {}
    today = day(a.today) if a.today else datetime.date.today()
    return portfolio, prices, state, bf, today


def cmd_report(a):
    portfolio, prices, state, bf, today = _inputs(a)
    rows, totals, missing = unrealized(portfolio, prices, today)
    if a.json:
        print(json.dumps({"lots": rows, "positions": totals, "missing_prices": missing,
                          "fy": fy_tax(realized_in_fy(state, fy_start(today)), fy_start(today), bf)},
                         indent=2, ensure_ascii=False))
        return 0
    print("%-12s %8s %15s %15s %14s %14s" % ("symbol", "shares", "cost", "value", "short-term", "long-term"))
    for t in totals:
        print("%-12s %8s %15s %15s %14s %14s" % (t["ticker"], qty(t["shares"]), inr(t["cost"]), inr(t["value"]),
                                                 inr(t["short"]), inr(t["long"])))
    if a.lots:
        print("")
        print("%-14s %-11s %8s %14s %14s %6s %s" % ("lot", "bought", "shares", "cost", "gain", "term", "long term from"))
        for r in rows:
            print("%-14s %-11s %8s %14s %14s %6s %s%s" % (r["id"], r["date"], qty(r["shares"]), inr(r["cost"]),
                                                          inr(r["gain"]), r["term"], r["long_term_on"],
                                                          "  (grandfathered)" if r["grandfathered"] else ""))
    for t in missing:
        print("No price for %s: add it to trader/prices.json (trader-in-alerts: nse.py quote %s --write)" % (t, t))
    f = fy_tax(realized_in_fy(state, fy_start(today)), fy_start(today), bf)
    print("")
    print("%s so far (recorded sales): STCG taxable %s, LTCG %s of which %s exempt; LTCG exemption left %s; "
          "estimated tax %s incl. 4%% cess." % (f["fy"], inr(f["stcg_taxable"]), inr(f["ltcg_before_exemption"]),
                                               inr(f["exemption_used"]), inr(f["exemption_left"]),
                                               inr(f["tax_with_cess"])))
    print(NOT_ADVICE)
    return 0


def cmd_sell(a):
    portfolio, prices, state, bf, today = _inputs(a)
    t = a.ticker.upper()
    lots = lots_of(portfolio).get(t)
    if not lots:
        raise TaxError("no lots for %s in %s" % (t, a.portfolio))
    price = a.price or prices.get(t)
    if not price:
        raise TaxError("no price for %s: pass --price" % t)
    sale = simulate_sale(lots, float(a.shares), float(price), today, a.expenses or 0.0)
    start = fy_start(today)
    realized = realized_in_fy(state, start)
    before = fy_tax(realized, start, bf)
    after = fy_tax(realized, start, bf, sale, today)
    sale["fy_before"], sale["fy_after"] = before, after
    sale["extra_tax"] = round(after["tax_with_cess"] - before["tax_with_cess"], 2)
    if a.json:
        print(json.dumps(sale, indent=2, ensure_ascii=False))
    else:
        print("Selling %s %s at %s on %s (FIFO):" % (qty(a.shares), t, inr(price), today))
        for u in sale["lots"]:
            print("  lot %-12s bought %s  %s shares  cost %s  proceeds %s  %s (%s term, %g%%)%s"
                  % (u["id"], u["acquired"], qty(u["shares"]), inr(u["basis"]), inr(u["proceeds"]),
                     inr(u["gain"]), u["term"], u["rate"] * 100, "  grandfathered cost" if u["grandfathered"] else ""))
        print("Total: proceeds %s, %s %s (%s short term, %s long term)."
              % (inr(sale["proceeds"]), "gain" if sale["gain"] >= 0 else "loss", inr(sale["gain"]),
                 inr(sale["short"]), inr(sale["long"])))
        print("%s with this sale: STCG taxable %s, LTCG taxable %s after the %s exemption (%s of it left). "
              "Estimated tax %s incl. cess, %s more than without this sale."
              % (after["fy"], inr(after["stcg_taxable"]), inr(after["ltcg_taxable"]), inr(after["exemption"]),
                 inr(after["exemption_left"]), inr(after["tax_with_cess"]), inr(sale["extra_tax"])))
        if any(u["term"] == "short" for u in sale["lots"]):
            nxt = min(day(u["acquired"]) for u in sale["lots"] if u["term"] == "short")
            print("Some shares are short term. Lot bought %s turns long term on %s."
                  % (nxt, anniversary(nxt) + datetime.timedelta(days=1)))
        for n in after["notes"]:
            print("Note: " + n)
        print(NOT_ADVICE)
    if a.record:
        portfolio, state = record_sale(portfolio, t, sale, today, float(price), state)
        write_json(a.portfolio, portfolio)
        write_json(a.state, state)
        if not a.json:
            print("Recorded: lots updated in %s, sale logged in %s." % (a.portfolio, a.state))
    return 0


def cmd_fy(a):
    portfolio, prices, state, bf, today = _inputs(a)
    start = int(a.year) if a.year else fy_start(today)
    realized = realized_in_fy(state, start)
    f = fy_tax(realized, start, bf)
    if a.json:
        print(json.dumps(dict(f, sales=len(realized)), indent=2, ensure_ascii=False))
        return 0
    lo, hi = fy_bounds(start)
    print("%s (%s to %s): %d recorded sale(s)." % (f["fy"], lo, hi, len(realized)))
    print("  STCG taxable after set-off:   %s" % inr(f["stcg_taxable"]))
    print("  LTCG before the exemption:    %s" % inr(f["ltcg_before_exemption"]))
    print("  exemption used / left:        %s / %s (of %s)" % (inr(f["exemption_used"]), inr(f["exemption_left"]),
                                                              inr(f["exemption"])))
    print("  LTCG taxable:                 %s" % inr(f["ltcg_taxable"]))
    print("  estimated tax:                %s + 4%% cess %s = %s (surcharge not included)"
          % (inr(f["tax"]), inr(f["cess"]), inr(f["tax_with_cess"])))
    cf = f["carry_forward"]
    if cf["short"] > 0 or cf["long"] > 0:
        print("  losses to carry forward:      %s short term, %s long term (8 years, if you file on time)"
              % (inr(cf["short"]), inr(cf["long"])))
    for n in f["notes"]:
        print("Note: " + n)
    print(NOT_ADVICE)
    return 0


def cmd_harvest(a):
    portfolio, prices, state, bf, today = _inputs(a)
    if a.gains:
        cands, base = gain_candidates(portfolio, prices, today, state, bf)
        props = orders_from(cands, "gain")
    else:
        cands, base = loss_candidates(portfolio, prices, today, state, bf, a.min_loss)
        props = orders_from(cands, "loss")
    if a.write and props:
        write_json(a.write, {
            "created_by": "trader-in-tax", "market": "IN", "currency": "INR",
            "created_at": datetime.datetime.now().replace(microsecond=0).isoformat(), "status": "proposed",
            "note": ("Tax harvesting proposals, not tax advice. Nothing is placed until trader-in-risk passes "
                     "each order and you say yes to it in the chat."),
            "orders": props})
    if a.json:
        print(json.dumps({"fy": base, "candidates": cands, "orders": props}, indent=2, ensure_ascii=False))
        return 0 if cands else 1
    print("%s so far: STCG taxable %s, LTCG %s, exemption left %s, estimated tax %s incl. cess."
          % (base["fy"], inr(base["stcg_taxable"]), inr(base["ltcg_before_exemption"]),
             inr(base["exemption_left"]), inr(base["tax_with_cess"])))
    if not cands:
        print("No long-term gains fit the exemption left this year." if a.gains
              else "No position has a FIFO loss of at least %s." % inr(a.min_loss))
        print(NOT_ADVICE)
        return 1
    for c in cands:
        if a.gains:
            print("%-12s sell %s at %s (lots %s): %s long-term gain, inside the exemption."
                  % (c["ticker"], qty(c["shares"]), inr(c["price"]), ", ".join(c["lots"]), inr(c["ltcg"])))
        else:
            print("%-12s sell %s at %s (FIFO lots %s): loss %s (%s short term, %s long term), tax saved this "
                  "year %s%s" % (c["ticker"], qty(c["shares"]), inr(c["price"]), ", ".join(c["lots"]),
                                 inr(c["loss"]), inr(c["short"]), inr(c["long"]), inr(c["tax_saved_this_fy"]),
                                 "" if c["tax_saved_this_fy"] > 0 else " (no gains to set off yet; the loss "
                                 "carries forward)"))
    if a.gains:
        print("Buy back on the next trading day, not the same day. The new shares start a fresh 12-month clock.")
    if a.write:
        print("Wrote %d proposed sell order(s) to %s. trader-in-risk checks them next." % (len(props), a.write))
    print(NOT_ADVICE)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Indian capital gains on listed shares (trader-in-tax). Not tax advice.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--prices", default="trader/prices.json")
        p.add_argument("--state", default="trader/state/tax.json")
        p.add_argument("--config", default="trader/config.json",
                       help='optional "carry_forward_losses": {"short": 0, "long": 0}')
        p.add_argument("--today", help="YYYY-MM-DD (default: today)")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("report", help="unrealized gains and losses")
    common(p)
    p.add_argument("--lots", action="store_true")
    p = sub.add_parser("sell", help="what a sale would realize and cost in tax")
    common(p)
    p.add_argument("ticker")
    p.add_argument("shares", type=float)
    p.add_argument("--price", type=float)
    p.add_argument("--expenses", type=float, help="brokerage and charges on the sale in rupees (not STT)")
    p.add_argument("--record", action="store_true")
    p = sub.add_parser("fy", help="this financial year's gains, set-off and estimated tax")
    common(p)
    p.add_argument("--year", help="start year of the FY, e.g. 2025 for FY 2025-26")
    p = sub.add_parser("harvest", help="loss harvesting, or --gains for the LTCG exemption")
    common(p)
    p.add_argument("--gains", action="store_true", help="use the LTCG exemption left this year")
    p.add_argument("--min-loss", type=float, default=1000.0, help="rupees (default 1000)")
    p.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"report": cmd_report, "sell": cmd_sell, "fy": cmd_fy, "harvest": cmd_harvest}[a.cmd](a)
    except TaxError as e:
        print("capgains.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
