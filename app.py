from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import time
from shapely.geometry import shape, Point, Polygon
import json

app = Flask(__name__)
CORS(app)

# --- Carte départements (à charger une seule fois) ---
with open("departements-version-simplifiee.geojson", encoding="utf-8") as f:
    departements_geojson = json.load(f)

departements_shapes = [
    {
        "code": feature["properties"]["code"],
        "nom": feature["properties"]["nom"],
        "geometry": shape(feature["geometry"])
    }
    for feature in departements_geojson["features"]
]

# --- Fonctions utilitaires ---
def clean_company_name(company_name):
    if not company_name:
        return ""
    replacements = {
        "BNP Paribas": "bnpparibas",
        "L'Oréal": "loreal",
        "Société Générale": "societegenerale",
        "France Travail": "pole-emploi"
    }
    for original, replacement in replacements.items():
        if original.lower() in company_name.lower():
            return replacement
    clean_name = company_name.split('(')[0].split('-')[0].split('/')[0].strip()
    clean_name = ''.join(e for e in clean_name if e.isalnum() or e.isspace())
    return clean_name.split()[0].lower() if clean_name else ""

def get_clearbit_logo(company_name):
    if not company_name:
        return ""
    logo_url = f"https://logo.clearbit.com/{company_name}.com?size=150"
    try:
        r = requests.head(logo_url, timeout=2)
        return logo_url if r.status_code == 200 else ""
    except:
        return ""

def format_salary(sal):
    if not sal: return "Non précisé"
    return f"{sal.get('libelle', '')} ({sal.get('complement1', '')})".strip()

def clean_description(desc):
    if not desc: return ""
    return ' '.join(desc.replace('<br />', '\n').replace('<p>', '\n').replace('</p>', '').split())

# --- Conversion polygone → départements ---
def get_departements_from_polygon(isochrone_polygon_coords):
    polygon = Polygon(isochrone_polygon_coords[0])  # premier anneau = surface principale
    codes = [d["code"] for d in departements_shapes if d["geometry"].intersects(polygon)]
    return list(set(codes))

# --- Requête à France Travail ---
def get_france_travail_jobs(region_codes=None, keyword=None, type_contrat=None, max_results=100):
    # Authentification
    auth_url = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
    client_id = 'PAR_parisgo_02ee6a6b30b8ee2045ade6e947fb9e8b91703dcb90afcc760e78a4a9aa1c1edd'
    client_secret = 'f019350d3fd8f7000df4a0c82817536bdad198ea518bf164fb2787dffe4dd9df'
    scope = 'api_offresdemploiv2 o2dsoffre'
    payload = f'grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}&scope={scope}'
    headers_auth = {'Content-Type': 'application/x-www-form-urlencoded'}

    try:
        r = requests.post(auth_url, headers=headers_auth, data=payload)
        r.raise_for_status()
        access_token = r.json().get("access_token")
    except Exception as e:
        print("❌ Auth error:", e)
        return []

    # Requête offres
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
            params['departement'] = region_codes

        try:
            r = requests.get("https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search", headers=headers, params=params)
            r.raise_for_status()
            results = r.json().get('resultats', [])
            if not results: break
            all_offers += results
            if len(results) < range_size or len(all_offers) >= max_results: break
            range_start += range_size
            time.sleep(0.3)
        except Exception as e:
            print("❌ Fetch error:", e)
            break

    # Formatage
    formatted = []
    for i, o in enumerate(all_offers[:max_results]):
        comp = o.get('entreprise', {}).get('nom', '').strip()
        logo = get_clearbit_logo(clean_company_name(comp)) if comp else ""
        formatted.append({
            "id": f"job{i}",
            "title": o.get("intitule", ""),
            "company": comp,
            "position": "Full-time",
            "salary": format_salary(o.get("salaire")),
            "lat": o.get('lieuTravail', {}).get('latitude'),
            "lng": o.get('lieuTravail', {}).get('longitude'),
            "address": o.get('lieuTravail', {}).get('libelle', ''),
            "type": o.get('typeContrat', ''),
            "description": clean_description(o.get('description', '')),
            "imageUrl": logo,
            "suburb": o.get('lieuTravail', {}).get('commune', ''),
            "url": o.get('origineOffre', {}).get('url', '')
        })

    return formatted

# --- Route POST ---
@app.route("/api/jobs", methods=["POST"])
def search_jobs():
    try:
        data = request.get_json(force=True)
        keyword = data.get("keyword")
        filters = data.get("filters", {})
        iso_coords = data.get("isochrone_polygon")  # [[ [lng, lat], [lng, lat], ... ]]

        if not iso_coords or not isinstance(iso_coords, list):
            return jsonify({"error": "Polygone isochrone invalide"}), 400

        departements = get_departements_from_polygon(iso_coords)
        if not departements:
            return jsonify({"jobs": []}), 200

        jobs = get_france_travail_jobs(
            region_codes=departements,
            keyword=keyword,
            type_contrat=filters.get("contrat"),
            max_results=100
        )

        # Filtrer localement avec turf.js côté front si nécessaire
        return jsonify({"jobs": jobs}), 200

    except Exception as e:
        print("❌ Erreur backend:", e)
        return jsonify({"error": str(e)}), 500

# --- Dev ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
