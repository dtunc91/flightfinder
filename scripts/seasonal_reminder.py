"""
Builds the monthly "seasonal pages" reminder (Markdown) from data/seasonal_calendar.json.

Run by .github/workflows/seasonal-reminder.yml on the 1st of each month, which posts the
output as a GitHub issue. Run locally with:  python scripts/seasonal_reminder.py [YYYY-MM]
"""
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://getmeoutofhere.live"
REPO = "https://github.com/dtunc91/flightfinder"
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def _shift(month: int, by: int) -> int:
    return (month - 1 + by) % 12 + 1


def _season_starts(peak: list) -> set:
    """First month of each season in the peak list, e.g. [12, 1, 2, 6, 7] -> {12, 6}."""
    months = set(peak)
    return {m for m in peak if _shift(m, -1) not in months} or {peak[0]}


def _post(slug: str) -> dict:
    try:
        with open(os.path.join(ROOT, "data", "blog", f"{slug}.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _line(t: dict, action: str) -> str:
    flag = "UK" if t.get("market") == "uk" else "US"
    if t.get("page"):
        slug = t["page"]
        post = _post(slug)
        upd = (post.get("updated_at") or post.get("published_at") or "")[:10]
        when = f", last updated {upd}" if upd else ", page file not found"
        if post.get("fare_tables"):
            action = action.replace("{table}", " check its price table shows;")
        action = action.replace("{table}", "")
        return (f"- [ ] **{t['name']}** ({flag}): {action}\n"
                f"  Page: {SITE}/blog/{slug}{when} · [edit]({REPO}/blob/main/data/blog/{slug}.json)")
    return (f"- [ ] **{t['name']}** ({flag}): this page doesn't exist yet. Ask Claude to write it, "
            f"at the address `/blog/{t.get('suggested_address', '')}` (no year in the address, so it can be reused every year).")


def build(today: date) -> tuple:
    with open(os.path.join(ROOT, "data", "seasonal_calendar.json"), encoding="utf-8") as f:
        topics = json.load(f)["topics"]
    m = today.month
    prepare = [t for t in topics if _season_starts(t["peak"]) & {_shift(m, 1), _shift(m, 2)}]
    peak_now = [t for t in topics if m in t["peak"] and t not in prepare]

    title = f"Seasonal pages for {MONTHS[m - 1]} {today.year}"
    out = [f"Searches for these topics rise over the next couple of months. Google needs a few weeks "
           f"to find and rank an updated page, so this is the month to get them ready.\n"]
    out.append(f"## Get ready now (searches start in {MONTHS[_shift(m, 1) - 1]} or {MONTHS[_shift(m, 2) - 1]})\n")
    out += [_line(t, "refresh dates, years and advice;{table} then Request indexing in Search Console.")
            for t in prepare] or ["Nothing new starts in the next two months."]
    out.append("\n## In season now (keep an eye on these)\n")
    out += [_line(t, "check it in Search Console; fix anything out of date.") for t in peak_now] or ["Nothing in season."]
    out.append("""
## Monthly 10-minute check

- [ ] Search Console → **Performance** → last 28 days → **Queries**: note any search that's rising, and send a screenshot to Claude.
- [ ] Search Console → **Pages**: is the Indexed number going up?
- [ ] Open two of the pages above on your phone and check they look right.
- [ ] After updating any page: Search Console → **URL inspection** → paste the address → **Request indexing**.

Close this issue when you're done. A new one arrives on the 1st of next month.
""")
    return title, "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        y, mo = (int(x) for x in sys.argv[1].split("-"))
        when = date(y, mo, 1)
    else:
        when = date.today()
    title, body = build(when)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"title={title}\n")
        with open(os.path.join(ROOT, "reminder.md"), "w", encoding="utf-8") as f:
            f.write(body)
    else:
        print(f"# {title}\n\n{body}")
