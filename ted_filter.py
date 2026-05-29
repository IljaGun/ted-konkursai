import requests
import anthropic
import csv
import os
import time
import json
import re
from datetime import datetime, timedelta

# --- KONFIGURACIJA ---
TED_API_KEY = os.environ['TED_API_KEY']
ANTHROPIC_API_KEY = os.environ['ANTHROPIC_API_KEY']
RESULTS_FILE = 'results.csv'

CPV_CODES = [
    '44210000', '44211000', '44212000', '44212100', '44212300', '44212400',
    '34510000', '34512000', '34513000', '34513200', '34513400',
    '34514000', '34515000', '34520000', '34521000', '34521400',
    '34931100', '34931200', '34931300', '34953000', '34953100',
    '45221100', '45221110', '45221111', '45221112', '45221113',
    '44611000', '44611200', '44611400', '44611600',
    '45223100', '45223110', '45223200', '45223210', '45223220',
]

COMPANY_PROFILE = """
Steel fabrication company in Kaunas, Lithuania.
- Manufactures NON-STANDARD steel structures (black steel / carbon steel ONLY)
- Products: pontoons, floating platforms, bridges, walkways, gangways, ramps, steel reservoirs, steel tanks, steel platforms, mezzanines
- Standards: EN 1090-1/2/3 or EN ISO 3834-2
- Minimum contract value: 100,000 EUR
- Can DELIVER anywhere in Europe
- NO on-site installation capability
- Subcontracting of steel fabrication work is preferred

REJECT IMMEDIATELY if ANY of these apply:
- Notice type is Result/Award (contract already awarded)
- Notice type is Planning/Prior information only
- Notice type is Consultation/Market engagement
- Requires on-site construction or installation work at the project location
- Aluminium, stainless steel, or non-carbon-steel materials required
- Standard catalogue products (containers, prefab boxes, standard shelving)
- Contract value clearly below 100,000 EUR
- Design-only or engineering services without manufacturing
- Supply from stock / trading / distribution only
- Rental or hire of equipment
- Chemical reagents, paints, hardware supplies, tools
- Windows, doors, lifts, elevators, HVAC, electrical, plumbing
- Building construction or renovation works
- Medical, laboratory, or scientific equipment

ACCEPT only if ALL of these are true:
- Active Competition/Contract notice (open tender, not yet awarded)
- Requires manufacturing or fabrication of custom steel structures
- Products match: pontoons, platforms, bridges, gangways, ramps, tanks, reservoirs, steel walkways, steel mezzanines, structural steel assemblies
- Delivery of fabricated product is sufficient (no installation required OR installation is optional/subcontractable)
- Contract value >= 100,000 EUR or unknown/not specified
"""

def get_ted_notices():
    query = ' OR '.join([f'PC={code}' for code in CPV_CODES])
    date_from = (datetime.now() - timedelta(days=2)).strftime('%Y%m%d')
    date_to = datetime.now().strftime('%Y%m%d')
    headers = {
        'Authorization': f'Bearer {TED_API_KEY}',
        'Content-Type': 'application/json'
    }
    body = {
        'query': f'({query}) AND PD>={date_from} AND PD<={date_to}',
        'fields': [
            'publication-number',
            'notice-type',
            'BT-821-Lot',
            'organisation-country-buyer',
            'tendering-party-name',
            'result-framework-maximum-value-cur-notice',
            'BT-13(t)-Part',
            'BT-21-Procedure'
        ],
        'limit': 100,
        'page': 1,
        'scope': 'ALL',
        'onlyLatestVersions': True
    }
    try:
        r = requests.post(
            'https://api.ted.europa.eu/v3/notices/search',
            headers=headers, json=body, timeout=30
        )
        if r.status_code == 200:
            return r.json().get('notices', [])
        print(f'TED API klaida: {r.status_code} {r.text[:200]}')
    except Exception as e:
        print(f'TED API exception: {e}')
    return []

def get_notice_content(publication_number):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }
    try:
        url = f'https://ted.europa.eu/en/notice/{publication_number}/pdf'
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            # Ištraukiame tekstą iš PDF
            from pdfminer.high_level import extract_text_to_fp
            from pdfminer.layout import LAParams
            import io
            output = io.StringIO()
            extract_text_to_fp(io.BytesIO(r.content), output, laparams=LAParams())
            text = output.getvalue()
            text = re.sub(r'\s+', ' ', text).strip()
            print(f'  PDF tekstas: {len(text)} simboliu')
            return text[:4000]
        print(f'PDF klaida {publication_number}: {r.status_code}')
    except Exception as e:
        print(f'PDF exception {publication_number}: {e}')
    return ''
    
def analyze_with_ai(notice, content=''):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    pub = notice.get('publication-number', 'N/A')
    links = notice.get('links', {})
    url = links.get('html', {}).get('ENG', f'https://ted.europa.eu/en/notice/{pub}/html')
    full_content = f"""
Publication: {pub}
URL: {url}
Notice JSON: {json.dumps(notice)[:800]}
Notice full text: {content}
"""
    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        messages=[{
            'role': 'user',
            'content': f"""{COMPANY_PROFILE}

Analyze this tender carefully against the REJECT and ACCEPT criteria above.
Respond ONLY in this exact format:
DECISION: YES or NO
REASON: (one sentence explaining the key reason)
VALUE: (EUR amount or UNKNOWN)
COUNTRY: (country name)
TITLE: (short tender title)
URL: {url}

Tender data:
{full_content}"""
        }]
    )
    return msg.content[0].text, url

def save_results(results):
    file_exists = os.path.isfile(RESULTS_FILE)
    with open(RESULTS_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Publication', 'Title', 'Country', 'Value', 'Reason', 'URL'])
        for r in results:
            writer.writerow([
                datetime.now().strftime('%Y-%m-%d'),
                r['publication_number'],
                r['title'],
                r['country'],
                r['value'],
                r['reason'],
                r['url']
            ])
    print(f'Issaugota {len(results)} irasu i {RESULTS_FILE}')

def main():
    print(f'Pradedama: {datetime.now()}')
    notices = get_ted_notices()
    print(f'Rasta {len(notices)} nauju konkursu')

    yes_results = []
    for i, notice in enumerate(notices):
        pub = notice.get('publication-number', 'N/A')
        print(f'{i+1}/{len(notices)}: {pub}')
        content = get_notice_content(pub)
        if content:
            print(f'  Turinys: {len(content)} simboliu')
        else:
            print(f'  Turinys: TUSCIAS')
        ai_response, url = analyze_with_ai(notice, content)
        if 'DECISION: YES' in ai_response:
            lines = ai_response.split('\n')
            yes_results.append({
                'publication_number': pub,
                'title': next((l.replace('TITLE:', '').strip() for l in lines if l.startswith('TITLE:')), 'N/A'),
                'country': next((l.replace('COUNTRY:', '').strip() for l in lines if l.startswith('COUNTRY:')), 'N/A'),
                'value': next((l.replace('VALUE:', '').strip() for l in lines if l.startswith('VALUE:')), 'N/A'),
                'reason': next((l.replace('REASON:', '').strip() for l in lines if l.startswith('REASON:')), 'N/A'),
                'url': url
            })
            print(f'  -> TINKA')
        else:
            reason_line = next((l for l in ai_response.split('\n') if l.startswith('REASON:')), '')
            print(f'  -> Netinka: {reason_line}')
        time.sleep(0.5)

    print(f'Tinkamu: {len(yes_results)}')
    if yes_results:
        save_results(yes_results)
    print('Baigta.')

if __name__ == '__main__':
    main()
