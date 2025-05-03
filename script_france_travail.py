import requests
import json
import time

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

def get_france_travail_jobs(region_code=None, keyword=None, max_results=100):
    auth_url = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
    client_id = 'PAR_parisgo_02ee6a6b30b8ee2045ade6e947fb9e8b91703dcb90afcc760e78a4a9aa1c1edd'
    client_secret = 'f019350d3fd8f7000df4a0c82817536bdad198ea518bf164fb2787dffe4dd9df'
    scope = 'api_offresdemploiv2 o2dsoffre'
    auth_payload = f'grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}&scope={scope}'
    auth_headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    try:
        auth_response = requests.post(auth_url, headers=auth_headers, data=auth_payload)
        auth_response.raise_for_status()
        auth_data = auth_response.json()
        access_token = auth_data['access_token']
    except Exception as e:
        print(f"Erreur d'authentification: {e}")
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
            'departement': region_code
        }
        params = {k: v for k, v in params.items() if v is not None}

        try:
            search_response = requests.get(search_url, headers=headers, params=params)
            search_response.raise_for_status()
            data = search_response.json()
            offers = data.get('resultats', [])
            if not offers:
                break
            all_offers.extend(offers)
            range_start += range_size
            if len(offers) < range_size or len(all_offers) >= max_results:
                break
            time.sleep(0.5)
        except Exception as e:
            print(f"Erreur lors de la récupération des offres: {e}")
            break

    formatted_jobs = []
    for i, offer in enumerate(all_offers[:max_results], start=1):
        company_name = offer.get('entreprise', {}).get('nom', '').strip()
        clean_company = clean_company_name(company_name)
        image_url = get_clearbit_logo(clean_company) if clean_company else ""

        contract_type = offer.get('typeContrat', 'CDI')
        position_type = "Full-time"
        if 'CDD' in contract_type:
            position_type = "Contract"
        elif 'partiel' in contract_type.lower():
            position_type = "Part-time"

        formatted_job = {
            "id": f"job{i}",
            "title": offer.get('intitule', ''),
            "company": company_name,
            "position": position_type,
            "salary": format_salary(offer.get('salaire', {})),
            "lat": offer.get('lieuTravail', {}).get('latitude', 0),
            "lng": offer.get('lieuTravail', {}).get('longitude', 0),
            "address": offer.get('lieuTravail', {}).get('libelle', ''),
            "type": position_type,
            "description": clean_description(offer.get('description', '')),
            "imageUrl": image_url,
            "suburb": offer.get('lieuTravail', {}).get('commune', ''),
            "url": offer.get('origineOffre', {}).get('url', '')
        }
        formatted_jobs.append(formatted_job)

    return formatted_jobs
