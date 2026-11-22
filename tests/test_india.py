"""The India edition (trader-in-*), offline, on real NSE / Yahoo files saved on 2026-11-22."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

from _load import FIX, ROOT, load

nse = load("trader-in-alerts", "nse")
cg = load("trader-in-tax", "capgains")
deals = load("trader-in-deals", "deals")
shp = load("trader-in-copycat", "shp")
news = load("trader-in-news", "news")
risk = load("trader-in-risk", "check")
broker = load("trader-in-broker", "ticket")

D = datetime.date
AL = os.path.join(FIX, "trader-in-alerts")


def text(*parts):
    with open(os.path.join(FIX, *parts), encoding="utf-8") as fh:
        return fh.read()


def run(main, argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


class Money(unittest.TestCase):
    def test_indian_digit_grouping(self):
        self.assertEqual(nse.inr(1234567.891), "₹12,34,567.89")
        self.assertEqual(nse.inr(999), "₹999.00")
        self.assertEqual(cg.inr(-125000), "-₹1,25,000.00")
        self.assertEqual(risk.inr(27550), "₹27,550")


class Clock(unittest.TestCase):
    def st(self, ist):
        return nse.market_status(nse.parse_now(ist))

    def test_ist_session_and_holidays(self):
        self.assertTrue(self.st("2026-11-23T09:15")["open"])
        self.assertFalse(self.st("2026-11-23T15:30")["open"])
        self.assertIn("pre-open", self.st("2026-11-23T09:05")["reason"])
        tue = self.st("2026-11-24T11:00")
        self.assertFalse(tue["open"])
        self.assertIn("Guru Nanak", tue["reason"])
        self.assertEqual(tue["next_open"], "2026-11-25 09:15")
        self.assertIn("election", self.st("2026-01-15T10:00")["reason"].lower())
        self.assertEqual(self.st("2026-11-21T10:00")["reason"], "weekend")

    def test_utc_input_and_unknown_year(self):
        self.assertTrue(self.st("2026-11-23T04:00Z")["open"])          # 09:30 IST
        self.assertFalse(self.st("2027-03-01T10:00")["calendar_known"])
        st = nse.market_status(nse.parse_now("2027-01-26T10:00"), {"2027-01-26": "Republic Day"})
        self.assertFalse(st["open"])
        self.assertTrue(st["calendar_known"])

    def test_the_2026_list_matches_the_nse_circular(self):
        self.assertEqual(len(nse.NSE_HOLIDAYS), 16)  # 15 in NSE/FAOP/71777 + 15 Jan (NSE/FAOP/72262)
        for d in nse.NSE_HOLIDAYS:
            self.assertLess(D.fromisoformat(d).weekday(), 5, d)


class Quotes(unittest.TestCase):
    def test_bhavcopy(self):
        table, day = nse.parse_bhavcopy(text("trader-in-alerts", "bhavcopy-20112026.csv"), D(2026, 11, 20))
        self.assertEqual(day, D(2026, 11, 20))
        self.assertEqual(table["RELIANCE"]["price"], 1377.5)
        self.assertEqual(table["M&M"]["price"], 3545.0)
        self.assertNotIn("1018GS2026", table)        # a government bond, not an equity series
        self.assertAlmostEqual(table["RELIANCE"]["delivery_pct"], 49.0, places=0)

    def test_a_holiday_file_holds_the_previous_day(self):
        with self.assertRaises(nse.AlertError) as e:
            nse.parse_bhavcopy(text("trader-in-alerts", "bhavcopy-15012026.csv"), D(2026, 1, 15))
        self.assertIn("2026-01-14", str(e.exception))

    def test_last_bhavcopy_walks_back_over_the_holiday(self):
        off = tempfile.mkdtemp()
        try:
            shutil.copy(os.path.join(AL, "bhavcopy-20112026.csv"), off)
            q, errs = nse.get_quotes(["RELIANCE", "TCS"], "bhavcopy", off, nse.parse_now("2026-11-24T20:00"))
            self.assertEqual(errs, [])
            self.assertEqual(q["TCS"]["price"], 2376.2)
        finally:
            shutil.rmtree(off)

    def test_yahoo(self):
        q = nse.parse_yahoo(text("trader-in-alerts", "yahoo-RELIANCE.json"), "RELIANCE")
        self.assertEqual(q["currency"], "INR")
        self.assertEqual(q["price"], 1377.5)
        self.assertEqual(nse.yahoo_symbol("m&m"), "M&M.NS")
        self.assertEqual(nse.yahoo_symbol("500325.BO"), "500325.BO")
        self.assertIn("M%26M.NS", nse.yahoo_url("M&M"))
        with self.assertRaises(nse.AlertError):
            nse.parse_yahoo(json.dumps({"chart": {"result": [{"meta": {"regularMarketPrice": 1, "currency": "USD"}}]}}), "AAPL")

    def test_symbols(self):
        rows = nse.parse_symbols(text("trader-in-alerts", "EQUITY_L.csv"))
        self.assertEqual([r["symbol"] for r in nse.find_symbol(rows, "INE002A01018")], ["RELIANCE"])
        # both share the words, which is why the skill confirms the company with the user
        self.assertEqual({r["symbol"] for r in nse.find_symbol(rows, "reliance industries")}, {"RELIANCE", "RELCHEMQ"})
        self.assertEqual(nse.find_symbol(rows, "m&m")[0]["name"], "Mahindra & Mahindra Limited")


class Alerts(unittest.TestCase):
    def test_rings_once_in_rupees(self):
        q = {"RELIANCE": {"price": 1377.5, "prev_close": 1375.6}, "TCS": {"price": 2376.2, "prev_close": 2342.7}}
        rules = [{"id": "r", "symbol": "RELIANCE", "type": "above", "price": 1000},
                 {"id": "t", "symbol": "TCS", "type": "pct_move", "pct": 1},
                 {"id": "b", "type": "opening_bell"}]
        now = nse.parse_now("2026-11-23T10:00")
        rings, state = nse.evaluate(rules, q, nse.market_status(now), {}, now)
        self.assertEqual([r["id"] for r in rings], ["r", "t", "b"])
        self.assertIn("₹1,377.50, at or above your target of ₹1,000.00", rings[0]["message"])
        again, _ = nse.evaluate(rules, q, nse.market_status(now), state, now)
        self.assertEqual(again, [])


class Tax(unittest.TestCase):
    def sale(self, term, gain, date):
        st, lt = cg.rates_on(D.fromisoformat(date))
        return {"date": date, "lots": [{"term": term, "gain": gain, "rate": st if term == "short" else lt}]}

    def test_set_off_exemption_and_rates(self):
        f = cg.fy_tax([self.sale("short", 100000, "2026-05-01"), self.sale("long", 200000, "2026-06-01"),
                       self.sale("short", -30000, "2026-07-01"), self.sale("long", -50000, "2026-08-01")], 2026)
        self.assertEqual((f["stcg_taxable"], f["ltcg_taxable"], f["tax"]), (70000, 25000, 17125))
        self.assertEqual(f["tax_with_cess"], 17810)

    def test_long_term_loss_cannot_touch_short_term_gain(self):
        f = cg.fy_tax([self.sale("short", 50000, "2026-05-01"), self.sale("long", -80000, "2026-06-01")], 2026)
        self.assertEqual(f["stcg_taxable"], 50000)
        self.assertEqual(f["carry_forward"]["long"], 80000)

    def test_short_term_loss_offsets_long_term_gain_and_brought_forward(self):
        f = cg.fy_tax([self.sale("long", 200000, "2026-05-01"), self.sale("short", -60000, "2026-06-01")], 2026)
        self.assertEqual((f["ltcg_taxable"], f["tax"]), (15000, 1875))
        f = cg.fy_tax([self.sale("short", 10000, "2026-05-01"), self.sale("long", 150000, "2026-06-01")], 2026,
                      {"short": 20000})
        self.assertEqual((f["stcg_taxable"], f["ltcg_taxable"]), (0, 15000))

    def test_old_rates_and_exemption(self):
        f = cg.fy_tax([self.sale("long", 100000, "2024-06-01"), self.sale("long", 100000, "2024-09-01")], 2024)
        self.assertEqual((f["ltcg_taxable"], f["tax"]), (75000, 7500))
        f = cg.fy_tax([self.sale("long", 150000, "2023-06-01")], 2023)
        self.assertEqual((f["exemption"], f["tax"]), (100000, 5000))

    def test_terms_years_and_grandfathering(self):
        self.assertEqual(cg.term(D(2025, 11, 22), D(2026, 11, 22)), "short")
        self.assertEqual(cg.term(D(2025, 11, 22), D(2026, 11, 23)), "long")
        self.assertEqual((cg.fy_start(D(2027, 3, 31)), cg.fy_start(D(2027, 4, 1))), (2026, 2027))
        lot = {"id": "A-1", "date": D(2015, 1, 1), "shares": 1, "cost": 100, "per_share": 100, "fmv": 300}
        self.assertEqual(cg.simulate_sale([lot], 1, 250, D(2026, 5, 1))["long"], 0)
        self.assertEqual(cg.simulate_sale([lot], 1, 400, D(2026, 5, 1))["long"], 100)
        self.assertEqual(cg.simulate_sale([lot], 1, 80, D(2026, 5, 1))["long"], -20)

    def test_example_portfolio_fifo_and_harvests(self):
        with open(os.path.join(ROOT, "trader-in-tax", "portfolio.example.json"), encoding="utf-8") as fh:
            pf = json.load(fh)
        prices = {"RELIANCE": 1377.5, "TCS": 2376.2, "INFY": 1098.0, "HDFCBANK": 846.0}
        today = D(2026, 11, 22)
        sale = cg.simulate_sale(cg.lots_of(pf)["INFY"], 50, 1098.0, today)
        self.assertEqual((sale["long"], sale["short"]), (16720, -720))
        f = cg.fy_tax([], 2026, None, sale, today)
        self.assertEqual((f["tax"], f["exemption_left"]), (0, 109000))
        gains, _ = cg.gain_candidates(pf, prices, today, {}, {})
        self.assertEqual([(c["ticker"], c["shares"]) for c in gains], [("HDFCBANK", 100), ("INFY", 40)])
        losses, _ = cg.loss_candidates(pf, prices, today, {}, {})
        self.assertEqual({c["ticker"] for c in losses}, {"TCS", "RELIANCE"})
        orders = cg.orders_from(gains, "gain")
        self.assertEqual(orders[0]["product"], "CNC")
        self.assertIn("next trading day", orders[0]["why"])


class Deals(unittest.TestCase):
    def setUp(self):
        self.off = tempfile.mkdtemp()
        for n in ("bulk.csv", "block.csv"):
            shutil.copy(os.path.join(FIX, "trader-in-deals", n), self.off)
        with open(os.path.join(self.off, "bulk.csv"), "a", encoding="utf-8") as fh:
            fh.write("20-NOV-2026,SHADOWFAX,Shadowfax Technologies Limited,ASHISH  KACHOLIA,BUY,1000000,308.00,-\n")

    def tearDown(self):
        shutil.rmtree(self.off)

    def test_parse_merge_and_intraday(self):
        day, errs = deals.load_day(self.off)
        self.assertEqual(errs, [])
        both = [d for d in day if d["kind"] == "block+bulk"]
        self.assertEqual(len(both), 1)
        self.assertEqual(both[0]["client"], "DINESH R AGARWAL")
        churn = [d for d in day if d["intraday"]]
        self.assertTrue(churn and all(d["client"] != "ASHISH KACHOLIA" for d in churn))

    def test_investor_match_and_watch_once(self):
        invs = deals.load_investors(os.path.join(self.off, "none.json"))
        self.assertEqual(deals.match_investor("ASHISH KACHOLIA", invs), "Ashish Kacholia")
        self.assertEqual(deals.match_investor("RARE ENTERPRISES", invs), "Rekha Jhunjhunwala")
        self.assertIsNone(deals.match_investor("QE SECURITIES LLP", invs))
        st = os.path.join(self.off, "deals.json")
        args = ["watch", "--offline", self.off, "--state", st, "--portfolio", os.path.join(self.off, "none.json"),
                "--investors", os.path.join(self.off, "none.json")]
        code, out = run(deals.main, args)
        self.assertEqual(code, 0)
        self.assertIn("ASHISH KACHOLIA", out)
        code, out = run(deals.main, args)
        self.assertEqual(code, 1)


class Copycat(unittest.TestCase):
    def setUp(self):
        self.lib = tempfile.mkdtemp()
        with open(os.path.join(FIX, "trader-in-copycat", "SHP_SIGMA_2025-12-31.xml"), "rb") as fh:
            self.snap = shp.parse_shp(fh.read())

    def tearDown(self):
        shutil.rmtree(self.lib)

    def test_parse(self):
        s = self.snap
        self.assertEqual((s["symbol"], s["as_of"]), ("SIGMA", "2025-12-31"))
        self.assertEqual((s["promoter_pct"], s["public_pct"]), (73.22, 26.78))
        top = s["holders"][0]
        self.assertEqual((top["name"], top["pct"], top["promoter"]), ("Prakash Ratilal Parikh", 20.8, True))
        public = {h["name"]: h for h in s["holders"] if not h["promoter"]}
        self.assertEqual(public["Binaben Janakbhai Shah"]["category"], "individual")
        self.assertEqual(public["Angat Godiwala"]["category"], "NRI")

    def test_no_pan_is_kept(self):
        blob = json.dumps(self.snap)
        self.assertNotIn("PermanentAccount", blob)
        self.assertNotRegex(blob, r"[A-Z]{5}[0-9]{4}[A-Z]")

    def test_diff_and_investor(self):
        shp.save(self.lib, self.snap, "fixture")
        old = json.loads(json.dumps(self.snap))
        old["as_of"] = "2025-09-30"
        for h in old["holders"]:
            if h["name"].startswith("Binaben"):
                h["pct"] = 3.10
        old["holders"] = [h for h in old["holders"] if not h["name"].startswith("Divyaprabhaben")]
        shp.save(self.lib, old, "fixture")
        ch = shp.diff_snaps(old, self.snap)
        self.assertIn(("raised", "Binaben Janakbhai Shah", 3.10, 4.12), ch)
        self.assertIn(("new above 1%", "Divyaprabhaben Pravinbhai Shah", None, 1.33), ch)
        rows = shp.investor_portfolio(shp.library(self.lib), ["BINABEN SHAH"], {"SIGMA": 300.0})
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["pct_of_company"], rows[0]["weight"]), (4.12, 100.0))

    def test_rss_and_link_guard(self):
        with open(os.path.join(FIX, "trader-in-copycat", "Shareholding_Pattern.xml"), "rb") as fh:
            items = shp.parse_rss(fh.read())
        self.assertTrue(items and all(i["link"].startswith(shp.ALLOWED_PREFIX) for i in items))
        with self.assertRaises(shp.ShpError):
            shp.fetch("https://example.com/SHP_1.xml")


class News(unittest.TestCase):
    def setUp(self):
        self.off = os.path.join(FIX, "trader-in-news")
        self.by_name, self.names = news.load_equity(self.off)

    def test_feeds_match_symbols_and_tag(self):
        items, errs = news.load_feeds(list(news.FEEDS), self.off, self.by_name)
        self.assertEqual(errs, [])
        tata = [i for i in items if i["symbol"] == "TATASTEEL"]
        self.assertGreaterEqual(len(tata), 5)
        apl = [i for i in items if i["symbol"] == "APLAPOLLO"]
        self.assertIn("acquisition", {i["tag"] for i in apl})
        acts = [i for i in items if i["feed"] == "corporate_actions"]
        self.assertTrue(acts and all(i["detail"].startswith("ex-date") for i in acts))

    def test_tags(self):
        self.assertEqual(news.tag_of("Resignation of Statutory Auditor"), "management change")
        self.assertEqual(news.tag_of("Interim Dividend - Rs 2.50 per share"), "dividend")
        self.assertEqual(news.tag_of("Disclosure of pledge of shares"), "pledge")
        self.assertEqual(news.norm("Mahindra & Mahindra Limited"), news.norm("MAHINDRA AND MAHINDRA LTD"))

    def test_check_alerts_once(self):
        st = os.path.join(tempfile.mkdtemp(), "news.json")
        args = ["check", "--offline", self.off, "--state", st, "--symbols", "TATASTEEL", "APLAPOLLO"]
        code, out = run(news.main, args)
        self.assertEqual(code, 0)
        self.assertIn("APLAPOLLO", out)
        code, out = run(news.main, args)
        self.assertEqual(code, 1)
        shutil.rmtree(os.path.dirname(st))


class RiskAndBroker(unittest.TestCase):
    CFG = {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 30, "min_order_inr": 500,
           "max_order_inr": 50000}
    PRICES = {"RELIANCE": 1377.5, "TCS": 2376.2, "INFY": 1098, "HDFCBANK": 846}
    EQUITY = {"RELIANCE": "EQ", "TCS": "EQ", "INFY": "EQ", "HDFCBANK": "EQ", "M&M": "EQ"}

    def verdicts(self, orders):
        res = risk.check(orders, self.CFG, None, self.PRICES, None, "2026-11-23", self.EQUITY)
        return res, [(r["verdict"], r["reasons"]) for r in res["results"]]

    def test_passes_budget_orders_with_charges(self):
        res, v = self.verdicts([{"symbol": "RELIANCE", "side": "buy", "qty": "20", "type": "market",
                                 "time_in_force": "day"}])
        self.assertEqual(v[0][0], "PASS")
        self.assertEqual(res["results"][0]["est_charges_inr"], 32.68)

    def test_india_blocks(self):
        bad = [{"symbol": "INFY", "side": "buy", "notional": "5000"},
               {"symbol": "TCS", "side": "buy", "qty": "2.5"},
               {"symbol": "HDFCBANK", "side": "buy", "qty": "5", "product": "MIS"},
               {"symbol": "NIFTY26NOVFUT", "side": "buy", "qty": "75"},
               {"symbol": "RELIANCE26NOV1400CE", "side": "buy", "qty": "500"},
               {"symbol": "FAKECO", "side": "buy", "qty": "10", "type": "limit", "limit_price": "100"},
               {"symbol": "M&M", "side": "sell", "qty": "1", "type": "limit", "limit_price": "3500"},
               {"symbol": "RELIANCE", "side": "buy", "qty": "10", "type": "sl", "stop_price": "1400",
                "limit_price": "1390"}]
        _, v = self.verdicts(bad)
        want = ["rupee-amount", "whole number", "MIS", "futures or options", "futures or options",
                "not on NSE", "short", "limit price must be"]
        for (verdict, reasons), w in zip(v, want):
            self.assertEqual(verdict, "BLOCK", w)
            self.assertTrue(any(w in r for r in reasons), (w, reasons))

    def test_fingerprints_match_between_risk_and_broker(self):
        o = {"symbol": "RELIANCE", "side": "buy", "qty": "20", "type": "market", "time_in_force": "day"}
        self.assertEqual(risk.fingerprint(o), broker.fingerprint(o))
        same = {"tradingsymbol": "RELIANCE", "transaction_type": "BUY", "quantity": 20, "order_type": "MARKET",
                "product": "CNC", "validity": "DAY", "exchange": "NSE"}
        self.assertEqual(broker.fingerprint(o), broker.fingerprint(same))
        self.assertNotEqual(broker.fingerprint(o), broker.fingerprint(dict(o, qty="21")))

    def test_yes_gate(self):
        o = {"symbol": "RELIANCE", "side": "buy", "qty": "20", "type": "market"}
        syms = ["RELIANCE", "TCS", "M&M"]
        for reply in ("ok", "sure", "yes to all", "yes buy TCS", "yes 25 shares", "yes, but wait", "yes?", ""):
            self.assertFalse(broker.parse_yes(reply, o, syms)[0], reply)
        for reply in ("yes", "Yes 20", "haan", "yes RELIANCE"):
            self.assertTrue(broker.parse_yes(reply, o, syms)[0], reply)

    def test_kite_args(self):
        a = broker.kite_args({"symbol": "M&M", "side": "sell", "qty": "3", "type": "sl", "stop_price": "3400",
                              "limit_price": "3390"})
        self.assertEqual(a, {"variety": "regular", "exchange": "NSE", "tradingsymbol": "M&M",
                             "transaction_type": "SELL", "quantity": 3, "product": "CNC", "order_type": "SL",
                             "validity": "DAY", "price": 3390.0, "trigger_price": 3400.0})

    def test_ticket_flow(self):
        d = tempfile.mkdtemp()
        try:
            p = lambda n: os.path.join(d, n)
            orders = [{"symbol": "RELIANCE", "side": "buy", "qty": "20", "type": "market", "time_in_force": "day"}]
            with open(p("orders.json"), "w") as fh:
                json.dump({"orders": orders}, fh)
            with open(p("config.json"), "w") as fh:
                json.dump(dict(self.CFG, broker="kite"), fh)
            common = ["--orders", p("orders.json"), "--config", p("config.json"), "--risk", p("risk.json"),
                      "--state", p("broker.json"), "--today", "2026-11-23"]
            code, out = run(broker.main, ["show"] + common)
            self.assertEqual(code, 1)
            self.assertIn("NOT READY", out)
            res = risk.check(orders, self.CFG, None, self.PRICES, None, "2026-11-23", self.EQUITY)
            with open(p("risk.json"), "w") as fh:
                json.dump(res, fh)
            code, out = run(broker.main, ["show"] + common)
            self.assertEqual(code, 0)
            self.assertIn('"tradingsymbol": "RELIANCE"', out)
            self.assertIn("REAL MONEY", out)
            code, out = run(broker.main, ["approve", "--index", "1", "--reply", "ok"] + common)
            self.assertEqual(code, 1)
            code, out = run(broker.main, ["approve", "--index", "1", "--reply", "yes"] + common)
            self.assertEqual(code, 0)
            code, _ = run(broker.main, ["result", "--index", "1", "--status", "placed", "--broker-id", "X1"] + common)
            self.assertEqual(code, 0)
            pf = broker.apply_fill(p("portfolio.json"), self.CFG, "RELIANCE", "buy", 20, 1377.5, "2026-11-23", 52.68)
            self.assertEqual(pf["positions"][0]["lots"][0]["cost"], 27602.68)
            with self.assertRaises(broker.TicketError):
                broker.apply_fill(p("portfolio.json"), self.CFG, "RELIANCE", "buy", 1.5, 1377.5, "2026-11-23")
            with open(p("broker.json")) as fh:
                state = json.load(fh)
            again = risk.check(orders, self.CFG, None, self.PRICES, state, "2026-11-23", self.EQUITY)
            self.assertEqual(again["results"][0]["verdict"], "BLOCK")
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
