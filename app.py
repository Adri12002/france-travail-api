from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import time
import os
import json
import shapely.geometry
from shapely.geometry import shape, Point, Polygon

app = Flask(__name__)
CORS(app)

# Charger les départements une seule fois au démarrage
with open("departements.geojson", encoding="utf-8") as f:
    DEPARTEMENTS = json.load(f)

def get_departements_from_polygon(geojson_polygon):
    polygon = shape(geojson_polygon)
    codes = []
    for feature in DEPARTEMENTS["features"]:
        dept_shape = shape(feature["geometry"])
        if polygon.intersects(dept_shape):
            codes.append(feature["properties"]["code"])
    return codes

def clean_company_name(company_name):
    if not company_name:
        return ""
    clean_name = company_name.split('(')[0].split('-')[0].split('/')[0].strip()
    clean_name = ''.join(e for e in clean_name if e.isalnum() or e.isspace())
    return clean_name.split()[0].lower() if clean_name else ""

def get_clearbit_logo(company_name):
    if not company_name:
        return ""
    logo_url = f"https://logo.clearbit.com/{company_name}.com?size=150"
    try:
        response = requests.head(logo_url, timeout=2)
        return logo_url if response.status_code == 200 else ""
    except:
        return ""

def format_salary(salary_info):
    if not salary_info:
        return "Non précisé"
    libelle = salary_info.get('libelle', '')
    complement = salary_info.get('complement1', '')
    if libelle and complement:
        return f"{libelle} ({complement})"
    return libelle or complement or "Non précisé"

def clean_description(desc):
    if not desc:
        return ""
    desc = desc.replace('<br />', '\n').replace('<p>', '\n').replace('</p>', '')
    return ' '.join(desc.split())

def get_france_travail_jobs(region_codes=None, keyword=None, type_contrat=None, max_results=100):
    auth_url = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
    client_id = os.environ.get("FT_CLIENT_ID")
    client_secret = os.environ.get("FT_CLIENT_SECRET")
    scope = 'api_offresdemploiv2 o2dsoffre'
    auth_payload = f'grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}&scope={scope}'
    auth_headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    try:
        auth_response = requests.post(auth_url, headers=auth_headers, data=auth_payload)
        auth_response.raise_for_status()
        access_token = auth_response.json().get('access_token')
    except Exception as e:
        print(f"Erreur d'authentification France Travail : {e}")
        return []

    search_url = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json',
        'User-Agent': 'HabitaBot/1.0'
    }

    all_offers = []
    range_start = 0
    range_size = 100

    while True:
        params = {
            'range': f'{range_start}-{range_start + range_size - 1}',
            'motsCles': keyword,
            'typeContrat': type_contrat
        }
        if region_codes:
            params['departement'] = ','.join(region_codes)

        try:
            response = requests.get(search_url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            offers = data.get('resultats', [])
            if not offers:
                break
            all_offers.extend(offers)
            range_start += range_size
            if len(offers) < range_size or len(all_offers) >= max_results:
                break
            time.sleep(0.5)
        except Exception as e:
            print(f"Erreur lors de la récupération des offres : {e}")
            break

    formatted = []
    for i, offer in enumerate(all_offers[:max_results], 1):
        lat = offer.get('lieuTravail', {}).get('latitude')
        lon = offer.get('lieuTravail', {}).get('longitude')
        if not lat or not lon:
            continue

        formatted.append({
            "id": f"job{i}",
            "title": offer.get('intitule', ''),
            "company": offer.get('entreprise', {}).get('nom', '').strip(),
            "position": "Full-time",
            "salary": format_salary(offer.get('salaire', {})),
            "lat": lat,
            "lng": lon,
            "address": offer.get('lieuTravail', {}).get('libelle', ''),
            "type": offer.get('typeContrat', ''),
            "description": clean_description(offer.get('description', '')),
            "imageUrl": get_clearbit_logo(clean_company_name(offer.get('entreprise', {}).get('nom', ''))),
            "suburb": offer.get('lieuTravail', {}).get('commune', ''),
            "url": offer.get('origineOffre', {}).get('url', '')
        })

    return formatted

@app.route("/api/jobs", methods=["POST"])
def search_jobs():
    try:
        data = request.get_json(force=True)
        keyword = data.get("keyword")
        filters = data.get("filters", {})
        polygon = data.get("polygon")

        type_contrat = filters.get("contrat")
        region_codes = get_departements_from_polygon(polygon) if polygon else None

        jobs = get_france_travail_jobs(
            keyword=keyword,
            region_codes=region_codes,
            type_contrat=type_contrat,
            max_results=150
        )

        if polygon:
            poly = shape(polygon)
            jobs = [job for job in jobs if poly.contains(Point(job["lng"], job["lat"]))]

        return jsonify({"jobs": jobs}), 200

    except Exception as e:
        print("❌ Erreur backend :", str(e))
        return jsonify({"error": "Erreur interne du serveur"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
