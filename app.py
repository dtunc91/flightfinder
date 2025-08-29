from flask import Flask, render_template, request, jsonify, send_from_directory
from datetime import datetime
from amadeus import Client
import requests
import os
import json
import unicodedata
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, template_folder='templates', static_folder='static')

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
            code = (a.get("code") or "").upper()
            if code and code not in seen:
                clean.append({
                    "code": code,
                    "label": a.get("label") or code,
                    "city": a.get("city") or ""
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
    # buckets
    code_pref, name_pref, substr = [], [], []
    for a in pool:
        code = (a.get("code") or "")
        label = a.get("label") or ""
        city = a.get("city") or ""
        blob = f"{_normalize(label)} {_normalize(city)}"
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

def resolve_label_for_code(code: str) -> str:
    """
    Try to find 'Name (CODE)' using local file first, then Amadeus, else CODE.
    """
    code = (code or "").upper().strip()
    if not code:
        return ""
    for a in _load_local_airports():
        if a["code"] == code:
            label = a.get("label") or a.get("city") or code
            return f"{label} ({code})"
    try:
        resp = amadeus.reference_data.locations.get(keyword=code, subType="AIRPORT")
        if resp.data:
            name = resp.data[0].get("name", code)
            return f"{name} ({code})"
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
    airport_names = {}  # used when resolving origin via Amadeus keyword

    form_data = {
        'trip_type': request.form.get('trip_type', 'oneway'),
        'passengers': request.form.get('passengers', '1'),
        'departure_date': request.form.get('departure_date', ''),
        'return_date': request.form.get('return_date', ''),
        'origin': request.form.get('origin', ''),
        'origin_code': request.form.get('origin_code', '')
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

        # Determine origin_code + human label
        origin_code = ''
        if origin_code_hidden and len(origin_code_hidden) == 3 and origin_code_hidden.isalpha():
            origin_code = origin_code_hidden
            origin_label = resolve_label_for_code(origin_code)
        else:
            if '(' in origin_raw and ')' in origin_raw:
                origin_code = origin_raw.split('(')[-1].replace(')', '').strip().upper()
                origin_label = origin_raw
            elif len(origin_raw) == 3 and origin_raw.isalpha():
                origin_code = origin_raw.upper()
                origin_label = resolve_label_for_code(origin_code)
            else:
                airport_names = load_airport_names(origin_raw)
                if airport_names:
                    origin_code = list(airport_names.keys())[0]
                    origin_label = f"{airport_names[origin_code]} ({origin_code})"
                else:
                    origin_code = 'LON'
                    origin_label = 'London (LON)'

        date = departure_date

        # Travelpayouts fetch
        params = {'origin': origin_code, 'currency': 'gbp', 'token': API_TOKEN}
        try:
            r = requests.get("https://api.travelpayouts.com/v2/prices/latest", params=params, timeout=15)
            if r.status_code == 200:
                data = r.json().get('data', [])[:10]
                for flight in data:
                    dest_code = flight.get('destination', 'N/A')
                    price = flight.get('value', 'N/A')

                    depart_str = datetime.strptime(departure_date, '%Y-%m-%d').strftime('%d%m')
                    if trip_type == 'roundtrip' and return_date:
                        return_str = datetime.strptime(return_date, '%Y-%m-%d').strftime('%d%m')
                        search_code = f"{origin_code}{depart_str}{dest_code}{return_str}"
                    else:
                        search_code = f"{origin_code}{depart_str}{dest_code}1"

                    booking_url = f"https://www.aviasales.com/search/{search_code}?adults={passengers}&marker=617752"

                    flights.append({
                        'destination_code': dest_code,
                        'destination_label': dest_code,  # keep fast; code is fine
                        'price': price,
                        'booking_url': booking_url
                    })
        except Exception:
            pass

    return render_template('index.html',
                           flights=flights,
                           origin_label=origin_label,
                           date=date,
                           form_data=form_data)

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
    """
    q = (request.args.get('query') or "").strip()
    if not q:
        return jsonify([])

    results = []

    # 1) Amadeus (if available)
    try:
        resp = amadeus.reference_data.locations.get(keyword=q, subType="AIRPORT")
        for a in (resp.data or []):
            code = a.get('iataCode')
            label = a.get('name', code) or ""
            city = (a.get('address') or {}).get('cityName', "") or ""
            if code:
                results.append({"code": code, "label": label, "city": city, "name": f"{label} ({code})"})
    except Exception:
        pass

    # 2) Local file fallback
    if not results:
        pool = _load_local_airports()
        hits = _search_local_airports(q, pool)
        if hits:
            results = [{"code": a["code"],
                        "label": a["label"],
                        "city": a.get("city", ""),
                        "name": f'{a["label"]} ({a["code"]})'} for a in hits]

    # 3) Built-in defaults
    if not results:
        hits = _search_local_airports(q, DEFAULT_AIRPORTS)
        results = [{"code": a["code"],
                    "label": a["label"],
                    "city": a.get("city", ""),
                    "name": f'{a["label"]} ({a["code"]})'} for a in hits]

    return jsonify(results)

# ---- Static helpers ----
@app.route('/google48b33f47cd3a277e.html')
def serve_verification_file():
    return send_from_directory('.', 'google48b33f47cd3a277e.html')

@app.route('/robots.txt')
def robots_txt():
    return send_from_directory('.', 'robots.txt')

# Optional: quick debug route
@app.route('/debug-templates')
def debug_templates():
    try:
        return "<br>".join(sorted(os.listdir(app.template_folder)))
    except Exception as e:
        return f"Error reading templates: {e}", 500

if __name__ == '__main__':
    app.run(debug=True)
