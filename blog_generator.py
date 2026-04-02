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
from dotenv import load_dotenv
load_dotenv()

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
    # ── Priority queue: generate these first ────────────────────────────────
    {
        "slug":        "easter-flight-deals-uk-2026",
        "emoji":       "🐣",
        "title":       "Easter 2026 Flight Deals from the UK",
        "subtitle":    "Four-day weekend. Direct flight. Prices still makeable if you book now.",
        "airport_names": "UK Airports",
        "cta_airport": "LHR",
        "best_months": None,
        "prompt_topic": (
            "Easter 2026 flight deals from UK airports — Easter weekend is 2–6 April 2026. "
            "Lead with specific prices on the best-value routes right now: "
            "Seville from £39 one-way on Ryanair from Stansted, Porto from £44 on easyJet from Bristol, "
            "Krakow from £31 on Ryanair from Luton, Marrakech from £67 return on easyJet from Gatwick, "
            "Malta from £78 return on Ryanair. "
            "Explain the Easter booking window — why booking 4-6 weeks out is the sweet spot vs leaving it late. "
            "Cover the Friday vs Saturday departure price gap (Saturday out of Heathrow on Good Friday can be "
            "£40-60 more than the same route Thursday night). "
            "Name which cities actually work for 3-4 nights at Easter vs which get overcrowded. "
            "Give a section on airport strategy: Stansted, Luton, and Bristol have far cheaper Easter fares "
            "than Heathrow for most European routes. "
            "End with 3 concrete route suggestions with prices and airlines for someone booking this week."
        ),
    },
    {
        "slug":        "summer-holidays-cheap-flights-uk",
        "emoji":       "☀️",
        "title":       "Cheap Summer Holiday Flights from the UK: 2026 Guide",
        "subtitle":    "Book before April and you can still get family summer flights for under £100pp",
        "airport_names": "UK Airports",
        "cta_airport": "MAN",
        "best_months": None,
        "prompt_topic": (
            "cheap summer holiday flights from UK airports in 2026 — covering July and August, when prices "
            "spike hardest. Lead with specific prices visible right now: "
            "Majorca from £89 return on Jet2 from Leeds Bradford, Corfu from £104 return on easyJet from Gatwick, "
            "Faro (Algarve) from £79 return on Ryanair from Stansted, Tenerife from £119 return on easyJet from Manchester, "
            "Lanzarote from £97 return on Ryanair from Birmingham. "
            "Be specific about the booking window: summer fares from major UK airports are already rising — "
            "what's available in mid-March vs what it'll look like in May. "
            "Give the honest truth about August prices (brutal) vs late July (marginally better). "
            "Cover the family angle: Jet2 vs TUI vs easyJet for families, which airports are calmer, "
            "and why flying from a regional airport (Leeds, Bristol, Birmingham, Edinburgh) often saves "
            "£50-100pp over flying from Heathrow or Gatwick. "
            "Include 3 concrete summer route suggestions for different budgets: under £80pp, £80-130pp, £130+pp."
        ),
    },
    {
        "slug":        "uk-bank-holiday-flight-deals",
        "emoji":       "📅",
        "title":       "UK Bank Holiday Flight Deals: The Best Routes for Every Long Weekend",
        "subtitle":    "May Day to August bank holiday — here's how to fly without paying peak prices",
        "airport_names": "UK Airports",
        "cta_airport": "STN",
        "best_months": None,
        "prompt_topic": (
            "making the most of every UK bank holiday weekend in 2026 for cheap flights. Cover all four "
            "remaining bank holidays: Easter (2-6 April), Early May bank holiday (4 May), Spring bank holiday "
            "(25-26 May), and August bank holiday (29-31 August). "
            "For each: name the best-value routes available, specific prices (e.g. Porto from £44 one-way for "
            "May Day on easyJet from Bristol, Budapest from £31 on Wizz Air from Luton), departure airport "
            "recommendations, and the booking timing sweet spot. "
            "The key insight to drive: Thursday night or Tuesday return flights for bank holiday weekends save "
            "£30-80 vs Friday out, Monday back. Give real examples. "
            "Cover the bank holiday premium — how much extra you're typically paying vs the same route mid-week "
            "in the same month. Some bank holidays are worse than others (August is brutal, May Day is often fine). "
            "Include a section on the airports that handle bank holiday traffic best (Stansted and Bristol tend "
            "to be less chaotic than Heathrow)."
        ),
    },
    {
        "slug":        "school-break-flights-uk-guide",
        "emoji":       "🎒",
        "title":       "School Holiday Flights from the UK: How to Beat the Price Spike",
        "subtitle":    "Half term, summer, Christmas — the honest guide to flying during school breaks",
        "airport_names": "UK Airports",
        "cta_airport": "MAN",
        "best_months": None,
        "prompt_topic": (
            "flying from the UK during school holidays — covering the real price reality and how to manage it. "
            "Break it into the main school holiday windows: "
            "Easter (2-6 April 2026), May half term (25-29 May), Summer (mid-July to start of September), "
            "October half term (late October), and Christmas/New Year. "
            "For each period, name the best-value destinations with specific prices: "
            "Lanzarote in summer from £97 return on Ryanair from Bristol vs the same route in October half term "
            "from £119 (still worth it). Majorca in May half term from £89 return on Jet2. "
            "Krakow in October half term from £39 one-way on Ryanair (cheap, cold, but brilliant). "
            "Be honest: summer holidays cost more. Show the price difference and explain when it's worth paying "
            "it vs when you should shift your dates slightly. "
            "Key tip: flying on the last Saturday of term (kids still in school) vs the first Saturday of holidays "
            "can save £50-80pp. Cover this with real numbers. "
            "Include the best family-friendly airports for school holiday travel: why Manchester, Bristol, and "
            "Birmingham often beat Heathrow for stress and price."
        ),
    },
    {
        "slug":        "september-christmas-flight-deals-uk",
        "emoji":       "🍂",
        "title":       "September and Christmas Flight Deals from the UK",
        "subtitle":    "Two of the UK's best windows to fly cheaply — here's what to book right now",
        "airport_names": "UK Airports",
        "cta_airport": "LGW",
        "best_months": None,
        "prompt_topic": (
            "two of the best travel windows for UK flyers: September (post-summer price drop) and "
            "Christmas/New Year. Write two distinct sections on each. "
            "September: prices fall off a cliff after August. Give specific examples — "
            "Athens from £67 return on easyJet in September vs £189 return in August (same route). "
            "Split, Croatia from £79 return on Ryanair from Stansted in September. "
            "Istanbul from £89 return on Turkish Airlines or Pegasus from Heathrow. "
            "Rome from £54 return on Ryanair from Stansted. "
            "Explain why September is the UK's best-kept travel secret: 24-27 degrees in most of southern Europe, "
            "no school holiday surcharge if you don't have kids, flights half the August price. "
            "Christmas/New Year: be specific about the price dynamics — "
            "flying out on 23 Dec vs 24 Dec (24th is often £40-60 cheaper). "
            "New Year's Eve city breaks: Prague, Lisbon, and Amsterdam from £89-139 return. "
            "Best cheap Christmas sun routes: Tenerife from £107 return on Jet2 over Christmas week, "
            "Lanzarote from £97 return on easyJet. "
            "Practical Christmas booking tips: January is when the Christmas flight deals appear — book now "
            "for December and lock in prices before the summer rush inflates everything further."
        ),
    },
    # ── Main pipeline (original topics below) ───────────────────────────────
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

    {
        "slug":        "best-time-to-book-flights-uk",
        "emoji":       "📅",
        "title":       "Best Time to Book Flights from the UK: What Actually Works",
        "subtitle":    "Forget the Tuesday myth — here's what the data actually says",
        "airport_names": "UK Airports",
        "cta_airport": "STN",
        "best_months": None,
        "prompt_topic": (
            "a practical guide to when to book cheap flights from UK airports — cutting through the "
            "myths and giving real, useful advice. Cover: "
            "The booking window sweet spot for short-haul European flights from the UK: typically "
            "6-10 weeks ahead is where the best prices cluster. Give specific examples: Ryanair "
            "from Stansted to Krakow, how the price moves from 12 weeks out to 2 weeks out. "
            "When the Tuesday booking rule does and doesn't apply — airlines don't systematically "
            "drop prices on Tuesdays anymore, but some flash sales do land midweek. "
            "The Christmas and Easter booking window: why leaving it past September for Christmas "
            "flights costs significantly more, with rough price differences. "
            "Summer flights: the February-March window for July-August is the sweet spot before "
            "prices spike when schools start booking. "
            "Last-minute flights: when they work (short-haul on routes with low demand, flying "
            "off-peak days) and when they're a gamble you'll lose (Bank Holidays, school holidays). "
            "Price alerts: how to use them properly rather than checking daily. "
            "Write from experience of watching flight prices move over time — give specific route "
            "examples with rough price points at each booking stage."
        ),
    },
    {
        "slug":        "ryanair-vs-easyjet-comparison-uk",
        "emoji":       "⚖️",
        "title":       "Ryanair vs easyJet: Which Is Actually Cheaper in 2025?",
        "subtitle":    "The honest all-in price comparison — bags, seats, and real totals",
        "airport_names": "UK Airports",
        "cta_airport": "STN",
        "best_months": None,
        "prompt_topic": (
            "an honest, practical comparison of Ryanair and easyJet for UK travellers — covering "
            "which is genuinely cheaper when you account for everything. Cover: "
            "Base fare comparison on a few specific competitive routes: Stansted to Malaga, "
            "Gatwick to Barcelona, Manchester to Lisbon — Ryanair typically wins on base fare but "
            "by how much, and what the all-in looks like once you add a cabin bag. "
            "The bag fee reality: Ryanair's small personal item (under seat only) is free, but a "
            "10kg cabin bag costs £12-25 depending on when you book; hold bags from £20. easyJet's "
            "small cabin bag is free, large cabin bag from £9, hold from £18. Walk through the "
            "maths on a return trip for a couple — who wins when you include bags. "
            "Seat selection: both charge for it, both can be skipped if you don't mind random "
            "assignment. Ryanair charges £4-15 per seat per flight; easyJet similar. "
            "On-time performance: easyJet's has been better historically — matters for connections. "
            "Which airports each flies from: Ryanair dominates Stansted, Dublin, and secondary "
            "regional airports; easyJet is stronger at Gatwick, Bristol, Edinburgh, Manchester. "
            "The verdict: when Ryanair wins (carry-on only, flying from Stansted, booking early), "
            "when easyJet wins (Gatwick routes, checked luggage needed, needing flexibility). "
            "Write from genuine experience booking both regularly."
        ),
    },
    {
        "slug":        "stansted-vs-luton-comparison-uk",
        "emoji":       "🛫",
        "title":       "Stansted vs Luton: Which London Airport Is Cheaper to Fly From?",
        "subtitle":    "Both are budget airline hubs — but they're not the same",
        "airport_names": "London Stansted & Luton",
        "cta_airport": "STN",
        "best_months": None,
        "prompt_topic": (
            "a practical comparison of London Stansted (STN) and London Luton (LTN) airports for "
            "budget travellers from London — covering prices, routes, and the travel-in logistics. "
            "Route comparison: Stansted is Ryanair's UK hub — the route network is bigger, "
            "especially for Eastern Europe (Krakow from £31, Bucharest from £39, Warsaw from £34), "
            "Iberia, and Morocco. Luton is Wizz Air's UK base plus easyJet and Ryanair. "
            "Price differences on specific routes: Stansted to Krakow vs Luton to Krakow — often "
            "similar, but worth checking both. Wizz Air from Luton can undercut Ryanair from "
            "Stansted on some Central/Eastern European routes. "
            "Getting there: both are roughly 30-40 minutes from central London by train or bus. "
            "Stansted Express from Liverpool Street is faster but more expensive (from £18 "
            "single); the Luton train (Thameslink from St Pancras) is cheaper. National Express "
            "coaches serve both and are cheaper but slower. "
            "Airport experience: Stansted is larger and more modern; Luton is functional but more "
            "cramped, especially at peak times. "
            "The verdict: for Eastern Europe, check both — Luton's Wizz Air routes often win on "
            "price. For Spain, Portugal, and Morocco, Stansted usually wins on frequency and fare. "
            "Write from personal experience using both airports."
        ),
    },
    {
        "slug":        "cheap-flights-nyc-to-europe",
        "emoji":       "🌍",
        "title":       "Cheap Flights from New York to Europe: What to Pay and When",
        "subtitle":    "Transatlantic doesn't have to mean expensive — if you know the angles",
        "airport_names": "JFK & Newark",
        "cta_airport": "JFK",
        "market":      "us",
        "best_months": [1, 2, 3, 9, 10, 11],
        "prompt_topic": (
            "a guide to finding affordable transatlantic flights from New York to Europe — "
            "specifically from JFK and Newark. Lead with specific prices: "
            "London from $349 round-trip on Norse Atlantic from JFK, "
            "Reykjavik from $329 round-trip on Icelandair from JFK, "
            "Dublin from $399 round-trip on Aer Lingus from JFK, "
            "Lisbon from $389 round-trip on TAP Air Portugal from Newark, "
            "Paris from $449 round-trip on Air France from JFK. "
            "The budget transatlantic carriers: Norse Atlantic, Icelandair. What the low base fare "
            "actually gets you (very little), what bag fees look like all-in, and when they're "
            "worth it vs when to pay a bit more for a legacy carrier. "
            "Seasonal pricing: January and February are the cheapest months — give the price "
            "difference vs peak summer. The shoulder season sweet spots (late March, October). "
            "The booking window: transatlantic needs more lead time than domestic — 3-4 months "
            "for summer, 6-8 weeks for off-peak. "
            "European gateway cities worth flying into instead of London: Lisbon and Dublin are "
            "consistently cheaper to fly into and excellent bases to explore further. "
            "Write from experience booking transatlantic flights regularly."
        ),
    },

    # ── UK gaps ──────────────────────────────────────────────────────────────
    {
        "slug":        "cheap-flights-birmingham-airport",
        "emoji":       "🇬🇧",
        "title":       "Cheapest Flights from Birmingham Airport: Routes Worth Booking Now",
        "subtitle":    "The Midlands has a seriously underrated airport — here's how to use it",
        "airport_names": "Birmingham Airport (BHX)",
        "cta_airport": "BHX",
        "market":      "uk",
        "best_months": None,
        "prompt_topic": (
            "the best cheap flight routes from Birmingham Airport (BHX) — written for Midlands "
            "travellers who are tired of hearing everything is easier from London. Lead with specific "
            "prices: Alicante from £44 one-way on Ryanair, Faro from £49 on easyJet, Lanzarote from "
            "£84 return on Jet2, Dubai from £234 on Emirates direct, Tenerife from £89 on Jet2. "
            "Cover the airlines that operate from BHX: Ryanair, easyJet, Jet2, TUI, Emirates, Wizz Air. "
            "Be specific about which routes are better from Birmingham vs driving to Heathrow or Stansted "
            "— Emirates' Dubai service alone makes BHX worth considering for long-haul. "
            "Give the honest parking/transport reality: BHX is easier to get to and cheaper to park at "
            "than London airports for anyone within 60 miles. "
            "Include the best seasons for each route and 3 specific trip suggestions with prices."
        ),
    },
    {
        "slug":        "greek-islands-flights-from-uk",
        "emoji":       "🏛️",
        "title":       "Cheap Flights to the Greek Islands from the UK: Which to Choose",
        "subtitle":    "Corfu, Crete, Zante, Rhodes, Santorini — flights compared honestly",
        "airport_names": "UK Airports",
        "cta_airport": "LGW",
        "market":      "uk",
        "best_months": [2, 3, 4, 5, 6, 7, 8],
        "prompt_topic": (
            "a practical guide to flying from UK airports to the Greek islands — covering all the "
            "main ones with honest takes on which are worth the flight price and which are overrated. "
            "Lead with specific prices: Corfu from £67 return on easyJet from Gatwick, "
            "Heraklion (Crete) from £79 return on easyJet from Luton, "
            "Zante (Zakynthos) from £89 return on Jet2 from Manchester, "
            "Rhodes from £84 return on Ryanair from Stansted, "
            "Santorini from £134 return on easyJet from Gatwick (cheapest you'll realistically find). "
            "Be direct about Santorini: it's expensive on the island itself, crowds are insane in July "
            "and August, and the flights reflect that. Suggest alternatives — Naxos, Milos, Paros — "
            "cheaper both to fly to and to stay on. "
            "Cover the best time to visit each island: shoulder season (May/June, September) vs peak "
            "summer. Name which airlines fly to each island and from which UK airports. "
            "Include a section on Crete as the best-value large island — multiple airports (Heraklion "
            "and Chania), good flight competition, and something for everyone."
        ),
    },
    {
        "slug":        "croatia-flights-from-uk",
        "emoji":       "🌊",
        "title":       "Cheap Flights to Croatia from the UK: Split, Dubrovnik and Beyond",
        "subtitle":    "Croatia keeps getting more popular — but the flights are still reasonable",
        "airport_names": "UK Airports",
        "cta_airport": "LGW",
        "market":      "uk",
        "best_months": [3, 4, 5, 6, 7, 8, 9],
        "prompt_topic": (
            "flying from UK airports to Croatia — covering Split, Dubrovnik, and Zadar, with honest "
            "takes on each destination and the flight options. Lead with prices: "
            "Split from £57 one-way on easyJet from Gatwick, "
            "Dubrovnik from £79 one-way on easyJet from Gatwick (pricier because it's more popular), "
            "Zadar from £49 one-way on Ryanair from Stansted (the underrated option). "
            "Be direct about Dubrovnik: it's genuinely overcrowded in July and August and the flight "
            "premium reflects the demand. Split is a better base — easier to get around, cheaper, and "
            "equally beautiful. Zadar barely anyone talks about but it's brilliant. "
            "Cover the shoulder season argument for Croatia: May and September are significantly "
            "cheaper to fly and the Adriatic is still warm enough for swimming. "
            "Give the island-hopping angle: flying into Split, island-hopping to Hvar/Brač/Vis, "
            "flying home from Dubrovnik — one of the best holiday formats from the UK. "
            "Include which UK airports serve each Croatian city and whether it's worth going via a hub."
        ),
    },
    {
        "slug":        "canary-islands-winter-flights-uk",
        "emoji":       "🌴",
        "title":       "Canary Islands from the UK: The Reliable Winter Sun That Actually Delivers",
        "subtitle":    "Tenerife, Lanzarote, Gran Canaria, Fuerteventura — which is worth the flight?",
        "airport_names": "UK Airports",
        "cta_airport": "MAN",
        "market":      "uk",
        "best_months": [9, 10, 11, 12, 1, 2, 3],
        "prompt_topic": (
            "the Canary Islands as a winter sun destination from UK airports — the four main islands "
            "compared honestly for UK travellers: Tenerife, Lanzarote, Gran Canaria, Fuerteventura. "
            "Lead with specific prices: "
            "Tenerife South (TFS) from £89 return on easyJet from Manchester, "
            "Lanzarote (ACE) from £79 return on Ryanair from Bristol, "
            "Gran Canaria (LPA) from £84 return on Jet2 from Birmingham, "
            "Fuerteventura (FUE) from £74 return on Ryanair from Stansted. "
            "Give an honest island comparison: Tenerife is the most varied (Mount Teide, proper towns, "
            "beaches), Lanzarote is volcanic and interesting if you explore, Gran Canaria has the best "
            "all-round infrastructure, Fuerteventura is all about beaches and wind sports. "
            "Cover which months are best for Canary Islands travel from the UK — the whole point is "
            "reliable winter sun, so give the actual temperature and sunshine hours honestly. "
            "Name which UK airports serve each island and which airlines dominate. "
            "Include a section on the family vs couples angle — Tenerife and Lanzarote tend to be more "
            "family-heavy in the resort areas; Gran Canaria has better options for couples."
        ),
    },
    {
        "slug":        "flight-delay-compensation-guide-uk",
        "emoji":       "⚖️",
        "title":       "Flight Delay Compensation: How to Claim What You're Owed",
        "subtitle":    "Most passengers don't claim. The ones who do get up to €600 back.",
        "airport_names": "UK & EU Airports",
        "cta_airport": "LHR",
        "market":      "uk",
        "best_months": None,
        "prompt_topic": (
            "a practical guide to flight delay and cancellation compensation for UK passengers — "
            "specifically how UK261 (the retained EU regulation) works and what passengers are "
            "actually entitled to. This is an important consumer rights topic most passengers don't "
            "know about. Cover: "
            "What you're entitled to: for delays over 3 hours on routes departing the UK, or arriving "
            "into the UK on a UK/EU carrier, passengers are owed €250-€600 depending on flight distance. "
            "Under 1,500km = €250. 1,500-3,500km = €400. Over 3,500km = €600. "
            "The key rule most people miss: the airline must prove 'extraordinary circumstances' to "
            "avoid paying. Bad weather counts. Air traffic control strikes count. But technical faults "
            "on the plane do NOT — that's the airline's responsibility. "
            "How to make a claim: you can do it yourself by emailing the airline's customer relations "
            "team with your flight details, delay length, and a reference to UK261/EU261. Most airlines "
            "have a claims form on their website. Give the specific approach for Ryanair, easyJet, BA. "
            "When to use a claims service: services like Compensair handle everything on a no-win no-fee "
            "basis, taking roughly 25-35% commission on what they recover. Worth it if you've been "
            "rejected by the airline or don't want the hassle of following up. "
            "Realistic timeline: claims can take 4-12 weeks if the airline plays ball; up to 18 months "
            "if it goes to arbitration or court. "
            "Common rejection excuses airlines use and how to counter them. "
            "End with a clear action step: if your flight was delayed 3+ hours in the last 6 years, "
            "it's worth checking whether you have a claim."
        ),
    },
    {
        "slug":        "cheap-flights-turkey-from-uk",
        "emoji":       "🕌",
        "title":       "Turkey from the UK: Still the Best Value Beach Holiday in 2026?",
        "subtitle":    "Antalya, Dalaman, Istanbul — the honest price check",
        "airport_names": "UK Airports",
        "cta_airport": "LGW",
        "market":      "uk",
        "best_months": [3, 4, 5, 6, 7, 8, 9, 10],
        "prompt_topic": (
            "Turkey as a flight destination from UK airports in 2026 — covering both the beach "
            "resorts (Antalya and Dalaman) and Istanbul as a city break. Lead with prices: "
            "Antalya from £119 return on Jet2 from Manchester, "
            "Dalaman from £104 return on TUI from Birmingham, "
            "Istanbul from £89 return on Turkish Airlines from Heathrow, "
            "Bodrum from £114 return on easyJet from Gatwick. "
            "Be honest about the Turkey value proposition: the Turkish lira has weakened significantly, "
            "meaning UK visitors' money goes much further on the ground than 3-4 years ago. "
            "A week all-inclusive in Antalya can still be done for £600-800pp including flights — "
            "give the specific breakdown. "
            "Cover the Istanbul angle separately: it's one of the great European (well, Eurasian) cities "
            "and Turkish Airlines flights from Heathrow are often excellent value. "
            "Name which UK airports serve Turkish beach resorts (charter flights via Jet2, TUI dominate) "
            "vs Istanbul (Turkish Airlines direct from LHR, easyJet from various). "
            "Include the honest travel advisory context — Turkey is generally very safe for tourists "
            "in resort areas and Istanbul but check the FCO advice for border regions."
        ),
    },
    # ── US gaps ───────────────────────────────────────────────────────────────
    {
        "slug":        "cheap-flights-from-los-angeles",
        "emoji":       "🌴",
        "title":       "Cheapest Flights from Los Angeles: LAX and the Alternatives",
        "subtitle":    "LA has three usable airports — here's how to get the cheapest fare out",
        "airport_names": "LAX, Burbank & Long Beach",
        "cta_airport": "LAX",
        "market":      "us",
        "best_months": None,
        "prompt_topic": (
            "cheap flights from Los Angeles — covering LAX as the main hub but also Burbank (BUR) "
            "and Long Beach (LGB) as budget alternatives worth checking. Lead with specific prices: "
            "New York from $99 one-way on JetBlue from LAX, "
            "Las Vegas from $39 one-way on Spirit from LAX (45 minutes, why people fly it), "
            "Tokyo from $499 round-trip on Japan Airlines from LAX, "
            "Cancun from $199 round-trip on Delta from LAX, "
            "San Francisco from $49 one-way on Southwest from Burbank. "
            "The Burbank and Long Beach angle: Southwest dominates Burbank — no bag fees, cheaper on "
            "many routes than LAX, and the airport is dramatically easier to get through. For anyone "
            "in the San Fernando Valley or Pasadena, Burbank beats LAX on nearly every measure. "
            "Cover the best international routes from LAX: Pacific routes (Japan, Korea, Australia, "
            "Philippines) where LAX has the most direct options and competitive fares. "
            "Give the domestic sweet spots: routes where LA has the most competition and therefore "
            "cheapest fares — Vegas, San Francisco, Seattle, Denver. "
            "Include the booking window that works for transpacific vs domestic."
        ),
    },
    {
        "slug":        "cheap-flights-from-chicago",
        "emoji":       "🌬️",
        "title":       "Cheapest Flights from Chicago: O'Hare vs Midway Compared",
        "subtitle":    "Two airports, one city — knowing which to use can save you $50-100",
        "airport_names": "O'Hare (ORD) & Midway (MDW)",
        "cta_airport": "ORD",
        "market":      "us",
        "best_months": None,
        "prompt_topic": (
            "cheap flights from Chicago — specifically how to use both O'Hare (ORD) and Midway (MDW) "
            "to find the best fares. Lead with specific prices: "
            "New York from $79 one-way on Frontier from O'Hare, "
            "Miami from $89 one-way on Spirit from O'Hare, "
            "Las Vegas from $59 one-way on Southwest from Midway, "
            "Los Angeles from $99 one-way on United from O'Hare, "
            "Cancun from $159 round-trip on American from O'Hare. "
            "The key Chicago insight: Midway is Southwest's hub. Southwest doesn't appear on Google "
            "Flights or most search engines, so Midway fares are systematically invisible to people "
            "who don't check southwest.com directly. On many domestic routes, Midway beats O'Hare "
            "on price when you factor in free bags. "
            "Cover O'Hare's strengths: it's a massive United and American hub with unbeatable "
            "international connectivity — transatlantic and transpacific routes that Midway can't touch. "
            "Give the honest getting-there comparison: both airports are roughly 45 minutes from the "
            "Loop by El train, similar cost. "
            "Include the best seasons for cheap fares out of Chicago and 3 specific trip picks."
        ),
    },
    {
        "slug":        "cheap-flights-from-miami",
        "emoji":       "🌊",
        "title":       "Cheapest Flights from Miami: Latin America, Caribbean and Beyond",
        "subtitle":    "Miami is one of the most connected airports in the western hemisphere — use it",
        "airport_names": "Miami International (MIA) & Fort Lauderdale (FLL)",
        "cta_airport": "MIA",
        "market":      "us",
        "best_months": None,
        "prompt_topic": (
            "cheap flights from Miami — covering both Miami International (MIA) and Fort Lauderdale "
            "(FLL) as two airports 30 miles apart with very different airline profiles. Lead with prices: "
            "Bogota from $179 round-trip on American from MIA, "
            "San Juan Puerto Rico from $99 round-trip on Spirit from MIA, "
            "London from $299 round-trip on British Airways from MIA, "
            "Cancun from $99 round-trip on Spirit from FLL, "
            "New York from $79 one-way on JetBlue from MIA. "
            "The Fort Lauderdale angle: Spirit's main South Florida hub is FLL, not MIA. For Caribbean "
            "and domestic routes on Spirit, FLL often has more flights and lower fares than MIA. "
            "Southwest also operates from FLL. For anyone in Broward County, FLL is the obvious choice. "
            "Cover Miami's Latin America dominance: American Airlines hubs here with the most "
            "comprehensive Latin America network in the US — if you're flying anywhere in Central "
            "or South America, check Miami first regardless of where you live. "
            "Include the Caribbean sweet spots from South Florida: Bahamas, Jamaica, Aruba, Dominican "
            "Republic — give specific prices and which airline wins on each route."
        ),
    },
    {
        "slug":        "cheap-flights-from-atlanta",
        "emoji":       "✈️",
        "title":       "Cheapest Flights from Atlanta: How to Use the World's Busiest Airport",
        "subtitle":    "Delta's hub means connectivity — here's how to find the actual deals",
        "airport_names": "Hartsfield-Jackson Atlanta (ATL)",
        "cta_airport": "ATL",
        "market":      "us",
        "best_months": None,
        "prompt_topic": (
            "cheap flights from Atlanta Hartsfield-Jackson (ATL) — the world's busiest airport and "
            "Delta's main hub. Lead with specific prices: "
            "New York from $79 one-way on Delta/Spirit from ATL, "
            "Los Angeles from $79 one-way on Spirit from ATL, "
            "Cancun from $139 round-trip on Delta from ATL, "
            "London from $329 round-trip on Delta/Virgin from ATL, "
            "Paris from $349 round-trip on Delta/Air France from ATL. "
            "The Atlanta dynamic: Delta dominates, which means excellent SkyMiles value for frequent "
            "flyers but sometimes inflated cash fares. The counter: Spirit and Frontier both operate "
            "heavily from ATL and regularly undercut Delta by 40-60% on leisure routes. "
            "Cover the international options that are genuinely competitive from Atlanta: Delta's "
            "direct routes to Europe (London, Paris, Amsterdam, Rome) are frequently price-competitive "
            "because Delta hubs here — no connection premium. "
            "Give the Southeast domestic sweet spots from Atlanta: routes where ATL's position as a "
            "hub creates genuine competition — Miami, Orlando, Nashville, Charlotte, New Orleans. "
            "Include 3 specific trip picks with prices for different traveler types."
        ),
    },
    {
        "slug":        "cheap-flights-from-san-francisco",
        "emoji":       "🌁",
        "title":       "Cheapest Flights from San Francisco: SFO, Oakland and San Jose",
        "subtitle":    "Three Bay Area airports — the one you ignore is often the cheapest",
        "airport_names": "SFO, Oakland (OAK) & San Jose (SJC)",
        "cta_airport": "SFO",
        "market":      "us",
        "best_months": None,
        "prompt_topic": (
            "cheap flights from the San Francisco Bay Area — covering all three usable airports: "
            "SFO (main hub), Oakland (OAK, Southwest-dominated), and San Jose (SJC, good for South Bay). "
            "Lead with specific prices: "
            "New York from $109 one-way on United from SFO, "
            "Los Angeles from $49 one-way on Southwest from OAK, "
            "Seattle from $69 one-way on Alaska from SFO, "
            "Tokyo from $489 round-trip on ANA from SFO, "
            "Las Vegas from $39 one-way on Southwest from OAK. "
            "The Oakland argument: Southwest's OAK hub means no-bag-fee flights on dozens of domestic "
            "routes. For anyone on BART's line to Coliseum, getting to OAK is just as easy as SFO "
            "and often $30-60 cheaper. Give specific route comparisons: LA, Vegas, Denver, Phoenix "
            "where OAK wins. "
            "SFO's strength: transpacific routes. United, ANA, JAL, Korean Air, Cathay Pacific all "
            "hub or focus here — Tokyo, Seoul, Hong Kong, Sydney fares are genuinely competitive "
            "because of the competition level. "
            "Cover San Jose: useful for South Bay residents, Alaska Airlines has solid service, "
            "and it's dramatically easier to get through than SFO."
        ),
    },
    {
        "slug":        "us-to-europe-cheap-flights-guide",
        "emoji":       "🌍",
        "title":       "Cheap Flights from the US to Europe: What to Pay and When to Book",
        "subtitle":    "Transatlantic doesn't have to cost $1,000+ — if you know the angles",
        "airport_names": "Major US Airports",
        "cta_airport": "JFK",
        "market":      "us",
        "best_months": [1, 2, 3, 9, 10, 11],
        "prompt_topic": (
            "a guide to finding affordable transatlantic flights from US airports to Europe — "
            "written for Americans who assume Europe is automatically expensive to fly to. Lead with "
            "specific prices on the best-value routes right now: "
            "London from $349 round-trip on Norse Atlantic from JFK, "
            "Reykjavik from $329 round-trip on Icelandair from multiple US gateways, "
            "Lisbon from $389 round-trip on TAP Air Portugal from Newark, "
            "Dublin from $399 round-trip on Aer Lingus from JFK, "
            "Rome from $449 round-trip on ITA Airways from JFK. "
            "The gateway city strategy: flying into Lisbon, Dublin or Reykjavik is consistently "
            "cheaper than London or Paris, and they're excellent bases to explore from. Give the "
            "price differential with examples. "
            "Budget transatlantic carriers: Norse Atlantic and Icelandair — explain what the base fare "
            "actually includes (not much) and the bag fee reality. When they're worth it vs when to "
            "pay a bit more for a legacy carrier. "
            "The seasonal price reality: January and February are the cheapest months to fly to Europe "
            "from the US — give the specific price difference vs peak summer ($200-400 cheaper). "
            "The booking window: 3-4 months for summer departures; 6-8 weeks for off-peak. "
            "Which US departure cities have the most transatlantic competition: JFK, Newark, Boston, "
            "Miami, Atlanta, Chicago, LAX — ranked by route availability and typical fares."
        ),
    },
    # ── US market topics ─────────────────────────────────────────────────────
    {
        "slug":        "cheap-flights-spring-break-us",
        "emoji":       "🌴",
        "title":       "Cheap Flights for Spring Break 2026: Best Deals from US Airports",
        "subtitle":    "Spring break doesn't have to cost a fortune — if you book the right way",
        "airport_names": "US Airports",
        "cta_airport": "JFK",
        "market":      "us",
        "best_months": [1, 2, 3],
        "prompt_topic": (
            "cheap flights for spring break 2026 from major US airports — targeting March and April "
            "travelers. Lead with specific prices on the best-value routes: "
            "Cancun from $189 round-trip on Spirit from Chicago O'Hare, "
            "San Juan Puerto Rico from $149 round-trip on JetBlue from JFK, "
            "Las Vegas from $109 round-trip on Frontier from Atlanta Hartsfield, "
            "Nassau Bahamas from $229 round-trip on American from Miami, "
            "Cabo San Lucas from $249 round-trip on Delta from LAX. "
            "Explain the spring break booking window — why booking 6-8 weeks out is critical vs "
            "leaving it to the last minute when prices double. "
            "Cover the college spring break dates (mid-March) vs family spring break (late March/early April) "
            "and how the price difference between those windows is real. "
            "Give honest takes on Spirit and Frontier — yes the base fare is cheap, but name the bag "
            "fees upfront so nobody gets a nasty surprise at the gate. "
            "End with 3 concrete route picks with prices and airlines for someone booking this week."
        ),
    },
    {
        "slug":        "memorial-day-weekend-flights-us",
        "emoji":       "🇺🇸",
        "title":       "Memorial Day Weekend Flights: Where to Go and What to Pay",
        "subtitle":    "Three-day weekend, a cheap flight, and somewhere that isn't your couch",
        "airport_names": "US Airports",
        "cta_airport": "ORD",
        "market":      "us",
        "best_months": [3, 4, 5],
        "prompt_topic": (
            "cheap flights for Memorial Day weekend 2026 from US airports — the last Monday of May, "
            "making it a three-day weekend. Lead with specific prices: "
            "New Orleans from $149 round-trip on Southwest from Dallas Love Field, "
            "Denver from $119 round-trip on Frontier from Chicago O'Hare, "
            "Cancun from $229 round-trip on United from Houston Intercontinental, "
            "Miami from $139 round-trip on Spirit from New York LaGuardia, "
            "Nashville from $99 round-trip on American from Charlotte Douglas. "
            "Be specific about the booking timing: Memorial Day fares from major US airports spike "
            "hard in April — what's bookable in March vs what it looks like in May. "
            "Cover the Thursday night departure trick — flying out Thursday evening and back Tuesday "
            "morning can save $60-100 vs the obvious Friday/Monday flights. "
            "Give the honest truth: Memorial Day weekend is one of the busiest travel weekends of "
            "the year, so airports will be packed. Name which airports handle it better. "
            "Include 3 concrete trip ideas for different budgets: under $150 round-trip, $150-$250, $250+."
        ),
    },
    {
        "slug":        "labor-day-weekend-cheap-flights-us",
        "emoji":       "✈️",
        "title":       "Labor Day Weekend Flights: Best Deals Before Summer Ends",
        "subtitle":    "One last summer trip — and the prices are usually better than you'd think",
        "airport_names": "US Airports",
        "cta_airport": "LAX",
        "market":      "us",
        "best_months": [6, 7, 8],
        "prompt_topic": (
            "cheap flights for Labor Day weekend 2026 from US airports — first Monday of September, "
            "one of the most underrated long weekend travel windows of the year. Lead with prices: "
            "San Francisco from $139 round-trip on Southwest from Los Angeles, "
            "Cancun from $199 round-trip on Spirit from Atlanta, "
            "Seattle from $129 round-trip on Alaska Airlines from Portland, "
            "New York City from $109 round-trip on JetBlue from Boston Logan, "
            "Chicago from $119 round-trip on United from Detroit Metro. "
            "The key insight: Labor Day is often cheaper than Memorial Day or Fourth of July because "
            "families are already back in school-prep mode. Give the specific price difference with "
            "examples on the same routes. "
            "Cover the domestic beach route options that still make sense in early September: "
            "Florida, Carolinas, California coast — warm enough, crowds thinner than August. "
            "Name the airlines most likely to discount Labor Day seats and when to watch for sales. "
            "End with 3 specific trip picks for different traveler types: solo, couple, group."
        ),
    },
    {
        "slug":        "thanksgiving-flights-cheap-us",
        "emoji":       "🦃",
        "title":       "Cheap Thanksgiving Flights 2026: How to Actually Find Them",
        "subtitle":    "Yes, Thanksgiving flights are expensive. Here's how to cut the damage.",
        "airport_names": "US Airports",
        "cta_airport": "ATL",
        "market":      "us",
        "best_months": [9, 10, 11],
        "prompt_topic": (
            "finding affordable Thanksgiving flights in 2026 — the most expensive domestic travel "
            "week of the year. Be direct about the reality: prices are high, but there are still "
            "ways to reduce the damage. Cover specific prices: "
            "New York JFK to LAX from $289 round-trip on JetBlue if booked by October, "
            "Chicago O'Hare to Miami from $219 round-trip on American booked in September, "
            "Dallas to Denver from $149 round-trip on Frontier booked early. "
            "The date strategy matters more than anything else: flying Tuesday instead of Wednesday "
            "before Thanksgiving saves $80-150 on most routes. Flying back Saturday instead of "
            "Sunday saves another $60-100. Give real numbers on this. "
            "Cover the booking window honestly: the best Thanksgiving fares appear in August and "
            "September. By October they're rising. By November you're paying full freight. "
            "Address the Spirit/Frontier question for Thanksgiving: sometimes worth it for short "
            "hops, genuinely not worth it for cross-country when bags and stress are factored in. "
            "Include a section on alternatives to flying — Amtrak and driving for under-4-hour trips."
        ),
    },
    {
        "slug":        "cheap-domestic-flights-us-guide",
        "emoji":       "🗺️",
        "title":       "How to Find Cheap Domestic Flights in the US: What Actually Works",
        "subtitle":    "Forget the myths — here's what genuinely gets you a cheaper ticket",
        "airport_names": "US Airports",
        "cta_airport": "ORD",
        "market":      "us",
        "best_months": None,
        "prompt_topic": (
            "a practical guide to finding cheap domestic flights in the US — what actually works "
            "versus what's just travel myth. Cover: "
            "The Tuesday/Wednesday booking rule — it's mostly dead now but here's when it still "
            "applies. Specific examples of routes where midweek departures save money: "
            "Chicago to Denver on Wednesday from $89 one-way on Frontier vs $149 on Friday. "
            "The 6-week sweet spot for domestic booking vs the 3-month rule for major holidays. "
            "Budget airline reality check: Spirit from $39 one-way Chicago to Orlando sounds great "
            "until you add a carry-on ($79) and a checked bag ($89). Give the real all-in math. "
            "Southwest's no-fee bag policy and when it genuinely beats Spirit on total price. "
            "JetBlue's Mint business class fare sales — sometimes $299 transcontinental when "
            "economy is $189, worth knowing about. "
            "The Google Flights date grid as the single most useful tool for domestic flight hunting. "
            "Positioning flights: flying into a nearby secondary airport (Midway instead of O'Hare, "
            "Oakland instead of SFO, Fort Lauderdale instead of Miami) and the actual savings. "
            "Write from genuine experience of booking hundreds of domestic flights."
        ),
    },
    {
        "slug":        "cheap-flights-from-nyc-us",
        "emoji":       "🗽",
        "title":       "Cheapest Flights from New York: JFK, LaGuardia and Newark Compared",
        "subtitle":    "Three airports, one city, very different prices — here's how to play it",
        "airport_names": "JFK, LaGuardia & Newark",
        "cta_airport": "JFK",
        "market":      "us",
        "best_months": None,
        "prompt_topic": (
            "the cheapest flights you can get from New York's three airports — JFK, LaGuardia, and "
            "Newark — and how to choose between them. Cover: "
            "Which airport is cheapest for which routes: Newark tends to have better transatlantic "
            "deals on United; JetBlue dominates JFK for domestic and Caribbean; LaGuardia is best "
            "for short-haul domestic hops. Give specific route examples with prices. "
            "Domestic deals from NYC right now: "
            "Miami from $89 one-way on Spirit from LaGuardia, "
            "Chicago from $79 one-way on Frontier from Newark, "
            "Los Angeles from $149 one-way on JetBlue from JFK, "
            "New Orleans from $119 one-way on American from JFK, "
            "San Juan Puerto Rico from $139 one-way on JetBlue from JFK. "
            "International deals: London from $399 round-trip on Norse Atlantic from JFK, "
            "Cancun from $219 round-trip on JetBlue from JFK, "
            "Reykjavik from $349 round-trip on Icelandair from JFK. "
            "The practical airport comparison: LaGuardia is closest to Manhattan but has the worst "
            "infrastructure; JFK is the most connected but slowest to get to from midtown; Newark "
            "is underrated if you're coming from Brooklyn or New Jersey. "
            "Include the best times of year to fly from NYC for the lowest fares on each route type."
        ),
    },
    {
        "slug":        "budget-airlines-us-guide",
        "emoji":       "💸",
        "title":       "US Budget Airlines Ranked: Spirit, Frontier, Allegiant — Worth It?",
        "subtitle":    "The honest truth about ultra-low-cost carriers and when to actually use them",
        "airport_names": "US Airports",
        "cta_airport": "ORD",
        "market":      "us",
        "best_months": None,
        "prompt_topic": (
            "an honest, practical ranking of US budget airlines — Spirit, Frontier, Allegiant, and "
            "briefly Sun Country — covering when they're genuinely worth it and when they're not. "
            "Spirit: base fares from $39 one-way sound incredible. Walk through a real example: "
            "Chicago to Orlando, Spirit $49 base + $89 checked bag + $79 carry-on + $25 seat = $242 "
            "vs Southwest at $189 all-in including two free bags. When Spirit actually wins: short "
            "hops under 2 hours where you're traveling carry-on only. "
            "Frontier: similar model to Spirit, slightly better seat pitch. Frontier's GoWild pass "
            "for frequent flyers — explain what it is and whether it's actually useful. "
            "Allegiant: different beast entirely — flies point-to-point from smaller secondary "
            "airports to leisure destinations (Vegas, Orlando, Florida beaches). Fares from $59 "
            "one-way from mid-size cities that Southwest doesn't serve. Works well for specific "
            "trips; terrible for connections. "
            "Southwest: not ultra-budget but worth including — no change fees, two free checked "
            "bags, and the points system is genuinely good. When it beats the ULCC carriers on "
            "real all-in price. "
            "End with a clear framework: use budget airlines when you have no checked bags, the "
            "route is under 3 hours, and you've done the all-in price math."
        ),
    },
    {
        "slug":        "fourth-of-july-flights-us",
        "emoji":       "🎆",
        "title":       "Fourth of July Weekend Flights: Where to Go Without Overpaying",
        "subtitle":    "July 4th travel is brutal on price — unless you know which routes to check",
        "airport_names": "US Airports",
        "cta_airport": "LAX",
        "market":      "us",
        "best_months": [4, 5, 6],
        "prompt_topic": (
            "cheap flights for Fourth of July weekend 2026 from US airports — one of the three "
            "most expensive domestic travel weekends of the year alongside Thanksgiving and "
            "Memorial Day. Be direct: prices are high, but some routes and strategies still work. "
            "Cover specific prices on routes that hold value: "
            "Seattle from $149 round-trip on Alaska from San Francisco, "
            "Denver from $129 round-trip on Frontier from Chicago, "
            "Nashville from $119 round-trip on Southwest from Atlanta, "
            "New York from $99 round-trip on JetBlue from Boston. "
            "The counterintuitive July 4th move: fly TO a big fireworks city rather than away "
            "from it — NYC, DC, Chicago, Boston all have the best Fourth celebrations and "
            "inbound fares are sometimes lower than outbound. "
            "Explain the date math: July 4th falls on a Saturday in 2026, making it a natural "
            "long weekend. Flying Thursday/returning Monday often beats the Friday/Sunday pattern. "
            "Destinations that genuinely work for July 4th weekend: national parks (book way "
            "ahead on accommodation), mountain towns, coastal drives. "
            "Name which airlines are most likely to run Fourth of July sales and when to watch."
        ),
    },
]

# ── Persona prompts ──────────────────────────────────────────────────────────

# UK persona
SYSTEM_PROMPT = """\
You are Jamie, a 32-year-old British travel writer based in North London. You work at a flight deals
website, travel often, and write sharp, useful posts that help ordinary UK people find cheap flights.
Your tone is like a well-travelled friend texting you a tip — direct, specific, occasionally funny.

CRITICAL STYLE RULES — follow every one precisely:
• Keep it SHORT and PUNCHY. Each paragraph is 2-4 sentences max. No padding, no waffle.
• Lead every route or destination mention with a specific price. Example: "Seville from £39 one-way
  on Ryanair from Stansted." Not "Seville can be cheap." Give the number first, context second.
• Name the airline and the UK departure airport for every route you mention.
• Use specific price ranges you'd actually see: "£31-£67 one-way", "returns from £78", "I've seen
  it dip to £44". Never use vague language like "affordable" or "reasonable" without a number.
• British English: colour, favourite, travelling, whilst, mum, mates, sorted, reckon, gutted, skint
• Name real airlines: Ryanair, easyJet, Jet2, TUI, Wizz Air, British Airways, TAP Air Portugal,
  Turkish Airlines, Pegasus. Always pair an airline with the specific UK airport it flies from.
• Be honest about downsides: bag fees, early starts, transfers. Don't pretend everything is perfect.
• DO NOT use: nestled, vibrant, bustling, thriving, picturesque, stunning, iconic, hidden gem,
  treasure, paradise, tapestry, testament, elevate, delve, comprehensive, navigate, realm,
  underscores, it's worth noting, furthermore, in conclusion, in summary, additionally, moreover,
  undoubtedly, certainly, absolutely, it's important to note, I'll be honest
• No bullet points or numbered lists in the body text — write as short punchy paragraphs
• Do not open a section with "I" — vary your sentence openings
• No em dashes (—) or double hyphens (--) — use commas or full stops instead
• No parenthetical asides with brackets — weave it into the sentence
• Never start a sentence with "And" or "But"
• No filler openers: no "Firstly", "Secondly", "Finally", "The bottom line", "The truth is"

Output: Return ONLY valid JSON. No markdown fences, no code blocks, no explanation text."""

USER_PROMPT = """\
Write a travel blog post about: {prompt_topic}

Today is {month_name} {year}.

Write exactly 4 sections. Each section has 3-5 SHORT paragraphs (2-4 sentences each).
Every destination or route you mention must include a specific price, the airline, and the UK airport.
Use HTML only for: <strong>bold text</strong> and <br><br> as paragraph breaks within a section body.

Example of the tone and format to aim for:
"<strong>Porto from £41 one-way on Ryanair from Stansted</strong> is one of the best-value flights
in Europe right now. Three nights there in March typically costs under £300 all-in if you avoid the
weekend. The food alone makes it worth the early start.<br><br>Seville is the other one worth
checking. easyJet fly from Gatwick from around £49 one-way, and mid-March temperatures sit in the
low 20s. My mate went last year for under £280 total including a central Airbnb."

Return ONLY this JSON (no markdown, no code fences, nothing else before or after):
{{
  "title": "SEO title with specific route or price angle, max 70 chars",
  "subtitle": "one punchy line with a specific claim or price, max 90 chars",
  "airport_names": "which UK airports this post covers, concise",
  "meta": "SEO meta description 145-160 chars — mention specific destinations and price ranges",
  "sections": [
    {{"heading": "section heading", "body": "short punchy paragraphs as a single HTML string, separated with <br><br>"}},
    {{"heading": "section heading", "body": "..."}},
    {{"heading": "section heading", "body": "..."}},
    {{"heading": "section heading", "body": "..."}}
  ],
  "cta_airport": "{cta_airport}"
}}

Topic brief: {prompt_topic}"""

# US persona
US_SYSTEM_PROMPT = """\
You are Alex, a 34-year-old American travel writer based in Brooklyn, New York. You work at a flight
deals website, fly constantly, and write sharp, useful posts that help regular Americans find cheap
flights. Your tone is like a well-traveled friend sending you a voice note — direct, a little dry,
genuinely helpful.

CRITICAL STYLE RULES — follow every one precisely:
• Keep it SHORT and PUNCHY. Each paragraph is 2-4 sentences max. No padding, no filler.
• Lead every route or destination mention with a specific price in USD. Example: "Cancun from $189
  round-trip on Spirit from Chicago O'Hare." Give the number first, context second.
• Name the airline and the specific US departure airport for every route you mention.
• Use specific price ranges you'd actually see: "$149-$229 round-trip", "one-ways from $79",
  "I've seen it drop to $109". Never use vague language like "affordable" without a number.
• American English: vacation not holiday, fall not autumn, round-trip not return, one-way,
  carry-on not hand luggage, cell not mobile, airplane not aeroplane, Labor Day, Thanksgiving.
• Name real US airlines: Delta, American Airlines, United, Southwest, JetBlue, Spirit, Frontier,
  Allegiant, Alaska Airlines. Always pair the airline with the specific US airport it flies from.
• Be honest about downsides: Spirit bag fees, connection times, middle seats. No sugarcoating.
• DO NOT use: nestled, vibrant, bustling, thriving, picturesque, stunning, iconic, hidden gem,
  treasure, paradise, tapestry, testament, elevate, delve, comprehensive, navigate, realm,
  underscores, it's worth noting, furthermore, in conclusion, in summary, additionally, moreover,
  undoubtedly, certainly, absolutely, it's important to note, I'll be honest
• No bullet points or numbered lists in the body text — write as short punchy paragraphs
• Do not open a section with "I" — vary your sentence openings
• No em dashes (—) or double hyphens (--) — use commas or full stops instead
• No parenthetical asides with brackets — weave it into the sentence
• Never start a sentence with "And" or "But"
• No filler openers: no "Firstly", "Secondly", "Finally", "The bottom line", "The truth is"

Output: Return ONLY valid JSON. No markdown fences, no code blocks, no explanation text."""

US_USER_PROMPT = """\
Write a travel blog post about: {prompt_topic}

Today is {month_name} {year}.

Write exactly 4 sections. Each section has 3-5 SHORT paragraphs (2-4 sentences each).
Every destination or route you mention must include a specific price in USD, the airline, and the US departure airport.
Use HTML only for: <strong>bold text</strong> and <br><br> as paragraph breaks within a section body.

Example of the tone and format to aim for:
"<strong>Cancun from $189 round-trip on Spirit out of Chicago O'Hare</strong> is the easiest
cheap vacation call you'll make this spring. Four nights there mid-April typically runs under
$700 all-in if you book the hotel now. The beach alone is worth the Spirit carry-on fee.<br><br>
San Jose del Cabo is the other one worth checking. Delta flies from JFK from around $249
round-trip, and March temperatures are in the low 80s. My friend booked last February for
under $850 total including a decent Airbnb."

Return ONLY this JSON (no markdown, no code fences, nothing else before or after):
{{
  "title": "SEO title with specific route or price angle, max 70 chars",
  "subtitle": "one punchy line with a specific claim or price, max 90 chars",
  "airport_names": "which US airports this post covers, concise",
  "meta": "SEO meta description 145-160 chars — mention specific destinations and USD price ranges",
  "sections": [
    {{"heading": "section heading", "body": "short punchy paragraphs as a single HTML string, separated with <br><br>"}},
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

    is_us = topic.get('market', 'uk') == 'us'
    sys_prompt  = US_SYSTEM_PROMPT if is_us else SYSTEM_PROMPT
    user_prompt = US_USER_PROMPT   if is_us else USER_PROMPT

    prompt = user_prompt.format(
        prompt_topic=topic['prompt_topic'],
        month_name=datetime.now().strftime('%B'),
        year=datetime.now().year,
        cta_airport=topic['cta_airport'],
    )

    print(f"[blog_generator] Generating: {topic['slug']} (market={topic.get('market', 'uk')}) …")

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            system=sys_prompt,
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
        "market":        topic.get('market', 'uk'),
        "related":       _build_related(topic['slug']),
        "published_at":  datetime.now().isoformat(),
        "updated_at":    datetime.now().isoformat(),
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

def run_bulk(n: int = 5, force: bool = False) -> int:
    """Generate up to n blog posts in one go. Returns count generated."""
    generated = 0
    published = _published_slugs()
    for topic in TOPIC_PIPELINE:
        if generated >= n:
            break
        if topic['slug'] in published and not force:
            continue
        post = generate_post(topic)
        if post:
            generated += 1
            published[topic['slug']] = time.time()
            with open(LOCK_FILE, 'w') as f:
                f.write(datetime.now().isoformat())
    print(f"[blog_generator] Bulk run complete: {generated} post(s) generated.")
    return generated


def _cli():
    parser = argparse.ArgumentParser(description="Generate weekly blog posts via Claude API")
    parser.add_argument('--force',   action='store_true', help='Regenerate even if recently published')
    parser.add_argument('--list',    action='store_true', help='List all topics and their status')
    parser.add_argument('--dry-run', action='store_true', help='Print JSON without saving')
    parser.add_argument('--topic',   metavar='SLUG',      help='Generate a specific topic by slug')
    parser.add_argument('--bulk',    metavar='N', type=int, default=0,
                        help='Generate up to N unpublished posts in one run')
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

    if args.bulk:
        run_bulk(n=args.bulk, force=args.force)
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
