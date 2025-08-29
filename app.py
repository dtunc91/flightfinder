from flask import Flask, render_template, request, jsonify, send_from_directory, url_for
from markupsafe import Markup
import requests
import json
import os
from datetime import datetime
from amadeus import Client
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
    except Exception as e:
        print(f"Error in load_airport_names: {e}")
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
            # Try to find airport code from local JSON first
            try:
                airports_file = os.path.join('static', 'airports.json')
                if os.path.exists(airports_file):
                    with open(airports_file, 'r') as f:
                        local_airports = json.load(f)
                    
                    # Search for matching airport
                    query_lower = origin_label_input.lower()
                    for airport in local_airports:
                        if (query_lower == airport.get('code', '').lower() or
                            query_lower in airport.get('label', '').lower() or
                            query_lower in airport.get('city', '').lower()):
                            origin_code = airport['code']
                            origin_label = f"{airport['label']} ({airport['code']})"
                            break
                    else:
                        # If not found locally, try Amadeus
                        airport_names = load_airport_names(origin_label_input)
                        origin_code = list(airport_names.keys())[0] if airport_names else 'LON'
                        origin_label = airport_names.get(origin_code, origin_label_input)
                else:
                    # No local file, use Amadeus
                    airport_names = load_airport_names(origin_label_input)
                    origin_code = list(airport_names.keys())[0] if airport_names else 'LON'
                    origin_label = airport_names.get(origin_code, origin_label_input)
            except Exception as e:
                print(f"Error processing origin: {e}")
                # Fallback to Amadeus
                airport_names = load_airport_names(origin_label_input)
                origin_code = list(airport_names.keys())[0] if airport_names else 'LON'
                origin_label = airport_names.get(origin_code, origin_label_input)

        date = departure_date

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
                    if departure_date:
                        try:
                            depart_str = datetime.strptime(departure_date, '%Y-%m-%d').strftime('%d%m')
                            if trip_type == 'roundtrip' and return_date:
                                return_str = datetime.strptime(return_date, '%Y-%m-%d').strftime('%d%m')
                                search_code = f"{origin_code}{depart_str}{dest_code}{return_str}"
                            else:
                                search_code = f"{origin_code}{depart_str}{dest_code}1"
                        except ValueError:
                            # Fallback if date parsing fails
                            search_code = f"{origin_code}0101{dest_code}1"
                    else:
                        search_code = f"{origin_code}0101{dest_code}1"

                    booking_url = f"https://www.aviasales.com/search/{search_code}?adults={passengers}&marker=617752"

                    # Try to get destination name from local airports
                    dest_label = dest_code
                    try:
                        airports_file = os.path.join('static', 'airports.json')
                        if os.path.exists(airports_file):
                            with open(airports_file, 'r') as f:
                                local_airports = json.load(f)
                            for airport in local_airports:
                                if airport.get('code') == dest_code:
                                    dest_label = airport.get('label', dest_code)
                                    break
                    except Exception:
                        pass

                    flights.append({
                        'destination_code': dest_code,
                        'destination_label': dest_label,
                        'price': price,
                        'booking_url': booking_url
                    })
            else:
                print(f"Travel API error: {r.status_code}")
        except Exception as e:
            print(f"Error fetching flights: {e}")

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

# ---------- Improved Autocomplete API ----------
@app.route('/api/airports', methods=['GET'])
def get_airports():
    query = request.args.get('query', '').strip()
    print(f"Airport search query: '{query}'")  # Debug log
    
    if not query:
        print("Empty query, returning empty array")
        return jsonify([])

    try:
        # Try Amadeus API first
        print("Trying Amadeus API...")
        response = amadeus.reference_data.locations.get(
            keyword=query,
            subType="AIRPORT"
        )
        airports = response.data if response.data else []
        print(f"Amadeus returned {len(airports)} airports")
        
        out = []
        for a in airports:
            code = a.get('iataCode')
            name = a.get('name', code)
            city = (a.get('address') or {}).get('cityName', '')
            if code:  # Only include airports with valid IATA codes
                out.append({"code": code, "label": name, "city": city})
        
        print(f"Returning {len(out)} formatted airports from Amadeus")
        return jsonify(out)
        
    except Exception as e:
        print(f"Amadeus API error: {e}")
        
        # Fallback: Load from local airports.json
        try:
            print("Trying local airports.json fallback...")
            airports_file = os.path.join('static', 'airports.json')
            
            if os.path.exists(airports_file):
                with open(airports_file, 'r') as f:
                    local_airports = json.load(f)
                
                print(f"Loaded {len(local_airports)} airports from local file")
                
                # Filter local airports based on query
                query_lower = query.lower()
                filtered = []
                
                for airport in local_airports:
                    code = airport.get('code', '').lower()
                    label = airport.get('label', '').lower()
                    city = airport.get('city', '').lower()
                    
                    if (query_lower in code or 
                        query_lower in label or 
                        query_lower in city):
                        filtered.append(airport)
                        
                    if len(filtered) >= 10:  # Limit to 10 results
                        break
                
                print(f"Using local fallback, returning {len(filtered)} airports")
                return jsonify(filtered)
            else:
                print("No local airports.json file found")
                
        except Exception as fallback_error:
            print(f"Local fallback error: {fallback_error}")
        
        # Ultimate fallback - return empty array
        print("All methods failed, returning empty array")
        return jsonify([])

# Test endpoint to check if airports.json is loading
@app.route('/test/airports')
def test_airports():
    try:
        airports_file = os.path.join('static', 'airports.json')
        
        if os.path.exists(airports_file):
            with open(airports_file, 'r') as f:
                airports = json.load(f)
            
            sample_airports = airports[:3] if len(airports) > 3 else airports
            return {
                "status": "success",
                "total_airports": len(airports),
                "file_path": airports_file,
                "sample_airports": sample_airports
            }
        else:
            return {
                "status": "error",
                "message": "airports.json file not found",
                "file_path": airports_file,
                "current_dir": os.getcwd(),
                "static_exists": os.path.exists('static')
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error loading airports: {e}",
            "file_path": airports_file if 'airports_file' in locals() else "unknown"
        }

# Test endpoint for API functionality
@app.route('/test/api')
def test_api():
    return {
        "amadeus_configured": bool(AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET),
        "travel_api_token": bool(API_TOKEN),
        "current_time": datetime.now().isoformat()
    }

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
    files = os.listdir('.')
    static_files = os.listdir('static') if os.path.exists('static') else []
    return {
        "root_files": files,
        "static_files": static_files,
        "current_dir": os.getcwd()
    }

if __name__ == '__main__':
    app.run(debug=True)
