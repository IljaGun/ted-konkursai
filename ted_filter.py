import requests
import anthropic
import csv
import os
import time
import json
from datetime import datetime, timedelta

# --- KONFIGURACIJA ---
TED_API_KEY = os.environ['TED_API_KEY']
ANTHROPIC_API_KEY = os.environ['ANTHROPIC_API_KEY']
RESULTS_FILE = 'results.csv'

CPV_CODES = [
    '44210000', '44212000', '44212100', '44212300', '44212400',
    '34510000', '34512000', '34513000', '34513200', '34513400',
    '34514000', '34515000', '34520000', '34521000', '34521400',
    '34931100', '34931200', '34931300', '34953000', '34953100',
    '43320000', '43321000', '44200000', '44211000',
    '44316400', '44316500', '44615000', '44615100'
]

COMPANY_PROFILE = """
Steel fabrication company in Kaunas, Lithuania.
- Manufactures NON-STANDARD steel structures (black steel only)
- Products: pontoons, bridges, platforms, gangways, ramps, reservoirs
- Standards: EN 1090-1/2/3 or EN ISO 3834-2
- Minimum contract value: 100,000 EUR
- Can DELIVER anywhere in Europe, NO on-site installation
- Subcontracting preferred

REJECT if:
- Requires on-site installation at location
- Aluminium, stainless steel, or non-black-steel materials
- Standard catalogue products (containers, boxes)
- Contract value below 100,000 EUR
- Design-only without manufacturing
- Supply from stock / trading only
"""

def get_ted_notices():
    query = ' OR '.join([f'PC={code}' for code in CPV_CODES])
    yesterday = (datetime.now() - timedelta(days=2)).strftime('%Y%m%d')
    today = datetime.now().strftime('%Y%m%d')
    headers = {
        'Authorization': f'Bearer {TED_API_KEY}',
        'Content-Type': 'application/json'
    }
    body = {
        'query': f'({query}) AND PD>={yesterday} AND PD<={today}',
        'fields': [
            'BT-821-Lot',
            'organisation-country-buyer',
            'tendering-party-name',
            'result-framework-maximum-value-cur-notice',
            'BT-13(t)-Part'
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

def get_notice_xml(publication_number):
    headers = {
        'Authorization': f'Bearer {TED_API_KEY}',
        'User-Agent': 'Mozilla/5.0 (compatible; TED-Monitor/1.0)'
    }
    try:
        url = f'https://ted.europa.eu/udl?uri=TED:NOTICE:{publication_number}:DATA:EN:XML'
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return r.text[:4000]
    except Exception as e:
        print(f'XML klaida {publication_number}: {e}')
    return ''

def analyze_with_ai(notice, xml_content=''):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    pub = notice.get('publication-number', 'N/A')
    links = notice.get('links', {})
    url = links.get('html', {}).get('ENG', f'https://ted.europa.eu/en/notice/-/detail/{pub}')
    content = f"""
Publication: {pub}
URL: {url}
Notice data: {json.dumps(notice)[:500]}
XML: {xml_content[:2000]}
"""
    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        messages=[{
            'role': 'user',
            'content': f"""{COMPANY_PROFILE}

Analyze this tender. Respond ONLY:
DECISION: YES or NO
REASON: (one sentence)
VALUE: (EUR or UNKNOWN)
COUNTRY: (country)
TITLE: (title)
URL: {url}

Tender:
{content}"""
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
        xml = get_notice_xml(pub)
        ai_response, url = analyze_with_ai(notice, xml)
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
            print(f'  -> Netinka')
        time.sleep(0.3)

    print(f'Tinkamu: {len(yes_results)}')
    if yes_results:
        save_results(yes_results)
    print('Baigta.')

if __name__ == '__main__':
    main()
