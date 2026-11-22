#!/usr/bin/env python3
"""NSE corporate announcements for trader-in-news (India). Standard library only.

    python3 news.py check                        # NEW filings for your holdings + watchlist; exit 0 if any
    python3 news.py check --symbols INFY TCS     # just these
    python3 news.py latest                       # the latest filings on NSE, tagged
    python3 news.py latest --tag results --tag dividend
    python3 news.py actions                      # upcoming dividends, splits, bonuses for your holdings

Sources (NSE's public RSS feeds, no login):
  announcements    https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml
  corporate action https://nsearchives.nseindia.com/content/RSS/Corporate_action.xml
  results          https://nsearchives.nseindia.com/content/RSS/Financial_Results.xml
  board meetings   https://nsearchives.nseindia.com/content/RSS/Board_Meetings.xml
Each feed holds only the most recent items (the announcements feed about the
last few hours), so run `check` often in market hours, or every evening, and
it alerts on each filing once (trader/state/news.json).

The feeds name the company, not the symbol. The tool matches names to NSE
symbols with NSE's equity list (trader/cache/EQUITY_L.csv, fetched if
missing). --offline DIR reads DIR/<feed>.xml and DIR/EQUITY_L.csv.
"""

import argparse
import csv
import datetime
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

UA = "trader-skill-india/1.0"   # NSE's archive stalls a User-Agent that contains a URL
BASE = "https://nsearchives.nseindia.com/content/RSS/%s.xml"
FEEDS = {"announcements": "Online_announcements", "corporate_actions": "Corporate_action",
         "results": "Financial_Results", "board_meetings": "Board_Meetings"}
EQUITY_L = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

# first match wins; (tag, pattern on the subject/description)
TAGS = [
    ("results", r"financial result|outcome of board meeting.*result|quarterly result|audited|unaudited"),
    ("dividend", r"dividend"),
    ("bonus", r"\bbonus\b"),
    ("split", r"split|sub-division|sub division"),
    ("buyback", r"buy ?back"),
    ("fund raise", r"\bqip\b|preferential|rights issue|fund rais|allotment|warrants|ncd|debenture"),
    ("merger", r"amalgamation|merger|demerger|scheme of arrangement"),
    ("acquisition", r"acquisition|acquire|takeover"),
    ("disposal", r"sale or disposal|disinvestment|divestment|slump sale"),
    ("order win", r"order|contract|award"),
    ("rating", r"credit rating|rating"),
    ("management change", r"resignation|appointment|cessation|change in (director|management|kmp)"),
    ("auditor", r"auditor"),
    ("pledge", r"pledge|encumbrance"),
    ("insider", r"insider|sast|regulation 29|regulation 31|reg\.? 7"),
    ("legal", r"litigation|dispute|penalty|show cause|sebi order|court|tribunal|nclt|gst demand|tax demand"),
    ("board meeting", r"board meeting"),
    ("investor meet", r"analyst|investor meet|con\. call|conference call|earnings call"),
    ("press release", r"press release|media release"),
]
RED_FLAGS = {"auditor", "pledge", "legal", "management change"}


class NewsError(Exception):
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
            return data
        except urllib.error.HTTPError as e:
            raise NewsError("%s answered HTTP %d" % (url, e.code))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = getattr(e, "reason", e)
    raise NewsError("could not reach %s: %s" % (url.split("/")[2], last))


# ---------------------------------------------------------------- names -> symbols

SUFFIX = re.compile(r"\b(limited|ltd|ltd\.|pvt|private|the|india|co|company|corporation|corp|inc)\b")


def norm(name):
    s = name.lower().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = SUFFIX.sub(" ", s)
    return " ".join(s.split())


def load_equity(offline=None, cache="trader/cache/EQUITY_L.csv"):
    if offline:
        path = os.path.join(offline, "EQUITY_L.csv")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    elif os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = fetch(EQUITY_L).decode("utf-8", "replace")
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write(text)
    rows = csv.reader(io.StringIO(text))
    head = [h.strip().upper() for h in next(rows, [])]
    i, j = head.index("SYMBOL"), head.index("NAME OF COMPANY")
    by_name, names = {}, {}
    for r in rows:
        if len(r) > max(i, j):
            by_name[norm(r[j])] = r[i].strip().upper()
            names[r[i].strip().upper()] = r[j].strip()
    return by_name, names


# ---------------------------------------------------------------- feeds

def tag_of(text):
    t = text.lower()
    for tag, pat in TAGS:
        if re.search(pat, t):
            return tag
    return "other"


def _fields(desc):
    """'A |SUBJECT: B |FACE VALUE:10' -> {'SUBJECT': 'B', ...} plus the lead text."""
    parts = [p.strip() for p in desc.split("|")]
    out = {"_lead": parts[0] if parts and ":" not in parts[0][:25] else ""}
    for p in parts:
        if ":" in p:
            k, v = p.split(":", 1)
            out[k.strip().upper()] = v.strip()
    return out


def parse_feed(data, feed, by_name):
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise NewsError("%s feed is not valid XML: %s" % (feed, e))
    items = []
    for it in root.iter("item"):
        title = " ".join((it.findtext("title") or "").split())
        desc = " ".join((it.findtext("description") or "").split())
        link = (it.findtext("link") or "").strip()
        when = (it.findtext("pubDate") or "").strip()
        company, extra = title, ""
        if " - Ex-Date:" in title:
            company, extra = title.split(" - Ex-Date:", 1)
            extra = "ex-date " + extra.strip()
        f = _fields(desc)
        subject = f.get("SUBJECT") or f.get("PURPOSE") or f.get("RELATING TO") or f.get("_lead") or desc
        tag = tag_of(subject + " " + (f.get("_lead") or ""))
        if feed == "results":
            tag = "results"
        sym = by_name.get(norm(company))
        key = "%s|%s|%s|%s" % (feed, company, subject, when)
        items.append({"feed": feed, "company": company.strip(), "symbol": sym, "subject": subject,
                      "detail": extra or desc[:300], "tag": tag, "red_flag": tag in RED_FLAGS,
                      "link": link, "published": when, "key": key, "fields": f})
    return items


def load_feeds(names, offline=None, by_name=None):
    items, errors = [], []
    for feed in names:
        try:
            if offline:
                with open(os.path.join(offline, FEEDS[feed] + ".xml"), "rb") as fh:
                    data = fh.read()
            else:
                data = fetch(BASE % FEEDS[feed])
            items += parse_feed(data, feed, by_name or {})
        except (NewsError, OSError) as e:
            errors.append(str(e))
    return items, errors


# ---------------------------------------------------------------- files

def _read(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise NewsError("%s is not valid JSON: %s" % (path, e))


def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def watched(a):
    syms = {s.upper() for s in (a.symbols or [])}
    if not a.symbols:
        for p in (_read(a.portfolio, {}) or {}).get("positions", []):
            syms.add(str(p.get("ticker") or p.get("symbol") or "").upper())
        tg = _read(a.targets, {}) or {}
        for w in tg.get("weights", []) if isinstance(tg, dict) else []:
            syms.add(str(w.get("ticker") or w.get("symbol") or "").upper())
        syms |= {s.upper() for s in ((_read(a.config, {}) or {}).get("watchlist") or [])}
    return syms - {""}


def line(i):
    flag = "  [check this]" if i["red_flag"] else ""
    return "%-17s %-12s %-18s %s%s%s" % (i["published"][:17], i["symbol"] or "?", i["tag"], i["subject"][:90],
                                          ("  (" + i["detail"][:60] + ")") if i["feed"] == "corporate_actions" else "",
                                          flag)


# ---------------------------------------------------------------- commands

def cmd_check(a):
    by_name, names = load_equity(a.offline)
    syms = watched(a)
    if not syms:
        raise NewsError("nothing to watch: no positions in %s, no targets, no \"watchlist\" in the config, "
                        "and no --symbols" % a.portfolio)
    unknown = sorted(s for s in syms if s not in names)
    items, errors = load_feeds(list(FEEDS), a.offline, by_name)
    st = _read(a.state, {}) or {}
    seen = set(st.get("seen", []))
    hits = [i for i in items if i["symbol"] in syms and i["key"] not in seen]
    for i in items:
        seen.add(i["key"])
    st["seen"] = sorted(seen)[-3000:]
    st["last_check"] = datetime.datetime.now().replace(microsecond=0).isoformat()
    if not a.no_write:
        _write(a.state, st)
    if a.json:
        print(json.dumps({"watched": sorted(syms), "new": hits, "errors": errors}, indent=2, ensure_ascii=False))
        return 0 if hits else 1
    for i in sorted(hits, key=lambda i: (not i["red_flag"], i["symbol"])):
        print("NEW " + line(i))
        if i["link"].endswith(".pdf"):
            print("    " + i["link"])
    if not hits:
        print("No new NSE filings for your %d symbol(s) in the %d item(s) on NSE's feeds right now."
              % (len(syms), len(items)))
    if unknown:
        print("Not on NSE's equity list, so never matched: %s" % ", ".join(unknown))
    for e in errors:
        print("error: " + e)
    print("Filings are what the company told the exchange, not advice. Read the PDF before acting on one.")
    return 0 if hits else 1


def cmd_latest(a):
    by_name, _ = load_equity(a.offline)
    items, errors = load_feeds(list(FEEDS) if not a.feed else a.feed, a.offline, by_name)
    if a.tag:
        items = [i for i in items if i["tag"] in a.tag]
    if a.json:
        print(json.dumps({"items": items, "errors": errors}, indent=2, ensure_ascii=False))
        return 0 if items else 1
    counts = {}
    for i in items:
        counts[i["tag"]] = counts.get(i["tag"], 0) + 1
    print("%d item(s) on NSE's feeds: %s" % (len(items), ", ".join("%s %d" % kv for kv in sorted(counts.items(),
                                                                                             key=lambda kv: -kv[1]))))
    for i in items[:a.top]:
        print(line(i))
    for e in errors:
        print("error: " + e)
    return 0 if items else 1


def cmd_actions(a):
    by_name, _ = load_equity(a.offline)
    syms = watched(a)
    items, errors = load_feeds(["corporate_actions"], a.offline, by_name)
    mine = [i for i in items if (i["symbol"] in syms) or a.all]
    if a.json:
        print(json.dumps({"actions": mine, "errors": errors}, indent=2, ensure_ascii=False))
        return 0 if mine else 1
    today = datetime.date.today()
    for i in mine:
        f = i["fields"]
        ex = i["detail"].replace("ex-date ", "")
        try:
            past = datetime.datetime.strptime(ex, "%d-%b-%Y").date() < today
        except ValueError:
            past = False
        print("%-12s %-11s %-60s record date %s%s" % (i["symbol"] or i["company"][:12], ex,
                                                      f.get("PURPOSE", i["subject"])[:60], f.get("RECORD DATE", "-"),
                                                      "  (ex-date passed)" if past else ""))
    if not mine:
        print("No corporate actions for your %d symbol(s) on NSE's feed right now (--all lists every company)."
              % len(syms))
    print("To get a dividend or bonus you must hold the shares at the end of the day BEFORE the ex-date "
          "(India settles T+1). After a split or bonus, update your lots for trader-in-tax.")
    return 0 if mine else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE announcements and corporate actions (trader-in-news).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--offline", metavar="DIR")
        p.add_argument("--json", action="store_true")
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--targets", default="trader/targets.json")
        p.add_argument("--config", default="trader/config.json", help='optional "watchlist": ["SYMBOL", ...]')
        p.add_argument("--symbols", nargs="*")

    p = sub.add_parser("check", help="new filings for your symbols")
    common(p)
    p.add_argument("--state", default="trader/state/news.json")
    p.add_argument("--no-write", action="store_true")
    p = sub.add_parser("latest", help="the latest filings on NSE")
    common(p)
    p.add_argument("--tag", action="append")
    p.add_argument("--feed", action="append", choices=list(FEEDS))
    p.add_argument("--top", type=int, default=40)
    p = sub.add_parser("actions", help="dividends, splits, bonuses")
    common(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"check": cmd_check, "latest": cmd_latest, "actions": cmd_actions}[a.cmd](a)
    except (NewsError, OSError, ValueError) as e:
        print("news.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
