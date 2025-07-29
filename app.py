from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from shapely.geometry import shape, Point
import requests, time, os, json, logging

# Configuration du logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("habita-api")

# Création de l'application FastAPI
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Chargement des départements une seule fois
with open("departements.geojson", encoding="utf-8") as f:
    DEPARTEMENTS = json.load(f)

# Modèles Pydantic pour la requête entrante
class Filters(BaseModel):
    contrat: Optional[str] = None

class JobSearchRequest(BaseModel):
    keyword: str
    filters: Optional[Filters] = Filters()
    polygon: Optional[Dict[str, Any]] = None

# Fonctions utilitaires
def get_departements_from_polygon(geojson_polygon):
    polygon = shape(geojson_polygon)
    codes = []
    for feature in DEPARTEMENTS["features"]:
        dept_shape = shape(feature["geometry"])
        if polygon.intersects(dept_shape):
            code = feature["properties"]["code"]
            codes.append(code)
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
    return f"{libelle} ({complement})" if libelle and complement else libelle or complement or "Non précisé"

def clean_description(desc):
    if not desc:
        return ""
    desc = desc.replace('<br />', '\n').replace('<p>', '\n').replace('</p>', '')
    return ' '.join(desc.split())

def get_token():
    auth_url = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
    client_id = "PAR_parisgo_439eedf0b21525284ed72cdd929becd8cc636adfeb3237bfb4602a885fdf89d5"
    client_secret = "4b5fd0f864dbc704487d192d3b8e43a57a2a263df8ea4d07369ab6b8690240d7"
    scope = 'api_offresdemploiv2 o2dsoffre'
    auth_payload = f'grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}&scope={scope}'
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    response = requests.post(auth_url, headers=headers, data=auth_payload)
    if response.status_code != 200:
        logger.error("Erreur d'authentification FT : %s", response.text)
        return None
    return response.json().get('access_token')

def fetch_offers(token, region_codes, keyword, type_contrat, max_results=150):
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json',
        'User-Agent': 'HabitaBot/1.0'
    }
    url = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
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

        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            logger.warning("Erreur FT status %s : %s", response.status_code, response.text)
            break
        data = response.json()
        offers = data.get('resultats', [])
        if not offers:
            break
        all_offers.extend(offers)
        range_start += range_size
        if len(offers) < range_size or len(all_offers) >= max_results:
            break
        time.sleep(0.5)

    return all_offers[:max_results]

# Route principale
@app.post("/api/jobs")
async def search_jobs(payload: JobSearchRequest):
    keyword = payload.keyword
    type_contrat = payload.filters.contrat if payload.filters else None
    region_codes = get_departements_from_polygon(payload.polygon) if payload.polygon else None

    token = get_token()
    if not token:
        return {"jobs": []}

    offers = fetch_offers(token, region_codes, keyword, type_contrat)

    # Format final
    jobs = []
    poly = shape(payload.polygon) if payload.polygon else None
    for i, offer in enumerate(offers, 1):
        lat = offer.get('lieuTravail', {}).get('latitude')
        lon = offer.get('lieuTravail', {}).get('longitude')
        if not lat or not lon:
            continue
        if poly and not poly.contains(Point(lon, lat)):
            continue

        jobs.append({
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

    logger.info("✅ %d offres envoyées", len(jobs))
    return {"jobs": jobs}
