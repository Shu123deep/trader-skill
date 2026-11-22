#!/usr/bin/env python3
"""Research desk for research.py (India). Standard library only.

It turns price history into the evidence Claude needs to make a suggestion:
trend, momentum, volatility, relative strength against the Nifty, and, for
traders, concrete plans with an entry, a stop-loss, a target and a position
size from your own risk budget.

    python3 research.py swing RELIANCE TCS             # swing setups on daily charts (days to weeks)
    python3 research.py intraday RELIANCE              # intraday levels and setup on 5-minute bars
    python3 research.py scan --universe nifty50        # rank an index for swing setups
    python3 research.py stock INFY                     # the investing view: returns, drawdown, vs Nifty
    python3 research.py portfolio                      # review trader/portfolio.json
    python3 research.py swing TCS --write              # the plan's entry order -> trader/orders.json

Data (free, delayed, never for timing an order to the second):
  daily and 5-minute bars  https://query1.finance.yahoo.com/v8/finance/chart/<SYM>.NS
                           (unofficial; NSE quotes about 15 minutes delayed)
  the Nifty                ^NSEI on the same endpoint
  index lists + industry   https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv
                           (and ind_nifty500list.csv), cached in trader/cache/
  --offline DIR reads DIR/chart-<SYM>-<interval>.json and DIR/ind_<name>list.csv.

Your risk rules come from trader/config.json:
  risk_per_trade_pct    swing: the most you lose if the stop is hit, % of account (default 1)
  intraday_risk_pct     intraday: the same, per trade (default 0.5)
  max_position_pct      cap on any one stock (default 25)
  allow_intraday        intraday plans are written as orders only if true (product MIS)
  allow_short_intraday  intraday short plans only if true (MIS shorts must be squared off the same day)

Every number here is arithmetic on past prices. A setup is a reason to look,
not a prediction: most short-term trades by individuals lose money.
"""

import argparse
import csv
import datetime
import gzip
import io
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "trader-skill-india/1.0"   # NSE's archive stalls a User-Agent that contains a URL
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s"
INDEX_LIST = "https://nsearchives.nseindia.com/content/indices/ind_%slist.csv"
NIFTY = "^NSEI"
IST = datetime.timedelta(hours=5, minutes=30)
YEAR = 248          # NSE trading days in a year, about
PAUSE = 0.4
DEFAULTS = {"risk_per_trade_pct": 1.0, "intraday_risk_pct": 0.5, "max_position_pct": 25, "budget": None,
            "allow_intraday": False, "allow_short_intraday": False}
NO_NEW_INTRADAY_AFTER = datetime.time(14, 30)
SQUARE_OFF = datetime.time(15, 15)


class ResearchError(Exception):
    pass


# ---------------------------------------------------------------- data

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
            raise ResearchError("%s answered HTTP %d" % (url.split("?")[0], e.code))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = getattr(e, "reason", e)
    raise ResearchError("could not reach %s: %s" % (url.split("/")[2], last))


def ysym(sym):
    s = sym.upper().strip()
    if s.startswith("^") or s.endswith((".NS", ".BO")):
        return s
    return s + ".NS"


def plain(sym):
    s = sym.upper().strip()
    return s[:-3] if s.endswith(".NS") else s


def parse_chart(text, sym):
    """Yahoo chart JSON -> [{t (IST datetime), o, h, l, c, v}] without empty bars."""
    try:
        res = json.loads(text)["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise ResearchError("Yahoo returned something unexpected for %s" % sym)
    if not res:
        raise ResearchError("Yahoo has no data for %s (check the symbol with nse.py symbol)" % sym)
    r = res[0]
    cur = r.get("meta", {}).get("currency")
    if cur and cur != "INR":
        raise ResearchError("%s is quoted in %s, not INR" % (sym, cur))
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    bars = []
    for i, ts in enumerate(r.get("timestamp") or []):
        try:
            o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
        except (KeyError, IndexError):
            continue
        if None in (o, h, l, c):
            continue
        t = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).replace(tzinfo=None) + IST
        bars.append({"t": t, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v or 0)})
    if not bars:
        raise ResearchError("no price bars for %s" % sym)
    return bars


def chart(sym, rng, interval, offline=None):
    if offline:
        path = os.path.join(offline, "chart-%s-%s.json" % (plain(sym).replace("^", ""), interval))
        if not os.path.exists(path):
            raise ResearchError("no offline file %s" % path)
        with open(path, encoding="utf-8") as fh:
            return parse_chart(fh.read(), sym)
    return parse_chart(fetch(CHART % (urllib.parse.quote(ysym(sym), safe=".-^"), rng, interval)), sym)


def index_list(name, offline=None, cache_dir="trader/cache", max_age_days=7):
    """{SYMBOL: industry} for nifty50 / nifty500 / niftynext50 ..."""
    fn = "ind_%slist.csv" % name
    path = os.path.join(offline, fn) if offline else os.path.join(cache_dir, fn)
    if offline or (os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_days * 86400):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = fetch(INDEX_LIST % name)
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        if r.get("Symbol"):
            out[r["Symbol"].upper()] = r.get("Industry", "")
    if not out:
        raise ResearchError("%s has no symbols" % fn)
    return out


# ---------------------------------------------------------------- indicators (pure)

def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def ema_series(xs, n):
    if len(xs) < n:
        return []
    k, out = 2.0 / (n + 1), [sum(xs[:n]) / n]
    for x in xs[n:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def rsi(closes, n=14):
    """Wilder's RSI."""
    if len(closes) <= n:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1 + ag / al)


def atr(bars, n=14):
    """Wilder's average true range."""
    if len(bars) <= n:
        return None
    trs = [max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])) for p, b in zip(bars, bars[1:])]
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def macd_hist(closes):
    e12, e26 = ema_series(closes, 12), ema_series(closes, 26)
    if not e26:
        return None
    line = [a - b for a, b in zip(e12[len(e12) - len(e26):], e26)]
    sig = ema_series(line, 9)
    return (line[-1] - sig[-1]) if sig else None


def ret(closes, n):
    return 100.0 * (closes[-1] / closes[-1 - n] - 1) if len(closes) > n and closes[-1 - n] else None


def max_drawdown(closes):
    peak, worst = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        worst = min(worst, c / peak - 1)
    return 100.0 * worst


def volatility(closes):
    rs = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rs) < 20:
        return None
    m = sum(rs) / len(rs)
    return 100.0 * math.sqrt(sum((r - m) ** 2 for r in rs) / (len(rs) - 1)) * math.sqrt(YEAR)


def tick(x):
    """Round to NSE's 5-paise tick (prices are quoted in multiples of Rs 0.05)."""
    return round(round(x / 0.05) * 0.05, 2)


# ---------------------------------------------------------------- sizing

def _read(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise ResearchError("%s is not valid JSON: %s" % (path, e))


def account_value(cfg, portfolio, prices):
    if portfolio:
        v = float(portfolio.get("cash") or 0)
        for p in portfolio.get("positions", []):
            t = str(p.get("ticker") or p.get("symbol") or "").upper()
            sh = float(p.get("shares") or 0)
            v += sh * (prices.get(t) or 0)
        return v, float(portfolio.get("cash") or 0)
    b = float(cfg.get("budget") or 0)
    return b, b


def size(entry, stop, account, cash, risk_pct, cap_pct):
    """Whole shares so that hitting the stop loses about risk_pct of the account."""
    per = abs(entry - stop)
    if per <= 0 or account <= 0:
        return {"qty": 0, "why": "no account value: set \"budget\" in trader/config.json"}
    risk_amt = account * risk_pct / 100.0
    q_risk = int(risk_amt // per)
    q_cap = int((account * cap_pct / 100.0) // entry)
    q_cash = int(cash // entry) if cash else q_cap
    q = max(min(q_risk, q_cap, q_cash), 0)
    limit = "risk" if q == q_risk else ("position cap" if q == q_cap else "cash")
    out = {"qty": q, "risk_inr": round(q * per, 2), "value_inr": round(q * entry, 2), "limited_by": limit,
           "risk_budget_inr": round(risk_amt, 2)}
    if q == 0:
        out["why"] = ("one share risks %s, more than your %s risk budget" % (_inr(per), _inr(risk_amt))
                      if q_risk == 0 else "not enough cash or room under the position cap for one share")
    return out


def _inr(x):
    x = float(x)
    sign = "-" if x < 0 else ""
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


# ---------------------------------------------------------------- swing

def swing_analysis(bars, nifty=None):
    """Daily bars (oldest first) -> facts and at most one long setup with a plan."""
    if len(bars) < 60:
        raise ResearchError("need at least 60 daily bars, got %d" % len(bars))
    c = [b["c"] for b in bars]
    last = bars[-1]
    a = atr(bars)
    s20, s50, s200 = sma(c, 20), sma(c, 50), sma(c, 200)
    r = rsi(c)
    vol20 = sum(b["v"] for b in bars[-21:-1]) / 20
    vr = last["v"] / vol20 if vol20 else None
    hi20 = max(b["h"] for b in bars[-21:-1])
    lo10 = min(b["l"] for b in bars[-10:])
    hi252 = max(b["h"] for b in bars[-YEAR:])
    lo252 = min(b["l"] for b in bars[-YEAR:])
    rs = None
    if nifty and len(nifty) > 63:
        n = [b["c"] for b in nifty]
        if ret(c, 63) is not None and ret(n, 63) is not None:
            rs = ret(c, 63) - ret(n, 63)
    mh = macd_hist(c)
    if s200 and last["c"] > s50 > s200:
        trend = "up"
    elif s200 and last["c"] < s50 < s200:
        trend = "down"
    elif not s200 and last["c"] > s50:
        trend = "up (under a year of data)"
    else:
        trend = "sideways / mixed"
    facts = {"date": last["t"].date().isoformat(), "close": round(last["c"], 2), "atr14": round(a, 2),
             "atr_pct": round(100 * a / last["c"], 2), "sma20": _r(s20), "sma50": _r(s50), "sma200": _r(s200),
             "rsi14": _r(r, 1), "volume_vs_20d": _r(vr), "high_20d": round(hi20, 2), "high_52w": round(hi252, 2),
             "low_52w": round(lo252, 2), "off_52w_high_pct": round(100 * (last["c"] / hi252 - 1), 1),
             "rs_vs_nifty_3m": _r(rs, 1), "macd_hist": _r(mh), "trend": trend,
             "ret_1m": _r(ret(c, 21), 1), "ret_3m": _r(ret(c, 63), 1)}
    checks = {"trend up": trend.startswith("up"), "beats the Nifty (3m)": bool(rs is not None and rs > 0),
              "momentum rising (MACD)": bool(mh is not None and mh > 0),
              "RSI 45-68 (strong, not stretched)": bool(r is not None and 45 <= r <= 68),
              "volume above average": bool(vr is not None and vr >= 1.2)}
    score = sum(checks.values())
    setup, notes = None, []
    extended = s20 and last["c"] > s20 + 3 * a
    if trend == "down":
        notes.append("downtrend (below the 50 and 200-day averages): no long setup; shorting is not possible "
                     "in delivery and F&O is out of scope")
    elif last["c"] > hi20 and vr and vr >= 1.5 and s50 and last["c"] > s50:
        setup = "breakout"
        entry = last["c"]
        stop = max(min(hi20 - 0.5 * a, entry - 1.5 * a), entry - 3 * a)
    elif trend.startswith("up") and r is not None and 38 <= r <= 55 and \
            any(m and abs(last["l"] - m) <= a for m in (s20, s50)):
        setup = "pullback in an uptrend"
        entry = last["c"]
        stop = max(min(lo10 - 0.25 * a, entry - 1.5 * a), entry - 3 * a)
    elif s200 and last["c"] > s200 and r is not None and r < 32:
        setup = "oversold in a long-term uptrend (counter-trend, lower odds)"
        entry = last["c"]
        stop = entry - 2 * a
    else:
        notes.append("no setup today: not a breakout on volume, not a pullback to support in an uptrend")
    if extended:
        notes.append("extended: more than 3 ATR above the 20-day average; chasing here risks buying the top")
        if setup == "breakout":
            setup = None
            notes.append("breakout skipped because it is extended; wait for a pullback toward %s" % _inr(s20))
    plan = None
    if setup:
        stop = tick(stop)
        risk = entry - stop
        target = tick(entry + 2 * risk)
        plan = {"setup": setup, "entry": tick(entry), "stop": stop, "target": target, "r_multiple": 2.0,
                "risk_per_share": round(risk, 2), "risk_pct": round(100 * risk / entry, 2),
                "horizon": "1 to 4 weeks; exit on a close below the stop, at the target, or after 15 sessions",
                "entry_note": "buy with a limit near %s next session; skip if it opens more than 1 ATR (%s) higher"
                              % (_inr(entry), _inr(a))}
        if hi252 and target > hi252 and setup != "breakout":
            notes.append("the target is above the 52-week high (%s), which often acts as resistance" % _inr(hi252))
    return {"facts": facts, "checks": checks, "score": score, "plan": plan, "notes": notes}


def _r(x, d=2):
    return None if x is None else round(x, d)


# ---------------------------------------------------------------- intraday

def sessions(bars5):
    by = {}
    for b in bars5:
        by.setdefault(b["t"].date(), []).append(b)
    return [by[d] for d in sorted(by)]


def vwap(bars):
    pv = sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in bars)
    v = sum(b["v"] for b in bars)
    return pv / v if v else None


def pivots(h, l, c):
    p = (h + l + c) / 3
    return {"P": p, "R1": 2 * p - l, "S1": 2 * p - h, "R2": p + (h - l), "S2": p - (h - l)}


def intraday_analysis(bars5, daily, now_ist, allow_short=False):
    """5-minute bars (several sessions) + daily bars -> levels and a setup.

    If the latest session is today and the market is open, it reads the
    opening range (first 15 minutes) and VWAP; otherwise it prepares the next
    session's levels from the last completed day."""
    # a bar is known only once it has closed: no look-ahead when replaying a past time
    bars5 = [b for b in bars5 if b["t"] + datetime.timedelta(minutes=5) <= now_ist]
    daily = [b for b in (daily or []) if b["t"].date() < now_ist.date()]
    ss = sessions(bars5)
    if len(ss) < 2:
        raise ResearchError("need at least two sessions of 5-minute bars")
    today = now_ist.date()
    live = ss[-1][0]["t"].date() == today and datetime.time(9, 15) <= now_ist.time() < datetime.time(15, 30)
    ref = ss[-2] if live else ss[-1]
    ph, pl, pc = max(b["h"] for b in ref), min(b["l"] for b in ref), ref[-1]["c"]
    lv = {k: round(v, 2) for k, v in pivots(ph, pl, pc).items()}
    a = atr(daily) if daily and len(daily) > 15 else None
    out = {"mode": "live session" if live else "plan for the next session",
           "levels": dict(lv, prev_high=round(ph, 2), prev_low=round(pl, 2), prev_close=round(pc, 2)),
           "daily_atr": _r(a), "plan": None, "notes": []}
    if not live:
        out["notes"].append(
            "Before the open: if it opens between S1 and R1, wait 15 minutes and trade a break of the opening "
            "range in the direction of VWAP. A gap of more than 2%% from %s often fades; do not chase it."
            % _inr(pc))
        out["notes"].append("Longs above %s (previous high) have room to R2 %s; below %s (previous low) the "
                            "next support is S2 %s." % (_inr(ph), _inr(lv["R2"]), _inr(pl), _inr(lv["S2"])))
        return out
    cur = ss[-1]
    orb = cur[:3]
    oh, ol = max(b["h"] for b in orb), min(b["l"] for b in orb)
    vw = vwap(cur)
    last = cur[-1]["c"]
    gap = 100 * (cur[0]["o"] / pc - 1)
    out.update({"opening_range": {"high": round(oh, 2), "low": round(ol, 2)}, "vwap": _r(vw), "last": round(last, 2),
                "gap_pct": round(gap, 2), "as_of": cur[-1]["t"].strftime("%H:%M IST")})
    if len(cur) < 3:
        out["notes"].append("the opening range forms in the first 15 minutes (09:15 to 09:30); check again after")
        return out
    if now_ist.time() >= NO_NEW_INTRADAY_AFTER:
        out["notes"].append("after 14:30 IST: no new intraday entries; brokers square off MIS around 15:15-15:20")
        return out
    if a and (oh - ol) > 0.6 * a:
        out["notes"].append("the opening range (%s) is already over 60%% of a normal day's range (%s): much of "
                            "the day's move may be done" % (_inr(oh - ol), _inr(a)))
    side = None
    if last > oh and vw and last > vw:
        side, entry = "buy", max(last, oh)
        stop = max(ol, vw - 0.1 * (oh - ol)) if vw < entry else ol
    elif last < ol and vw and last < vw and allow_short:
        side, entry = "sell", min(last, ol)
        stop = min(oh, vw + 0.1 * (oh - ol)) if vw > entry else oh
    elif last < ol and vw and last < vw:
        out["notes"].append("below the opening range and VWAP: a short setup, but intraday shorting is off "
                            "(\"allow_short_intraday\": false)")
    else:
        out["notes"].append("inside the opening range or on the wrong side of VWAP: no trade yet")
    if side:
        stop = tick(stop)
        risk = abs(entry - stop)
        if risk <= 0:
            return out
        target = tick(entry + 2 * risk if side == "buy" else entry - 2 * risk)
        nxt = min([v for v in lv.values() if v > entry], default=None) if side == "buy" else \
            max([v for v in lv.values() if v < entry], default=None)
        out["plan"] = {"setup": "opening-range breakout %s" % ("up" if side == "buy" else "down"), "side": side,
                       "entry": tick(entry), "stop": stop, "target": target, "r_multiple": 2.0,
                       "risk_per_share": round(risk, 2), "next_level": _r(nxt),
                       "exit": "stop-loss order right after the fill; square off by 15:15 IST whatever happens"}
        if nxt and ((side == "buy" and nxt < target) or (side == "sell" and nxt > target)):
            out["notes"].append("a pivot level (%s) sits before the target: expect selling or buying there"
                                % _inr(nxt))
    return out


def intraday_charges(value):
    """Approximate statutory charges for a round trip (buy + sell) in MIS, before brokerage."""
    stt = value * 0.00025                 # 0.025% on the sell side
    stamp = value * 0.00003               # 0.003% on the buy side
    exch = 2 * value * 0.0000297          # NSE transaction charge, both legs
    sebi = 2 * value * 0.000001
    return round(stt + stamp + exch + sebi + (exch + sebi) * 0.18, 2)


# ---------------------------------------------------------------- investing view

def stock_view(bars, nifty):
    c = [b["c"] for b in bars]
    n = [b["c"] for b in nifty] if nifty else []
    out = {"close": round(c[-1], 2), "date": bars[-1]["t"].date().isoformat()}
    for label, k in (("1m", 21), ("3m", 63), ("6m", 126), ("1y", YEAR - 1)):
        out["ret_" + label] = _r(ret(c, k), 1)
        out["nifty_" + label] = _r(ret(n, k), 1) if n else None
    out.update({"high_52w": round(max(b["h"] for b in bars[-YEAR:]), 2),
                "low_52w": round(min(b["l"] for b in bars[-YEAR:]), 2),
                "max_drawdown_1y_pct": round(max_drawdown(c[-YEAR:]), 1),
                "volatility_pct": _r(volatility(c[-YEAR:]), 1),
                "nifty_volatility_pct": _r(volatility(n[-YEAR:]), 1) if n else None,
                "above_200d": bool(sma(c, 200) and c[-1] > sma(c, 200)), "sma200": _r(sma(c, 200))})
    return out


# ---------------------------------------------------------------- commands

def _ctx(a):
    cfg = dict(DEFAULTS)
    cfg.update(_read(a.config, {}) or {})
    pf = _read(a.portfolio, None)
    pdoc = _read(a.prices, {}) or {}
    prices = {str(k).upper(): float(v) for k, v in (pdoc.get("prices", pdoc) or {}).items() if isinstance(v, (int, float))}
    return cfg, pf, prices


def _now(a):
    if a.now:
        return datetime.datetime.fromisoformat(a.now)
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + IST


def _nifty(a, rng="1y", interval="1d"):
    try:
        return chart(NIFTY, rng, interval, a.offline)
    except ResearchError:
        return None


def _write_orders(path, orders, note):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"created_by": "research.py", "market": "IN", "currency": "INR",
                   "created_at": datetime.datetime.now().replace(microsecond=0).isoformat(), "status": "proposed",
                   "note": note, "orders": orders}, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def print_swing(sym, res, sz):
    f = res["facts"]
    print("%s  %s close %s  trend %s  RSI %s  ATR %s (%.1f%%)  vs Nifty 3m %s pts  score %d/5"
          % (sym, f["date"], _inr(f["close"]), f["trend"], f["rsi14"], _inr(f["atr14"]), f["atr_pct"],
             "n/a" if f["rs_vs_nifty_3m"] is None else "%+.1f" % f["rs_vs_nifty_3m"], res["score"]))
    print("    50d %s  200d %s  20d high %s  52w %s to %s (%.1f%% off the high)  volume %sx its 20-day average"
          % (_inr(f["sma50"]) if f["sma50"] else "-", _inr(f["sma200"]) if f["sma200"] else "-", _inr(f["high_20d"]),
             _inr(f["low_52w"]), _inr(f["high_52w"]), f["off_52w_high_pct"], f["volume_vs_20d"]))
    p = res["plan"]
    if p:
        print("    SETUP %s: entry %s, stop %s (%.1f%% risk), target %s (2R)."
              % (p["setup"], _inr(p["entry"]), _inr(p["stop"]), p["risk_pct"], _inr(p["target"])))
        if sz and sz["qty"]:
            print("    size: %d shares = %s, losing %s at the stop (%s budget, limited by %s)"
                  % (sz["qty"], _inr(sz["value_inr"]), _inr(sz["risk_inr"]), _inr(sz["risk_budget_inr"]),
                     sz["limited_by"]))
        elif sz:
            print("    size: 0 shares: %s" % sz.get("why"))
        print("    " + p["entry_note"] + ". " + p["horizon"] + ".")
    for n in res["notes"]:
        print("    note: " + n)


def cmd_swing(a):
    cfg, pf, prices = _ctx(a)
    nifty = _nifty(a)
    acct, cash = account_value(cfg, pf, prices)
    out, orders, errors = [], [], []
    for i, s in enumerate(a.symbols):
        if i and not a.offline:
            time.sleep(PAUSE)
        try:
            res = swing_analysis(chart(s, "1y", "1d", a.offline), nifty)
        except ResearchError as e:
            errors.append(str(e))
            continue
        sz = None
        if res["plan"]:
            sz = size(res["plan"]["entry"], res["plan"]["stop"], acct, cash, float(cfg["risk_per_trade_pct"]),
                      float(cfg["max_position_pct"]))
            if sz["qty"]:
                p = res["plan"]
                orders.append({"symbol": plain(s), "exchange": "NSE", "side": "buy", "qty": str(sz["qty"]),
                               "type": "limit", "limit_price": "%.2f" % p["entry"], "product": "CNC",
                               "validity": "DAY", "stop_loss": "%.2f" % p["stop"], "target": "%.2f" % p["target"],
                               "why": "swing %s: stop %s, target %s, risk %s. After the fill, set a GTT / stop-loss "
                                      "at the stop." % (p["setup"], _inr(p["stop"]), _inr(p["target"]),
                                                        _inr(sz["risk_inr"]))})
        out.append({"symbol": plain(s), "analysis": res, "size": sz})
    if a.json:
        print(json.dumps({"results": out, "errors": errors, "account_inr": round(acct, 2)}, indent=2, default=str))
    else:
        for r in out:
            print_swing(r["symbol"], r["analysis"], r["size"])
        for e in errors:
            print("error: " + e)
        print("Daily bars from Yahoo, delayed. A setup is a reason to look, not a prediction. Education, not "
              "financial advice.")
    if a.write:
        if not orders:
            print("Nothing to write: no setup with a position size.")
        else:
            _write_orders(a.write, orders, "Swing plans from research.py. Nothing is placed until "
                          "check.py passes each order and you say yes to it.")
            print("Wrote %d entry order(s) to %s. Next: check.py." % (len(orders), a.write))
    return 0 if out else 1


def cmd_intraday(a):
    cfg, pf, prices = _ctx(a)
    acct, cash = account_value(cfg, pf, prices)
    now = _now(a)
    out, orders, errors = [], [], []
    for i, s in enumerate(a.symbols):
        if i and not a.offline:
            time.sleep(PAUSE)
        try:
            b5 = chart(s, "5d", "5m", a.offline)
            daily = chart(s, "3mo", "1d", a.offline)
            res = intraday_analysis(b5, daily, now, bool(cfg.get("allow_short_intraday")))
        except ResearchError as e:
            errors.append(str(e))
            continue
        sz = None
        p = res["plan"]
        if p:
            sz = size(p["entry"], p["stop"], acct, cash, float(cfg["intraday_risk_pct"]), float(cfg["max_position_pct"]))
            if sz["qty"]:
                sz["round_trip_charges_inr"] = intraday_charges(sz["value_inr"])
                if cfg.get("allow_intraday"):
                    orders.append({"symbol": plain(s), "exchange": "NSE", "side": p["side"], "qty": str(sz["qty"]),
                                   "type": "limit", "limit_price": "%.2f" % p["entry"], "product": "MIS",
                                   "validity": "DAY", "stop_loss": "%.2f" % p["stop"], "target": "%.2f" % p["target"],
                                   "why": "intraday %s: stop %s, target %s. Place the stop-loss (SL-M, MIS) right "
                                          "after the fill; square off by 15:15 IST."
                                          % (p["setup"], _inr(p["stop"]), _inr(p["target"]))})
        out.append({"symbol": plain(s), "analysis": res, "size": sz})
    if a.json:
        print(json.dumps({"results": out, "errors": errors, "now_ist": now.isoformat()}, indent=2, default=str))
    else:
        for r in out:
            x, lv = r["analysis"], r["analysis"]["levels"]
            print("%s  %s  prev H/L/C %s / %s / %s  pivots S2 %s S1 %s P %s R1 %s R2 %s"
                  % (r["symbol"], x["mode"], _inr(lv["prev_high"]), _inr(lv["prev_low"]), _inr(lv["prev_close"]),
                     _inr(lv["S2"]), _inr(lv["S1"]), _inr(lv["P"]), _inr(lv["R1"]), _inr(lv["R2"])))
            if x.get("opening_range"):
                print("    %s: last %s, opening range %s to %s, VWAP %s, gap %+.2f%%"
                      % (x["as_of"], _inr(x["last"]), _inr(x["opening_range"]["low"]),
                         _inr(x["opening_range"]["high"]), _inr(x["vwap"]) if x["vwap"] else "-", x["gap_pct"]))
            p, sz = x["plan"], r["size"]
            if p:
                print("    SETUP %s: %s at %s, stop %s, target %s (2R). %s."
                      % (p["setup"], p["side"].upper(), _inr(p["entry"]), _inr(p["stop"]), _inr(p["target"]), p["exit"]))
                if sz and sz["qty"]:
                    print("    size: %d shares = %s, losing %s at the stop; round-trip charges about %s"
                          % (sz["qty"], _inr(sz["value_inr"]), _inr(sz["risk_inr"]), _inr(sz["round_trip_charges_inr"])))
                elif sz:
                    print("    size: 0 shares: %s" % sz.get("why"))
            for n in x["notes"]:
                print("    note: " + n)
        for e in errors:
            print("error: " + e)
        print("5-minute bars from Yahoo are about 15 minutes delayed: use these as levels to watch in your broker's "
              "live chart, not as live signals. SEBI found most individual intraday traders lose money. Education, "
              "not financial advice.")
    if a.write:
        if not cfg.get("allow_intraday"):
            print("Not written: intraday orders need \"allow_intraday\": true in trader/config.json.")
        elif orders:
            _write_orders(a.write, orders, "Intraday plans from research.py (MIS). Nothing is placed until "
                          "check.py passes each order and you say yes to it.")
            print("Wrote %d intraday order(s) to %s. Next: check.py." % (len(orders), a.write))
        else:
            print("Nothing to write: no intraday setup with a position size.")
    return 0 if out else 1


def universe(a, cfg, pf):
    if a.universe in ("holdings", "watchlist"):
        syms = [str(p.get("ticker") or p.get("symbol") or "").upper() for p in (pf or {}).get("positions", [])]
        if a.universe == "watchlist":
            syms += [s.upper() for s in cfg.get("watchlist") or []]
        return [s for s in dict.fromkeys(syms) if s], {}
    names = index_list(a.universe, a.offline)
    return sorted(names), names


def cmd_scan(a):
    cfg, pf, prices = _ctx(a)
    syms, industry = universe(a, cfg, pf)
    if not syms:
        raise ResearchError("the universe %s is empty" % a.universe)
    nifty = _nifty(a)
    acct, cash = account_value(cfg, pf, prices)
    rows, errors = [], []
    for i, s in enumerate(syms):
        if i and not a.offline:
            time.sleep(PAUSE)
        try:
            res = swing_analysis(chart(s, "1y", "1d", a.offline), nifty)
        except ResearchError as e:
            errors.append(str(e))
            continue
        sz = None
        if res["plan"]:
            sz = size(res["plan"]["entry"], res["plan"]["stop"], acct, cash, float(cfg["risk_per_trade_pct"]),
                      float(cfg["max_position_pct"]))
        rows.append({"symbol": s, "industry": industry.get(s, ""), "analysis": res, "size": sz})
    rows.sort(key=lambda r: (r["analysis"]["plan"] is None, -r["analysis"]["score"],
                             -(r["analysis"]["facts"]["rs_vs_nifty_3m"] or -999)))
    if a.json:
        print(json.dumps({"universe": a.universe, "scanned": len(rows), "results": rows[:a.top], "errors": errors},
                         indent=2, default=str))
        return 0
    setups = [r for r in rows if r["analysis"]["plan"]]
    print("Scanned %d of %d %s stocks: %d with a swing setup today." % (len(rows), len(syms), a.universe, len(setups)))
    print("%-12s %-26s %5s %-34s %10s %10s %10s %8s" % ("symbol", "industry", "score", "setup", "entry", "stop",
                                                       "target", "vs Nifty"))
    for r in rows[:a.top]:
        x, p = r["analysis"], r["analysis"]["plan"]
        print("%-12s %-26s %5s %-34s %10s %10s %10s %8s" % (
            r["symbol"], r["industry"][:26], "%d/5" % x["score"], (p["setup"] if p else "-")[:34],
            _inr(p["entry"]) if p else "", _inr(p["stop"]) if p else "", _inr(p["target"]) if p else "",
            "n/a" if x["facts"]["rs_vs_nifty_3m"] is None else "%+.1f" % x["facts"]["rs_vs_nifty_3m"]))
    if errors:
        print("%d symbol(s) failed: %s" % (len(errors), "; ".join(errors[:3])))
    by_ind = {}
    for r in setups:
        by_ind[r["industry"]] = by_ind.get(r["industry"], 0) + 1
    crowded = [k for k, v in by_ind.items() if k and v >= 3]
    if crowded:
        print("Several setups in %s: taking them all is one sector bet, not several trades." % ", ".join(crowded))
    print("Run `swing SYMBOL` on a name for its full plan and size. Education, not financial advice.")
    return 0


def cmd_stock(a):
    nifty = _nifty(a)
    for i, s in enumerate(a.symbols):
        if i and not a.offline:
            time.sleep(PAUSE)
        v = stock_view(chart(s, "1y", "1d", a.offline), nifty)
        if a.json:
            print(json.dumps(dict(v, symbol=plain(s)), indent=2))
            continue
        print("%s  %s close %s  52w %s to %s  max drawdown 1y %.1f%%  volatility %s%% (Nifty %s%%)  %s the 200-day %s"
              % (plain(s), v["date"], _inr(v["close"]), _inr(v["low_52w"]), _inr(v["high_52w"]), v["max_drawdown_1y_pct"],
                 v["volatility_pct"], v["nifty_volatility_pct"], "above" if v["above_200d"] else "below",
                 _inr(v["sma200"]) if v["sma200"] else "-"))
        print("    returns  " + "  ".join("%s %s (Nifty %s)" % (k, _pct(v["ret_" + k]), _pct(v["nifty_" + k]))
                                       for k in ("1m", "3m", "6m", "1y")))
    if not a.json:
        print("Price history only: for an investing call also read the results, valuation and filings "
              "(news.py, shp.py, web research). Education, not financial advice.")
    return 0


def _pct(x):
    return "n/a" if x is None else "%+.1f%%" % x


def cmd_portfolio(a):
    cfg, pf, prices = _ctx(a)
    if not pf or not pf.get("positions"):
        raise ResearchError("no positions in %s" % a.portfolio)
    try:
        industry = index_list("nifty500", a.offline)
    except (ResearchError, OSError):
        industry = {}
    nifty = _nifty(a)
    today = _now(a).date()
    rows, total = [], float(pf.get("cash") or 0)
    for i, p in enumerate(pf["positions"]):
        s = str(p.get("ticker") or p.get("symbol") or "").upper()
        if i and not a.offline:
            time.sleep(PAUSE)
        try:
            bars = chart(s, "1y", "1d", a.offline)
            res = swing_analysis(bars, nifty)
            price = bars[-1]["c"]
        except ResearchError as e:
            rows.append({"symbol": s, "error": str(e)})
            continue
        sh = float(p.get("shares") or 0)
        cost = sum(float(l.get("cost") or 0) for l in p.get("lots", []))
        soon = []
        for l in p.get("lots", []):
            try:
                d = datetime.date.fromisoformat(str(l.get("date"))[:10])
            except ValueError:
                continue
            lt = d.replace(year=d.year + 1) + datetime.timedelta(days=1) if not (d.month == 2 and d.day == 29) else \
                datetime.date(d.year + 1, 3, 1)
            if today < lt <= today + datetime.timedelta(days=60):
                soon.append({"lot": l.get("id"), "long_term_on": lt.isoformat()})
        rows.append({"symbol": s, "industry": industry.get(s, ""), "value": sh * price, "cost": cost, "price": price,
                     "trend": res["facts"]["trend"], "rs": res["facts"]["rs_vs_nifty_3m"], "soon_long_term": soon})
        total += sh * price
    flags = []
    good = [r for r in rows if "error" not in r]
    for r in good:
        r["weight"] = 100 * r["value"] / total if total else 0
        if r["weight"] > float(cfg.get("max_position_pct") or 25):
            flags.append("%s is %.1f%% of the account, over your %s%% cap" % (r["symbol"], r["weight"], cfg["max_position_pct"]))
        if r["trend"] == "down" and r["value"] < r["cost"]:
            flags.append("%s is in a downtrend and below cost: decide whether the reason you bought it still holds "
                         "(and see capgains.py harvest)" % r["symbol"])
        for s in r["soon_long_term"]:
            flags.append("%s lot %s turns long term on %s: selling before then is taxed at 20%%, after at 12.5%% "
                         "above the exemption" % (r["symbol"], s["lot"], s["long_term_on"]))
    by_ind = {}
    for r in good:
        if r["industry"]:
            by_ind[r["industry"]] = by_ind.get(r["industry"], 0) + r["weight"]
    for k, v in by_ind.items():
        if v > 40:
            flags.append("%.0f%% of the account is in %s: one sector" % (v, k))
    cash_pct = 100 * float(pf.get("cash") or 0) / total if total else 0
    if a.json:
        print(json.dumps({"total_inr": round(total, 2), "cash_pct": round(cash_pct, 1), "positions": rows,
                          "industries": by_ind, "flags": flags}, indent=2, default=str))
        return 0
    print("Account %s, cash %.1f%%." % (_inr(total), cash_pct))
    print("%-12s %-26s %7s %14s %9s %-18s %9s" % ("symbol", "industry", "weight", "value", "gain", "trend", "vs Nifty"))
    for r in rows:
        if "error" in r:
            print("%-12s error: %s" % (r["symbol"], r["error"]))
            continue
        g = 100 * (r["value"] / r["cost"] - 1) if r["cost"] else 0
        print("%-12s %-26s %6.1f%% %14s %+8.1f%% %-18s %9s" % (r["symbol"], (r["industry"] or "-")[:26], r["weight"],
                                                            _inr(r["value"]), g, r["trend"][:18],
                                                            "n/a" if r["rs"] is None else "%+.1f" % r["rs"]))
    for f in flags:
        print("FLAG " + f)
    if not flags:
        print("No flags: within your cap, no sector over 40%, nothing in a downtrend below cost.")
    print("Education, not financial advice.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Research desk for Indian stocks (research.py).")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--now", help="IST time, ISO (default: now)")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("swing", help="swing setups on daily charts")
    p.add_argument("symbols", nargs="+")
    p.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH")
    p = sub.add_parser("intraday", help="intraday levels and setup on 5-minute bars")
    p.add_argument("symbols", nargs="+")
    p.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH")
    p = sub.add_parser("scan", help="rank an index for swing setups")
    p.add_argument("--universe", default="nifty50",
                   help="nifty50, niftynext50, nifty100, nifty500, holdings or watchlist")
    p.add_argument("--top", type=int, default=15)
    p = sub.add_parser("stock", help="the investing view")
    p.add_argument("symbols", nargs="+")
    sub.add_parser("portfolio", help="review trader/portfolio.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"swing": cmd_swing, "intraday": cmd_intraday, "scan": cmd_scan, "stock": cmd_stock,
                "portfolio": cmd_portfolio}[a.cmd](a)
    except ResearchError as e:
        print("research.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
