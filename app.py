from flask import Flask, render_template, request, jsonify, send_from_directory, url_for
from datetime import datetime
from amadeus import Client
import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()

# Be explicit about folders
app = Flask(__name__, template_folder='templates', static_folder='static')

# ---- Secrets / API keys ----
API_TOKEN = os.getenv('API_TOKEN')  # Travelpayouts API token
AMADEUS_CLIENT_ID = os.getenv('AMADEUS_CLIENT_ID')
AMADEUS_CLIENT_SECRET = os.getenv('AMADEUS_CLIENT_SECRET')

amadeus = Client(
    client_id=AMADEUS_CLIENT_ID,
    client_secret=AMADEUS_CLIENT_SECRET
)

# ---- Template globals ----
@app.context_processor
def inject_now():
    return {"current_year": datetime.utcnow().year}


# ---- Helpers ----
def load_airport_names(query: str) -> dict:
    """
    Query Amadeus for airports by keyword.
    Returns dict: { IATA: 'Airport Name' }
    """
    try:
        resp = amadeus.reference_data.locations.get(keyword=query, subType="AIRPORT")
        airports = resp.data if resp.data else []
        return {a['iataCode']: a.get('name', a['iataCode']) for a in airports}
    except Exception:
        return {}


def read_local_airports() -> list:
    """
    Optional local fallback file: static/airports.json
    Format for each item: { "code": "LHR", "label": "Heathrow", "city": "London" }
    """
    path = os.path.join(app.static_folder, 'airports.json')
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def resolve_label_for_code(code: str) -> str:
    """
    Try to find a human label for an IATA code.
    """
    code = (code or '').upper().strip()
    if not code:
        return ''
    # try local first
    for a in read_local_airports():
        if (a.get('code') or '').upper() == code:
            label = a.get('label') or a.get('city') or code
            return f"{label} ({code})"
    # fallback to Amadeus
    try:
        resp = amadeus.reference_data.locations.get(keyword=code, subType="AIRPORT")
        if resp.data:
            name = resp.data[0].get('name', code)
            return f"{name} ({code})"
    except Exception:
        pass
    return code


# ---- Routes ----
@app.route('/', methods=['GET', 'POST'])
def index():
    flights = []
    origin_label = ""
    date = ""
    airport_names = {}  # only used when we resolve the input query

    # Keep previously submitted values visible
    form_data = {
        'trip_type': request.form.get('trip_type', 'oneway'),
        'passengers': request.form.get('passengers', '1'),
        'departure_date': request.form.get('departure_date', ''),
        'return_date': request.form.get('return_date', ''),
        'origin': request.form.get('origin', '')
    }

    if request.method == 'POST':
        origin_raw = (request.form.get('origin') or '').strip()
        origin_code_hidden = (request.form.get('origin_code') or '').strip().upper()
        departure_date = request.form.get('departure_date')
        return_date = request.form.get('return_date')
        trip_type = request.form.get('trip_type', 'oneway')
        passengers = request.form.get('passengers', '1')

        # Store latest form values
        form_data.update({
            'trip_type': trip_type,
            'passengers': passengers,
            'departure_date': departure_date,
            'return_date': return_date,
            'origin': origin_raw
        })

        # --- Determine origin_code + origin_label robustly ---
        origin_code = ''
        # 1) Prefer hidden field (our newer UI sets this)
        if origin_code_hidden and len(origin_code_hidden) == 3 and origin_code_hidden.isalpha():
            origin_code = origin_code_hidden
            origin_label = resolve_label_for_code(origin_code)
        else:
            # 2) If input looks like "Heathrow (LHR)"
            if '(' in origin_raw and ')' in origin_raw:
                origin_code = origin_raw.split('(')[-1].replace(')', '').strip().upper()
                origin_label = origin_raw
            # 3) If input looks like a code, use it directly
            elif len(origin_raw) == 3 and origin_raw.isalpha():
                origin_code = origin_raw.upper()
                origin_label = resolve_label_for_code(origin_code)
            # 4) Else query Amadeus by keyword and take the first match
            else:
                airport_names = load_airport_names(origin_raw)
                if airport_names:
                    origin_code = list(airport_names.keys())[0]
                    origin_label = f"{airport_names[origin_code]} ({origin_code})"
                else:
                    # final fallback: London
                    origin_code = 'LON'
                    origin_label = 'London (LON)'

        date = departure_date

        # --- Travelpayouts fetch (v2/prices/latest) ---
        params = {
            'origin': origin_code,
            'currency': 'gbp',
            'token': API_TOKEN
        }
        try:
            r = requests.get("https://api.travelpayouts.com/v2/prices/latest", params=params, timeout=15)
            if r.status_code == 200:
                data = r.json().get('data', [])[:10]
                for flight in data:
                    dest_code = flight.get('destination', 'N/A')
                    price = flight.get('value', 'N/A')

                    # Build Aviasales deep link
                    depart_str = datetime.strptime(departure_date, '%Y-%m-%d').strftime('%d%m')
                    if trip_type == 'roundtrip' and return_date:
                        return_str = datetime.strptime(return_date, '%Y-%m-%d').strftime('%d%m')
                        search_code = f"{origin_code}{depart_str}{dest_code}{return_str}"
                    else:
                        search_code = f"{origin_code}{depart_str}{dest_code}1"

                    booking_url = f"https://www.aviasales.com/search/{search_code}?adults={passengers}&marker=617752"

                    flights.append({
                        'destination_code': dest_code,
                        'destination_label': airport_names.get(dest_code, dest_code),
                        'price': price,
                        'booking_url': booking_url
                    })
        except Exception:
            # Fail silently; page will show empty results message via JS
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


# ---- Autocomplete API (backward compatible) ----
@app.route('/api/airports', methods=['GET'])
def get_airports():
    """
    Backward-compatible airport lookup.
    Returns items like:
      {
        "code": "LHR",
        "label": "Heathrow",           # new field (clean name)
        "city": "London",              # new field (optional)
        "name": "Heathrow (LHR)"       # legacy field your older JS used
      }
    Tries Amadeus first; falls back to static/airports.json if available.
    """
    q = (request.args.get('query') or "").strip()
    if not q:
        return jsonify([])

    results = []

    # 1) Try Amadeus
    try:
        resp = amadeus.reference_data.locations.get(keyword=q, subType="AIRPORT")
        for a in (resp.data or []):
            code = a.get('iataCode')
            label = a.get('name', code) or ""
            city = (a.get('address') or {}).get('cityName', "") or ""
            if code:
                results.append({
                    "code": code,
                    "label": label,
                    "city": city,
                    "name": f"{label} ({code})"
                })
    except Exception:
        pass

    # 2) Fallback to local JSON if Amadeus gave nothing
    if not results:
        local = read_local_airports()
        if local:
            s = q.lower()
            code_hits = [a for a in local if (a.get('code') or '').lower().startswith(s)]
            label_hits = [a for a in local if s in (a.get('label', '').lower() + ' ' + a.get('city', '').lower())]
            seen = set()
            merged = []
            for a in code_hits + label_hits:
                c = (a.get('code') or '').upper()
                label = a.get('label', '') or a.get('city', '') or c
                city = a.get('city', '')
                if c and c not in seen:
                    merged.append({
                        "code": c,
                        "label": label,
                        "city": city,
                        "name": f"{label} ({c})"
                    })
                    seen.add(c)
            results = merged[:25]

    return jsonify(results)


# ---- Misc / static helpers ----
@app.route('/google48b33f47cd3a277e.html')
def serve_verification_file():
    return send_from_directory('.', 'google48b33f47cd3a277e.html')

@app.route('/robots.txt')
def robots_txt():
    return send_from_directory('.', 'robots.txt')

@app.route('/debug-files')
def debug_files():
    return '<br>'.join(os.listdir('.'))

# Optional: quick template listing for debugging
@app.route('/debug-templates')
def debug_templates():
    try:
        return "<br>".join(sorted(os.listdir(app.template_folder)))
    except Exception as e:
        return f"Error reading templates: {e}", 500


if __name__ == '__main__':
    app.run(debug=True)
