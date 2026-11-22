# copycat tool (scripts/shp.py)

The Indian edition of the US 13F copycat. In the US, big funds file a 13F. In
India, every company files a shareholding pattern each quarter naming its
holders above 1%. Put together across companies, those filings show what a
"superstar" investor holds.

`${CLAUDE_SKILL_DIR}` is the skill's folder; the tools are in its `scripts/` folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/shp.py" fetch https://nsearchives.nseindia.com/corporate/xbrl/SHP_..._WEB.xml
python3 "${CLAUDE_SKILL_DIR}/scripts/shp.py" collect                       # new filings from NSE's RSS feed
python3 "${CLAUDE_SKILL_DIR}/scripts/shp.py" holders TATASTEEL             # promoter %, public %, named holders
python3 "${CLAUDE_SKILL_DIR}/scripts/shp.py" diff ALICON                   # vs the previous quarter in the library
python3 "${CLAUDE_SKILL_DIR}/scripts/shp.py" investor "ashish kacholia"    # holdings across the library
python3 "${CLAUDE_SKILL_DIR}/scripts/shp.py" investor kacholia --write     # -> trader/targets.json
python3 "${CLAUDE_SKILL_DIR}/scripts/shp.py" library                       # what the library holds
```

## The library: where the filings come from

Parsed filings live in `trader/cache/shp/` (one JSON per company per
quarter). They get there two ways, both from NSE's public archive:

1. `collect` reads NSE's Shareholding Pattern RSS feed
   (`nsearchives.nseindia.com/content/RSS/Shareholding_Pattern.xml`) and
   fetches every filing it has not seen. The feed shows only the latest few,
   so run it daily (a scheduled task) and the library grows.
2. `fetch <link>` for a particular company. Ask the user to open the company
   on nseindia.com, go to Corporate filings > Shareholding Pattern, and copy
   the XBRL link for the quarter they want (and the quarter before, for
   `diff`). The tool only fetches `nsearchives.nseindia.com` links; a file
   they downloaded can be passed as a path.

NSE's search API needs a browser session, so this skill does not use it, and
paid sites' "superstar portfolio" pages are not scraped. **The investor view
covers only the companies in the library.** Say so every time.

## What a filing says

- `promoter_pct`, `public_pct`, `mf_pct` (mutual funds), `fpi_pct` (foreign
  portfolio investors, category I), as reported.
- Every named holder: name, category (promoter group, mutual fund, insurance,
  FPI, company, individual, director / KMP, NRI...), shares and % of the
  company. Public holders are named only above 1%. Promoters are all named.
- The filings include each holder's PAN. The tool drops it while parsing; it
  is never saved, printed or written to targets.

## Copying an investor

`investor NAME` matches holder names against the query plus the aliases in
`investors.json` (the same list as the deals tool (references/deals.md): a person and the
companies they invest through). It values each holding at
`trader/prices.json` (refresh with references/alerts.md `nse.py quote
SYMBOLS --write`) and with `--write` saves weights to `trader/targets.json`.
Then:

```bash
python3 size.py/size.py --whole-shares --prices trader/prices.json --write
```

Indian shares trade in whole units only, so always size with `--whole-shares`.
Small and micro caps, where these investors often hold, can be illiquid and
can hit price bands (upper / lower circuit): say so when the list has them.

## Limits

- Filings are due within 21 days of the quarter end, so they are weeks old.
- Under 1% is invisible: "dropped below 1%" in `diff` may be a partial sale.
- Names vary between filings ("ASHISH KACHOLIA" vs "Ashish Kacholia" vs his
  company). Check the `held_as` names before trusting a match.

## Example

`fetch` on Tata Steel's March 2025 filing gives promoter group 33.19%
(Tata Sons alone 31.76%), public 66.81%, mutual funds 12.04%, FPIs 17.53%,
and LIC at 7.81%. These match NSE's feed summary for that filing.

## Safety

- Education and tooling, not financial advice. A holding in a filing is a fact about the past, not a
  recommendation; the investor may already have sold.
- Nothing in this pack places an order except the broker tool (references/broker.md) (or ticket.py), and only after
  the risk tool (references/risk.md) passed that exact order today and the user typed an explicit yes for that one order.
- PANs in the filings are personal data: never store, print or repeat them.
