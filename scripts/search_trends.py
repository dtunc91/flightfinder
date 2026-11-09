"""
Weekly "what are people searching for" report (Markdown), used to decide which pages to write.

Sources (each one is optional; a missing key just skips that section):
  1. Google Search Console - searches that are rising for getmeoutofhere.live, and ones close
     to page 1.            Needs GSC_CREDENTIALS (service account JSON with access to the property).
  2. Aviasales popular destinations - the most-searched destinations from big UK and US cities.
                            Needs TRAVELPAYOUTS_TOKEN (the same token the site uses as API_TOKEN).
  3. Google Trends daily trending searches (UK and US), filtered to travel. No key needed.

Run by .github/workflows/search-trends.yml every week, which posts the output as a GitHub issue.
Run locally:  python scripts/search_trends.py
"""
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.environ.get("GSC_SITE", "https://getmeoutofhere.live/")
TP_TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN") or os.environ.get("API_TOKEN") or ""

UK_ORIGINS = ["LON", "MAN", "BHX", "EDI", "GLA", "BRS"]
US_ORIGINS = ["NYC", "CHI", "LAX", "ATL", "DFW"]
TRAVEL_WORDS = re.compile(
    r"\b(flights?|airports?|airlines?|holidays?|ryanair|easyjet|jet2|tui|british airways|wizz|"
    r"travel|trip|cruise|vacation|spring break|half[- ]term|passport|visa|heathrow|gatwick|"
    r"stansted|luton|manchester airport|delta|united airlines|american airlines|southwest|"
    r"spirit airlines|jetblue|frontier|tsa|faa)\b", re.I)

# City codes the price service uses that aren't single airports
CITY_NAMES = {
    "LON": "London", "PAR": "Paris", "ROM": "Rome", "MIL": "Milan", "NYC": "New York",
    "CHI": "Chicago", "WAS": "Washington", "STO": "Stockholm", "TYO": "Tokyo", "OSA": "Osaka",
    "SEL": "Seoul", "BJS": "Beijing", "SHA": "Shanghai", "YTO": "Toronto", "YMQ": "Montreal",
    "SAO": "Sao Paulo", "BUE": "Buenos Aires", "REK": "Reykjavik", "MOW": "Moscow",
    "BER": "Berlin", "BUH": "Bucharest", "IST": "Istanbul", "TCI": "Tenerife", "DXB": "Dubai",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _airport_names() -> dict:
    """Code -> city name. Uses the price service's free city/airport lists, then local data."""
    names = {}
    try:
        cities = requests.get("https://api.travelpayouts.com/data/en/cities.json", timeout=30).json()
        city_name = {c["code"]: c.get("name") for c in cities if c.get("code") and c.get("name")}
        airports = requests.get("https://api.travelpayouts.com/data/en/airports.json", timeout=30).json()
        for a in airports:
            if a.get("code") and city_name.get(a.get("city_code")):
                names[a["code"]] = city_name[a["city_code"]]
        names.update(city_name)
        return names
    except Exception:
        pass  # offline or list unavailable: fall back to local airport names
    try:
        with open(os.path.join(ROOT, "static", "airports.json"), encoding="utf-8") as f:
            for a in json.load(f):
                label = re.sub(r"\s*\([A-Z]{3}\).*$", "", a.get("label", ""))
                label = re.sub(r"\s+(International\s+)?Airport$", "", label, flags=re.I).strip()
                if a.get("code") and label:
                    names[a["code"]] = label
    except (OSError, ValueError):
        pass
    names.update(CITY_NAMES)
    return names


def _posts() -> dict:
    """slug -> lowercase title, summary and address of each post (what the page is *about*)."""
    out = {}
    for path in glob.glob(os.path.join(ROOT, "data", "blog", "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                p = json.load(f)
        except (OSError, ValueError):
            continue
        slug = os.path.basename(path)[:-5]
        out[slug] = " ".join([p.get("title", ""), p.get("meta", ""), slug.replace("-", " ")]).lower()
    return out


def _covered_by(term: str, posts: dict) -> str:
    """Slug of the post that best covers a destination name or search phrase, or ''."""
    stop = {"flights", "flight", "cheap", "cheapest", "from", "the", "and", "uk", "usa", "for", "to",
            "best", "deals", "deal", "airport", "airports", "fly", "flying", "tickets", "ticket"}
    words = [w for w in re.findall(r"[a-zà-ÿ]{3,}", term.lower()) if w not in stop]
    if not words:
        return ""
    best, score = "", 0
    for slug, text in posts.items():
        s = sum(text.count(w) for w in words) + 5 * sum(w in slug for w in words)
        if s > score and all(w in text for w in words):
            best, score = slug, s
    return best


# ── 1. Google Search Console ─────────────────────────────────────────────────

def search_console() -> dict:
    raw = os.environ.get("GSC_CREDENTIALS", "").strip()
    if not raw:
        return {"skipped": "Search Console isn't connected yet (GSC_CREDENTIALS secret not set)."}
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        creds = service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
        session = AuthorizedSession(creds)
    except Exception as e:  # bad JSON or missing library
        return {"skipped": f"Search Console login failed: {e}"}

    url = ("https://searchconsole.googleapis.com/webmasters/v3/sites/"
           + requests.utils.quote(SITE, safe="") + "/searchAnalytics/query")
    end = date.today() - timedelta(days=3)          # Search Console data lags ~2-3 days
    periods = {"now": (end - timedelta(days=27), end),
               "before": (end - timedelta(days=55), end - timedelta(days=28))}
    data = {}
    for key, (start, stop) in periods.items():
        r = session.post(url, json={"startDate": start.isoformat(), "endDate": stop.isoformat(),
                                    "dimensions": ["query"], "rowLimit": 1000}, timeout=30)
        if r.status_code != 200:
            return {"skipped": f"Search Console said {r.status_code}: {r.text[:200]}"}
        data[key] = {row["keys"][0]: row for row in r.json().get("rows", [])}

    now, before = data["now"], data["before"]
    rising = []
    for q, row in now.items():
        prev = before.get(q, {}).get("impressions", 0)
        gain = row["impressions"] - prev
        if gain >= 5:
            rising.append({"query": q, "impressions": int(row["impressions"]), "before": int(prev),
                           "clicks": int(row.get("clicks", 0)), "position": round(row.get("position", 0), 1)})
    rising.sort(key=lambda x: x["impressions"] - x["before"], reverse=True)
    near = [{"query": q, "impressions": int(r["impressions"]), "position": round(r["position"], 1)}
            for q, r in now.items() if 8 <= r.get("position", 0) <= 20 and r["impressions"] >= 10]
    near.sort(key=lambda x: x["impressions"], reverse=True)
    return {"rising": rising[:15], "near_page_one": near[:10],
            "totals": {"impressions": int(sum(r["impressions"] for r in now.values())),
                       "impressions_before": int(sum(r["impressions"] for r in before.values())),
                       "clicks": int(sum(r.get("clicks", 0) for r in now.values()))},
            "period": f"{periods['now'][0]:%d %b} - {periods['now'][1]:%d %b %Y}"}


# ── 2. Aviasales popular destinations ────────────────────────────────────────

def popular_destinations(origins: list, currency: str) -> dict:
    if not TP_TOKEN:
        return {"skipped": "TRAVELPAYOUTS_TOKEN secret not set."}
    tally = {}
    failures = 0
    for origin in origins:
        try:
            r = requests.get("https://api.travelpayouts.com/v1/city-directions",
                             params={"origin": origin, "currency": currency, "token": TP_TOKEN},
                             timeout=20)
            rows = (r.json().get("data") or {}) if r.status_code == 200 else {}
        except Exception:
            rows = {}
        if not rows:
            failures += 1
            continue
        items = rows.values() if isinstance(rows, dict) else rows
        for rank, item in enumerate(items, start=1):
            dest = (item.get("destination") or "").upper()
            if not dest or dest == origin:
                continue
            t = tally.setdefault(dest, {"code": dest, "cities": [], "best_rank": rank, "price": None})
            t["cities"].append(origin)
            t["best_rank"] = min(t["best_rank"], rank)
            price = item.get("price")
            if isinstance(price, (int, float)) and (t["price"] is None or price < t["price"]):
                t["price"] = price
    if not tally:
        return {"skipped": f"The price service returned no popular destinations ({failures} cities tried)."}
    ranked = sorted(tally.values(), key=lambda t: (-len(t["cities"]), t["best_rank"]))
    return {"top": ranked[:15]}


# ── 3. Google Trends daily trending searches ─────────────────────────────────

def trending(geo: str) -> dict:
    try:
        r = requests.get("https://trends.google.com/trending/rss", params={"geo": geo}, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; getmeoutofhere-trends/1.0)"})
        if r.status_code != 200:
            return {"skipped": f"Google Trends said {r.status_code}."}
        root = ET.fromstring(r.content)
    except Exception as e:
        return {"skipped": f"Google Trends feed unavailable ({e.__class__.__name__})."}
    ns = {"ht": "https://trends.google.com/trending/rss"}
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        news = " ".join(n.findtext("ht:news_item_title", default="", namespaces=ns)
                        for n in item.findall("ht:news_item", ns))
        if TRAVEL_WORDS.search(title + " " + news):
            out.append({"term": title,
                        "traffic": (item.findtext("ht:approx_traffic", default="", namespaces=ns) or "").strip()})
    return {"items": out[:10]}


# ── report ───────────────────────────────────────────────────────────────────

def build(today: date):
    names, posts = _airport_names(), _posts()
    monday = today - timedelta(days=today.weekday())
    title = f"Search trends report: week of {monday.day} {monday:%b %Y}"
    gsc = search_console()
    uk = popular_destinations(UK_ORIGINS, "gbp")
    us = popular_destinations(US_ORIGINS, "usd")
    tr_uk, tr_us = trending("GB"), trending("US")
    ideas = []

    md = ["What people are searching for this week, and which pages we could write or improve. "
          "Ticking an idea and asking Claude to do it is all that's needed.\n"]

    md.append("## 1. Your site in Google (Search Console)\n")
    if "skipped" in gsc:
        md.append(f"_{gsc['skipped']}_ See the setup notes at the bottom.\n")
    else:
        t = gsc["totals"]
        md.append(f"{gsc['period']}: **{t['impressions']:,} impressions** "
                  f"(previous 28 days: {t['impressions_before']:,}), **{t['clicks']} click{'' if t['clicks'] == 1 else 's'}**.\n")
        if gsc["rising"]:
            md.append("**Rising searches**\n\n| Search | Impressions | Before | Position | Our page |\n|---|---:|---:|---:|---|")
            for q in gsc["rising"]:
                page = _covered_by(q["query"], posts)
                md.append(f"| {q['query']} | {q['impressions']} | {q['before']} | {q['position']} | "
                          f"{'`' + page + '`' if page else '**none**'} |")
                if not page:
                    ideas.append(f"Write a page for the rising search \"{q['query']}\"")
            md.append("")
        if gsc["near_page_one"]:
            md.append("**Close to page 1** (positions 8-20: improving these pages is the quickest win)\n")
            for q in gsc["near_page_one"]:
                page = _covered_by(q["query"], posts)
                md.append(f"- \"{q['query']}\": position {q['position']}, {q['impressions']} impressions"
                          + (f" · page `{page}`" if page else ""))
                if page:
                    ideas.append(f"Improve `{page}` for \"{q['query']}\" (position {q['position']})")
            md.append("")

    for label, res, sym in (("UK", uk, "£"), ("US", us, "$")):
        md.append(f"## {'2' if label == 'UK' else '3'}. Most-searched flight destinations from {label} cities (Aviasales)\n")
        if "skipped" in res:
            md.append(f"_{res['skipped']}_\n")
            continue
        md.append("| Destination | Popular from | Cheapest seen | Our page |\n|---|---|---:|---|")
        for d in res["top"]:
            name = names.get(d["code"], d["code"])
            page = _covered_by(name, posts)
            price = f"{sym}{int(d['price'])}" if d["price"] else ""
            md.append(f"| {name} ({d['code']}) | {', '.join(d['cities'])} | {price} | "
                      f"{'`' + page + '`' if page else '**none**'} |")
            if not page and len(d["cities"]) >= 2:
                ideas.append(f"Write \"Cheap flights to {name} from the {label}\" "
                             f"(popular from {', '.join(d['cities'])})")
        md.append("")

    md.append("## 4. Trending travel searches today (Google Trends)\n")
    for label, res in (("UK", tr_uk), ("US", tr_us)):
        if "skipped" in res:
            md.append(f"- {label}: _{res['skipped']}_")
        elif not res["items"]:
            md.append(f"- {label}: no travel-related searches in today's top trends.")
        else:
            md.append(f"- {label}: " + "; ".join(
                f"{i['term']}" + (f" ({i['traffic']} searches)" if i["traffic"] else "") for i in res["items"]))
    md.append("")

    md.append("## Ideas for this week\n")
    seen = set()
    ideas = [i for i in ideas if not (i in seen or seen.add(i))][:8]
    md += [f"- [ ] {i}" for i in ideas] or ["Nothing new stands out this week."]
    if ideas:
        md.append("\nTo act on these, paste the ideas you want into a chat with Claude: "
                  "\"Create or improve these pages: ...\". Prices stay as typical ranges, with no invented stories.")

    md.append("""
<details><summary>Setup notes (only needed once)</summary>

- **Search Console:** in Google Search Console go to Settings → Users and permissions → Add user,
  enter the email address of the site's Google service account (the `client_email` inside the
  credentials JSON), and give it "Restricted" access. In Google Cloud for that same project, enable
  the "Google Search Console API". Then add the credentials JSON as the GitHub secret `GSC_CREDENTIALS`.
- **Aviasales:** add the site's Travelpayouts token as the GitHub secret `TRAVELPAYOUTS_TOKEN`.
- Secrets live in GitHub → Settings → Secrets and variables → Actions → New repository secret.
</details>
""")
    return title, "\n".join(md)


if __name__ == "__main__":
    title, body = build(date.today())
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"title={title}\n")
        with open(os.path.join(ROOT, "trends.md"), "w", encoding="utf-8") as f:
            f.write(body)
    else:
        sys.stdout.write(f"# {title}\n\n{body}\n")
