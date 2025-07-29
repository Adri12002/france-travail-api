from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import json
from shapely.geometry import shape, Point
from typing import Optional, List, Dict, Any
import asyncio
import httpx

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
    # L'appel HTTP est lent, ne le fais que côté frontend idéalement !
    return f"https://logo.clearbit.com/{company_name}.com?size=150" if company_name else ""

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

async def get_access_token():
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
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(auth_url, headers=headers, data=auth_payload)
        r.raise_for_status()
        access_token = r.json().get('access_token')
    return access_token

async def fetch_department_jobs(
    dept_code: str, 
    access_token: str, 
    keyword: Optional[str], 
    type_contrat: Optional[str], 
    per_dept_max: int = 200
) -> List[Dict[str, Any]]:
    search_url = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json',
        'User-Agent': 'HabitaBot/1.0'
    }
    range_start = 0
    range_size = 100
    offers = []
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            params = {
                'range': f'{range_start}-{range_start + range_size - 1}',
                'motsCles': keyword,
                'typeContrat': type_contrat,
                'departement': dept_code
            }
            r = await client.get(search_url, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            batch = data.get('resultats', [])
            if not batch:
                break
            offers.extend(batch)
            if len(batch) < range_size or len(offers) >= per_dept_max:
                break
            range_start += range_size
            await asyncio.sleep(0.2)  # pour éviter le flood de l'API
    return offers[:per_dept_max]

async def get_france_travail_jobs(region_codes=None, keyword=None, type_contrat=None, max_results=800):
    access_token = await get_access_token()
    # si pas de région = tout france (rare et très lent)
    if not region_codes:
        region_codes = [None]

    # Lancer toutes les requêtes de département en parallèle
    tasks = [
        fetch_department_jobs(dept, access_token, keyword, type_contrat, per_dept_max=250)
        for dept in region_codes
    ]
    results = await asyncio.gather(*tasks)
    all_offers = [offer for batch in results for offer in batch]

    # Déduplication par URL d'origine ou id
    seen = set()
    deduped = []
    for offer in all_offers:
        url = offer.get('origineOffre', {}).get('url') or offer.get('id')
        if url and url not in seen:
            seen.add(url)
            deduped.append(offer)

    # Format final
    jobs = []
    for i, offer in enumerate(deduped[:max_results], 1):
        lat = offer.get('lieuTravail', {}).get('latitude')
        lon = offer.get('lieuTravail', {}).get('longitude')
        if not lat or not lon:
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
    return jobs

@app.post("/api/jobs")
async def search_jobs(request: JobRequest):
    keyword = request.keyword
    filters = request.filters or {}
    polygon = request.polygon
    type_contrat = filters.contrat if filters else None

    region_codes = get_departements_from_polygon(polygon) if polygon else None
    jobs = await get_france_travail_jobs(
        keyword=keyword,
        region_codes=region_codes,
        type_contrat=type_contrat,
        max_results=900
    )

    if polygon:
        poly = shape(polygon)
        jobs = [job for job in jobs if poly.contains(Point(job["lng"], job["lat"]))]

    return {"jobs": jobs}
