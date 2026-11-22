#!/usr/bin/env python3
"""NSE clock, prices, symbols and price alerts for nse.py (India).
Standard library only.

    python3 nse.py clock                              # is the NSE open? next open
    python3 nse.py symbol reliance                    # look up symbol / company / ISIN
    python3 nse.py quote RELIANCE TCS --write         # quotes -> trader/prices.json (INR)
    python3 nse.py quote --holdings --write           # every symbol in portfolio + targets
    python3 nse.py quote INFY --source bhavcopy       # NSE's official end-of-day close
    python3 nse.py add --symbol RELIANCE --above 1600 # rules live in trader/alerts.json
    python3 nse.py add --symbol HDFCBANK --stop 800
    python3 nse.py add --symbol TCS --pct 3
    python3 nse.py add --opening-bell
    python3 nse.py check                              # rings what hit, once; exit 1 if nothing rang
    python3 nse.py list
    python3 nse.py reset reliance-above-1600

Symbols are NSE trading symbols (RELIANCE, M&M, BAJAJ-AUTO). Add ".BO" for a
BSE-only stock on Yahoo (e.g. 500325.BO). Prices are in rupees.

Sources (free, delayed; for alerts and sizing checks, never for placing orders):
  yahoo     https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS
            (unofficial JSON, intraday, about 15 minutes delayed; the default)
  bhavcopy  https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
            (NSE's official end-of-day file, all symbols in one download). On a
            holiday NSE serves the PREVIOUS trading day's file under the new
            date, so the tool always checks the date inside the file.
  symbols   https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
  --offline DIR reads DIR/yahoo-<SYMBOL>.json, DIR/bhavcopy-DDMMYYYY.csv and
  DIR/EQUITY_L.csv instead of the network.

NSE calendar: the 2026 trading holidays from NSE circular NSE/FAOP/71777
(12 Dec 2025) plus 15 Jan 2026 (Maharashtra municipal elections,
NSE/FAOP/72262). Normal session 09:15-15:30 IST, pre-open 09:00-09:08, no
daylight saving. Muhurat trading (a special evening session on Diwali) is
announced separately each year and is not modelled. NSE publishes the next
year's list in December: add it under "holidays" in trader/config.json, e.g.
{"holidays": {"2027-01-26": "Republic Day"}}.
"""

import argparse
import csv
import datetime
import gzip
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# NSE's archive server stalls requests whose User-Agent contains a URL (tested
# 2026-11-22), so the name is plain.
UA = "trader-skill-india/1.0"
PAUSE = 0.5

NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election in Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
CALENDAR_YEARS = (2026,)
IST = datetime.timedelta(hours=5, minutes=30)
PRE_OPEN = datetime.time(9, 0)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=1d"
BHAV = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_%s.csv"
EQUITY_L = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"


class AlertError(Exception):
    pass


# ---------------------------------------------------------------- money

def inr(x, decimals=2):
    """Rupees with Indian digit grouping: 12,34,567.89."""
    x = float(x)
    sign = "-" if x < 0 else ""
    s = ("%." + str(decimals) + "f") % abs(x)
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return "%s₹%s%s" % (sign, whole, ("." + frac) if frac else "")


# ---------------------------------------------------------------- time

def holidays(extra=None):
    h = dict(NSE_HOLIDAYS)
    h.update(extra or {})
    return h


def to_ist(utc):
    return utc.replace(tzinfo=None) + IST


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise AlertError("--now wants an ISO time like 2026-11-23T09:20 (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return dt - IST
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def trading_day(d, hol):
    if d.weekday() >= 5:
        return False, "weekend"
    name = hol.get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def market_status(now_utc, extra_holidays=None):
    hol = holidays(extra_holidays)
    local = to_ist(now_utc)
    d, t = local.date(), local.time()
    ok, reason = trading_day(d, hol)
    years = set(CALENDAR_YEARS) | {int(k[:4]) for k in (extra_holidays or {})}
    st = {"now_ist": local.strftime("%Y-%m-%d %H:%M"), "date": d.isoformat(),
          "weekday": local.strftime("%A"), "calendar_known": d.year in years,
          "open": False, "reason": reason}
    if ok:
        st["opens"], st["closes"] = OPEN.strftime("%H:%M"), CLOSE.strftime("%H:%M")
        if OPEN <= t < CLOSE:
            st["open"], st["reason"] = True, "normal market"
        elif PRE_OPEN <= t < OPEN:
            st["reason"] = "pre-open session (orders collected, matched at 09:08)"
        elif t < PRE_OPEN:
            st["reason"] = "before the open"
        else:
            st["reason"] = "after the close"
    nd = d if (ok and t < OPEN) else d + datetime.timedelta(days=1)
    for _ in range(15):
        if trading_day(nd, hol)[0]:
            st["next_open"] = "%s 09:15" % nd.isoformat()
            break
        nd += datetime.timedelta(days=1)
    upcoming = sorted(k for k in hol if k > d.isoformat())
    if upcoming:
        st["next_holiday"] = "%s %s" % (upcoming[0], hol[upcoming[0]])
    return st


# ---------------------------------------------------------------- network

def fetch(url, timeout=30, binary=False, tries=2):
    """GET with gzip, one retry on a timeout or dropped connection."""
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
            return data if binary else data.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise AlertError("%s answered HTTP %d" % (url.split("?")[0], e.code))
        except urllib.error.URLError as e:
            last = e.reason
        except (TimeoutError, OSError) as e:
            last = e
    raise AlertError("could not reach %s: %s" % (url.split("/")[2], last))


def yahoo_symbol(sym):
    s = sym.upper().strip()
    if s.endswith((".NS", ".BO")):
        return s
    return s + ".NS"


def plain(sym):
    s = sym.upper().strip()
    return s[:-3] if s.endswith(".NS") else s


def yahoo_url(sym):
    return YAHOO % urllib.parse.quote(yahoo_symbol(sym), safe=".-")


def parse_yahoo(text, sym):
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise AlertError("Yahoo returned something unexpected for %s" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise AlertError("Yahoo has no data for %s: %s" % (sym, err.get("description", "unknown symbol")))
    m = res[0].get("meta", {})
    price = m.get("regularMarketPrice")
    if price is None:
        raise AlertError("Yahoo has no price for %s" % sym)
    if m.get("currency") and m["currency"] != "INR":
        raise AlertError("%s is quoted in %s, not INR: is it an Indian listing?" % (sym, m["currency"]))
    t = m.get("regularMarketTime")
    when = (datetime.datetime.fromtimestamp(t, datetime.timezone.utc) + IST).strftime("%Y-%m-%d %H:%M IST") if t else ""
    return {"symbol": plain(sym), "price": float(price), "high": m.get("regularMarketDayHigh"),
            "low": m.get("regularMarketDayLow"),
            "prev_close": m.get("previousClose") or m.get("chartPreviousClose"),
            "time": when, "exchange": m.get("fullExchangeName"), "currency": "INR", "source": "yahoo"}


def bhav_url(d):
    return BHAV % d.strftime("%d%m%Y")


def parse_bhavcopy(text, want_date=None):
    """{SYMBOL: row} for equity series (EQ, BE, BZ, SM, ST) and the file's trade date.
    Raises if want_date is given and the file holds another day (NSE serves the
    previous day's file on a holiday)."""
    rows = csv.reader(io.StringIO(text))
    header = [h.strip().upper() for h in next(rows, [])]
    if "SYMBOL" not in header or "CLOSE_PRICE" not in header:
        raise AlertError("this does not look like an NSE bhavcopy (sec_bhavdata_full) file")
    ix = {h: i for i, h in enumerate(header)}
    out, dates = {}, set()
    for r in rows:
        if len(r) < len(header):
            continue
        v = {h: r[i].strip() for h, i in ix.items()}
        if v["SERIES"] not in ("EQ", "BE", "BZ", "SM", "ST"):
            continue
        dates.add(v["DATE1"])
        try:
            out[v["SYMBOL"].upper()] = {
                "symbol": v["SYMBOL"].upper(), "series": v["SERIES"], "price": float(v["CLOSE_PRICE"]),
                "open": float(v["OPEN_PRICE"]), "high": float(v["HIGH_PRICE"]), "low": float(v["LOW_PRICE"]),
                "prev_close": float(v["PREV_CLOSE"]), "volume": int(float(v["TTL_TRD_QNTY"])),
                "delivery_pct": (float(v["DELIV_PER"]) if v.get("DELIV_PER", "-") not in ("-", "") else None),
                "time": v["DATE1"] + " close", "currency": "INR", "source": "bhavcopy"}
        except (ValueError, KeyError):
            continue
    if not dates:
        raise AlertError("the bhavcopy has no equity rows")
    file_date = datetime.datetime.strptime(sorted(dates)[0], "%d-%b-%Y").date()
    if want_date and file_date != want_date:
        raise AlertError("NSE has no bhavcopy for %s (it served %s's file instead; %s was not a "
                         "trading day or the file is not out yet, usually after 18:00 IST)"
                         % (want_date, file_date, want_date))
    return out, file_date


def last_bhavcopy(now_utc, hol, offline=None, days_back=7):
    """The most recent bhavcopy on or before today, checking the date inside."""
    d = to_ist(now_utc).date()
    if to_ist(now_utc).time() < datetime.time(18, 0):
        d -= datetime.timedelta(days=1)
    tried = []
    for _ in range(days_back):
        if trading_day(d, hol)[0]:
            name = "bhavcopy-%s.csv" % d.strftime("%d%m%Y")
            try:
                if offline:
                    path = os.path.join(offline, name)
                    if not os.path.exists(path):
                        raise AlertError("no offline file %s" % path)
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()
                else:
                    text = fetch(bhav_url(d))
                return parse_bhavcopy(text, d)
            except AlertError as e:
                tried.append(str(e))
        d -= datetime.timedelta(days=1)
    raise AlertError("no bhavcopy found in the last %d days: %s" % (days_back, "; ".join(tried[-2:])))


def get_quotes(symbols, source="yahoo", offline=None, now_utc=None, hol=None, pause=PAUSE):
    quotes, errors = {}, []
    if source == "bhavcopy":
        try:
            table, _ = last_bhavcopy(now_utc or parse_now(None), hol or holidays(), offline)
        except AlertError as e:
            return {}, [str(e)]
        for s in symbols:
            q = table.get(plain(s))
            if q:
                quotes[plain(s)] = q
            else:
                errors.append("%s is not in the NSE bhavcopy (BSE-only, suspended, or a typo?)" % s)
        return quotes, errors
    for i, s in enumerate(symbols):
        try:
            if offline:
                path = os.path.join(offline, "yahoo-%s.json" % plain(s))
                if not os.path.exists(path):
                    raise AlertError("no offline quote file %s" % path)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            else:
                if i and pause:
                    time.sleep(pause)
                text = fetch(yahoo_url(s))
            quotes[plain(s)] = parse_yahoo(text, s)
        except AlertError as e:
            errors.append(str(e))
    return quotes, errors


# ---------------------------------------------------------------- symbols

def load_symbols(offline=None, cache="trader/cache/EQUITY_L.csv", max_age_days=7):
    text = None
    if offline:
        path = os.path.join(offline, "EQUITY_L.csv")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    elif cache and os.path.exists(cache) and time.time() - os.path.getmtime(cache) < max_age_days * 86400:
        with open(cache, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = fetch(EQUITY_L)
        if cache:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w", encoding="utf-8") as fh:
                fh.write(text)
    return parse_symbols(text)


def parse_symbols(text):
    rows = csv.reader(io.StringIO(text))
    header = [h.strip().upper() for h in next(rows, [])]
    ix = {h: i for i, h in enumerate(header)}
    need = ("SYMBOL", "NAME OF COMPANY", "SERIES", "ISIN NUMBER")
    if not all(k in ix for k in need):
        raise AlertError("EQUITY_L.csv has unexpected columns: %s" % ", ".join(header))
    out = []
    for r in rows:
        if len(r) < len(header):
            continue
        out.append({"symbol": r[ix["SYMBOL"]].strip(), "name": r[ix["NAME OF COMPANY"]].strip(),
                    "series": r[ix["SERIES"]].strip(), "isin": r[ix["ISIN NUMBER"]].strip(),
                    "listed": r[ix.get("DATE OF LISTING", 0)].strip()})
    return out


def find_symbol(rows, q):
    q = q.strip().upper()
    exact = [r for r in rows if r["symbol"].upper() == q or r["isin"].upper() == q]
    if exact:
        return exact
    words = q.split()
    return [r for r in rows if all(w in r["name"].upper() for w in words)][:15]


# ---------------------------------------------------------------- files

def _read(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise AlertError("%s is not valid JSON: %s" % (path, e))


def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def write_prices(path, quotes, now_utc, source):
    old = _read(path, {})
    prices, detail = dict(old.get("prices", {})), dict(old.get("detail", {}))
    for s, q in quotes.items():
        prices[s] = q["price"]
        detail[s] = {"prev_close": q.get("prev_close"), "time": q.get("time"), "source": q.get("source")}
    doc = {"as_of": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "currency": "INR", "market": "IN",
           "source": source,
           "note": "Delayed quotes for alerts and sizing checks. Your broker's price is the one that fills.",
           "prices": prices, "detail": detail}
    _write(path, doc)
    return doc


def config_holidays(path):
    cfg = _read(path, {}) or {}
    h = cfg.get("holidays") or {}
    return {str(k)[:10]: str(v) for k, v in h.items()} if isinstance(h, dict) else {}


# ---------------------------------------------------------------- rules

TYPES = {"above", "below", "stop", "target", "pct_move", "opening_bell"}


def validate(rule):
    t = rule.get("type")
    if t not in TYPES:
        raise AlertError("rule %s: unknown type %r" % (rule.get("id"), t))
    if t != "opening_bell" and not rule.get("symbol"):
        raise AlertError("rule %s needs a symbol" % rule.get("id"))
    if t in ("above", "below", "stop", "target") and not isinstance(rule.get("price"), (int, float)):
        raise AlertError("rule %s needs a numeric price" % rule.get("id"))
    if t == "pct_move" and not isinstance(rule.get("pct"), (int, float)):
        raise AlertError("rule %s needs a numeric pct" % rule.get("id"))


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def evaluate(rules, quotes, status, state, now_utc):
    """Pure function. Returns (rings, new_state)."""
    state = json.loads(json.dumps(state or {}))
    rung = state.setdefault("rung", {})
    today = status["date"]
    stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    rings = []
    for r in rules:
        validate(r)
        rid = r.get("id") or "%s-%s" % (r.get("symbol", "market"), r["type"])
        typ = {"stop": "below", "target": "above"}.get(r["type"], r["type"])
        if typ == "opening_bell":
            if status["open"] and rung.get(rid, "")[:10] != today:
                rings.append({"id": rid, "symbol": "",
                              "message": "Opening bell: the NSE is open (%s IST)." % status["now_ist"]})
                rung[rid] = stamp
            continue
        sym = plain(r["symbol"])
        q = quotes.get(sym)
        if not q:
            continue
        p = q["price"]
        if typ in ("above", "below"):
            hit = p >= r["price"] if typ == "above" else p <= r["price"]
            if hit and rid not in rung:
                word = "at or above" if typ == "above" else "at or below"
                label = {"stop": "stop", "below": "level", "above": "target"}.get(r["type"], "target")
                rings.append({"id": rid, "symbol": sym, "message": "%s %s %s, %s your %s of %s%s." % (
                    sym, "is" if status["open"] else "last traded at", inr(p), word, label, inr(r["price"]),
                    (" (" + r["note"] + ")") if r.get("note") else "")})
                rung[rid] = stamp
            elif not hit and rid in rung and r.get("rearm"):
                del rung[rid]
        elif typ == "pct_move":
            ref = r.get("ref") or q.get("prev_close") or q.get("open")
            if not ref:
                continue
            move = 100.0 * (p / float(ref) - 1)
            want = r.get("direction")
            hit = abs(move) >= r["pct"] and (not want or (want == "up") == (move > 0))
            if hit and rung.get(rid, "")[:10] != today:
                rings.append({"id": rid, "symbol": sym, "message": "%s moved %+.2f%% to %s (from %s)."
                              % (sym, move, inr(p), inr(float(ref)))})
                rung[rid] = stamp
    state["last_check"] = stamp
    return rings, state


# ---------------------------------------------------------------- commands

def holdings(a):
    syms = []
    for p in (_read(a.portfolio, {}) or {}).get("positions", []):
        syms.append(str(p.get("ticker") or p.get("symbol") or ""))
    tg = _read(a.targets, {})
    for w in tg.get("weights", []) if isinstance(tg, dict) else []:
        syms.append(str(w.get("ticker") or w.get("symbol") or ""))
    return [s for s in syms if s]


def _dedupe(items):
    out = []
    for s in items:
        s = plain(s)
        if s and s not in out:
            out.append(s)
    return out


def cmd_clock(a):
    st = market_status(parse_now(a.now), config_holidays(a.config))
    if a.json:
        print(json.dumps(st, indent=2))
        return 0 if st["open"] else 1
    print("India %s IST (%s): NSE %s, %s." % (st["now_ist"], st["weekday"],
                                            "OPEN" if st["open"] else "closed", st["reason"]))
    if st.get("opens"):
        print("Today's session: %s to %s IST." % (st["opens"], st["closes"]))
    if not st["open"] and st.get("next_open"):
        print("Next open: %s IST." % st["next_open"])
    if st.get("next_holiday"):
        print("Next NSE holiday: %s." % st["next_holiday"])
    if not st["calendar_known"]:
        print("The holiday list does not cover %s: add NSE's list to \"holidays\" in trader/config.json."
              % st["date"][:4])
    return 0 if st["open"] else 1


def cmd_symbol(a):
    rows = load_symbols(a.offline)
    hits = find_symbol(rows, " ".join(a.query))
    if a.json:
        print(json.dumps(hits, indent=2))
    elif not hits:
        print("No NSE equity matches %r. Try part of the company name." % " ".join(a.query))
    else:
        for r in hits:
            print("%-12s %-4s %s  %s" % (r["symbol"], r["series"], r["isin"], r["name"]))
    return 0 if hits else 1


def cmd_quote(a):
    syms = _dedupe(list(a.symbols) + (holdings(a) if a.holdings else []))
    if not syms:
        raise AlertError("which symbols? nse.py quote RELIANCE TCS, or --holdings")
    now = parse_now(a.now)
    quotes, errors = get_quotes(syms, a.source, a.offline, now, holidays(config_holidays(a.config)))
    if a.write and quotes:
        write_prices(a.write, quotes, now, a.source)
    if a.json:
        print(json.dumps({"quotes": quotes, "errors": errors}, indent=2, ensure_ascii=False))
    else:
        for s in syms:
            q = quotes.get(s)
            if not q:
                continue
            chg = ""
            if q.get("prev_close"):
                chg = " (%+.2f%% vs prev close %s)" % (100.0 * (q["price"] / q["prev_close"] - 1), inr(q["prev_close"]))
            extra = ("  delivery %.1f%%" % q["delivery_pct"]) if q.get("delivery_pct") is not None else ""
            print("%-12s %s%s  %s  [%s]%s" % (s, inr(q["price"]), chg, q.get("time", ""), q["source"], extra))
        for e in errors:
            print("error: " + e)
        if a.write and quotes:
            print("Wrote %d price(s) to %s." % (len(quotes), a.write))
        print("Quotes are delayed and for alerts only. Your broker's price is the one that fills.")
    return 0 if quotes else 1


def cmd_check(a):
    doc = _read(a.rules, None)
    if not doc or not doc.get("rules"):
        raise AlertError("no rules in %s: add one with nse.py add --symbol RELIANCE --above 1600" % a.rules)
    rules = doc["rules"]
    now = parse_now(a.now)
    extra = config_holidays(a.config)
    status = market_status(now, extra)
    syms = _dedupe([str(r.get("symbol", "")) for r in rules if r.get("type") != "opening_bell"])
    quotes, errors = get_quotes(syms, a.source, a.offline, now, holidays(extra)) if syms else ({}, [])
    state = _read(a.state, {})
    rings, state = evaluate(rules, quotes, status, state, now)
    if not a.no_write:
        _write(a.state, state)
        if quotes:
            write_prices(a.prices_out, quotes, now, a.source)
    if a.json:
        print(json.dumps({"market": status, "rings": rings, "errors": errors,
                          "quotes": {s: q["price"] for s, q in quotes.items()}}, indent=2, ensure_ascii=False))
    else:
        print("NSE %s (%s IST, %s)." % ("OPEN" if status["open"] else "closed", status["now_ist"], status["reason"]))
        for r in rings:
            print("ALERT " + r["message"])
        if not rings:
            print("Nothing hit. %d rule(s) watched." % len(rules))
        for e in errors:
            print("error: " + e)
        if rings:
            print("An alert is information, not an order. To act on one, size it (size.py), "
                  "check it (check.py) and say yes to it (ticket.py).")
    return 0 if rings else 1


def cmd_add(a):
    doc = _read(a.rules, {"rules": []})
    doc.setdefault("rules", [])
    if a.opening_bell:
        rule = {"id": a.id or "opening-bell", "type": "opening_bell"}
    else:
        if not a.symbol:
            raise AlertError("--symbol is required (or --opening-bell)")
        sym = plain(a.symbol)
        if a.above is not None:
            rule = {"type": "above", "price": a.above}
        elif a.below is not None:
            rule = {"type": "below", "price": a.below}
        elif a.stop is not None:
            rule = {"type": "stop", "price": a.stop}
        elif a.pct is not None:
            rule = {"type": "pct_move", "pct": a.pct}
            if a.direction:
                rule["direction"] = a.direction
        else:
            raise AlertError("give one of --above, --below, --stop or --pct")
        val = rule.get("price", rule.get("pct"))
        rule = dict({"id": a.id or "%s-%s-%s" % (sym.lower(), rule["type"], _n(val))}, symbol=sym, **rule)
        if a.rearm:
            rule["rearm"] = True
        if a.note:
            rule["note"] = a.note
    validate(rule)
    if any(r.get("id") == rule["id"] for r in doc["rules"]):
        raise AlertError("a rule with id %s already exists" % rule["id"])
    doc["rules"].append(rule)
    _write(a.rules, doc)
    print("Added %s: %s" % (rule["id"], json.dumps(rule, ensure_ascii=False)))
    return 0


def cmd_list(a):
    doc = _read(a.rules, {"rules": []})
    state = _read(a.state, {})
    if not doc.get("rules"):
        print("No rules yet.")
        return 1
    for r in doc["rules"]:
        rid = r.get("id")
        rung = state.get("rung", {}).get(rid)
        print("%-28s %s%s" % (rid, json.dumps({k: v for k, v in r.items() if k != "id"}, ensure_ascii=False),
                              ("  (rang %s)" % rung) if rung else ""))
    return 0


def cmd_reset(a):
    state = _read(a.state, {})
    if a.id not in state.get("rung", {}):
        print("%s has not rung (nothing to reset)." % a.id)
        return 1
    del state["rung"][a.id]
    _write(a.state, state)
    print("Re-armed %s." % a.id)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE clock, prices, symbols and alerts (nse.py).")
    sub = ap.add_subparsers(dest="cmd")

    def net(p):
        p.add_argument("--source", default="yahoo", choices=["yahoo", "bhavcopy"])
        p.add_argument("--offline", metavar="DIR", help="read saved files from DIR, no network")
        p.add_argument("--now", help="pretend it is this time (ISO; no offset = IST)")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("clock", help="is the NSE open?")
    p.add_argument("--now")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("symbol", help="look up an NSE symbol, company name or ISIN")
    p.add_argument("query", nargs="+")
    p.add_argument("--offline", metavar="DIR")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("quote", help="latest delayed quotes in INR")
    net(p)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--holdings", action="store_true")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--targets", default="trader/targets.json")
    p.add_argument("--write", nargs="?", const="trader/prices.json", metavar="PATH")
    p = sub.add_parser("check", help="check every rule, ring what hit")
    net(p)
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p.add_argument("--prices-out", default="trader/prices.json")
    p.add_argument("--no-write", action="store_true")
    p = sub.add_parser("add", help="add a rule")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--symbol")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--above", type=float)
    g.add_argument("--below", type=float)
    g.add_argument("--stop", type=float)
    g.add_argument("--pct", type=float)
    g.add_argument("--opening-bell", action="store_true")
    p.add_argument("--direction", choices=["up", "down"])
    p.add_argument("--rearm", action="store_true")
    p.add_argument("--note")
    p.add_argument("--id")
    p = sub.add_parser("list", help="list the rules")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p = sub.add_parser("reset", help="re-arm a rule that rang")
    p.add_argument("id")
    p.add_argument("--state", default="trader/state/alerts.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"clock": cmd_clock, "symbol": cmd_symbol, "quote": cmd_quote, "check": cmd_check,
                "add": cmd_add, "list": cmd_list, "reset": cmd_reset}[a.cmd](a)
    except AlertError as e:
        print("nse.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
