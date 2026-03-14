#!/usr/bin/env python3
"""
Weekly blog post generator for getmeoutofhere.live

Uses the Claude API to write human-sounding travel content for UK audiences.
Posts are saved as JSON files to data/blog/ and automatically picked up
by the Flask app on the next request.

CLI usage:
    python blog_generator.py            # generate next due post
    python blog_generator.py --force    # regenerate even if recent
    python blog_generator.py --list     # show topic queue + status
    python blog_generator.py --topic march-flight-deals-uk  # specific topic

Called automatically by APScheduler inside app.py every Monday at 08:00.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import anthropic

# ── Paths ────────────────────────────────────────────────────────────────────
_HERE     = os.path.dirname(os.path.abspath(__file__))
BLOG_DIR  = os.path.join(_HERE, 'data', 'blog')
LOCK_FILE = os.path.join(_HERE, 'data', '.blog_last_run')

os.makedirs(BLOG_DIR, exist_ok=True)

# How old a post must be (in days) before it gets refreshed with new content
STALE_DAYS = 340

# ── Static blog posts already in app.py (for related-links cross-linking) ───
STATIC_POSTS = [
    ("cheapest-flights-from-london",     "Cheapest places to fly from London"),
    ("cheapest-flights-from-manchester", "Cheapest flights from Manchester"),
    ("cheapest-flights-from-edinburgh",  "Cheapest flights from Edinburgh"),
    ("cheapest-flights-from-bristol",    "Cheapest flights from Bristol"),
]

# ── Topic pipeline ───────────────────────────────────────────────────────────
# best_months: calendar months when this topic is most SEO-relevant.
# None = evergreen (publish any time once seasonal queue is clear).
TOPIC_PIPELINE = [
    {
        "slug":        "march-flight-deals-uk",
        "emoji":       "🌱",
        "title":       "Best Flight Deals from the UK This March",
        "subtitle":    "Spring is almost here — and the prices are still wintery",
        "airport_names": "London, Manchester, Edinburgh & Birmingham",
        "cta_airport": "LHR",
        "best_months": [1, 2, 3],
        "prompt_topic": (
            "the best flight deals available from UK airports in March — a genuinely underrated month "
            "to travel. Cover why March is good value, which European city breaks shine at this time of "
            "year (Seville, Lisbon, Prague, Porto are personal favourites), realistic one-way prices from "
            "UK airports, which airlines to watch, and why booking 3-6 weeks out tends to work well in "
            "March. Include a section on longer-haul March options (Morocco, Jordan, even Tokyo). "
            "Write from personal experience of March trips — mention the relief of finding deals after "
            "the January slump."
        ),
    },
    {
        "slug":        "best-summer-destinations-london",
        "emoji":       "☀️",
        "title":       "Best Places to Fly from London This Summer",
        "subtitle":    "Before the prices go absolutely mental — routes worth booking now",
        "airport_names": "Heathrow, Gatwick, Stansted & Luton",
        "cta_airport": "LHR",
        "best_months": [3, 4, 5],
        "prompt_topic": (
            "the best holiday destinations to fly to from London airports in summer (June–August), covering "
            "a genuine range: beach holidays (Greece, Croatia, Canaries), city breaks (Rome, Seville, "
            "Lisbon), and one or two off-the-beaten-path suggestions. For each destination mention which "
            "London airport serves it, which airlines, realistic return prices, and what the destination "
            "actually feels like in July/August (crowds, heat, vibes). Be honest — some places are "
            "genuinely packed in August and others are surprisingly manageable. Include a practical "
            "section on booking timing: when summer prices spike and when to pull the trigger."
        ),
    },
    {
        "slug":        "easter-weekend-breaks-from-uk",
        "emoji":       "🐣",
        "title":       "Best Easter Weekend Breaks You Can Fly to from the UK",
        "subtitle":    "Four days, a direct flight, and a city you've been meaning to visit — sorted",
        "airport_names": "UK Airports",
        "cta_airport": "LHR",
        "best_months": [2, 3],
        "prompt_topic": (
            "the best destinations for a long Easter weekend break from UK airports — 3 or 4 nights. "
            "Focus on cities reachable within 3 hours: Seville, Porto, Bruges, Budapest, Krakow, "
            "Marrakech, Valletta (Malta). Explain what makes each work well for a short break, realistic "
            "flight costs, and tips on flying around the bank holiday to save money (Friday vs Saturday "
            "departure makes a huge difference). Include a personal Easter trip story and be honest about "
            "which cities get overcrowded at Easter vs which are surprisingly calm."
        ),
    },
    {
        "slug":        "may-half-term-flights-uk",
        "emoji":       "👨‍👩‍👧",
        "title":       "May Half-Term: Affordable Family Flights from the UK",
        "subtitle":    "Yes, school-holiday prices are up — but there's still smart money to spend",
        "airport_names": "UK Airports",
        "cta_airport": "MAN",
        "best_months": [3, 4, 5],
        "prompt_topic": (
            "family flight options for May half-term from UK airports, with a realistic take on costs "
            "during school holidays. Cover destinations that work brilliantly for families with kids of "
            "different ages: beaches (Majorca, Lanzarote, Corfu), cities with kid-friendly things "
            "(Rome, Paris, Lisbon), and slightly more adventurous picks. Compare Jet2 vs easyJet vs TUI "
            "for family-friendly experience (beyond just price). Give practical tips on how to shave "
            "cost: which airports, which days to fly, checking luggage vs carry-on, and booking the "
            "accommodation separately. Write from the perspective of someone who's done family trips and "
            "knows the logistical realities."
        ),
    },
    {
        "slug":        "cheap-august-flights-uk",
        "emoji":       "🌊",
        "title":       "Actually Getting a Cheap Flight in August from the UK",
        "subtitle":    "August prices are brutal. Here's how to make the best of it anyway.",
        "airport_names": "UK Airports",
        "cta_airport": "LHR",
        "best_months": [5, 6, 7],
        "prompt_topic": (
            "the honest reality of finding value flights from UK airports in August — the most expensive "
            "month to fly. Cover which destinations hold their value better relative to the experience "
            "(less-touristy parts of the Balkans, Eastern Europe, Portuguese islands), the booking window "
            "strategies that actually work for August (book early, not last-minute), midweek vs weekend "
            "flight pricing, and which routes see the least August premium. Be direct about when August "
            "travel is genuinely not worth the premium, and when it is. Include a section on 'if you "
            "must fly in August, here's how to keep it under £X' with realistic numbers."
        ),
    },
    {
        "slug":        "september-october-best-time-fly-uk",
        "emoji":       "🍂",
        "title":       "Why September and October Are the UK's Best Months to Fly",
        "subtitle":    "The kids are back at school and the prices fell off a cliff — use it",
        "airport_names": "UK Airports",
        "cta_airport": "LGW",
        "best_months": [7, 8, 9],
        "prompt_topic": (
            "why September and October are genuinely the best months to fly from the UK: prices drop "
            "dramatically after August, Mediterranean destinations are still warm with smaller crowds, "
            "and if you don't have school-age kids you can travel any day of the week. "
            "Cover specific routes and price drops to expect (e.g. Greece, Croatia, Spain, Portugal in "
            "September vs August), the cities that are even better in autumn than summer (Rome, Lisbon, "
            "Marrakech), and October destinations that only really come into their own in autumn "
            "(Istanbul, New York, Morocco). Include a personal story of an autumn trip that exceeded "
            "expectations specifically because of the season."
        ),
    },
    {
        "slug":        "christmas-market-trips-from-uk",
        "emoji":       "🎄",
        "title":       "Best European Christmas Markets You Can Fly to from the UK",
        "subtitle":    "Two hours on a plane beats fighting through Birmingham's Bullring",
        "airport_names": "UK Airports",
        "cta_airport": "LHR",
        "best_months": [9, 10, 11],
        "prompt_topic": (
            "the best European Christmas market destinations accessible from UK airports: Strasbourg "
            "(the OG), Prague, Vienna, Bruges, Hamburg, Cologne, Nuremberg, Tallinn. Be honest about "
            "which are worth the trip vs overhyped, realistic flight costs from UK airports in November "
            "and December, and the practical matter of when to visit (early December avoids the worst "
            "crowds). Include tips on combining a Christmas market city with a longer break to justify "
            "the flight. Personal experience of Christmas market trips and what you'd do differently. "
            "Mention which budget airlines fly to each city and from which UK airports."
        ),
    },
    {
        "slug":        "winter-sun-from-uk",
        "emoji":       "🌴",
        "title":       "Winter Sun: Warm Destinations to Fly to from the UK This Winter",
        "subtitle":    "When the grey sets in around November, these are the routes worth checking",
        "airport_names": "UK Airports",
        "cta_airport": "LGW",
        "best_months": [9, 10, 11, 12],
        "prompt_topic": (
            "the best warm weather destinations to fly to from UK airports in winter — genuinely warm, "
            "not 'warm for November': Tenerife and Lanzarote (the reliable ones), Madeira (underrated), "
            "Cape Verde (fewer people know about it), Egypt/Hurghada, Malta, Dubai, Marrakech for "
            "culture-and-warmth. Be honest about which ones actually have reliable winter sunshine vs "
            "just mild weather. Cover flight costs, which airports in the UK serve each destination, "
            "and whether peak-season prices in these winter-sun spots make them less of a bargain than "
            "they appear. Include a personal winter sun trip story."
        ),
    },
    {
        "slug":        "budget-city-breaks-europe-from-uk",
        "emoji":       "🏙️",
        "title":       "Best Budget City Breaks in Europe from the UK Right Now",
        "subtitle":    "Forget Amsterdam and Paris — these cities go further on your money",
        "airport_names": "UK Airports",
        "cta_airport": "STN",
        "best_months": None,
        "prompt_topic": (
            "the best value European city breaks you can do from UK airports — genuinely budget-friendly "
            "destinations where flights AND the destination itself are affordable: Krakow, Budapest, "
            "Tbilisi (if flying via a hub), Riga, Porto, Sofia, Bucharest, Belgrade, Tirana, Kutaisi. "
            "Give an honest assessment of each: what to expect, what the flight usually costs from UK, "
            "approximate total spend for a 3-night trip including accommodation and food. Challenge the "
            "default 'city break' choices (Amsterdam, Barcelona) with better-value alternatives. "
            "Write with genuine enthusiasm for these underappreciated places."
        ),
    },
    {
        "slug":        "hidden-gem-destinations-uk-flights",
        "emoji":       "🔭",
        "title":       "Flights from the UK to Places Your Mates Haven't Been Yet",
        "subtitle":    "Not Ibiza. Not Prague again. Somewhere that'll actually surprise people.",
        "airport_names": "UK Airports",
        "cta_airport": "LHR",
        "best_months": None,
        "prompt_topic": (
            "genuinely underrated destinations with direct or easy-connection flights from UK airports: "
            "Kotor in Montenegro (before Dubrovnik prices kick in), Tbilisi in Georgia, Tirana in Albania "
            "(cheapest capital in Europe), Palermo over Rome, Split over Dubrovnik, Faroe Islands for "
            "the adventurous, Madeira over Lisbon, Ohrid in North Macedonia. For each, explain what "
            "makes it special, how to get there from the UK, approximate costs, and why it beats the "
            "more obvious alternative. Write with the passion of someone who genuinely loves finding "
            "these places before they become too popular."
        ),
    },
    {
        "slug":        "last-minute-flights-uk-guide",
        "emoji":       "⚡",
        "title":       "How to Find Last-Minute Flights from the UK (When They're Actually Cheap)",
        "subtitle":    "The spontaneous trip is sometimes the best one — and occasionally the cheapest",
        "airport_names": "UK Airports",
        "cta_airport": "LGW",
        "best_months": None,
        "prompt_topic": (
            "the reality of last-minute flight deals from UK airports — when they actually exist (and "
            "when they really don't). Cover which routes and airlines are most likely to discount in the "
            "final 48-72 hours, why last-minute is sometimes genuinely cheaper (unsold seats on specific "
            "routes), the tools and approaches that actually work vs hype, and the practical barriers "
            "(accommodation availability, passport/visa needs). Be honest: most of the time last-minute "
            "flights from UK are not cheaper than booking 4-6 weeks ahead. But sometimes they are, and "
            "here's when. Include a personal story of a last-minute spontaneous trip."
        ),
    },
    {
        "slug":        "weekend-breaks-from-manchester",
        "emoji":       "🔴",
        "title":       "Best Weekend Breaks from Manchester Airport",
        "subtitle":    "No need to schlep down to London — Manchester's flight options are seriously good",
        "airport_names": "Manchester Airport (MAN)",
        "cta_airport": "MAN",
        "best_months": None,
        "prompt_topic": (
            "the best weekend break destinations you can fly to direct from Manchester Airport — "
            "written for people in the north of England who are tired of being told London has the "
            "best flights. Cover: Dublin, Amsterdam, Barcelona, Malaga, Reykjavik, Rome, New York "
            "(for a longer break), Dubrovnik, and a few less obvious MAN routes. Explain which "
            "airlines fly each route from MAN (Jet2 is big here), realistic prices, and the sheer "
            "convenience of not having to get to London first. Include a personal experience of flying "
            "from Manchester and why it's genuinely underrated as a departure hub."
        ),
    },
    {
        "slug":        "valentines-weekend-breaks-uk",
        "emoji":       "💝",
        "title":       "Valentine's Weekend Breaks You Can Actually Fly to from the UK",
        "subtitle":    "Skip the overpriced London restaurant — a flight to somewhere romantic costs about the same",
        "airport_names": "UK Airports",
        "cta_airport": "LHR",
        "best_months": [12, 1, 2],
        "prompt_topic": (
            "the best romantic city breaks for Valentine's weekend from UK airports: Paris (yes, even if "
            "it's obvious — it still delivers), Rome, Venice (chaotic but worth it once), Lisbon "
            "(underrated for romance), Seville, Copenhagen (romantic in a hygge way), Vienna. Be honest "
            "about which cities are overpriced in mid-February vs which represent good value. Include "
            "practical tips on keeping a couples' city break affordable (self-catering apartment vs "
            "hotel, free romantic things to do). Write warmly but realistically — this isn't a brochure."
        ),
    },
    {
        "slug":        "long-haul-deals-from-uk",
        "emoji":       "🌏",
        "title":       "Long-Haul Flight Deals from UK Airports: Where to Go and When to Book",
        "subtitle":    "Asia, the Americas, Africa — further doesn't always mean unaffordable",
        "airport_names": "Heathrow, Manchester & Gatwick",
        "cta_airport": "LHR",
        "best_months": None,
        "prompt_topic": (
            "finding affordable long-haul flights from UK airports: Bangkok and Southeast Asia "
            "(consistently good value from Heathrow), Tokyo (pricier but worth it), New York "
            "(competitive with BA, Virgin, Norwegian and others), Cape Town, Dubai (as a stopover "
            "destination), Singapore, Toronto. Cover the best booking windows for each region "
            "(long-haul works differently to Europe — 3-6 months ahead often beats 6 weeks), which "
            "airlines genuinely offer better value on long-haul, and the months when each destination "
            "is both affordable AND worth visiting. Personal long-haul trip experiences and honest "
            "takes on what's worth the cost."
        ),
    },
    {
        "slug":        "solo-travel-destinations-from-uk",
        "emoji":       "🧳",
        "title":       "Best Places to Solo Travel to from the UK",
        "subtitle":    "Travelling alone is one of the best things you can do — these cities make it easy",
        "airport_names": "UK Airports",
        "cta_airport": "STN",
        "best_months": None,
        "prompt_topic": (
            "the best destinations for solo travel from UK airports — cities that are safe, social, "
            "easy to navigate alone, and rewarding to explore without a companion. Cover Lisbon "
            "(hostel scene is great, people are friendly), Reykjavik (safe, walkable, interesting), "
            "Bangkok (overwhelming but incredible for solo), Amsterdam (easy to navigate, lots to do "
            "alone), Budapest (cheap, beautiful, good nightlife if you want it), Porto (relaxed, easy). "
            "Address common solo travel concerns honestly: dining alone (fine), safety (varies by city), "
            "meeting people (hostels, tours, apps). Include personal solo travel experiences."
        ),
    },
    {
        "slug":        "january-new-year-flight-deals-uk",
        "emoji":       "🎉",
        "title":       "Best Flights to Book from the UK in January",
        "subtitle":    "January sales apply to flights too — this window closes faster than you'd think",
        "airport_names": "UK Airports",
        "cta_airport": "LHR",
        "best_months": [12, 1],
        "prompt_topic": (
            "the January booking window for UK flights and why it's genuinely one of the best times to "
            "lock in summer travel. Cover why January is a good time to book (airlines want cashflow, "
            "competition is high, summer seats still available), which routes tend to drop in January "
            "sales, and the specific destinations and dates worth targeting. Be honest about the "
            "difference between headline 'sale' prices and what's actually bookable. Include a section "
            "on what to book in January for the biggest return: the logic of booking summer flights "
            "before February, when prices typically start firming up."
        ),
    },
    {
        "slug":        "bank-holiday-flight-strategy-uk",
        "emoji":       "📅",
        "title":       "How to Make the Most of UK Bank Holidays: A Flight Strategy",
        "subtitle":    "Eight free days per year — here's how to turn them into proper trips",
        "airport_names": "UK Airports",
        "cta_airport": "LGW",
        "best_months": [1, 2, 3],
        "prompt_topic": (
            "a strategic guide to using UK bank holidays to maximise trips with minimal annual leave: "
            "Easter (4-day weekend + 2 AL days = 9 days for the cost of 2), early May bank holiday "
            "(bolt on Mon-Fri = 9 days for 4 AL), late May, August bank holiday, Christmas. For each, "
            "suggest destinations that work well for the timing, how to add annual leave efficiently, "
            "and which directions give the best value at that time of year. Write with the practical "
            "enthusiasm of someone who has genuinely gamed the UK leave system. Include specific "
            "examples with real destination suggestions and realistic costs."
        ),
    },
    {
        "slug":        "portugal-flights-from-uk",
        "emoji":       "🇵🇹",
        "title":       "Portugal from the UK: Still the Best Value Flight Destination?",
        "subtitle":    "I keep going back. Here's whether the fares are still worth it in 2025.",
        "airport_names": "UK Airports",
        "cta_airport": "LIS",
        "best_months": None,
        "prompt_topic": (
            "an honest assessment of Portugal as a flight destination from UK airports in 2025 — "
            "whether it still represents good value given how popular it's become. Cover Lisbon (tourist "
            "prices rising but still great), Porto (slightly more manageable), Faro/Algarve (depends "
            "entirely on when you go), Madeira (consistently underrated), and briefly the Azores. "
            "Include realistic flight costs from multiple UK airports, which airlines serve each region, "
            "the best times to visit each part, and an honest take on overtourism and price rises. "
            "Write from genuine personal experience of multiple Portugal trips."
        ),
    },
    {
        "slug":        "spain-flights-from-uk",
        "emoji":       "🇪🇸",
        "title":       "Spain from the UK: Best Value Destinations and When to Go",
        "subtitle":    "There's more to Spain than Benidorm — though Benidorm has its place too",
        "airport_names": "UK Airports",
        "cta_airport": "BCN",
        "best_months": None,
        "prompt_topic": (
            "a comprehensive guide to flying from UK airports to Spain: the obvious ones (Barcelona, "
            "Madrid, Seville, Malaga, Majorca, Ibiza) and the less-obvious ones (Bilbao, San Sebastián, "
            "Valencia, Alicante, Almería, Gran Canaria in winter). Be opinionated about which parts "
            "of Spain are genuinely worth it vs overpriced for UK tourists, what time of year each "
            "destination is at its best, realistic flight costs from UK, and which airlines/airports "
            "to use for each. Include personal Spain trip experiences and contrast different regions. "
            "Have an actual opinion on Barcelona tourism levels."
        ),
    },
    {
        "slug":        "iceland-reykjavik-from-uk",
        "emoji":       "🌋",
        "title":       "Iceland from the UK: Is It Actually Worth the Money?",
        "subtitle":    "Spoiler: yes. But you need to know what you're getting yourself into.",
        "airport_names": "UK Airports",
        "cta_airport": "LHR",
        "best_months": [9, 10, 11, 12, 1, 2],
        "prompt_topic": (
            "an honest guide to visiting Iceland from UK airports — the flights (Icelandair from LHR, "
            "easyJet and Wizz Air options), the costs (Iceland is genuinely expensive and you should "
            "budget accordingly), what's actually worth doing, the Northern Lights reality (you need "
            "luck, September–March, and to get away from Reykjavik), summer vs winter visits, and "
            "whether the 'Ring Road' is realistic on a short trip. Be direct about the costs — a 4-day "
            "Iceland trip will cost £800-£1,200+ per person all-in if you're honest. But explain why "
            "it can still be worth it. Write from personal experience of going."
        ),
    },
    {
        "slug":        "morocco-flights-from-uk",
        "emoji":       "🕌",
        "title":       "Morocco from the UK: The Best Value Long-Weekend Destination Right Now",
        "subtitle":    "Three hours from Gatwick and it feels like a completely different world",
        "airport_names": "UK Airports",
        "cta_airport": "LGW",
        "best_months": [10, 11, 12, 1, 2, 3],
        "prompt_topic": (
            "Morocco as a destination from UK airports — specifically Marrakech, and briefly Fes and "
            "Casablanca. Cover: the flights (Ryanair and easyJet from multiple UK airports, often "
            "surprisingly cheap), the reality of visiting Marrakech (the medina is chaotic and "
            "wonderful, the touts are relentless — just be prepared), when to go (spring and autumn "
            "are best, summer is brutally hot), the cost reality (flights can be cheap, but good "
            "accommodation and guided experiences add up), and how a Marrakech long weekend actually "
            "works. Write from genuine experience — include the moment that made the trip."
        ),
    },
]

# ── Persona prompt ───────────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are Jamie, a 32-year-old British travel writer based in North London. You work at a flight deals
website, travel genuinely often, and write blog posts that help ordinary UK people find good value
flights. Your tone is like a well-travelled friend giving real advice — warm, direct, occasionally
funny, always honest.

Writing rules (follow all of these precisely):
• British English throughout: colour, favourite, travelling, whilst, cheers, mum, fortnight, queue,
  autumn, cosy, flat, flat white, mates, sorted, brilliant, reckon, gutted, proper, knackered, skint
• First person throughout — you've been to these places or have strong informed opinions about them
• Name real airlines by name: Ryanair, easyJet, Jet2, TUI, Wizz Air, British Airways (not BA), Iberia,
  TAP Air Portugal, Turkish Airlines, Pegasus. Be specific about which UK airports each operates from.
• Give realistic price ranges you've personally seen — use specific numbers, not round ones:
  "I paid £43 one-way", "returns were sitting at £78-£115", "I've seen it as low as £31"
• Include genuine opinions — including slightly contrarian ones. Challenge assumptions.
• Mention minor downsides honestly: early morning starts, bag fees, long transfers, overpriced airports
• Occasionally reference friends or family: "my mate Sarah went last October", "took my sister for her
  birthday", "my partner refuses to book anything before 8am so we've had some rows about this"
• Never use these words or phrases: nestled, vibrant, bustling, thriving, picturesque, stunning, iconic,
  hidden gem, treasure, paradise, tapestry, testament, elevate, delve, comprehensive, navigate, realm,
  underscores, it's worth noting, furthermore, in conclusion, in summary, additionally, moreover,
  undoubtedly, certainly, absolutely, it's important to note, I'll be honest
• Write in paragraphs — NO bullet points or numbered lists anywhere in the body text
• Vary paragraph lengths: mix short punchy sentences with longer flowing ones. Some sections might
  have a 2-sentence paragraph followed by a 5-sentence one.
• Never sound like a brochure. Measured and honest beats enthusiastic and vague.
• Do not open sections with "I" — vary your sentence openings
• Never use em dashes (—) or double hyphens (--) anywhere. Use commas, full stops, or rewrite the sentence instead.
• Never use colons to introduce a list — write it as a sentence instead
• Never write parenthetical asides with brackets like (this) — weave the thought into the sentence naturally
• Never start a sentence with "And" or "But" — rewrite to avoid it
• Avoid overly structured writing — no "Firstly", "Secondly", "Finally", "The bottom line", "The truth is"

Output: Return ONLY valid JSON. No markdown fences, no code blocks, no explanation text."""

USER_PROMPT = """\
Write a travel blog post about: {prompt_topic}

Today is {month_name} {year}. Reference the current season naturally where it makes sense.

Write exactly 4 sections. Each section must be 3-6 paragraphs of flowing prose (never lists).
Use HTML only for: <strong>bold text</strong> and <br><br> as paragraph breaks within a section body.

Return ONLY this JSON (no markdown, no code fences, nothing else before or after):
{{
  "title": "engaging SEO title, max 70 chars",
  "subtitle": "one punchy line, max 90 chars",
  "airport_names": "which UK airports this post covers, concise",
  "meta": "SEO meta description 145-160 chars, factual and specific",
  "sections": [
    {{"heading": "section heading", "body": "full section prose as a single HTML string, paragraphs separated with <br><br>"}},
    {{"heading": "section heading", "body": "..."}},
    {{"heading": "section heading", "body": "..."}},
    {{"heading": "section heading", "body": "..."}}
  ],
  "cta_airport": "{cta_airport}"
}}

Topic brief: {prompt_topic}"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _published_slugs() -> dict:
    """Return {slug: mtime_seconds} for every JSON post in BLOG_DIR."""
    result = {}
    if not os.path.isdir(BLOG_DIR):
        return result
    for fn in os.listdir(BLOG_DIR):
        if fn.endswith('.json') and not fn.startswith('.'):
            slug = fn[:-5]
            result[slug] = os.path.getmtime(os.path.join(BLOG_DIR, fn))
    return result


def _seasonal_match(months, current_month: int) -> bool:
    """True if current_month is within 2 months of any month in the list."""
    if not months:
        return False
    return any(min((current_month - m) % 12, (m - current_month) % 12) <= 2 for m in months)


def pick_next_topic(force: bool = False, specific_slug: str = None) -> dict | None:
    """
    Return the next topic to generate.

    Priority:
    1. Unpublished topic whose best_months matches current month (±2 months)
    2. Unpublished evergreen topic
    3. Oldest stale seasonal topic (age ≥ STALE_DAYS)
    4. Oldest stale topic overall
    Returns None if everything is fresh.
    """
    published = _published_slugs()
    now_month = datetime.now().month
    now_ts    = time.time()

    if specific_slug:
        for t in TOPIC_PIPELINE:
            if t['slug'] == specific_slug:
                return t
        return None

    unpub_seasonal, unpub_evergreen = [], []
    stale_seasonal, stale_any       = [], []

    for topic in TOPIC_PIPELINE:
        slug    = topic['slug']
        months  = topic.get('best_months')
        is_seas = _seasonal_match(months, now_month)

        if slug not in published:
            (unpub_seasonal if is_seas else unpub_evergreen).append(topic)
        elif not force:
            age = (now_ts - published[slug]) / 86400
            if age >= STALE_DAYS:
                (stale_seasonal if is_seas else stale_any).append((age, topic))

    if force:
        # Regenerate the next seasonally-relevant or first-in-list
        for topic in TOPIC_PIPELINE:
            if _seasonal_match(topic.get('best_months'), now_month):
                return topic
        return TOPIC_PIPELINE[0]

    for bucket in (unpub_seasonal, unpub_evergreen):
        if bucket:
            return bucket[0]
    for stale in (stale_seasonal, stale_any):
        if stale:
            return max(stale, key=lambda x: x[0])[1]
    return None


def _build_related(exclude_slug: str) -> list:
    """Return up to 3 [slug, title] pairs for the related posts section."""
    published = _published_slugs()
    related = []

    # From already-generated disk posts
    for fn in sorted(os.listdir(BLOG_DIR)):
        if not fn.endswith('.json') or fn.startswith('.'):
            continue
        slug = fn[:-5]
        if slug == exclude_slug:
            continue
        try:
            with open(os.path.join(BLOG_DIR, fn), encoding='utf-8') as f:
                d = json.load(f)
            related.append([slug, d.get('title', slug)])
        except Exception:
            pass
        if len(related) >= 3:
            return related

    # Fill remainder from static hardcoded posts
    for slug, title in STATIC_POSTS:
        if slug == exclude_slug or any(r[0] == slug for r in related):
            continue
        related.append([slug, title])
        if len(related) >= 3:
            break

    return related


# ── Core generator ───────────────────────────────────────────────────────────

def generate_post(topic: dict, dry_run: bool = False) -> dict | None:
    """
    Call the Claude API and save the blog post JSON to BLOG_DIR.
    Returns the post dict, or None on failure.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("[blog_generator] ANTHROPIC_API_KEY not set — skipping generation.", file=sys.stderr)
        return None

    client = anthropic.Anthropic(api_key=api_key)

    prompt = USER_PROMPT.format(
        prompt_topic=topic['prompt_topic'],
        month_name=datetime.now().strftime('%B'),
        year=datetime.now().year,
        cta_airport=topic['cta_airport'],
    )

    print(f"[blog_generator] Generating: {topic['slug']} …")

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"[blog_generator] API error: {exc}", file=sys.stderr)
        return None

    raw = msg.content[0].text.strip()

    # Strip accidental markdown code fences
    if raw.startswith('```'):
        parts = raw.split('```')
        raw = parts[1] if len(parts) >= 2 else raw
        if raw.startswith('json'):
            raw = raw[4:]
    raw = raw.strip('`').strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[blog_generator] JSON parse error: {exc}\nRaw: {raw[:300]}", file=sys.stderr)
        return None

    def _clean(text: str) -> str:
        """Remove AI-tell punctuation patterns."""
        import re
        text = re.sub(r'\s*—\s*', ', ', text)   # em dash → comma
        text = re.sub(r'--+', ',', text)          # double hyphen → comma
        return text

    for section in data.get('sections', []):
        if 'body' in section:
            section['body'] = _clean(section['body'])
        if 'heading' in section:
            section['heading'] = _clean(section['heading'])
    for field in ('title', 'subtitle', 'meta'):
        if field in data:
            data[field] = _clean(data[field])

    post = {
        "slug":          topic['slug'],
        "emoji":         topic.get('emoji', '✈️'),
        "title":         data.get('title',         topic['title']),
        "subtitle":      data.get('subtitle',      topic['subtitle']),
        "airport_names": data.get('airport_names', topic.get('airport_names', 'UK Airports')),
        "meta":          data.get('meta', ''),
        "sections":      data.get('sections', []),
        "cta_airport":   data.get('cta_airport',   topic['cta_airport']),
        "related":       _build_related(topic['slug']),
        "published_at":  datetime.now().isoformat(),
    }

    if not post['sections']:
        print("[blog_generator] No sections in response — aborting save.", file=sys.stderr)
        return None

    if dry_run:
        print(json.dumps(post, indent=2, ensure_ascii=False))
        return post

    path = os.path.join(BLOG_DIR, f"{topic['slug']}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(post, f, ensure_ascii=False, indent=2)

    print(f"[blog_generator] Saved → {path}")
    return post


# ── Public API (called by app.py scheduler) ──────────────────────────────────

def run_next(force: bool = False) -> bool:
    """
    Generate the next due blog post.
    Uses a lock file to prevent duplicate runs across gunicorn workers.
    Returns True if a post was generated.
    """
    # Lock: skip if another worker ran within the last 23 hours
    if not force and os.path.exists(LOCK_FILE):
        age_hours = (time.time() - os.path.getmtime(LOCK_FILE)) / 3600
        if age_hours < 23:
            return False

    topic = pick_next_topic(force=force)
    if topic is None:
        print("[blog_generator] No topics due — all posts are fresh.")
        return False

    post = generate_post(topic)
    if post:
        # Update lock file timestamp
        with open(LOCK_FILE, 'w') as f:
            f.write(datetime.now().isoformat())
        return True
    return False


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(description="Generate weekly blog posts via Claude API")
    parser.add_argument('--force',   action='store_true', help='Regenerate even if recently published')
    parser.add_argument('--list',    action='store_true', help='List all topics and their status')
    parser.add_argument('--dry-run', action='store_true', help='Print JSON without saving')
    parser.add_argument('--topic',   metavar='SLUG',      help='Generate a specific topic by slug')
    args = parser.parse_args()

    if args.list:
        published = _published_slugs()
        now_month = datetime.now().month
        print(f"\n{'SLUG':<45} {'STATUS':<12} {'SEASONAL'}")
        print("-" * 75)
        for t in TOPIC_PIPELINE:
            slug = t['slug']
            if slug in published:
                age = (time.time() - published[slug]) / 86400
                status = f"ok ({int(age)}d)" if age < STALE_DAYS else f"STALE ({int(age)}d)"
            else:
                status = "unpublished"
            seas = "✓" if _seasonal_match(t.get('best_months'), now_month) else ""
            print(f"{slug:<45} {status:<12} {seas}")
        print()
        return

    if args.topic:
        topic = pick_next_topic(specific_slug=args.topic)
        if not topic:
            print(f"Topic '{args.topic}' not found in pipeline.")
            sys.exit(1)
        generate_post(topic, dry_run=args.dry_run)
        return

    topic = pick_next_topic(force=args.force)
    if topic is None:
        print("No topics due for generation.")
        return

    generate_post(topic, dry_run=args.dry_run)


if __name__ == '__main__':
    _cli()
