#!/usr/bin/env python3
"""NSE bulk and block deals for deals.py (India). Standard library only.

    python3 deals.py today                      # the day's deals, biggest first, intraday churn hidden
    python3 deals.py today --all                # include same-day buy+sell churn
    python3 deals.py watch                      # new deals by your investors or in your holdings; exit 0 if any
    python3 deals.py investor kacholia          # every deal by that investor in the local ledger
    python3 deals.py investors                  # the watchlist (investors.json in this folder + trader/)
    python3 deals.py symbol GALLANTT            # every deal in one stock in the ledger

What the deals are (SEBI / NSE rules):
  bulk deal   one client's trades in a stock on one day add up to more than
              0.5% of the company's listed shares; the broker reports it and
              NSE publishes it the same evening
  block deal  a single trade of at least Rs 10 crore in the special block
              deal windows (08:45-09:00 and 14:05-14:20 IST)

Sources (NSE's public archive, no login):
  https://nsearchives.nseindia.com/content/equities/bulk.csv
  https://nsearchives.nseindia.com/content/equities/block.csv
Both files hold the LATEST trading day only, so `watch` keeps every deal it
sees in trader/state/deals.json. Run it each evening and the ledger becomes
your history. --offline DIR reads DIR/bulk.csv and DIR/block.csv.

A client that both bought and sold the same stock on the same day is marked
"intraday": usually a market maker or high-frequency firm, not an investor
taking a position. They are hidden unless you pass --all.
"""

import argparse
import csv
import datetime
import gzip
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

UA = "trader-skill-india/1.0"   # NSE's archive stalls a User-Agent that contains a URL
URLS = {"bulk": "https://nsearchives.nseindia.com/content/equities/bulk.csv",
        "block": "https://nsearchives.nseindia.com/content/equities/block.csv"}
HERE = os.path.dirname(os.path.abspath(__file__))
CRORE = 1e7


class DealError(Exception):
    pass


def fetch(url, timeout=30, tries=2):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"})
    last = None
    for attempt in range(tries):
        if attempt:
            time.sleep(2)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
            return data.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise DealError("%s answered HTTP %d" % (url, e.code))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = getattr(e, "reason", e)
    raise DealError("could not reach %s: %s" % (url.split("/")[2], last))


def crore(x):
    return "₹%s Cr" % ("{:,.2f}".format(x / CRORE))


def _num(s):
    return float(str(s).replace(",", "").strip() or 0)


def parse_deals(text, kind):
    """Rows of NSE's bulk.csv / block.csv -> deal dicts."""
    rows = list(csv.reader(io.StringIO(text.strip())))
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]

    def col(*names):
        for n in names:
            for i, h in enumerate(header):
                if h.startswith(n):
                    return i
        raise DealError("%s.csv has no %s column (columns: %s)" % (kind, names[0], ", ".join(header)))
    ci = {"date": col("date"), "symbol": col("symbol"), "name": col("security name"), "client": col("client name"),
          "side": col("buy/sell", "buy / sell"), "qty": col("quantity"), "price": col("trade price", "wght")}
    out = []
    for r in rows[1:]:
        if len(r) <= max(ci.values()) or not r[ci["symbol"]].strip():
            continue
        try:
            d = datetime.datetime.strptime(r[ci["date"]].strip(), "%d-%b-%Y").date()
        except ValueError:
            continue
        q, p = _num(r[ci["qty"]]), _num(r[ci["price"]])
        deal = {"date": d.isoformat(), "kind": kind, "symbol": r[ci["symbol"]].strip().upper(),
                "company": r[ci["name"]].strip(), "client": " ".join(r[ci["client"]].split()).upper(),
                "side": "buy" if r[ci["side"]].strip().upper().startswith("B") else "sell",
                "qty": int(q), "price": p, "value": round(q * p, 2)}
        key = "|".join(str(deal[k]) for k in ("date", "kind", "symbol", "client", "side", "qty", "price"))
        deal["id"] = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        out.append(deal)
    return out


def mark_intraday(deals):
    sides = {}
    for d in deals:
        sides.setdefault((d["date"], d["symbol"], d["client"]), set()).add(d["side"])
    for d in deals:
        d["intraday"] = sides[(d["date"], d["symbol"], d["client"])] == {"buy", "sell"}
    return deals


def load_day(offline=None):
    deals, errors = [], []
    for kind, url in URLS.items():
        try:
            if offline:
                path = os.path.join(offline, "%s.csv" % kind)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            else:
                text = fetch(url)
            deals += parse_deals(text, kind)
        except (DealError, OSError) as e:
            errors.append(str(e))
    return mark_intraday(merge_kinds(deals)), errors


def merge_kinds(deals):
    """A block trade over 0.5% of the company is reported in both files: keep one, kind "block+bulk"."""
    seen, out = {}, []
    for d in deals:
        k = (d["date"], d["symbol"], d["client"], d["side"], d["qty"], d["price"])
        if k in seen:
            first = seen[k]
            if d["kind"] not in first["kind"]:
                first["kind"] = "block+bulk"
            continue
        seen[k] = d
        out.append(d)
    return out


# ---------------------------------------------------------------- watchlist

def _read(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise DealError("%s is not valid JSON: %s" % (path, e))


def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def load_investors(user_path):
    """The built-in list plus the user's own; the user's entries win by name."""
    base = _read(os.path.join(HERE, "investors.json"), {}).get("investors", [])
    mine = (_read(user_path, {}) or {}).get("investors", [])
    by = {i["name"]: i for i in base}
    for i in mine:
        by[i["name"]] = i
    return list(by.values())


def tokens(s):
    return set("".join(c if c.isalnum() else " " for c in s.upper()).split())


def match_investor(client, investors):
    """The investor whose alias tokens are all in the client name, or None."""
    t = tokens(client)
    for inv in investors:
        for alias in inv.get("match", []):
            want = tokens(alias)
            if want and want <= t:
                return inv["name"]
    return None


def holdings(path):
    pf = _read(path, {}) or {}
    return {str(p.get("ticker") or p.get("symbol") or "").upper() for p in pf.get("positions", [])} - {""}


# ---------------------------------------------------------------- output

def line(d, who=None):
    return "%s %-10s %-4s %-12s %-44s %12s @ ₹%-10s %s%s%s" % (
        d["date"], d["kind"], d["side"].upper(), d["symbol"], d["client"][:44], "{:,}".format(d["qty"]),
        "{:,.2f}".format(d["price"]), crore(d["value"]), ("  [%s]" % who) if who else "",
        "  (intraday)" if d.get("intraday") else "")


def cmd_today(a):
    deals, errors = load_day(a.offline)
    shown = [d for d in deals if a.all or not d["intraday"]]
    shown.sort(key=lambda d: -d["value"])
    if a.json:
        print(json.dumps({"deals": shown, "errors": errors}, indent=2, ensure_ascii=False))
        return 0 if deals else 1
    if deals:
        day = max(d["date"] for d in deals)
        hidden = len(deals) - len(shown)
        print("NSE bulk and block deals for %s: %d deal(s)%s." % (
            day, len(deals), (", %d same-day buy+sell churn hidden (--all shows them)" % hidden) if hidden else ""))
    invs = load_investors(a.investors)
    for d in shown[:a.top]:
        print(line(d, match_investor(d["client"], invs)))
    for e in errors:
        print("error: " + e)
    print("Deals are disclosures of trades already done, not recommendations. Education, not financial advice.")
    return 0 if deals else 1


def cmd_watch(a):
    deals, errors = load_day(a.offline)
    invs = load_investors(a.investors)
    mine = holdings(a.portfolio)
    st = _read(a.state, {}) or {}
    ledger = st.setdefault("deals", {})
    new_hits = []
    for d in deals:
        who = match_investor(d["client"], invs)
        if d["id"] not in ledger:
            ledger[d["id"]] = dict(d, investor=who)
            if (who or d["symbol"] in mine) and (a.all or not d["intraday"]):
                new_hits.append((d, who))
    st["last_check"] = datetime.datetime.now().replace(microsecond=0).isoformat()
    if not a.no_write:
        _write(a.state, st)
    if a.json:
        print(json.dumps({"new": [dict(d, investor=w) for d, w in new_hits], "errors": errors},
                         indent=2, ensure_ascii=False))
        return 0 if new_hits else 1
    for d, who in sorted(new_hits, key=lambda x: -x[0]["value"]):
        reason = who or "you hold %s" % d["symbol"]
        print("NEW " + line(d, reason))
    if not new_hits:
        print("No new deals by your %d watched investor(s) or in your %d holding(s). %d deal(s) in the ledger."
              % (len(invs), len(mine), len(ledger)))
    for e in errors:
        print("error: " + e)
    if new_hits:
        print("A deal is a disclosure, not a signal. Copying one is a decision for you: size it, check it "
              "(check.py), and say yes to it (ticket.py).")
    return 0 if new_hits else 1


def _ledger(a):
    return list(((_read(a.state, {}) or {}).get("deals") or {}).values())


def cmd_investor(a):
    invs = load_investors(a.investors)
    q = " ".join(a.name)
    rows = []
    for d in _ledger(a):
        who = d.get("investor") or match_investor(d["client"], invs)
        if (who and tokens(q) <= tokens(who)) or tokens(q) <= tokens(d["client"]):
            rows.append(d)
    rows.sort(key=lambda d: (d["date"], d["symbol"]))
    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0 if rows else 1
    if not rows:
        print("No deals matching %r in the ledger yet (%s). The ledger grows each time `watch` runs."
              % (q, a.state))
        return 1
    net = {}
    for d in rows:
        print(line(d))
        n = net.setdefault(d["symbol"], [0, 0.0])
        sign = 1 if d["side"] == "buy" else -1
        n[0] += sign * d["qty"]
        n[1] += sign * d["value"]
    print("")
    print("Net by stock in the ledger (bought minus sold):")
    for s, (qn, v) in sorted(net.items(), key=lambda x: -abs(x[1][1])):
        print("  %-12s %+15s shares  %s" % (s, "{:,}".format(qn), crore(v)))
    print("Only deals above the bulk/block thresholds are disclosed; smaller trades never appear here.")
    return 0


def cmd_symbol(a):
    rows = sorted([d for d in _ledger(a) if d["symbol"] == a.symbol.upper()], key=lambda d: d["date"])
    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    for d in ([] if a.json else rows):
        print(line(d, d.get("investor")))
    if not rows and not a.json:
        print("No deals in %s in the ledger yet." % a.symbol.upper())
    return 0 if rows else 1


def cmd_investors(a):
    for i in sorted(load_investors(a.investors), key=lambda x: x["name"]):
        print("%-34s matches client names containing all of: %s" % (i["name"], " | ".join(i.get("match", []))))
    print("Add your own in %s: {\"investors\": [{\"name\": \"...\", \"match\": [\"WORD WORD\"]}]}" % a.investors)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE bulk and block deals (deals.py).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--offline", metavar="DIR", help="read DIR/bulk.csv and DIR/block.csv")
        p.add_argument("--investors", default="trader/investors.json")
        p.add_argument("--state", default="trader/state/deals.json")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("today", help="the latest day's deals")
    common(p)
    p.add_argument("--all", action="store_true", help="include same-day buy+sell churn")
    p.add_argument("--top", type=int, default=25)
    p = sub.add_parser("watch", help="new deals by watched investors or in your holdings")
    common(p)
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--all", action="store_true")
    p.add_argument("--no-write", action="store_true")
    p = sub.add_parser("investor", help="an investor's deals in the ledger")
    common(p)
    p.add_argument("name", nargs="+")
    p = sub.add_parser("symbol", help="every deal in one stock in the ledger")
    common(p)
    p.add_argument("symbol")
    p = sub.add_parser("investors", help="list the watched investors")
    common(p)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"today": cmd_today, "watch": cmd_watch, "investor": cmd_investor, "symbol": cmd_symbol,
                "investors": cmd_investors}[a.cmd](a)
    except DealError as e:
        print("deals.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
