from flask import Flask, render_template, request, jsonify, send_from_directory
from markupsafe import Markup
import requests
import json
from datetime import datetime
from amadeus import Client

app = Flask(__name__)

API_TOKEN = "8d7038ac3e129418d7b8f9e827db1cd0"
AMADEUS_CLIENT_ID = "LGJVRcjkGHj2MCA09MMszzqyAuABZHCh"
AMADEUS_CLIENT_SECRET = "1gL3WMAs4hKlH8pF"

amadeus = Client(
    client_id=AMADEUS_CLIENT_ID,
    client_secret=AMADEUS_CLIENT_SECRET
)

def load_airport_names(query):
    response = amadeus.reference_data.locations.get(
        keyword=query,
        subType="AIRPORT"
    )
    airports = response.data if response.data else []
    return {airport['iataCode']: airport['name'] for airport in airports}

@app.route('/', methods=['GET', 'POST'])
def index():
    flights = []
    origin_label = ""
    date = ""
    airport_names = {}

    if request.method == 'POST':
        origin_label_input = request.form.get('origin')
        departure_date = request.form.get('departure_date')
        return_date = request.form.get('return_date')
        trip_type = request.form.get('trip_type')
        passengers = request.form.get('passengers', '1')

        # Try to extract the IATA code from the selected input
        if '(' in origin_label_input and ')' in origin_label_input:
            origin_code = origin_label_input.split('(')[-1].replace(')', '').strip()
            origin_label = origin_label_input
        else:
            # fallback — attempt to find airport code via API
            airport_names = load_airport_names(origin_label_input)
            origin_code = list(airport_names.keys())[0] if airport_names else 'LON'
            origin_label = airport_names.get(origin_code, origin_label_input)

        date = departure_date

        params = {
            'origin': origin_code,
            'currency': 'gbp',
            'token': API_TOKEN
        }

        r = requests.get("https://api.travelpayouts.com/v2/prices/latest", params=params)
        if r.status_code == 200:
            data = r.json().get('data', [])[:10]
            for flight in data:
                dest_code = flight.get('destination', 'N/A')
                price = flight.get('value', 'N/A')

                # Format booking code depending on trip type
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

    return render_template('index.html', flights=flights, origin_label=origin_label, date=date)

@app.route('/api/airports', methods=['GET'])
def get_airports():
    query = request.args.get('query', '')
    if query:
        airport_names = load_airport_names(query)
        airports_response = [{'code': code, 'name': f"{name} ({code})"} for code, name in airport_names.items()]
        return jsonify(airports_response)
    return jsonify([])

@app.route('/google48b33f47cd3a277e.html')
def serve_verification_file():
    return send_from_directory('.', 'google48b33f47cd3a277e.html')

if __name__ == '__main__':
    app.run(debug=True)
