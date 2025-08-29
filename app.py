from flask import Flask, render_template, request, jsonify, send_from_directory, url_for
from markupsafe import Markup
import requests
import json
from datetime import datetime
from amadeus import Client
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

API_TOKEN = os.getenv('API_TOKEN')

# Amadeus
AMADEUS_CLIENT_ID = os.getenv('AMADEUS_CLIENT_ID')
AMADEUS_CLIENT_SECRET = os.getenv('AMADEUS_CLIENT_SECRET')

amadeus = Client(
    client_id=AMADEUS_CLIENT_ID,
    client_secret=AMADEUS_CLIENT_SECRET
)

# Make current year available in all templates
@app.context_processor
def inject_now():
    return {"current_year": datetime.utcnow().year}

def load_airport_names(query):
    """
    Returns dict: { IATA: 'Airport Name' }
    """
    try:
        response = amadeus.reference_data.locations.get(
            keyword=query,
            subType="AIRPORT"
        )
        airports = response.data if response.data else []
        return {a['iataCode']: a.get('name', a['iataCode']) for a in airports}
    except Exception:
        return {}

@app.route('/', methods=['GET', 'POST'])
def index():
    flights = []
    origin_label = ""
    date = ""
    airport_names = {}
    form_data = {
        'trip_type': request.form.get('trip_type', 'oneway'),
        'passengers': request.form.get('passengers', '1'),
        'departure_date': request.form.get('departure_date', ''),
        'return_date': request.form.get('return_date', ''),
        'origin': request.form.get('origin', '')
    }

    if request.method == 'POST':
        origin_label_input = request.form.get('origin')
        departure_date = request.form.get('departure_date')
        return_date = request.form.get('return_date')
        trip_type = request.form.get('trip_type', 'oneway')
        passengers = request.form.get('passengers', '1')

        form_data = {
            'trip_type': trip_type,
            'passengers': passengers,
            'departure_date': departure_date,
            'return_date': return_date,
            'origin': origin_label_input
        }

        # Detect IATA code in input, else resolve via Amadeus
        if '(' in origin_label_input and ')' in origin_label_input:
            origin_code = origin_label_input.split('(')[-1].replace(')', '').strip()
            origin_label = origin_label_input
        else:
            airport_names = load_airport_names(origin_label_input)
            origin_code = list(airport_names.keys())[0] if airport_names else 'LON'
            origin_label = airport_names.get(origin_code, origin_label_input)

        date = departure_date

        params = {
            'origin': origin_code,
            'currency': 'gbp',
            'token': API_TOKEN
        }

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

    return render_template('index.html', flights=flights, origin_label=origin_label, date=date, form_data=form_data)

# ---------- New content routes ----------
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

# ---------- Autocomplete API ----------
@app.route('/api/airports', methods=['GET'])
def get_airports():
    """
    Return JSON with keys our frontend expects.
    Example item: { "code": "LHR", "label": "Heathrow", "city": "London" }
    """
    query = request.args.get('query', '').strip()
    if not query:
        return jsonify([])

    try:
        response = amadeus.reference_data.locations.get(
            keyword=query,
            subType="AIRPORT"
        )
        airports = response.data if response.data else []
        out = []
        for a in airports:
            code = a.get('iataCode')
            name = a.get('name', code)
            city = (a.get('address') or {}).get('cityName', '')
            out.append({"code": code, "label": name, "city": city})
        return jsonify(out)
    except Exception:
        # Graceful fallback
        airports = load_airport_names(query)
        return jsonify([{"code": c, "label": n, "city": ""} for c, n in airports.items()])

# ---------- Misc ----------
@app.route('/google48b33f47cd3a277e.html')
def serve_verification_file():
    return send_from_directory('.', 'google48b33f47cd3a277e.html')

@app.route('/robots.txt')
def robots_txt():
    return send_from_directory('.', 'robots.txt')

@app.route('/debug-files')
def debug_files():
    import os
    return '<br>'.join(os.listdir('.'))

if __name__ == '__main__':
    app.run(debug=True)
