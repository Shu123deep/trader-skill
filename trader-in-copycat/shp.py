#!/usr/bin/env python3
"""Shareholding patterns for trader-in-copycat (India). Standard library only.

Every listed company files its shareholding pattern with NSE and BSE each
quarter (SEBI LODR Regulation 31). It names every shareholder with more than
1% of the company: promoters, mutual funds, insurers, foreign investors, and
the individuals and companies that "superstar investors" hold through. This
tool reads those XBRL filings and rebuilds an investor's portfolio from them,
the way trader-copycat reads a US 13F.

    python3 shp.py fetch URL_OR_FILE ...        # parse filings into the local library
    python3 shp.py collect                      # new filings from NSE's RSS feed into the library
    python3 shp.py holders TATASTEEL            # who owns it: promoter %, public %, every named >1% holder
    python3 shp.py diff ALICON                  # what changed vs the previous quarter in the library
    python3 shp.py investor "ashish kacholia"   # an investor's holdings across the library
    python3 shp.py investor kacholia --write    # -> trader/targets.json weights for trader-budget
    python3 shp.py library                      # what is in the library

Where filings come from (NSE's public archive, no login):
  - NSE's "Shareholding Pattern" RSS feed lists the latest filings:
    https://nsearchives.nseindia.com/content/RSS/Shareholding_Pattern.xml
    `collect` fetches the new ones; run it daily and the library grows.
  - For a particular company, open its page on nseindia.com (Corporate
    filings > Shareholding Pattern), copy the XBRL link (it looks like
    https://nsearchives.nseindia.com/corporate/xbrl/SHP_..._WEB.xml) and
    pass it to `fetch`.
NSE's search API needs a browser session, so this tool does not use it.

The filings carry each holder's PAN. This tool never stores or prints it.
"""

import argparse
import datetime
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

UA = "trader-skill-india/1.0"   # NSE's archive stalls a User-Agent that contains a URL
RSS = "https://nsearchives.nseindia.com/content/RSS/Shareholding_Pattern.xml"
ALLOWED_PREFIX = "https://nsearchives.nseindia.com/"
HERE = os.path.dirname(os.path.abspath(__file__))
NS = "in-bse-shp:"

# axis -> plain category for named holders
CATEGORY = [
    ("MutualFunds", "mutual fund"), ("InsuranceCompanies", "insurance"),
    ("ForeignPortfolioInvestor", "foreign portfolio investor"), ("AlternativeInvestmentFunds", "AIF"),
    ("Banks", "bank"), ("ProvidentFunds", "provident/pension fund"), ("SovereignWealth", "sovereign fund"),
    ("OtherInstitutionsForeign", "foreign institution"), ("ForeignCompanies", "foreign company"),
    ("KeyManagerialPersonnel", "director / KMP"), ("NonResidentIndians", "NRI"),
    ("InExcessOfRsTwoLakh", "individual"), ("UpToRsTwoLakh", "individual"),
    ("BodiesCorporate", "company"), ("OtherNonInstitutions", "other"),
]


class ShpError(Exception):
    pass


# ---------------------------------------------------------------- network

def fetch(url, timeout=60, tries=2):
    if not url.startswith(ALLOWED_PREFIX):
        raise ShpError("only NSE archive links (%s...) are fetched; download other files yourself" % ALLOWED_PREFIX)
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
            raise ShpError("%s answered HTTP %d" % (url, e.code))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = getattr(e, "reason", e)
    raise ShpError("could not reach %s: %s" % (url.split("/")[2], last))


# ---------------------------------------------------------------- parse

def _local(tag):
    return tag.split("}", 1)[-1]


def parse_shp(data):
    """XBRL bytes -> {symbol, company, isin, as_of, promoter_pct, public_pct, holders: [...]}.
    Percentages are 0-100. PAN is dropped."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ShpError("not a valid XBRL file: %s" % e)
    contexts = {}
    for c in root:
        if _local(c.tag) != "context":
            continue
        typed = [(_local(m.tag), m.get("dimension", "")) for m in c.iter() if _local(m.tag) == "typedMember"]
        explicit = [(m.text or "").strip() for m in c.iter() if _local(m.tag) == "explicitMember"]
        contexts[c.get("id")] = {"typed": typed[0][1].split(":")[-1] if typed else None, "explicit": explicit}
    facts = {}
    for el in root:
        ref = el.get("contextRef")
        if ref is None:
            continue
        name = _local(el.tag)
        if name == "PermanentAccountNumberOfShareholder":
            continue
        facts.setdefault(ref, {})[name] = (el.text or "").strip()
    head = {}
    for f in facts.values():
        for k in ("Symbol", "NameOfTheCompany", "ISIN", "DateOfReport", "ScripCode"):
            if f.get(k) and k not in head:
                head[k] = f[k]
    if not head.get("Symbol") and not head.get("NameOfTheCompany"):
        raise ShpError("this XBRL is not a shareholding pattern (no company symbol or name)")

    def pct(v):
        try:
            return round(float(v) * 100, 2)
        except (TypeError, ValueError):
            return None

    def shares(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    holders = []
    for cid, f in facts.items():
        if "NameOfTheShareholder" not in f:
            continue
        nums = facts.get(cid[2:] if cid.startswith("D_") else cid, {})
        axis = (contexts.get(cid) or {}).get("typed") or ""
        promoter = "TypeOfPromoterShareholding" in f
        cat = "promoter group" if promoter else next((label for key, label in CATEGORY if key in axis), "other")
        sub = f.get("CategoryOfOtherIndianShareholders") or f.get("CategoryOfOtherNonInstitutions") or \
            f.get("CategoryOfOtherInstitutions") or ""
        holders.append({"name": " ".join(f["NameOfTheShareholder"].split()), "category": cat,
                        "detail": sub if sub and sub != "******" else "", "promoter": promoter,
                        "shares": shares(nums.get("NumberOfShares") or nums.get("NumberOfFullyPaidUpEquityShares")),
                        "pct": pct(nums.get("ShareholdingAsAPercentageOfTotalNumberOfShares"))})
    summary = {}
    for cid, c in contexts.items():
        if c["typed"] or len(c["explicit"]) != 1:
            continue
        m = c["explicit"][0].split(":")[-1]
        p = pct(facts.get(cid, {}).get("ShareholdingAsAPercentageOfTotalNumberOfShares"))
        if p is not None and m not in summary:
            summary[m] = p
    total = None
    for cid, c in contexts.items():
        if not c["typed"] and c["explicit"] and c["explicit"][-1].endswith("ShareholdingPatternMember"):
            total = shares(facts.get(cid, {}).get("NumberOfShares")) or total
    holders.sort(key=lambda h: (-(h["pct"] or 0), h["name"]))
    return {"symbol": (head.get("Symbol") or "").upper(), "company": head.get("NameOfTheCompany", ""),
            "isin": head.get("ISIN", ""), "bse_code": head.get("ScripCode", ""), "as_of": head.get("DateOfReport", ""),
            "promoter_pct": summary.get("ShareholdingOfPromoterAndPromoterGroupMember"),
            "public_pct": summary.get("PublicShareholdingMember"),
            "mf_pct": summary.get("MutualFundsOrUTIMember"),
            "fpi_pct": summary.get("InstitutionsForeignPortfolioInvestorCategoryOneMember"),
            "total_shares": total, "holders": holders}


def parse_rss(data):
    root = ET.fromstring(data)
    out = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        if link.endswith(".xml") and "/SHP_" in link:
            out.append({"company": (it.findtext("title") or "").strip(), "link": link,
                        "description": (it.findtext("description") or "").strip(),
                        "published": (it.findtext("pubDate") or "").strip()})
    return out


# ---------------------------------------------------------------- library

def _read(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise ShpError("%s is not valid JSON: %s" % (path, e))


def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def save(lib, snap, source):
    key = "%s_%s" % (snap["symbol"] or snap["isin"], snap["as_of"])
    snap = dict(snap, source=source, saved=datetime.date.today().isoformat())
    _write(os.path.join(lib, key + ".json"), snap)
    return key


def library(lib):
    out = []
    if not os.path.isdir(lib):
        return out
    for fn in sorted(os.listdir(lib)):
        if fn.endswith(".json"):
            try:
                out.append(_read(os.path.join(lib, fn), None))
            except ShpError:
                continue
    return [s for s in out if s]


def latest_by_symbol(snaps):
    best = {}
    for s in snaps:
        k = s["symbol"] or s["isin"]
        if k not in best or s["as_of"] > best[k]["as_of"]:
            best[k] = s
    return best


def tokens(s):
    return set("".join(c if c.isalnum() else " " for c in s.upper()).split())


def load_investors(user_path):
    base = (_read(os.path.join(HERE, "investors.json"), {}) or {}).get("investors", [])
    mine = (_read(user_path, {}) or {}).get("investors", [])
    by = {i["name"]: i for i in base}
    for i in mine:
        by[i["name"]] = i
    return list(by.values())


def aliases_for(query, investors):
    """The query plus every alias of a watched investor whose name or alias matches it."""
    q = tokens(query)
    out = [query]
    for inv in investors:
        names = [inv["name"]] + inv.get("match", [])
        if any(q <= tokens(n) for n in names):
            out += inv.get("match", [])
    return out


def holder_matches(name, aliases):
    t = tokens(name)
    return any(tokens(a) and tokens(a) <= t for a in aliases)


# ---------------------------------------------------------------- commands

def _bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def cmd_fetch(a):
    n = 0
    for src in a.sources:
        data = fetch(src) if src.startswith("http") else _bytes(src)
        snap = parse_shp(data)
        key = save(a.library, snap, src)
        n += 1
        print("Saved %s: %s, quarter ending %s, %d named holder(s)." % (key, snap["company"], snap["as_of"],
                                                                     len(snap["holders"])))
    return 0 if n else 1


def cmd_collect(a):
    data = _bytes(a.offline) if a.offline else fetch(RSS)
    items = parse_rss(data)
    st = _read(a.state, {}) or {}
    seen = set(st.get("seen", []))
    new = [i for i in items if i["link"] not in seen]
    saved = 0
    for i, it in enumerate(new):
        if i:
            time.sleep(0.5)
        try:
            snap = parse_shp(fetch(it["link"]))
            key = save(a.library, snap, it["link"])
            saved += 1
            print("Saved %s (%s)." % (key, it["company"]))
        except ShpError as e:
            print("skip %s: %s" % (it["company"], e))
        seen.add(it["link"])
    st["seen"] = sorted(seen)[-2000:]
    st["last_collect"] = datetime.datetime.now().replace(microsecond=0).isoformat()
    _write(a.state, st)
    print("%d filing(s) in the feed, %d new, %d saved. Library: %s (%d snapshot(s))."
          % (len(items), len(new), saved, a.library, len(library(a.library))))
    return 0


def _snaps_for(a, symbol):
    rows = sorted([s for s in library(a.library) if s["symbol"] == symbol.upper()], key=lambda s: s["as_of"])
    if not rows:
        raise ShpError("no shareholding pattern for %s in %s. Run `shp.py fetch <XBRL link>` first (Corporate "
                       "filings > Shareholding Pattern on nseindia.com)." % (symbol.upper(), a.library))
    return rows


def _p(x):
    return "   n/a" if x is None else "%6.2f%%" % x


def cmd_holders(a):
    s = _snaps_for(a, a.symbol)[-1]
    if a.json:
        print(json.dumps(s, indent=2, ensure_ascii=False))
        return 0
    print("%s (%s, ISIN %s), shareholding as of %s" % (s["company"], s["symbol"], s["isin"], s["as_of"]))
    print("Promoter group %s, public %s, mutual funds %s, FPIs %s"
          % (_p(s["promoter_pct"]), _p(s["public_pct"]), _p(s["mf_pct"]), _p(s["fpi_pct"])))
    print("")
    print("%-8s %-26s %-50s %16s" % ("holding", "category", "holder (named when over 1%)", "shares"))
    for h in s["holders"]:
        if not h["pct"] and not a.all:
            continue
        print("%-8s %-26s %-50s %16s" % (_p(h["pct"]), h["category"][:26], h["name"][:50],
                                          "{:,}".format(h["shares"]) if h["shares"] else ""))
    hidden = sum(1 for h in s["holders"] if not h["pct"])
    if hidden and not a.all:
        print("(%d promoter-group member(s) with under 0.01%% hidden; --all shows them)" % hidden)
    return 0


def diff_snaps(old, new):
    o = {h["name"].upper(): h for h in old["holders"] if h["pct"]}
    n = {h["name"].upper(): h for h in new["holders"] if h["pct"]}
    out = []
    for k in sorted(set(o) | set(n)):
        a, b = o.get(k), n.get(k)
        if a and not b:
            out.append(("dropped below 1% or exited", a["name"], a["pct"], None))
        elif b and not a:
            out.append(("new above 1%", b["name"], None, b["pct"]))
        elif abs((b["pct"] or 0) - (a["pct"] or 0)) >= 0.01:
            out.append(("raised" if b["pct"] > a["pct"] else "cut", b["name"], a["pct"], b["pct"]))
    return out


def cmd_diff(a):
    rows = _snaps_for(a, a.symbol)
    if len(rows) < 2:
        raise ShpError("only one quarter of %s in the library (%s). Fetch the previous quarter's XBRL too."
                       % (a.symbol.upper(), rows[0]["as_of"]))
    old, new = rows[-2], rows[-1]
    ch = diff_snaps(old, new)
    if a.json:
        print(json.dumps({"from": old["as_of"], "to": new["as_of"], "changes": ch}, indent=2, ensure_ascii=False))
        return 0
    print("%s: %s -> %s. Promoter %s -> %s, public %s -> %s."
          % (new["company"], old["as_of"], new["as_of"], _p(old["promoter_pct"]), _p(new["promoter_pct"]),
             _p(old["public_pct"]), _p(new["public_pct"])))
    for what, name, p0, p1 in ch:
        print("  %-28s %-50s %s -> %s" % (what, name[:50], _p(p0), _p(p1)))
    if not ch:
        print("  No named holder above 1% changed.")
    print("Holders under 1% are not named, so 'dropped below 1%' does not mean they sold everything.")
    return 0


def investor_portfolio(snaps, aliases, prices=None):
    rows = []
    for sym, s in sorted(latest_by_symbol(snaps).items()):
        hit = [h for h in s["holders"] if holder_matches(h["name"], aliases) and h["pct"]]
        if not hit:
            continue
        sh = sum(h["shares"] or 0 for h in hit)
        price = (prices or {}).get(s["symbol"])
        rows.append({"ticker": s["symbol"], "name": s["company"], "as_of": s["as_of"],
                     "pct_of_company": round(sum(h["pct"] for h in hit), 2), "shares": sh,
                     "held_as": sorted({h["name"] for h in hit}), "price": price,
                     "value": round(sh * price, 2) if price else None})
    valued = [r for r in rows if r["value"]]
    total = sum(r["value"] for r in valued)
    for r in rows:
        r["weight"] = round(100.0 * r["value"] / total, 2) if r["value"] and total else None
    rows.sort(key=lambda r: (-(r["value"] or 0), -r["pct_of_company"]))
    return rows


def cmd_investor(a):
    q = " ".join(a.name)
    aliases = aliases_for(q, load_investors(a.investors))
    pdoc = _read(a.prices, {}) or {}
    prices = {str(k).upper(): float(v) for k, v in (pdoc.get("prices", pdoc) or {}).items()
              if isinstance(v, (int, float))}
    snaps = library(a.library)
    rows = investor_portfolio(snaps, aliases, prices)
    if a.json:
        print(json.dumps({"query": q, "aliases": aliases, "holdings": rows}, indent=2, ensure_ascii=False))
        return 0 if rows else 1
    covered = len(latest_by_symbol(snaps))
    if not rows:
        print("No holder matching %s in the %d compan(ies) in the library. The library only knows the filings "
              "you fetched or collected." % (" | ".join(aliases), covered))
        return 1
    print("%s: %d holding(s) found across %d compan(ies) in the library (matched: %s)."
          % (q, len(rows), covered, " | ".join(aliases)))
    print("%-12s %-34s %9s %14s %16s %7s  %s" % ("symbol", "company", "% of co.", "shares", "value", "weight", "as of"))
    for r in rows:
        print("%-12s %-34s %8.2f%% %14s %16s %7s  %s" % (
            r["ticker"], r["name"][:34], r["pct_of_company"], "{:,}".format(r["shares"]),
            ("₹{:,.2f} Cr".format(r["value"] / 1e7)) if r["value"] else "no price",
            ("%.2f%%" % r["weight"]) if r["weight"] is not None else "-", r["as_of"]))
    missing = [r["ticker"] for r in rows if not r["value"]]
    if missing:
        print("No price for %s: run trader-in-alerts `nse.py quote %s --write`, then run this again to weight them."
              % (", ".join(missing), " ".join(missing)))
    if a.write:
        weights = [{"ticker": r["ticker"], "name": r["name"], "weight": r["weight"], "pct_of_company": r["pct_of_company"],
                    "shares": r["shares"], "value": r["value"], "as_of": r["as_of"]} for r in rows if r["weight"]]
        if not weights:
            raise ShpError("nothing to write: no holding has a price")
        _write(a.write, {"source": "shareholding patterns (holders over 1%%) matching %s" % " | ".join(aliases),
                         "market": "IN", "currency": "INR",
                         "as_of": max(r["as_of"] for r in rows),
                         "method": "named holdings over 1% of each company, valued at trader/prices.json, scaled to 100",
                         "weights": weights})
        print("Wrote %d weight(s) to %s for trader-budget (size.py --whole-shares)." % (len(weights), a.write))
    print("Only holdings over 1% of a company are named, filings lag the quarter by up to 21 days, and the "
          "library covers only the companies you fetched. Education, not financial advice.")
    return 0


def cmd_library(a):
    snaps = library(a.library)
    for s in sorted(snaps, key=lambda s: (s["symbol"], s["as_of"])):
        print("%-12s %s  %-40s %3d holder(s)" % (s["symbol"], s["as_of"], s["company"][:40], len(s["holders"])))
    print("%d snapshot(s), %d compan(ies) in %s." % (len(snaps), len(latest_by_symbol(snaps)), a.library))
    return 0 if snaps else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Indian shareholding patterns (trader-in-copycat).")
    ap.add_argument("--library", default="trader/cache/shp", help="where parsed filings are kept")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("fetch", help="parse XBRL links or files into the library")
    p.add_argument("sources", nargs="+")
    p = sub.add_parser("collect", help="new filings from NSE's RSS feed")
    p.add_argument("--offline", metavar="FILE", help="a saved Shareholding_Pattern.xml")
    p.add_argument("--state", default="trader/state/shp.json")
    p = sub.add_parser("holders", help="who owns a company")
    p.add_argument("symbol")
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("diff", help="changes vs the previous quarter")
    p.add_argument("symbol")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("investor", help="an investor's holdings across the library")
    p.add_argument("name", nargs="+")
    p.add_argument("--investors", default="trader/investors.json")
    p.add_argument("--prices", default="trader/prices.json")
    p.add_argument("--write", nargs="?", const="trader/targets.json", metavar="PATH")
    p.add_argument("--json", action="store_true")
    sub.add_parser("library", help="list the library")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"fetch": cmd_fetch, "collect": cmd_collect, "holders": cmd_holders, "diff": cmd_diff,
                "investor": cmd_investor, "library": cmd_library}[a.cmd](a)
    except (ShpError, OSError) as e:
        print("shp.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
