from flask import Flask, render_template, request
import requests
import json
from datetime import datetime, timedelta

app = Flask(__name__)

API_TOKEN = "8d7038ac3e129418d7b8f9e827db1cd0"

def load_airport_names():
    with open("static/airports.json") as f:
        airports = json.load(f)
        return {a['code']: a['label'] for a in airports}

@app.route('/', methods=['GET', 'POST'])
def index():
    flights = []
    origin_label = ""
    date = ""
    airport_names = load_airport_names()

    if request.method == 'POST':
        origin_label_input = request.form.get('origin')
        departure_date = request.form.get('departure_date')
        return_date = request.form.get('return_date')
        trip_type = request.form.get('trip_type')
        passengers = request.form.get('passengers', '1')

        origin_code = None
        for code, label in airport_names.items():
            if label == origin_label_input:
                origin_code = code
                origin_label = label
                break

        if not origin_code:
            origin_code = 'LON'
            origin_label = origin_label_input

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

                if trip_type == 'roundtrip' and return_date:
                    depart_str = datetime.strptime(departure_date, '%Y-%m-%d').strftime('%d%m')
                    return_str = datetime.strptime(return_date, '%Y-%m-%d').strftime('%d%m')
                    search_code = f"{origin_code}{depart_str}{dest_code}{return_str}"
                else:
                    depart_str = datetime.strptime(departure_date, '%Y-%m-%d').strftime('%d%m')
                    search_code = f"{origin_code}{depart_str}{dest_code}1"

                booking_url = f"https://www.aviasales.com/search/{search_code}?adults={passengers}&marker=617752"

                flights.append({
                    'destination_code': dest_code,
                    'destination_label': airport_names.get(dest_code, dest_code),
                    'price': price,
                    'booking_url': booking_url
                })

    return render_template('index.html', flights=flights, origin_label=origin_label, date=date)

if __name__ == '__main__':
    app.run(debug=True)
