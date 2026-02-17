import re
import requests
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify)
from bs4 import BeautifulSoup
from app_package import db
from app_package.models import Contact, SearchLog, AppSetting

search_bp = Blueprint('search', __name__)

SEARCH_TEMPLATES = [
    'spice exporters India contact email',
    'poultry suppliers India email phone',
    'masala manufacturers India contact',
    'food processing companies India email',
    'wholesale suppliers India contact details',
    'exporters India directory email',
    'manufacturers India email phone',
    'traders suppliers India contact',
    'distributors India email',
    'B2B suppliers India contact details',
]


def google_search(query, api_key, cse_id, num=10):
    """Search using Google Custom Search API."""
    url = 'https://www.googleapis.com/customsearch/v1'
    params = {'key': api_key, 'cx': cse_id, 'q': query, 'num': min(num, 10)}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get('items', []):
            results.append({
                'title': item.get('title', ''),
                'link': item.get('link', ''),
                'snippet': item.get('snippet', ''),
            })
        return results, None
    except Exception as e:
        return [], str(e)


def serpapi_search(query, api_key, num=10):
    """Search using SerpAPI (Google Search)."""
    try:
        from serpapi import GoogleSearch
        params = {
            "q": query,
            "api_key": api_key,
            "engine": "google",
            "num": num,
            "gl": "in",
            "hl": "en",
        }
        search = GoogleSearch(params)
        data = search.get_dict()
        results = []
        for item in data.get("organic_results", []):
            results.append({
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            })
        return results, None
    except Exception as e:
        return [], str(e)


def extract_contacts_from_url(url):
    """Scrape a webpage for contact info (emails, phones)."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'lxml')
        text = soup.get_text(separator=' ')

        # Extract emails
        emails = list(set(re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', text)))
        # Filter out common non-contact emails
        emails = [e for e in emails if not any(x in e.lower() for x in
                  ['example.com', 'sentry.', 'wixpress', 'schema.org', '.png', '.jpg'])]

        # Extract phone numbers (Indian format)
        phones = list(set(re.findall(r'(?:\+91[\s\-]?)?(?:\d[\s\-]?){10}', text)))
        phones = [re.sub(r'[\s\-]', '', p) for p in phones]
        phones = [p for p in phones if len(p) >= 10 and len(p) <= 13]

        # Try to get company name from title
        title = soup.title.string.strip() if soup.title and soup.title.string else ''

        return {
            'emails': emails[:5],
            'phones': phones[:5],
            'title': title,
        }
    except Exception:
        return {'emails': [], 'phones': [], 'title': ''}


def check_duplicate(company_name, email=''):
    """Check if contact already exists."""
    if email:
        existing = db.session.query(Contact).filter(
            (Contact.company_name == company_name) | (Contact.email == email)
        ).first()
    else:
        existing = db.session.query(Contact).filter_by(company_name=company_name).first()
    return existing is not None


@search_bp.route('/')
def search_page():
    recent_searches = db.session.query(SearchLog).order_by(SearchLog.created_at.desc()).limit(10).all()
    serpapi_key = AppSetting.get('serpapi_key', '')
    google_key = AppSetting.get('google_api_key', '')
    google_cse = AppSetting.get('google_cse_id', '')
    return render_template('search/index.html',
                           templates=SEARCH_TEMPLATES,
                           recent_searches=recent_searches,
                           has_serpapi=bool(serpapi_key),
                           has_google=bool(google_key and google_cse))


@search_bp.route('/run', methods=['POST'])
def run_search():
    query = request.form.get('query', '').strip()
    if not query:
        flash('Enter a search query.', 'error')
        return redirect(url_for('search.search_page'))

    engine = request.form.get('search_engine', 'serpapi')
    serpapi_key = AppSetting.get('serpapi_key', '')
    google_api_key = AppSetting.get('google_api_key', '')
    google_cse_id = AppSetting.get('google_cse_id', '')

    search_results = []
    error = None
    source = 'google_api'

    if engine == 'serpapi' and serpapi_key:
        search_results, error = serpapi_search(query, serpapi_key)
        source = 'serpapi'
    elif engine == 'google' and google_api_key and google_cse_id:
        search_results, error = google_search(query, google_api_key, google_cse_id)
        source = 'google_api'
    elif serpapi_key:
        search_results, error = serpapi_search(query, serpapi_key)
        source = 'serpapi'
    elif google_api_key and google_cse_id:
        search_results, error = google_search(query, google_api_key, google_cse_id)
        source = 'google_api'
    else:
        flash('No search API configured. Add a SerpAPI key or Google API key in Settings.', 'warning')
        return redirect(url_for('search.search_page'))

    if error:
        flash(f'Search error ({source}): {error}', 'error')
        return redirect(url_for('search.search_page'))

    results = []
    for sr in search_results:
        scraped = extract_contacts_from_url(sr['link'])
        company_name = sr['title'].split(' - ')[0].split(' | ')[0].strip()[:200]
        email = scraped['emails'][0] if scraped['emails'] else ''

        results.append({
            'company_name': company_name,
            'website': sr['link'],
            'snippet': sr['snippet'],
            'emails': scraped['emails'],
            'phones': scraped['phones'],
            'email': email,
            'phone': scraped['phones'][0] if scraped['phones'] else '',
            'is_duplicate': check_duplicate(company_name, email),
        })

    # Log search
    log = SearchLog(query=query, source=source, results_count=len(results))
    db.session.add(log)
    db.session.commit()

    return render_template('search/results.html',
                           query=query, results=results, search_log_id=log.id,
                           source=source)


@search_bp.route('/save', methods=['POST'])
def save_contacts():
    search_log_id = request.form.get('search_log_id')
    search_source = request.form.get('search_source', 'google_search')
    selected = request.form.getlist('selected')

    if not selected:
        flash('No contacts selected.', 'error')
        return redirect(url_for('search.search_page'))

    saved = 0
    for idx in selected:
        company = request.form.get(f'company_{idx}', '').strip()
        email = request.form.get(f'email_{idx}', '').strip()
        phone = request.form.get(f'phone_{idx}', '').strip()
        website = request.form.get(f'website_{idx}', '').strip()

        if not company:
            continue

        if check_duplicate(company, email):
            continue

        contact = Contact(
            company_name=company,
            email=email,
            phone=phone,
            website=website,
            source=search_source,
            category='Other'
        )
        db.session.add(contact)
        saved += 1

    # Update search log
    if search_log_id:
        log = db.session.get(SearchLog, int(search_log_id))
        if log:
            log.contacts_saved = saved

    db.session.commit()
    flash(f'Saved {saved} new contacts.', 'success')
    return redirect(url_for('contacts.list_contacts'))
