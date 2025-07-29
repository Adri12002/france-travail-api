from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
import time
import json
from shapely.geometry import shape, Point
from typing import Optional, List

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Chargement unique des départements
with open("departements.geojson", encoding="utf-8") as f:
    DEPARTEMENTS = json.load(f)

class FilterModel(BaseModel):
    contrat: Optional[str] = None

class JobRequest(BaseModel):
    keyword: Optional[str]
    filters: Optional[FilterModel] = FilterModel()
    polygon: Optional[dict] = None

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

def get_france_travail_jobs(region_codes=None, keyword=None, type_contrat=None, max_results=100):
    auth_url = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
    client_id = "PAR_parisgo_439eedf0b21525284ed72cdd929becd8cc636adfeb3237bfb4602a885fdf89d5"
    client_secret = "4b5fd0f864dbc704487d192d3b8e43a57a2a263df8ea4d07369ab6b8690240d7"
    scope = 'api_offresdemploiv2 o2dsoffre'
    auth_payload = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
        'scope': scope
    }
    auth_headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    try:
        auth_response = requests.post(auth_url, headers=auth_headers, data=auth_payload)
        auth_response.raise_for_status()
        access_token = auth_response.json().get('access_token')
    except:
        return []

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json',
        'User-Agent': 'HabitaBot/1.0'
    }

    search_url = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
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
        except:
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

@app.post("/api/jobs")
async def search_jobs(request: JobRequest):
    keyword = request.keyword
    filters = request.filters or {}
    polygon = request.polygon
    type_contrat = filters.contrat if filters else None

    region_codes = get_departements_from_polygon(polygon) if polygon else None
    jobs = get_france_travail_jobs(
        keyword=keyword,
        region_codes=region_codes,
        type_contrat=type_contrat,
        max_results=750
    )

    if polygon:
        poly = shape(polygon)
        jobs = [job for job in jobs if poly.contains(Point(job["lng"], job["lat"]))]

    return {"jobs": jobs}
