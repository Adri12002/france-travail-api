from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import time
import os

app = Flask(__name__)
CORS(app)

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

# --- Fonction principale de récupération France Travail ---

def get_france_travail_jobs(region_code=None, keyword=None, type_contrat=None, max_results=100):
    auth_url = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
    client_id = 'PAR_parisgo_02ee6a6b30b8ee2045ade6e947fb9e8b91703dcb90afcc760e78a4a9aa1c1edd'
    client_secret = 'f019350d3fd8f7000df4a0c82817536bdad198ea518bf164fb2787dffe4dd9df'
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
            'departement': region_code,
            'typeContrat': type_contrat
        }
        params = {k: v for k, v in params.items() if v}

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
        company_name = offer.get('entreprise', {}).get('nom', '').strip()
        clean_name = clean_company_name(company_name)
        logo = get_clearbit_logo(clean_name) if clean_name else ""

        formatted.append({
            "id": f"job{i}",
            "title": offer.get('intitule', ''),
            "company": company_name,
            "position": "Full-time",
            "salary": format_salary(offer.get('salaire', {})),
            "lat": offer.get('lieuTravail', {}).get('latitude', 0),
            "lng": offer.get('lieuTravail', {}).get('longitude', 0),
            "address": offer.get('lieuTravail', {}).get('libelle', ''),
            "type": offer.get('typeContrat', ''),
            "description": clean_description(offer.get('description', '')),
            "imageUrl": logo,
            "suburb": offer.get('lieuTravail', {}).get('commune', ''),
            "url": offer.get('origineOffre', {}).get('url', '')
        })

    return formatted

# --- ROUTES ---

@app.route("/api/jobs", methods=["POST"])
def search_jobs():
    try:
        data = request.get_json(force=True)
        keyword = data.get("keyword")
        filters = data.get("filters", {})

        type_contrat = filters.get("contrat")
        departement = filters.get("departement")

        jobs = get_france_travail_jobs(
            keyword=keyword,
            region_code=departement,
            type_contrat=type_contrat,
            max_results=100
        )

        return jsonify({"jobs": jobs}), 200

    except Exception as e:
        print("❌ Erreur backend :", str(e))
        return jsonify({"error": "Erreur interne du serveur"}), 500

# Optionnel : route GET simple pour test rapide
@app.route("/jobs", methods=["GET"])
def jobs_simple():
    return jsonify(get_france_travail_jobs(keyword="data", region_code="75", max_results=10))

# --- Lancement local ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
