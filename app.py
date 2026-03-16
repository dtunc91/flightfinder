from flask import Flask, render_template, request, jsonify, send_from_directory, abort
from datetime import datetime, timedelta
from amadeus import Client
import requests
import os
import json
import csv
import re
import time
import unicodedata
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials as SACredentials

load_dotenv()

app = Flask(__name__, template_folder='templates', static_folder='static')

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
SUBSCRIBERS_FILE = os.path.join(DATA_DIR, 'subscribers.csv')

_sheets_client = None

def _get_sheet():
    """Return the subscribers Google Sheet worksheet, caching the client."""
    global _sheets_client
    creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
    sheet_id   = os.environ.get('GOOGLE_SHEETS_SPREADSHEET_ID')
    if not creds_json or not sheet_id:
        return None
    if _sheets_client is None:
        info = json.loads(creds_json)
        scopes = ['https://www.googleapis.com/auth/spreadsheets']
        creds = SACredentials.from_service_account_info(info, scopes=scopes)
        _sheets_client = gspread.authorize(creds)
    spreadsheet = _sheets_client.open_by_key(sheet_id)
    try:
        return spreadsheet.worksheet('subscribers')
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title='subscribers', rows=1, cols=4)
        ws.append_row(['email', 'airport_code', 'airport_name', 'signed_up_at'])
        return ws

# Major airports for SEO landing pages + sitemap
SEO_AIRPORTS = [
    # UK
    'LHR','LGW','MAN','EDI','BHX','BRS','LTN','STN','GLA','NCL',
    'LPL','LBA','SOU','ABZ','BFS','CWL',
    # Popular international
    'CDG','AMS','FRA','MAD','BCN','FCO','DXB','JFK','LAX','BKK',
    'SIN','DUB','LIS','ATH','PRG','VIE','CPH','OSL','ARN','HEL',
    'WAW','BUD','ZRH','GVA','MXP','FCO','IST','NRT','SYD','YYZ',
]

# ---- Live deals feed ----
_live_deals_cache = {}  # {country_code: {"data": [], "fetched_at": 0}}
LIVE_DEALS_TTL = 3600  # re-fetch every hour

# Kept for backward-compat (used as GB fallback)
LIVE_DEAL_ORIGINS = [
    ("LHR", "London"),
    ("MAN", "Manchester"),
    ("EDI", "Edinburgh"),
    ("BRS", "Bristol"),
    ("BHX", "Birmingham"),
    ("GLA", "Glasgow"),
    ("LBA", "Leeds"),
    ("NCL", "Newcastle"),
]

# ---- Country → airports for geo-based live deals ----
COUNTRY_AIRPORTS = {
    'GB': LIVE_DEAL_ORIGINS,
    'ES': [("MAD","Madrid"),("BCN","Barcelona"),("AGP","Malaga"),("PMI","Palma"),("ALC","Alicante"),("VLC","Valencia"),("SVQ","Seville"),("BIO","Bilbao")],
    'DE': [("FRA","Frankfurt"),("MUC","Munich"),("BER","Berlin"),("HAM","Hamburg"),("DUS","Dusseldorf"),("STR","Stuttgart"),("CGN","Cologne"),("NUE","Nuremberg")],
    'FR': [("CDG","Paris"),("ORY","Paris Orly"),("NCE","Nice"),("LYS","Lyon"),("MRS","Marseille"),("TLS","Toulouse"),("BOD","Bordeaux"),("NTE","Nantes")],
    'IT': [("FCO","Rome"),("MXP","Milan"),("NAP","Naples"),("VCE","Venice"),("BLQ","Bologna"),("PSA","Pisa"),("CTA","Catania"),("PMO","Palermo")],
    'NL': [("AMS","Amsterdam"),("EIN","Eindhoven"),("RTM","Rotterdam")],
    'PT': [("LIS","Lisbon"),("OPO","Porto"),("FAO","Faro"),("FNC","Funchal")],
    'IE': [("DUB","Dublin"),("ORK","Cork"),("SNN","Shannon")],
    'PL': [("WAW","Warsaw"),("KRK","Krakow"),("GDN","Gdansk"),("WRO","Wroclaw"),("POZ","Poznan"),("KTW","Katowice")],
    'US': [("JFK","New York"),("LAX","Los Angeles"),("ORD","Chicago"),("DFW","Dallas"),("ATL","Atlanta"),("DEN","Denver"),("SFO","San Francisco"),("MIA","Miami")],
    'AU': [("SYD","Sydney"),("MEL","Melbourne"),("BNE","Brisbane"),("PER","Perth"),("ADL","Adelaide")],
    'CA': [("YYZ","Toronto"),("YVR","Vancouver"),("YUL","Montreal"),("YYC","Calgary")],
    'AE': [("DXB","Dubai"),("AUH","Abu Dhabi"),("SHJ","Sharjah")],
    'GR': [("ATH","Athens"),("SKG","Thessaloniki"),("HER","Heraklion"),("RHO","Rhodes")],
    'TR': [("IST","Istanbul"),("SAW","Istanbul Sabiha"),("AYT","Antalya"),("ESB","Ankara")],
    'SE': [("ARN","Stockholm"),("GOT","Gothenburg"),("MMX","Malmo")],
    'NO': [("OSL","Oslo"),("BGO","Bergen"),("TRD","Trondheim")],
    'DK': [("CPH","Copenhagen"),("AAL","Aalborg"),("BLL","Billund")],
    'FI': [("HEL","Helsinki"),("TMP","Tampere"),("TKU","Turku")],
    'BE': [("BRU","Brussels"),("CRL","Charleroi"),("ANR","Antwerp")],
    'CH': [("ZRH","Zurich"),("GVA","Geneva"),("BSL","Basel")],
    'AT': [("VIE","Vienna"),("GRZ","Graz"),("SZG","Salzburg")],
    'CZ': [("PRG","Prague"),("BRQ","Brno"),("OSR","Ostrava")],
    'HU': [("BUD","Budapest"),("DEB","Debrecen")],
    'RO': [("OTP","Bucharest"),("CLJ","Cluj"),("TSR","Timisoara")],
    'HR': [("ZAG","Zagreb"),("SPU","Split"),("DBV","Dubrovnik")],
    'BG': [("SOF","Sofia"),("VAR","Varna"),("BOJ","Burgas")],
    'RS': [("BEG","Belgrade"),("INI","Nis")],
    'SK': [("BTS","Bratislava"),("KSC","Kosice")],
    'JP': [("NRT","Tokyo Narita"),("HND","Tokyo Haneda"),("KIX","Osaka"),("NGO","Nagoya"),("CTS","Sapporo")],
    'CN': [("PEK","Beijing"),("PVG","Shanghai"),("CAN","Guangzhou"),("CTU","Chengdu"),("SZX","Shenzhen")],
    'IN': [("DEL","Delhi"),("BOM","Mumbai"),("MAA","Chennai"),("BLR","Bangalore"),("HYD","Hyderabad"),("CCU","Kolkata")],
    'SG': [("SIN","Singapore")],
    'TH': [("BKK","Bangkok"),("DMK","Bangkok Don Mueang"),("HKT","Phuket"),("CNX","Chiang Mai")],
    'MY': [("KUL","Kuala Lumpur"),("PEN","Penang"),("BKI","Kota Kinabalu")],
    'ID': [("CGK","Jakarta"),("DPS","Bali"),("SUB","Surabaya")],
    'MX': [("MEX","Mexico City"),("CUN","Cancun"),("GDL","Guadalajara"),("MTY","Monterrey")],
    'BR': [("GRU","Sao Paulo"),("GIG","Rio de Janeiro"),("BSB","Brasilia"),("SSA","Salvador")],
    'AR': [("EZE","Buenos Aires"),("COR","Cordoba"),("MDZ","Mendoza")],
    'ZA': [("JNB","Johannesburg"),("CPT","Cape Town"),("DUR","Durban")],
    'EG': [("CAI","Cairo"),("HRG","Hurghada"),("SSH","Sharm el-Sheikh")],
    'MA': [("CMN","Casablanca"),("RAK","Marrakech"),("FEZ","Fez"),("TNG","Tangier")],
    'IL': [("TLV","Tel Aviv")],
    'SA': [("RUH","Riyadh"),("JED","Jeddah"),("DMM","Dammam")],
    'QA': [("DOH","Doha")],
    'NZ': [("AKL","Auckland"),("WLG","Wellington"),("CHC","Christchurch"),("ZQN","Queenstown")],
    'IS': [("KEF","Reykjavik")],
    'CY': [("LCA","Larnaca"),("PFO","Paphos")],
    'MT': [("MLA","Malta")],
    'LU': [("LUX","Luxembourg")],
    'AL': [("TIA","Tirana")],
    'ME': [("TGD","Podgorica"),("TIV","Tivat")],
    'BA': [("SJJ","Sarajevo")],
    'MK': [("SKP","Skopje")],
}

# ---- Country → (currency_code, symbol) ----
COUNTRY_CURRENCY = {
    'GB': ('gbp', '£'),
    'US': ('usd', '$'),
    'AU': ('aud', 'A$'),
    'CA': ('cad', 'C$'),
    'AE': ('aed', 'AED '),
    'JP': ('jpy', '¥'),
    'IN': ('inr', '₹'),
    'SG': ('sgd', 'S$'),
    'HK': ('hkd', 'HK$'),
    'NZ': ('nzd', 'NZ$'),
    'CH': ('chf', 'CHF '),
    'NO': ('nok', 'kr '),
    'SE': ('sek', 'kr '),
    'DK': ('dkk', 'kr '),
    'IS': ('isk', 'kr '),
    'CN': ('cny', '¥'),
    'BR': ('brl', 'R$'),
    'MX': ('mxn', 'MX$'),
    'ZA': ('zar', 'R '),
    'TR': ('try', '₺'),
    'IL': ('ils', '₪'),
    'SA': ('sar', 'SAR '),
    'QA': ('qar', 'QAR '),
    'EG': ('egp', 'E£'),
    'MA': ('mad', 'MAD '),
    'TH': ('thb', '฿'),
    'ID': ('idr', 'Rp '),
    'MY': ('myr', 'RM '),
    'AR': ('ars', 'ARS '),
    'PL': ('pln', 'zł'),
    'CZ': ('czk', 'Kč'),
    'HU': ('huf', 'Ft'),
    'RO': ('ron', 'lei'),
    'BG': ('bgn', 'лв'),
    'RS': ('rsd', 'din'),
}
# Eurozone
for _c in ['DE','FR','IT','ES','NL','BE','AT','PT','FI','GR','IE','LU','SK','SI','EE','LV','LT','MT','CY','HR']:
    COUNTRY_CURRENCY.setdefault(_c, ('eur', '€'))

# ---- Geo-IP cache (avoids repeat calls to ipapi.co) ----
_geo_cache = {}  # {ip: {"country": "GB", "fetched_at": 0}}
GEO_CACHE_TTL = 3600

# ---- Blog post content ----
BLOG_POSTS = {
    'cheapest-flights-from-london': {
        'emoji': '🏙️',
        'title': 'Cheapest Places to Fly from London',
        'subtitle': "Where to go when you just need to get away — without breaking the bank",
        'airport_names': 'Heathrow, Gatwick, Stansted, Luton & London City',
        'slug': 'cheapest-flights-from-london',
        'meta': 'The cheapest places to fly from London airports — Amsterdam, Dublin, Barcelona and more. Tips on when to book and which airport to use.',
        'sections': [
            {
                'heading': 'Why London is one of the best cities for cheap flights',
                'body': (
                    "London has five airports served by dozens of budget and full-service airlines, which means genuine competition on almost every route. "
                    "Ryanair dominates Stansted, easyJet is strong at Gatwick and Luton, and British Airways adds competition on popular routes like Dublin and Amsterdam. That rivalry keeps prices low. "
                    "The key is knowing which airport serves your destination — Stansted is 45 minutes from central London by train, Gatwick 30 minutes. Factor that in when comparing prices."
                ),
            },
            {
                'heading': 'The consistently cheapest routes from London',
                'body': (
                    "<strong>Dublin (DUB)</strong> — One of the most frequently discounted routes. Ryanair flies from Stansted multiple times a day, easyJet from Gatwick. Prices can drop below £20 one-way on off-peak dates.<br><br>"
                    "<strong>Amsterdam (AMS)</strong> — 90 minutes and multiple airlines from several London airports. Fares typically start from around £30–50 one-way.<br><br>"
                    "<strong>Barcelona (BCN)</strong> — Popular year-round. Outside July and August, one-way fares often start around £40–60 with Vueling, easyJet or Ryanair.<br><br>"
                    "<strong>Lisbon &amp; Porto (LIS / OPO)</strong> — Portugal has become a go-to cheap-flight destination. Ryanair and easyJet compete heavily; fares can start from around £35–55 one-way.<br><br>"
                    "<strong>Kraków &amp; Warsaw (KRK / WAW)</strong> — Poland is exceptional value from London. Fares to Kraków from Stansted often start under £30 one-way.<br><br>"
                    "<strong>Alicante &amp; Malaga (ALC / AGP)</strong> — The Spanish costas are served by multiple budget carriers, with fares from around £40–70 one-way outside peak summer."
                ),
            },
            {
                'heading': 'Tips for finding the cheapest fares from London',
                'body': (
                    "<strong>Use Stansted for the lowest base fares.</strong> Ryanair's UK hub is Stansted — checking STN first often surfaces the cheapest options.<br><br>"
                    "<strong>Book 4–8 weeks ahead for short breaks.</strong> For European city breaks, the sweet spot is typically 6–8 weeks out.<br><br>"
                    "<strong>Avoid school holidays.</strong> Prices spike sharply in July, August and October half-term. Mid-September to early November is often the best value window.<br><br>"
                    "<strong>Be flexible on your return date.</strong> Shifting your return by one day — say, Tuesday instead of Sunday — can cut the return leg cost significantly.<br><br>"
                    "<strong>Check all five London airports.</strong> Heathrow has the most routes but is often pricier. Stansted and Luton typically have the cheapest budget carrier fares."
                ),
            },
        ],
        'cta_airport': 'LHR',
        'related': [
            ('cheapest-flights-from-manchester', 'Cheapest flights from Manchester'),
            ('cheapest-flights-from-bristol', 'Cheapest flights from Bristol'),
            ('cheapest-flights-from-edinburgh', 'Cheapest flights from Edinburgh'),
        ],
    },
    'cheapest-flights-from-manchester': {
        'emoji': '✈️',
        'title': 'Cheapest Places to Fly from Manchester',
        'subtitle': "Great routes, strong competition, and no need to travel south for a deal",
        'airport_names': 'Manchester Airport (MAN)',
        'slug': 'cheapest-flights-from-manchester',
        'meta': 'Find the cheapest flights from Manchester Airport — Dublin, Amsterdam, Faro, Barcelona and more with tips on when and how to book.',
        'sections': [
            {
                'heading': "Manchester: the north's best-connected airport",
                'body': (
                    "Manchester Airport is the UK's third busiest, with direct long-haul routes many regional airports can't match. "
                    "For budget short-break hunters, its strength is the breadth of European routes served by Ryanair, easyJet, Jet2 and Wizz Air. "
                    "Competition between those four carriers on Spanish, Portuguese and Central European routes means you can regularly find competitive fares without going anywhere near Heathrow."
                ),
            },
            {
                'heading': 'Best value routes from Manchester',
                'body': (
                    "<strong>Dublin (DUB)</strong> — Ryanair and Aer Lingus both compete; one-way fares frequently start from under £30.<br><br>"
                    "<strong>Amsterdam (AMS)</strong> — KLM flies direct alongside budget carriers. Fares typically from around £40–70 one-way.<br><br>"
                    "<strong>Faro (FAO)</strong> — Gateway to the Algarve. Jet2 and easyJet compete heavily; fares from around £50–80 one-way outside peak season.<br><br>"
                    "<strong>Alicante &amp; Malaga (ALC / AGP)</strong> — Two of the most popular sun routes from Manchester. Jet2 and Ryanair compete, with fares often from around £45–80 one-way.<br><br>"
                    "<strong>Palma, Mallorca (PMI)</strong> — A big Jet2 route. Shoulder season (April, May, September, October) yields the best deals.<br><br>"
                    "<strong>Kraków (KRK)</strong> — Ryanair and Wizz Air both fly it; fares can sometimes drop below £25 one-way.<br><br>"
                    "<strong>Tenerife (TFS)</strong> — Year-round sun with multiple airlines; fares from around £80–120 one-way, better in winter."
                ),
            },
            {
                'heading': 'How to get the best deals from Manchester',
                'body': (
                    "<strong>Compare Jet2 and budget carriers carefully.</strong> Jet2 often includes checked luggage, which can make them genuinely better value once you add Ryanair's bag fees.<br><br>"
                    "<strong>Midweek departures are cheaper.</strong> Tuesday and Wednesday departures consistently come in lower than Friday/Sunday from Manchester.<br><br>"
                    "<strong>Shoulder-season sun is exceptional value.</strong> May, early June, and September/October offer good weather on Spanish and Portuguese routes with significantly lower fares than July/August.<br><br>"
                    "<strong>Sign up for price alerts.</strong> Manchester routes can drop sharply in sale windows — having alerts set means you catch these quickly."
                ),
            },
        ],
        'cta_airport': 'MAN',
        'related': [
            ('cheapest-flights-from-london', 'Cheapest flights from London'),
            ('cheapest-flights-from-edinburgh', 'Cheapest flights from Edinburgh'),
            ('cheapest-flights-from-bristol', 'Cheapest flights from Bristol'),
        ],
    },
    'cheapest-flights-from-edinburgh': {
        'emoji': '🏴󠁧󠁢󠁳󠁣󠁴󠁿',
        'title': 'Cheapest Places to Fly from Edinburgh',
        'subtitle': "Scotland's busiest airport punches well above its weight for cheap European routes",
        'airport_names': 'Edinburgh Airport (EDI)',
        'slug': 'cheapest-flights-from-edinburgh',
        'meta': 'Cheap flights from Edinburgh Airport — Amsterdam, Dublin, Barcelona, Reykjavik and beyond. Find out which routes offer the best value.',
        'sections': [
            {
                'heading': "Edinburgh Airport: smaller than you'd think, better than you'd expect",
                'body': (
                    "Edinburgh Airport is Scotland's busiest, and while it doesn't have the breadth of London or Manchester, it has solid European coverage — and increasingly competitive fares as Ryanair, easyJet and Wizz Air have all expanded their Scottish operations. "
                    "For Scots and visitors, it often means you don't need to take a domestic flight to London just to find a cheap deal to Europe."
                ),
            },
            {
                'heading': 'Best value destinations from Edinburgh',
                'body': (
                    "<strong>Amsterdam (AMS)</strong> — One of the most popular routes from Edinburgh, well-served by multiple carriers. Fares often start from around £45–70 one-way.<br><br>"
                    "<strong>Dublin (DUB)</strong> — Ryanair and Aer Lingus both fly this route. Prices frequently start from around £30–50 one-way.<br><br>"
                    "<strong>Barcelona (BCN)</strong> — easyJet and Ryanair fly direct; fares from around £55–90 one-way outside peak summer.<br><br>"
                    "<strong>Alicante &amp; Malaga (ALC / AGP)</strong> — Sun routes that have grown in popularity. Fares typically start from around £60–90 one-way.<br><br>"
                    "<strong>Faro (FAO)</strong> — The Algarve direct from Scotland, with fares often starting from around £65–95 one-way.<br><br>"
                    "<strong>Reykjavik (KEF)</strong> — Icelandair flies this route and it's a genuinely unique destination. Fares from around £80–120 one-way — well worth it.<br><br>"
                    "<strong>Paris (CDG/ORY)</strong> — A two-hour flight with easyJet, great for long weekends; fares often from around £50–80 one-way."
                ),
            },
            {
                'heading': 'Tips for flying cheap from Edinburgh',
                'body': (
                    "<strong>Book early for summer.</strong> Edinburgh is a popular inbound tourism destination, which pushes summer outbound fares up too. For July/August travel, booking 3–4 months ahead is sensible.<br><br>"
                    "<strong>Autumn and winter are great for European city breaks.</strong> October through March sees fewer tourists and lower fares on most routes — ideal for Amsterdam or Barcelona.<br><br>"
                    "<strong>Check Glasgow (GLA) too.</strong> Glasgow Airport is 45 minutes away and sometimes has better fares on certain routes — worth a quick comparison.<br><br>"
                    "<strong>Shift your date by a day or two.</strong> On Edinburgh routes, the day of the week can make a meaningful price difference. Use the search tool to compare across dates."
                ),
            },
        ],
        'cta_airport': 'EDI',
        'related': [
            ('cheapest-flights-from-london', 'Cheapest flights from London'),
            ('cheapest-flights-from-manchester', 'Cheapest flights from Manchester'),
            ('cheapest-flights-from-bristol', 'Cheapest flights from Bristol'),
        ],
    },
    'cheapest-flights-from-bristol': {
        'emoji': '🌞',
        'title': 'Cheapest Places to Fly from Bristol',
        'subtitle': "The southwest's gateway to Europe — with more cheap routes than you might expect",
        'airport_names': 'Bristol Airport (BRS)',
        'slug': 'cheapest-flights-from-bristol',
        'meta': 'Cheap flights from Bristol Airport — Amsterdam, Dublin, Faro, Barcelona and beyond. Tips on the best routes and how to find deals.',
        'sections': [
            {
                'heading': "Bristol Airport: the southwest's best gateway",
                'body': (
                    "Bristol Airport serves the southwest of England and South Wales — and it punches above its size. "
                    "easyJet has a significant base here, and Ryanair covers a growing number of routes, meaning genuine competition on popular European destinations. "
                    "For anyone in Bristol, Bath, Cardiff, Gloucester or Somerset, it's almost always worth checking BRS before travelling to Heathrow or Gatwick."
                ),
            },
            {
                'heading': 'The cheapest and most popular routes from Bristol',
                'body': (
                    "<strong>Amsterdam (AMS)</strong> — easyJet flies direct; fares typically from around £50–75 one-way.<br><br>"
                    "<strong>Dublin (DUB)</strong> — Ryanair covers this well; fares often from around £30–55 one-way.<br><br>"
                    "<strong>Faro (FAO)</strong> — The Algarve gateway, popular from Bristol. easyJet flies it seasonally; fares from around £60–90 one-way.<br><br>"
                    "<strong>Barcelona (BCN)</strong> — easyJet and Vueling both serve this route. Great for a long weekend; fares from around £55–85 one-way outside peak summer.<br><br>"
                    "<strong>Palma, Mallorca (PMI)</strong> — Very popular in summer. Shoulder season fares (April/May and September/October) are considerably cheaper than July/August.<br><br>"
                    "<strong>Malaga &amp; Alicante (AGP / ALC)</strong> — Solid summer sun routes; fares from around £60–95 one-way in shoulder season.<br><br>"
                    "<strong>Prague (PRG)</strong> — A brilliant city break and often excellent value from Bristol; fares from around £45–70 one-way.<br><br>"
                    "<strong>Tenerife (TFS)</strong> — Year-round from Bristol. One of the best winter sun options; fares from around £85–120 one-way."
                ),
            },
            {
                'heading': 'How to find the best deals from Bristol',
                'body': (
                    "<strong>easyJet sales from Bristol are frequent.</strong> Bristol features heavily in easyJet promotional fares — worth signing up for their alerts.<br><br>"
                    "<strong>Travel mid-week for lower prices.</strong> Tuesday and Wednesday departures are consistently cheaper on most Bristol routes.<br><br>"
                    "<strong>Book October half-term early.</strong> Bristol prices spike during school holidays. Aim for the last week of October if you need that window, which can be cheaper.<br><br>"
                    "<strong>Compare with Cardiff (CWL).</strong> Cardiff Airport is around 40 minutes away and occasionally has better fares on certain routes.<br><br>"
                    "<strong>Early morning flights are cheapest.</strong> Bristol's first departures of the day are typically priced lower than midday or evening slots."
                ),
            },
        ],
        'cta_airport': 'BRS',
        'related': [
            ('cheapest-flights-from-london', 'Cheapest flights from London'),
            ('cheapest-flights-from-manchester', 'Cheapest flights from Manchester'),
            ('cheapest-flights-from-edinburgh', 'Cheapest flights from Edinburgh'),
        ],
    },
}

# ---- Secrets / API keys ----
API_TOKEN = os.getenv('API_TOKEN')  # Travelpayouts API token
AMADEUS_CLIENT_ID = os.getenv('AMADEUS_CLIENT_ID')
AMADEUS_CLIENT_SECRET = os.getenv('AMADEUS_CLIENT_SECRET')

amadeus = Client(client_id=AMADEUS_CLIENT_ID, client_secret=AMADEUS_CLIENT_SECRET)

# ---- Template globals ----
@app.context_processor
def inject_now():
    return {"current_year": datetime.utcnow().year}

# ---- Built-in minimal fallback (last line of defence) ----
DEFAULT_AIRPORTS = [
    {"code":"LHR","label":"Heathrow","city":"London"},
    {"code":"LGW","label":"Gatwick","city":"London"},
    {"code":"STN","label":"Stansted","city":"London"},
    {"code":"LTN","label":"Luton","city":"London"},
    {"code":"LCY","label":"City","city":"London"},
    {"code":"MAN","label":"Manchester","city":"Manchester"},
    {"code":"EDI","label":"Edinburgh","city":"Edinburgh"},
    {"code":"DUB","label":"Dublin","city":"Dublin"},
    {"code":"CDG","label":"Charles de Gaulle","city":"Paris"},
    {"code":"AMS","label":"Schiphol","city":"Amsterdam"},
    {"code":"BCN","label":"Barcelona","city":"Barcelona"},
    {"code":"MAD","label":"Madrid","city":"Madrid"}
]

# ---- OurAirports (worldwide, typed dataset — downloaded weekly) ----
OURAIRPORTS_URL       = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OURAIRPORTS_CACHE_FILE = os.path.join(DATA_DIR, 'ourairports_cache.csv')
OURAIRPORTS_CACHE_TTL  = 86400 * 7   # re-download once a week
_OA_CACHE = {"by_code": {}, "by_country": {}, "all": [], "loaded": False, "fetched_at": 0}

def _load_ourairports() -> dict:
    """Download OurAirports CSV once a week, cache to disk, parse into memory.

    Returns dict with:
      by_code    – {IATA: airport_dict}   (all airports with IATA codes)
      by_country – {CC: [airport_dict, ...]}  (large+medium only, large first)
      all        – flat list for full-text search
    """
    now = time.time()
    if _OA_CACHE["loaded"] and now - _OA_CACHE["fetched_at"] < OURAIRPORTS_CACHE_TTL:
        return _OA_CACHE

    # Download if cache file is missing or stale
    needs_download = (
        not os.path.exists(OURAIRPORTS_CACHE_FILE) or
        now - os.path.getmtime(OURAIRPORTS_CACHE_FILE) >= OURAIRPORTS_CACHE_TTL
    )
    if needs_download:
        try:
            resp = requests.get(OURAIRPORTS_URL, timeout=20)
            if resp.status_code == 200:
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(OURAIRPORTS_CACHE_FILE, 'w', encoding='utf-8') as fh:
                    fh.write(resp.text)
        except Exception:
            pass  # fall through and try to read whatever is on disk

    if not os.path.exists(OURAIRPORTS_CACHE_FILE):
        _OA_CACHE["loaded"] = True
        _OA_CACHE["fetched_at"] = now
        return _OA_CACHE

    try:
        by_code, by_country, all_airports = {}, {}, []
        TYPE_RANK = {'large_airport': 0, 'medium_airport': 1}
        with open(OURAIRPORTS_CACHE_FILE, 'r', encoding='utf-8') as fh:
            for row in csv.DictReader(fh):
                iata    = (row.get('iata_code') or '').strip().upper()
                if not iata or len(iata) != 3:
                    continue
                atype   = (row.get('type') or '').strip()
                name    = (row.get('name') or '').strip()
                city    = (row.get('municipality') or '').strip()
                country = (row.get('iso_country') or '').strip().upper()
                entry = {
                    "code": iata, "label": name, "city": city,
                    "country": country, "type": atype,
                    "name": _display_name(name, iata),
                }
                by_code[iata] = entry
                all_airports.append(entry)
                if atype in ('large_airport', 'medium_airport') and country:
                    by_country.setdefault(country, []).append(entry)

        for lst in by_country.values():
            lst.sort(key=lambda x: (TYPE_RANK.get(x['type'], 2), x['label']))

        _OA_CACHE.update({
            "by_code": by_code, "by_country": by_country,
            "all": all_airports, "loaded": True, "fetched_at": now,
        })
    except Exception:
        _OA_CACHE["loaded"] = True
        _OA_CACHE["fetched_at"] = now

    return _OA_CACHE

# ---- Local airports cache (offline coverage) ----
_AIRPORTS_CACHE = {"data": [], "mtime": None}

def _normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.lower().strip()

def _load_local_airports() -> list:
    """
    Load static/airports.json once and cache; hot-reload if file changes.
    Expected format: [{ "code": "LHR", "label": "Heathrow", "city": "London" }, ...]
    Your file may omit city and may include (CODE) inside label, which is fine.
    """
    path = os.path.join(app.static_folder, 'airports.json')
    if not os.path.exists(path):
        _AIRPORTS_CACHE["data"] = []
        _AIRPORTS_CACHE["mtime"] = None
        return []

    mtime = os.path.getmtime(path)
    if _AIRPORTS_CACHE["mtime"] == mtime and _AIRPORTS_CACHE["data"]:
        return _AIRPORTS_CACHE["data"]

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # sanitize + unique by code
        seen, clean = set(), []
        for a in data if isinstance(data, list) else []:
            code = (a.get("code") or "").upper().strip()
            if code and code not in seen:
                raw_label = (a.get("label") or code).strip()
                m = re.search(r',\s*([A-Z]{2})\s*$', raw_label)
                clean.append({
                    "code": code,
                    "label": raw_label,
                    "city": (a.get("city") or "").strip(),
                    "country": m.group(1) if m else (a.get("country") or "")
                })
                seen.add(code)

        _AIRPORTS_CACHE["data"] = clean
        _AIRPORTS_CACHE["mtime"] = mtime
        return clean
    except Exception:
        _AIRPORTS_CACHE["data"] = []
        _AIRPORTS_CACHE["mtime"] = None
        return []

def _search_local_airports(q: str, pool: list) -> list:
    """Smart search: code prefix > name/city prefix > substring."""
    qs = _normalize(q)
    if not qs:
        return []
    code_pref, name_pref, substr = [], [], []
    for a in pool:
        code = (a.get("code") or "")
        label = a.get("label") or ""
        city = a.get("city") or ""
        blob = f"{_normalize(label)} {_normalize(city)}".strip()

        if code.lower().startswith(qs):
            code_pref.append(a)
        elif blob.startswith(qs):
            name_pref.append(a)
        elif qs in blob:
            substr.append(a)

    # merge unique keeping order
    seen, out = set(), []
    for bucket in (code_pref, name_pref, substr):
        for a in bucket:
            c = a["code"]
            if c not in seen:
                out.append(a)
                seen.add(c)

    return out[:25]

def _get_airport_index() -> dict:
    """Build {IATA_CODE: airport_dict} for O(1) lookups. OurAirports-first."""
    oa = _load_ourairports()
    if oa["by_code"]:
        return oa["by_code"]
    return {a['code']: a for a in _load_local_airports()}

def _display_name(label: str, code: str) -> str:
    """
    Returns a nice display string. Avoids duplicating (CODE) if label already contains it.
    Examples:
      label="Utirik Airport (UTK), MH", code="UTK" -> "Utirik Airport (UTK), MH"
      label="Heathrow", code="LHR" -> "Heathrow (LHR)"
    """
    label = (label or "").strip()
    code = (code or "").strip().upper()
    if not label and not code:
        return ""
    if code and f"({code})" in label:
        return label
    return f"{label} ({code})" if (label and code) else (label or code)

def resolve_label_for_code(code: str) -> str:
    """
    Try to find 'Name (CODE)' using local file first, then Amadeus, else CODE.
    """
    code = (code or "").upper().strip()
    if not code:
        return ""

    for a in _load_local_airports():
        if a["code"] == code:
            return _display_name(a.get("label") or code, code)

    try:
        resp = amadeus.reference_data.locations.get(keyword=code, subType="AIRPORT")
        if resp.data:
            name = resp.data[0].get("name", code)
            return _display_name(name, code)
    except Exception:
        pass

    return code

def load_airport_names(query: str) -> dict:
    """
    Query Amadeus for airports by keyword; { IATA: 'Airport Name' }
    """
    try:
        resp = amadeus.reference_data.locations.get(keyword=query, subType="AIRPORT")
        airports = resp.data if resp.data else []
        return {a["iataCode"]: a.get("name", a["iataCode"]) for a in airports}
    except Exception:
        return {}

# ---- Main search page ----
@app.route('/', methods=['GET', 'POST'])
def index():
    flights = []
    origin_label = ""
    date = ""

    form_data = {
        'trip_type': request.form.get('trip_type', 'oneway'),
        'passengers': request.form.get('passengers', '1'),
        'departure_date': request.form.get('departure_date', ''),
        'return_date': request.form.get('return_date', ''),
        'origin': request.form.get('origin', ''),
        'origin_code': request.form.get('origin_code', ''),
        'currency': request.form.get('currency', 'gbp'),
        'currency_symbol': request.form.get('currency_symbol', '£'),
    }

    if request.method == 'POST':
        origin_raw = (request.form.get('origin') or '').strip()
        origin_code_hidden = (request.form.get('origin_code') or '').strip().upper()
        departure_date = request.form.get('departure_date')
        return_date = request.form.get('return_date')
        trip_type = request.form.get('trip_type', 'oneway')
        passengers = request.form.get('passengers', '1')

        form_data.update({
            'trip_type': trip_type,
            'passengers': passengers,
            'departure_date': departure_date,
            'return_date': return_date,
            'origin': origin_raw,
            'origin_code': origin_code_hidden
        })

        # Determine origin_code + human label (prefer hidden code; frontend now ensures it's set)
        origin_code = ''
        if origin_code_hidden and len(origin_code_hidden) == 3 and origin_code_hidden.isalpha():
            origin_code = origin_code_hidden
            origin_label = resolve_label_for_code(origin_code)
        else:
            # fallback behavior (still here as safety)
            if '(' in origin_raw and ')' in origin_raw:
                guessed = origin_raw.split('(')[-1].replace(')', '').strip().upper()
                if len(guessed) == 3 and guessed.isalpha():
                    origin_code = guessed
                    origin_label = origin_raw
            elif len(origin_raw) == 3 and origin_raw.isalpha():
                origin_code = origin_raw.upper()
                origin_label = resolve_label_for_code(origin_code)
            else:
                # last-resort: try Amadeus keyword
                airport_names = load_airport_names(origin_raw)
                if airport_names:
                    origin_code = list(airport_names.keys())[0]
                    origin_label = _display_name(airport_names[origin_code], origin_code)
                else:
                    origin_code = 'LON'
                    origin_label = 'London (LON)'

        date = departure_date

        # Travelpayouts fetch — get a large batch then split into domestic/international
        params = {'origin': origin_code, 'currency': form_data['currency'], 'token': API_TOKEN, 'limit': 100}
        try:
            r = requests.get("https://api.travelpayouts.com/v2/prices/latest", params=params, timeout=15)
            if r.status_code == 200:
                data = r.json().get('data', [])
                airport_index = _get_airport_index()
                origin_info = airport_index.get(origin_code, {})
                origin_country = origin_info.get('country', '')

                domestic_flights, international_flights = [], []

                for flight in data:
                    dest_code = flight.get('destination', 'N/A')
                    price = flight.get('value', 'N/A')
                    num_stops = flight.get('number_of_changes', 0)

                    dest_info = airport_index.get(dest_code, {})
                    dest_label = _display_name(dest_info.get('label') or dest_code, dest_code) if dest_info else dest_code
                    dest_city = dest_info.get('city', '')
                    dest_country = dest_info.get('country', '')

                    depart_str = datetime.strptime(departure_date, '%Y-%m-%d').strftime('%d%m')
                    if trip_type == 'roundtrip' and return_date:
                        return_str = datetime.strptime(return_date, '%Y-%m-%d').strftime('%d%m')
                        search_code = f"{origin_code}{depart_str}{dest_code}{return_str}"
                    else:
                        search_code = f"{origin_code}{depart_str}{dest_code}1"

                    booking_url = f"https://www.aviasales.com/search/{search_code}?adults={passengers}&marker=617752"

                    entry = {
                        'destination_code': dest_code,
                        'destination_label': dest_label,
                        'destination_city': dest_city,
                        'destination_country': dest_country,
                        'price': price,
                        'num_stops': num_stops,
                        'booking_url': booking_url
                    }

                    if origin_country and dest_country == origin_country:
                        domestic_flights.append(entry)
                    else:
                        international_flights.append(entry)

                # Up to 10 of each, domestic first
                flights = domestic_flights[:10] + international_flights[:10]
        except Exception:
            pass

    airport_index = _get_airport_index()
    origin_country = airport_index.get(form_data.get('origin_code', ''), {}).get('country', '')

    all_posts = _get_all_blog_posts()
    # Show up to 6 cards: newest disk posts first, then static fallbacks
    disk = _load_disk_blog_posts()
    sorted_disk = sorted(disk.values(),
                         key=lambda p: p.get('published_at', ''),
                         reverse=True)
    shown_slugs = [p['slug'] for p in sorted_disk[:6]]
    for slug in all_posts:
        if slug not in shown_slugs:
            shown_slugs.append(slug)
        if len(shown_slugs) >= 6:
            break
    blog_cards = [all_posts[s] for s in shown_slugs if s in all_posts]

    return render_template(
        'index.html',
        flights=flights,
        origin_label=origin_label,
        origin_country=origin_country,
        date=date,
        form_data=form_data,
        blog_cards=blog_cards,
    )

# ---- Content pages ----
@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/faq')
def faq():
    return render_template('faq.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

# ---- Autocomplete API: Amadeus -> local file -> built-in defaults ----
@app.route('/api/airports', methods=['GET'])
def get_airports():
    """
    Returns items like:
      { "code": "LHR", "label": "Heathrow", "city": "London", "name": "Heathrow (LHR)" }
    Your local airports.json labels may already include (CODE), and we avoid duplicating.
    """
    q       = (request.args.get('query')   or "").strip()
    country = (request.args.get('country') or "").strip().upper()

    # No query but country provided → popular airports for that country (OurAirports-first)
    if not q and country:
        oa = _load_ourairports()
        airports_for_country = oa["by_country"].get(country, [])
        if not airports_for_country:
            # Fallback to hardcoded list for unmapped countries
            airports_for_country = [
                {"code": c, "label": city, "city": city, "name": _display_name(city, c)}
                for c, city in (COUNTRY_AIRPORTS.get(country) or COUNTRY_AIRPORTS.get('GB', []))
            ]
        return jsonify([
            {"code": a["code"], "label": a["label"], "city": a.get("city", ""), "name": a["name"]}
            for a in airports_for_country[:10]
        ])

    if not q:
        return jsonify([])

    results = []

    # 1) Amadeus (if available)
    try:
        resp = amadeus.reference_data.locations.get(keyword=q, subType="AIRPORT")
        for a in (resp.data or []):
            code = (a.get('iataCode') or "").strip().upper()
            label = (a.get('name', code) or "").strip()
            city = ((a.get('address') or {}).get('cityName', "") or "").strip()
            if code:
                results.append({
                    "code": code,
                    "label": label or code,
                    "city": city,
                    "name": _display_name(label, code)
                })
    except Exception:
        pass

    # 2) OurAirports (worldwide typed dataset — large airports ranked first)
    if not results:
        oa = _load_ourairports()
        if oa["all"]:
            TYPE_RANK = {'large_airport': 0, 'medium_airport': 1}
            hits = _search_local_airports(q, oa["all"])
            hits.sort(key=lambda x: TYPE_RANK.get(x.get('type'), 2))
            results = [{
                "code": a["code"],
                "label": a["label"],
                "city": a.get("city", ""),
                "name": a["name"]
            } for a in hits[:12]]

    # 3) Local airports.json (static fallback)
    if not results:
        pool = _load_local_airports()
        hits = _search_local_airports(q, pool)
        if hits:
            results = [{
                "code": a["code"],
                "label": a["label"],
                "city": a.get("city", ""),
                "name": _display_name(a.get("label"), a.get("code"))
            } for a in hits]

    # 4) Built-in defaults
    if not results:
        hits = _search_local_airports(q, DEFAULT_AIRPORTS)
        results = [{
            "code": a["code"],
            "label": a["label"],
            "city": a.get("city", ""),
            "name": _display_name(a.get("label"), a.get("code"))
        } for a in hits]

    return jsonify(results)

# ---- SEO landing pages ----
@app.route('/cheap-flights-from/<string:code>')
def seo_airport(code):
    code = code.upper()
    airport_index = _get_airport_index()
    info = airport_index.get(code)
    if not info:
        abort(404)
    label = _display_name(info.get('label') or code, code)
    city = info.get('city', '') or label
    return render_template(
        'index.html',
        flights=[],
        origin_label=label,
        date='',
        form_data={
            'origin': label,
            'origin_code': code,
            'departure_date': '',
            'return_date': '',
            'passengers': 1,
            'trip_type': 'oneway',
        },
        seo_page={'code': code, 'label': label, 'city': city},
    )

# ---- Email price-alert signup ----
@app.route('/subscribe', methods=['POST'])
def subscribe():
    email = (request.form.get('email') or '').strip().lower()
    airport_code = (request.form.get('airport_code') or '').strip().upper()
    airport_name = (request.form.get('airport_name') or '').strip()
    if not email or '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'ok': False, 'error': 'Please enter a valid email address.'}), 400

    row = [email, airport_code, airport_name, datetime.utcnow().isoformat()]

    # Primary: Google Sheets
    sheets_ok = False
    try:
        ws = _get_sheet()
        if ws:
            ws.append_row(row, value_input_option='RAW')
            sheets_ok = True
    except Exception as exc:
        print(f"[subscribe] Sheets error: {exc}", file=__import__('sys').stderr)

    # Fallback: local CSV (always written if Sheets failed)
    if not sheets_ok:
        os.makedirs(DATA_DIR, exist_ok=True)
        new_file = not os.path.exists(SUBSCRIBERS_FILE)
        with open(SUBSCRIBERS_FILE, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=['email', 'airport_code', 'airport_name', 'signed_up_at'])
            if new_file:
                w.writeheader()
            w.writerow(dict(zip(['email', 'airport_code', 'airport_name', 'signed_up_at'], row)))

    return jsonify({'ok': True})

# ---- Geo detection API ----
@app.route('/api/geo')
def api_geo():
    """Detect user's country from IP and return relevant airports + currency."""
    forwarded_for = request.headers.get('X-Forwarded-For', '')
    client_ip = forwarded_for.split(',')[0].strip() if forwarded_for else request.remote_addr

    # Localhost / private ranges → default to GB
    if not client_ip or client_ip in ('127.0.0.1', '::1') or client_ip.startswith('192.168.') or client_ip.startswith('10.'):
        country = 'GB'
    else:
        # Check server-side geo cache
        cached = _geo_cache.get(client_ip)
        if cached and time.time() - cached['fetched_at'] < GEO_CACHE_TTL:
            country = cached['country']
        else:
            country = 'GB'  # safe default
            try:
                r = requests.get(f'https://ipapi.co/{client_ip}/json/', timeout=3)
                if r.status_code == 200:
                    data = r.json()
                    detected = data.get('country_code', 'GB')
                    if detected and len(detected) == 2:
                        country = detected.upper()
                _geo_cache[client_ip] = {'country': country, 'fetched_at': time.time()}
            except Exception:
                pass

    currency_code, symbol = COUNTRY_CURRENCY.get(country, ('eur', '€'))
    # Top airports for this country via OurAirports, fall back to hardcoded list
    oa = _load_ourairports()
    oa_airports = oa["by_country"].get(country, [])
    if oa_airports:
        top_airport_code = oa_airports[0]["code"]
    else:
        fallback = COUNTRY_AIRPORTS.get(country, COUNTRY_AIRPORTS['GB'])
        top_airport_code = fallback[0][0] if fallback else 'LHR'

    return jsonify({
        'country': country,
        'currency': currency_code,
        'symbol': symbol,
        'top_airport': top_airport_code,
    })


# ---- Live deals API ----
@app.route('/api/live-deals')
def api_live_deals():
    country = (request.args.get('country') or 'GB').upper()
    # Fall back to GB if country not in our mapping
    now = time.time()
    cached = _live_deals_cache.get(country)
    if cached and cached['data'] and now - cached['fetched_at'] < LIVE_DEALS_TTL:
        return jsonify(cached['data'])

    if not API_TOKEN:
        return jsonify([])

    # Resolve origin airports: OurAirports large airports for this country,
    # falling back to hardcoded COUNTRY_AIRPORTS, then GB.
    oa = _load_ourairports()
    oa_airports = oa["by_country"].get(country, [])
    if oa_airports:
        origins = [(a["code"], a["city"] or a["label"]) for a in oa_airports[:8]]
    else:
        origins = COUNTRY_AIRPORTS.get(country) or COUNTRY_AIRPORTS.get('GB', [])

    if not origins:
        return jsonify([])

    currency_code, currency_symbol = COUNTRY_CURRENCY.get(country, ('eur', '€'))
    airport_index = _get_airport_index()
    results = []

    today = datetime.utcnow().date()
    week_end = today + timedelta(days=7)

    for origin_code, origin_city in origins:
        try:
            params = {
                'origin': origin_code,
                'currency': currency_code,
                'token': API_TOKEN,
                'limit': 30,
                'sorting': 'price',
            }
            r = requests.get(
                "https://api.travelpayouts.com/v2/prices/latest",
                params=params, timeout=8
            )
            if r.status_code == 200:
                data = r.json().get('data', [])
                if not data:
                    continue

                # Filter to flights departing within the next 7 days
                week_data = []
                for f in data:
                    raw = (f.get('depart_date') or '')[:10]
                    if raw:
                        try:
                            d = datetime.strptime(raw, '%Y-%m-%d').date()
                            if today <= d <= week_end:
                                week_data.append(f)
                        except ValueError:
                            pass

                if not week_data:
                    continue  # no this-week deals for this origin

                # Drop domestic flights (dest in same country as origin)
                week_data = [
                    f for f in week_data
                    if airport_index.get(f.get('destination', ''), {}).get('country', '').upper() != country
                ]
                if not week_data:
                    continue

                best = min(week_data, key=lambda x: x.get('value', 9999))
                dest_code = best.get('destination', '')
                price = best.get('value', 0)
                if price and 5 < price < 2000:
                    dest_info = airport_index.get(dest_code, {})
                    dest_city = dest_info.get('city', '') or dest_code
                    # Format departure date e.g. "Fri 14 Mar"
                    raw_date = (best.get('depart_date') or '')[:10]
                    try:
                        dep_dt = datetime.strptime(raw_date, '%Y-%m-%d')
                        date_label = dep_dt.strftime('%a ') + str(dep_dt.day) + dep_dt.strftime(' %b')
                    except ValueError:
                        date_label = ''
                    results.append({
                        'route': f"{origin_city} \u2192 {dest_city}",
                        'price': price,
                        'symbol': currency_symbol,
                        'date': date_label,
                    })
        except Exception:
            pass

    if results:
        _live_deals_cache[country] = {'data': results, 'fetched_at': now}

    return jsonify(results)


# ---- Blog posts: load from data/blog/*.json (generated) + BLOG_POSTS (static) ----
_BLOG_DISK_CACHE = {"data": {}, "mtime_sum": 0}
BLOG_DIR = os.path.join(DATA_DIR, 'blog')

def _load_disk_blog_posts() -> dict:
    """Load all *.json files from data/blog/, caching by aggregate mtime."""
    if not os.path.isdir(BLOG_DIR):
        return {}
    files = [f for f in os.listdir(BLOG_DIR) if f.endswith('.json') and not f.startswith('.')]
    mtime_sum = sum(os.path.getmtime(os.path.join(BLOG_DIR, f)) for f in files)
    if _BLOG_DISK_CACHE["mtime_sum"] == mtime_sum and _BLOG_DISK_CACHE["data"]:
        return _BLOG_DISK_CACHE["data"]
    posts = {}
    for fn in files:
        try:
            with open(os.path.join(BLOG_DIR, fn), encoding='utf-8') as f:
                p = json.load(f)
            slug = p.get('slug') or fn[:-5]
            # Ensure related is list of lists (JSON tuples come back as lists — that's fine)
            posts[slug] = p
        except Exception:
            pass
    _BLOG_DISK_CACHE.update({"data": posts, "mtime_sum": mtime_sum})
    return posts

def _get_all_blog_posts() -> dict:
    """Merged view: disk-generated posts take precedence over static BLOG_POSTS."""
    merged = dict(BLOG_POSTS)
    merged.update(_load_disk_blog_posts())
    return merged

@app.route('/blog/<string:slug>')
def blog_post(slug):
    post = _get_all_blog_posts().get(slug)
    if not post:
        abort(404)
    return render_template('blog_post.html', post=post)


# ---- Sitemap ----
@app.route('/sitemap.xml')
def sitemap():
    airport_index = _get_airport_index()
    pages = [
        ('https://getmeoutofhere.live/', '1.0'),
        ('https://getmeoutofhere.live/about', '0.5'),
        ('https://getmeoutofhere.live/faq', '0.5'),
        ('https://getmeoutofhere.live/privacy', '0.3'),
        ('https://getmeoutofhere.live/terms', '0.3'),
    ]
    for slug in _get_all_blog_posts():
        pages.append((f'https://getmeoutofhere.live/blog/{slug}', '0.7'))
    for code in SEO_AIRPORTS:
        if code in airport_index:
            pages.append((f'https://getmeoutofhere.live/cheap-flights-from/{code}', '0.8'))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url, priority in pages:
        lines.append(f'  <url><loc>{url}</loc><priority>{priority}</priority></url>')
    lines.append('</urlset>')
    return '\n'.join(lines), 200, {'Content-Type': 'application/xml'}

# ---- Static helpers ----
@app.route('/google48b33f47cd3a277e.html')
def serve_verification_file():
    return send_from_directory(app.root_path, 'google48b33f47cd3a277e.html')

@app.route('/google4a38a2e0e650c32c.html')
def serve_verification_file2():
    return 'google-site-verification: google4a38a2e0e650c32c.html', 200, {'Content-Type': 'text/html'}

@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.root_path, 'robots.txt')

# Optional: quick debug route
@app.route('/debug-templates')
def debug_templates():
    try:
        return "<br>".join(sorted(os.listdir(app.template_folder)))
    except Exception as e:
        return f"Error reading templates: {e}", 500

# ---- Weekly blog scheduler ----
# Runs every Monday at 08:00 server time.
# The lock file in blog_generator prevents duplicate runs across gunicorn workers.
def _scheduled_blog_run():
    try:
        import blog_generator
        blog_generator.run_next()
    except Exception as exc:
        app.logger.error(f"Blog scheduler error: {exc}")

# Only start scheduler in the real process (not in Werkzeug's reloader watcher)
if not (app.debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true'):
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(_scheduled_blog_run, 'cron', day_of_week='mon', hour=8, minute=0)
        _scheduler.start()
    except ImportError:
        pass  # APScheduler not installed — run blog_generator.py manually or via cron

if __name__ == '__main__':
    app.run(debug=True)
