"""Job Tracker — monitors job portals (LinkedIn, Foundit, Naukri) for user-defined searches."""
import json
import re
import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash)

from app_package import db
from app_package.models import (JobTracker, JobListing, AppSetting,
                                CandidateSearch, CandidateResult, Contact)

job_tracker_bp = Blueprint('job_tracker', __name__,
                           template_folder='../templates')

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Accept-Language': 'en-IN,en;q=0.9',
}

CATEGORY_CHOICES = [
    ('it', 'IT / Software'),
    ('marketing', 'Marketing'),
    ('sales', 'Sales'),
    ('finance', 'Finance / Accounting'),
    ('hr', 'HR / Recruiting'),
    ('design', 'Design / Creative'),
    ('engineering', 'Engineering'),
    ('healthcare', 'Healthcare'),
    ('education', 'Education'),
    ('other', 'Other'),
]

PLATFORM_CHOICES = [
    ('linkedin', 'LinkedIn'),
    ('foundit', 'Foundit (Monster)'),
    ('naukri', 'Naukri'),
    ('facebook', 'Facebook'),
    ('instagram', 'Instagram'),
    ('twitter', 'Twitter / X'),
    ('google_jobs', 'Google Jobs'),
]

JOB_TYPE_CHOICES = [
    ('', 'Any'),
    ('full-time', 'Full Time'),
    ('part-time', 'Part Time'),
    ('remote', 'Remote'),
    ('internship', 'Internship'),
    ('contract', 'Contract'),
]


# ---------------------------------------------------------------------------
#  Scraping helpers
# ---------------------------------------------------------------------------

def scrape_linkedin(query, city='mumbai', job_type=''):
    """Scrape LinkedIn public job search results."""
    listings = []
    encoded_query = quote_plus(query)
    encoded_city = quote_plus(city.title() + ', India')
    url = f'https://www.linkedin.com/jobs/search?keywords={encoded_query}&location={encoded_city}'

    if job_type == 'remote':
        url += '&f_WT=2'
    elif job_type == 'internship':
        url += '&f_E=1'

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning('LinkedIn request failed: %s', e)
        return listings

    soup = BeautifulSoup(resp.text, 'html.parser')

    cards = soup.select('.base-card, .job-search-card, .result-card')
    for card in cards[:30]:
        title_el = card.select_one('.base-search-card__title, h3')
        comp_el = card.select_one('.base-search-card__subtitle, h4')
        loc_el = card.select_one('.job-search-card__location')
        date_el = card.select_one('.job-search-card__listdate, time')
        link_el = card.find('a', href=True)

        title = title_el.get_text(strip=True) if title_el else ''
        if not title:
            continue

        company = comp_el.get_text(strip=True) if comp_el else ''
        location = loc_el.get_text(strip=True) if loc_el else ''
        posted = date_el.get_text(strip=True) if date_el else ''
        if not posted and date_el and date_el.get('datetime'):
            posted = date_el['datetime']

        href = link_el['href'].split('?')[0] if link_el else ''

        listings.append({
            'title': title,
            'company': company,
            'location': location,
            'salary': '',
            'experience': '',
            'url': href,
            'description': '',
            'posted_date': posted,
        })

    return listings


def scrape_foundit(query, city='mumbai'):
    """Scrape Foundit (Monster India) job results."""
    listings = []
    encoded = quote_plus(query)
    city_slug = city.lower().replace(' ', '-')
    url = f'https://www.foundit.in/srp/results?query={encoded}&locations={city_slug}'

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning('Foundit request failed: %s', e)
        return listings

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Foundit uses Next.js — try __NEXT_DATA__
    script = soup.find('script', id='__NEXT_DATA__')
    if script:
        try:
            data = json.loads(script.string)
            props = data.get('props', {}).get('pageProps', {})

            def find_jobs(obj):
                if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
                    keys = set(obj[0].keys())
                    job_keys = {'title', 'company', 'companyName', 'designation', 'jobId', 'jdURL'}
                    if keys & job_keys:
                        return obj
                if isinstance(obj, dict):
                    for v in obj.values():
                        result = find_jobs(v)
                        if result:
                            return result
                return None

            jobs = find_jobs(props)
            if jobs:
                for job in jobs[:30]:
                    title = job.get('title', job.get('designation', ''))
                    company = job.get('companyName', job.get('company', ''))
                    location = job.get('locations', job.get('location', ''))
                    if isinstance(location, list):
                        location = ', '.join(location)
                    salary = job.get('salary', job.get('ctc', ''))
                    exp = job.get('experience', job.get('exp', ''))
                    job_url = job.get('jdURL', job.get('url', ''))
                    if job_url and not job_url.startswith('http'):
                        job_url = 'https://www.foundit.in' + job_url

                    if title:
                        listings.append({
                            'title': title,
                            'company': company,
                            'location': str(location),
                            'salary': str(salary),
                            'experience': str(exp),
                            'url': job_url,
                            'description': job.get('description', job.get('snippet', '')),
                            'posted_date': job.get('postedDate', job.get('createdDate', '')),
                        })
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    return listings


def scrape_naukri(query, city='mumbai'):
    """Scrape Naukri job results."""
    listings = []
    query_slug = query.lower().replace(' ', '-')
    city_slug = city.lower().replace(' ', '-')
    url = f'https://www.naukri.com/{query_slug}-jobs-in-{city_slug}'

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning('Naukri request failed: %s', e)
        return listings

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Try __NEXT_DATA__
    script = soup.find('script', id='__NEXT_DATA__')
    if script:
        try:
            data = json.loads(script.string)
            props = data.get('props', {}).get('pageProps', {})

            def find_jobs(obj):
                if isinstance(obj, list) and len(obj) > 2 and isinstance(obj[0], dict):
                    keys = set(obj[0].keys())
                    job_keys = {'title', 'companyName', 'jdURL', 'jobId', 'designation', 'salary'}
                    if keys & job_keys:
                        return obj
                if isinstance(obj, dict):
                    for v in obj.values():
                        result = find_jobs(v)
                        if result:
                            return result
                return None

            jobs = find_jobs(props)
            if jobs:
                for job in jobs[:30]:
                    title = job.get('title', job.get('designation', ''))
                    company = job.get('companyName', '')
                    location = job.get('placeholders', [{}])
                    if isinstance(location, list):
                        loc_parts = [p.get('label', '') for p in location if p.get('type') == 'location']
                        location = ', '.join(loc_parts) if loc_parts else ''
                    salary = job.get('salary', '')
                    if isinstance(salary, dict):
                        salary = salary.get('label', '')
                    exp = job.get('experience', '')
                    if isinstance(exp, dict):
                        exp = exp.get('label', '')
                    job_url = job.get('jdURL', '')
                    if job_url and not job_url.startswith('http'):
                        job_url = 'https://www.naukri.com' + job_url

                    if title:
                        listings.append({
                            'title': title,
                            'company': str(company),
                            'location': str(location),
                            'salary': str(salary),
                            'experience': str(exp),
                            'url': job_url,
                            'description': job.get('jobDescription', ''),
                            'posted_date': job.get('footerPlaceholderLabel', ''),
                        })
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # Fallback: try HTML parsing
    if not listings:
        cards = soup.select('.srp-jobtuple-wrapper, .cust-job-tuple, article[class*=tuple]')
        for card in cards[:30]:
            title_el = card.select_one('.title, a.title, .desig')
            comp_el = card.select_one('.comp-name, .subTitle a, .companyInfo a')
            loc_el = card.select_one('.loc, .locWdth, .location')
            sal_el = card.select_one('.sal, .salary')
            exp_el = card.select_one('.exp, .experience')
            link_el = card.find('a', href=True)

            title = title_el.get_text(strip=True) if title_el else ''
            if not title:
                continue

            listings.append({
                'title': title,
                'company': comp_el.get_text(strip=True) if comp_el else '',
                'location': loc_el.get_text(strip=True) if loc_el else '',
                'salary': sal_el.get_text(strip=True) if sal_el else '',
                'experience': exp_el.get_text(strip=True) if exp_el else '',
                'url': link_el['href'] if link_el else '',
                'description': '',
                'posted_date': '',
            })

    return listings


def _scrape_serpapi_social(query, city, site_filter):
    """Scrape social-media job posts via SerpAPI Google search with site: operator."""
    api_key = AppSetting.get('serpapi_key', '')
    if not api_key:
        return []

    search_q = f'{query} hiring OR vacancy OR job OR recruitment {city} {site_filter}'
    params = {
        'api_key': api_key,
        'engine': 'google',
        'q': search_q,
        'num': 20,
        'hl': 'en',
        'gl': 'in',
    }

    try:
        resp = requests.get('https://serpapi.com/search', params=params, timeout=30)
        data = resp.json()
    except Exception as e:
        logger.warning('SerpAPI social search failed: %s', e)
        return []

    if 'error' in data:
        logger.warning('SerpAPI error: %s', data['error'])
        return []

    listings = []
    for item in data.get('organic_results', []):
        title = item.get('title', '').strip()
        link = item.get('link', '').strip()
        snippet = item.get('snippet', '')
        if not title or not link:
            continue
        listings.append({
            'title': title,
            'company': '',
            'location': city.title(),
            'salary': '',
            'experience': '',
            'url': link,
            'description': snippet,
            'posted_date': item.get('date', ''),
        })

    return listings


def scrape_facebook_jobs(query, city='mumbai'):
    """Find job posts on Facebook via SerpAPI."""
    return _scrape_serpapi_social(query, city, 'site:facebook.com')


def scrape_instagram_jobs(query, city='mumbai'):
    """Find job posts on Instagram via SerpAPI."""
    return _scrape_serpapi_social(query, city, 'site:instagram.com')


def scrape_twitter_jobs(query, city='mumbai'):
    """Find job posts on Twitter/X via SerpAPI."""
    return _scrape_serpapi_social(query, city, 'site:x.com OR site:twitter.com')


def scrape_google_jobs(query, city='mumbai'):
    """Scrape Google Jobs aggregator via SerpAPI google_jobs engine."""
    api_key = AppSetting.get('serpapi_key', '')
    if not api_key:
        return []

    params = {
        'api_key': api_key,
        'engine': 'google_jobs',
        'q': f'{query} {city}',
        'hl': 'en',
        'gl': 'in',
    }

    try:
        resp = requests.get('https://serpapi.com/search', params=params, timeout=30)
        data = resp.json()
    except Exception as e:
        logger.warning('SerpAPI Google Jobs failed: %s', e)
        return []

    if 'error' in data:
        logger.warning('SerpAPI error: %s', data['error'])
        return []

    listings = []
    for job in data.get('jobs_results', []):
        title = job.get('title', '').strip()
        if not title:
            continue

        # Extract salary and job type from detected_extensions
        extensions = job.get('detected_extensions', {})
        salary = extensions.get('salary', '')
        schedule = extensions.get('schedule_type', '')

        # Get apply link
        apply_options = job.get('apply_options', [])
        url = apply_options[0].get('link', '') if apply_options else ''

        listings.append({
            'title': title,
            'company': job.get('company_name', ''),
            'location': job.get('location', ''),
            'salary': salary,
            'experience': schedule,
            'url': url,
            'description': (job.get('description', '') or '')[:500],
            'posted_date': extensions.get('posted_at', ''),
        })

    return listings


def check_job_tracker(tracker):
    """Run a scrape for a single JobTracker. Returns list of newly saved JobListings."""
    platforms = json.loads(tracker.platforms) if tracker.platforms else ['linkedin']
    city = (tracker.city or 'Mumbai').strip()
    new_listings = []

    for platform in platforms:
        if platform == 'linkedin':
            raw = scrape_linkedin(tracker.search_query, city, tracker.job_type or '')
        elif platform == 'foundit':
            raw = scrape_foundit(tracker.search_query, city)
        elif platform == 'naukri':
            raw = scrape_naukri(tracker.search_query, city)
        elif platform == 'facebook':
            raw = scrape_facebook_jobs(tracker.search_query, city)
        elif platform == 'instagram':
            raw = scrape_instagram_jobs(tracker.search_query, city)
        elif platform == 'twitter':
            raw = scrape_twitter_jobs(tracker.search_query, city)
        elif platform == 'google_jobs':
            raw = scrape_google_jobs(tracker.search_query, city)
        else:
            continue

        for item in raw:
            item_url = item.get('url', '').strip()
            if not item_url:
                continue

            # Duplicate check
            exists = db.session.query(JobListing).filter_by(
                tracker_id=tracker.id, url=item_url
            ).first()
            if exists:
                continue

            listing = JobListing(
                tracker_id=tracker.id,
                title=item.get('title', '')[:500],
                company=item.get('company', '')[:300],
                location=item.get('location', '')[:200],
                salary=item.get('salary', '')[:200],
                experience=item.get('experience', '')[:100],
                url=item_url[:1000],
                platform=platform,
                description=item.get('description', ''),
                posted_date=item.get('posted_date', '')[:100],
                is_new=True,
            )
            db.session.add(listing)
            new_listings.append(listing)

    tracker.last_checked = datetime.now(timezone.utc)
    db.session.commit()
    return new_listings


def send_job_alert(tracker, new_listings):
    """Send WhatsApp alert for new job listings."""
    if not tracker.whatsapp_number or not new_listings:
        return

    phone = tracker.whatsapp_number.strip()
    if not phone.startswith('+'):
        phone = '+91' + phone.lstrip('0')

    lines = [f'\U0001f4bc Job Alert: {tracker.search_query}\n']
    lines.append(f'{len(new_listings)} new job(s) found in {tracker.city}:\n')
    for i, listing in enumerate(new_listings[:10], 1):
        lines.append(f'{i}. {listing.title}')
        if listing.company:
            lines.append(f'   \U0001f3e2 {listing.company}')
        if listing.location:
            lines.append(f'   \U0001f4cd {listing.location}')
        if listing.salary:
            lines.append(f'   \U0001f4b0 {listing.salary}')
        if listing.url:
            lines.append(f'   \U0001f517 {listing.url}')
        lines.append('')

    if len(new_listings) > 10:
        lines.append(f'... and {len(new_listings) - 10} more.')

    message = '\n'.join(lines)

    try:
        from app_package.routes.whatsapp_sender import send_whatsapp_pywhatkit, send_whatsapp_twilio
        wa_mode = AppSetting.get('whatsapp_mode', 'pywhatkit')
        if wa_mode == 'twilio':
            send_whatsapp_twilio(phone, message)
        else:
            send_whatsapp_pywhatkit(phone, message)
    except Exception as e:
        logger.error('Job alert WhatsApp failed: %s', e)


# ---------------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------------

@job_tracker_bp.route('/')
def dashboard():
    trackers = db.session.query(JobTracker).order_by(JobTracker.created_at.desc()).all()
    for t in trackers:
        t.total_listings = t.listings.count()
        t.new_listings = t.listings.filter_by(is_new=True).count()
    platform_names = [label for _, label in PLATFORM_CHOICES]
    return render_template('job_tracker/dashboard.html', trackers=trackers,
                           platform_names=platform_names)


@job_tracker_bp.route('/add', methods=['GET', 'POST'])
def add_tracker():
    if request.method == 'POST':
        platforms = request.form.getlist('platforms') or ['linkedin']
        tracker = JobTracker(
            search_query=request.form.get('search_query', '').strip(),
            category=request.form.get('category', 'other'),
            city=request.form.get('city', 'Mumbai').strip(),
            experience=request.form.get('experience', '').strip(),
            job_type=request.form.get('job_type', ''),
            platforms=json.dumps(platforms),
            whatsapp_number=request.form.get('whatsapp_number', '').strip(),
            is_active=True,
        )
        if not tracker.search_query:
            flash('Search query is required.', 'warning')
            return render_template('job_tracker/form.html', tracker=None,
                                   categories=CATEGORY_CHOICES,
                                   platforms=PLATFORM_CHOICES,
                                   job_types=JOB_TYPE_CHOICES)
        db.session.add(tracker)
        db.session.commit()
        flash(f'Job tracker "{tracker.search_query}" created.', 'success')
        return redirect(url_for('job_tracker.dashboard'))

    return render_template('job_tracker/form.html', tracker=None,
                           categories=CATEGORY_CHOICES,
                           platforms=PLATFORM_CHOICES,
                           job_types=JOB_TYPE_CHOICES)


@job_tracker_bp.route('/edit/<int:tracker_id>', methods=['GET', 'POST'])
def edit_tracker(tracker_id):
    tracker = db.session.get(JobTracker, tracker_id)
    if not tracker:
        flash('Tracker not found.', 'warning')
        return redirect(url_for('job_tracker.dashboard'))

    if request.method == 'POST':
        platforms = request.form.getlist('platforms') or ['linkedin']
        tracker.search_query = request.form.get('search_query', '').strip()
        tracker.category = request.form.get('category', 'other')
        tracker.city = request.form.get('city', 'Mumbai').strip()
        tracker.experience = request.form.get('experience', '').strip()
        tracker.job_type = request.form.get('job_type', '')
        tracker.platforms = json.dumps(platforms)
        tracker.whatsapp_number = request.form.get('whatsapp_number', '').strip()
        tracker.is_active = 'is_active' in request.form
        db.session.commit()
        flash(f'Job tracker "{tracker.search_query}" updated.', 'success')
        return redirect(url_for('job_tracker.dashboard'))

    return render_template('job_tracker/form.html', tracker=tracker,
                           categories=CATEGORY_CHOICES,
                           platforms=PLATFORM_CHOICES,
                           job_types=JOB_TYPE_CHOICES)


@job_tracker_bp.route('/delete/<int:tracker_id>', methods=['POST'])
def delete_tracker(tracker_id):
    tracker = db.session.get(JobTracker, tracker_id)
    if tracker:
        name = tracker.search_query
        db.session.delete(tracker)
        db.session.commit()
        flash(f'Job tracker "{name}" deleted.', 'success')
    return redirect(url_for('job_tracker.dashboard'))


@job_tracker_bp.route('/results/<int:tracker_id>')
def results(tracker_id):
    tracker = db.session.get(JobTracker, tracker_id)
    if not tracker:
        flash('Tracker not found.', 'warning')
        return redirect(url_for('job_tracker.dashboard'))

    page = request.args.get('page', 1, type=int)
    listings = (tracker.listings
                .order_by(JobListing.found_at.desc())
                .paginate(page=page, per_page=20, error_out=False))

    db.session.query(JobListing).filter_by(
        tracker_id=tracker_id, is_new=True
    ).update({'is_new': False})
    db.session.commit()

    return render_template('job_tracker/results.html',
                           tracker=tracker, listings=listings)


@job_tracker_bp.route('/check/<int:tracker_id>', methods=['POST'])
def check_single(tracker_id):
    tracker = db.session.get(JobTracker, tracker_id)
    if not tracker:
        flash('Tracker not found.', 'warning')
        return redirect(url_for('job_tracker.dashboard'))

    new = check_job_tracker(tracker)
    if new:
        send_job_alert(tracker, new)
        flash(f'Found {len(new)} new job(s) for "{tracker.search_query}".', 'success')
    else:
        flash(f'No new jobs found for "{tracker.search_query}".', 'info')

    return redirect(url_for('job_tracker.results', tracker_id=tracker_id))


@job_tracker_bp.route('/check-all', methods=['POST'])
def check_all():
    trackers = db.session.query(JobTracker).filter_by(is_active=True).all()
    total_new = 0
    for tracker in trackers:
        new = check_job_tracker(tracker)
        if new:
            send_job_alert(tracker, new)
            total_new += len(new)

    flash(f'Checked {len(trackers)} tracker(s). Found {total_new} new job(s) total.', 'success')
    return redirect(url_for('job_tracker.dashboard'))


# ---------------------------------------------------------------------------
#  SerpAPI — Candidate Search (LinkedIn, Facebook, Instagram)
# ---------------------------------------------------------------------------

CANDIDATE_PLATFORM_CHOICES = [
    ('linkedin', 'LinkedIn', 'bi-linkedin', '#0a66c2'),
    ('facebook', 'Facebook', 'bi-facebook', '#1877f2'),
    ('instagram', 'Instagram', 'bi-instagram', '#e4405f'),
]


def search_serpapi_people(query, location='Mumbai', num=10):
    """Search Google via SerpAPI for LinkedIn profiles matching the query."""
    api_key = AppSetting.get('serpapi_key', '')
    if not api_key:
        return [], 'SerpAPI key not configured. Go to Settings to add it.'

    search_q = f'{query} site:linkedin.com/in {location}'
    params = {
        'api_key': api_key,
        'engine': 'google',
        'q': search_q,
        'num': min(num, 40),
        'hl': 'en',
        'gl': 'in',
    }

    try:
        resp = requests.get('https://serpapi.com/search', params=params, timeout=30)
        data = resp.json()
    except Exception as e:
        logger.error('SerpAPI request failed: %s', e)
        return [], f'SerpAPI request failed: {e}'

    if 'error' in data:
        return [], data['error']

    results = []
    for item in data.get('organic_results', []):
        link = item.get('link', '')
        if 'linkedin.com/in/' not in link:
            continue

        # LinkedIn titles are like: "John Doe - Electrical Engineer - ABC Corp | LinkedIn"
        raw_title = item.get('title', '').replace(' | LinkedIn', '').replace(' - LinkedIn', '')
        parts = [p.strip() for p in raw_title.split(' - ') if p.strip()]

        name = parts[0] if parts else ''
        title = parts[1] if len(parts) > 1 else ''
        company = parts[2] if len(parts) > 2 else ''

        # Try to extract location from snippet
        snippet = item.get('snippet', '')
        loc = ''
        for loc_keyword in [location.title(), 'India', 'Mumbai', 'Delhi', 'Bangalore',
                            'Chennai', 'Pune', 'Hyderabad', 'Kolkata']:
            if loc_keyword.lower() in snippet.lower():
                loc = loc_keyword
                break

        results.append({
            'name': name,
            'title': title,
            'company': company,
            'location': loc,
            'linkedin_url': link.split('?')[0],  # Clean URL
            'snippet': snippet,
        })

    return results, None


def find_candidate_contact(name, company, location='', linkedin_url=''):
    """Find email/phone for a candidate ONLY from their LinkedIn profile data.

    Uses SerpAPI to fetch Google's cached/indexed version of the LinkedIn profile,
    which may contain contact info that LinkedIn showed publicly.
    Does NOT search random websites — that gives wrong contact details.
    """
    api_key = AppSetting.get('serpapi_key', '')
    if not api_key:
        return '', '', 'SerpAPI key not configured.'

    all_emails = []
    all_phones = []

    # Strategy 1: Search for the exact LinkedIn profile URL to get Google's cached snippet
    if linkedin_url:
        try:
            params = {
                'api_key': api_key,
                'engine': 'google',
                'q': f'"{linkedin_url}"',
                'num': 5,
                'hl': 'en',
                'gl': 'in',
            }
            resp = requests.get('https://serpapi.com/search', params=params, timeout=20)
            data = resp.json()

            for item in data.get('organic_results', []):
                link = item.get('link', '')
                snippet = item.get('snippet', '')
                # Only extract from the LinkedIn result itself or pages that mention this profile
                if 'linkedin.com' in link:
                    all_emails.extend(re.findall(
                        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', snippet))
                    all_phones.extend(re.findall(
                        r'(?:\+91[\s\-]?)?(?:\d[\s\-]?){10}', snippet))
        except Exception as e:
            logger.warning('LinkedIn profile search failed: %s', e)

    # Strategy 2: Search for the person's name specifically on LinkedIn
    if not all_emails and not all_phones:
        search_parts = [f'"{name}"', 'site:linkedin.com/in']
        if company:
            search_parts.append(f'"{company}"')
        search_parts.append('email OR phone OR contact OR mobile')

        try:
            params = {
                'api_key': api_key,
                'engine': 'google',
                'q': ' '.join(search_parts),
                'num': 5,
                'hl': 'en',
                'gl': 'in',
            }
            resp = requests.get('https://serpapi.com/search', params=params, timeout=20)
            data = resp.json()

            for item in data.get('organic_results', []):
                link = item.get('link', '')
                snippet = item.get('snippet', '')
                # ONLY extract from LinkedIn results
                if 'linkedin.com' not in link:
                    continue
                all_emails.extend(re.findall(
                    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', snippet))
                all_phones.extend(re.findall(
                    r'(?:\+91[\s\-]?)?(?:\d[\s\-]?){10}', snippet))
        except Exception as e:
            logger.warning('Candidate LinkedIn search failed: %s', e)

    # Filter junk emails (generic/institutional, not personal)
    junk_domains = [
        'example.com', 'sentry.', 'wixpress', 'schema.org',
        '.png', '.jpg', '.gif', 'linkedin.com', 'noreply',
    ]
    all_emails = [e for e in all_emails
                  if not any(x in e.lower() for x in junk_domains)]

    # Clean results
    email = all_emails[0] if all_emails else ''
    phone_raw = all_phones[0] if all_phones else ''
    phone = re.sub(r'[\s\-]', '', phone_raw) if phone_raw else ''
    if phone and (len(phone) < 10 or len(phone) > 13):
        phone = ''

    return email, phone, None


def find_candidate_facebook(name, company='', location=''):
    """Find a candidate's Facebook profile using SerpAPI Google search.

    Searches: "Name" site:facebook.com "Company/Location"
    Returns: (facebook_url, error_msg)
    """
    api_key = AppSetting.get('serpapi_key', '')
    if not api_key:
        return '', 'SerpAPI key not configured.'

    # Build targeted search — name in quotes for exact match
    parts = [f'"{name}"', 'site:facebook.com']
    if location:
        parts.append(location)
    if company:
        parts.append(f'"{company}"')
    search_q = ' '.join(parts)

    try:
        params = {
            'api_key': api_key,
            'engine': 'google',
            'q': search_q,
            'num': 5,
            'hl': 'en',
            'gl': 'in',
        }
        resp = requests.get('https://serpapi.com/search', params=params, timeout=20)
        data = resp.json()
    except Exception as e:
        return '', str(e)

    if 'error' in data:
        return '', data['error']

    # Look for facebook.com profile links
    for item in data.get('organic_results', []):
        link = item.get('link', '')
        title = item.get('title', '').lower()

        # Only match actual profile pages, not posts/groups/pages
        if 'facebook.com' not in link:
            continue

        # Skip non-profile URLs (groups, posts, pages, events, marketplace)
        skip_patterns = ['/groups/', '/posts/', '/events/', '/marketplace/',
                         '/watch/', '/pages/', '/hashtag/', '/stories/']
        if any(pat in link for pat in skip_patterns):
            continue

        # Check if the name appears in the title (basic verification)
        name_parts = name.lower().split()
        name_match = any(part in title for part in name_parts if len(part) > 2)

        if name_match:
            # Clean the URL
            fb_url = link.split('?')[0]
            return fb_url, None

    # If no exact name match, try first facebook profile result
    for item in data.get('organic_results', []):
        link = item.get('link', '')
        if 'facebook.com' not in link:
            continue
        skip_patterns = ['/groups/', '/posts/', '/events/', '/marketplace/',
                         '/watch/', '/pages/', '/hashtag/', '/stories/']
        if any(pat in link for pat in skip_patterns):
            continue
        return link.split('?')[0], None

    return '', None


def search_serpapi_facebook_people(query, location='Mumbai', num=10):
    """Search Google via SerpAPI for Facebook profiles matching the query."""
    api_key = AppSetting.get('serpapi_key', '')
    if not api_key:
        return [], 'SerpAPI key not configured. Go to Settings to add it.'

    search_q = f'{query} site:facebook.com {location}'
    params = {
        'api_key': api_key,
        'engine': 'google',
        'q': search_q,
        'num': min(num, 40),
        'hl': 'en',
        'gl': 'in',
    }

    try:
        resp = requests.get('https://serpapi.com/search', params=params, timeout=30)
        data = resp.json()
    except Exception as e:
        logger.error('SerpAPI Facebook search failed: %s', e)
        return [], f'SerpAPI request failed: {e}'

    if 'error' in data:
        return [], data['error']

    results = []
    skip_patterns = ['/groups/', '/posts/', '/events/', '/marketplace/',
                     '/watch/', '/pages/', '/hashtag/', '/stories/', '/reel/']
    for item in data.get('organic_results', []):
        link = item.get('link', '')
        if 'facebook.com' not in link:
            continue
        if any(pat in link for pat in skip_patterns):
            continue

        raw_title = item.get('title', '')
        # Facebook titles: "Name | Facebook" or "Name - City | Facebook"
        name = raw_title.replace(' | Facebook', '').replace(' - Facebook', '').strip()
        # Try to split "Name - Title" pattern
        parts = [p.strip() for p in name.split(' - ') if p.strip()]
        name = parts[0] if parts else name
        title = parts[1] if len(parts) > 1 else ''
        company = parts[2] if len(parts) > 2 else ''

        snippet = item.get('snippet', '')
        loc = ''
        for loc_keyword in [location.title(), 'India', 'Mumbai', 'Delhi', 'Bangalore',
                            'Chennai', 'Pune', 'Hyderabad', 'Kolkata']:
            if loc_keyword.lower() in snippet.lower():
                loc = loc_keyword
                break

        results.append({
            'name': name,
            'title': title,
            'company': company,
            'location': loc,
            'facebook_url': link.split('?')[0],
            'snippet': snippet,
        })

    return results, None


def search_serpapi_instagram_people(query, location='Mumbai', num=10):
    """Search Google via SerpAPI for Instagram profiles matching the query."""
    api_key = AppSetting.get('serpapi_key', '')
    if not api_key:
        return [], 'SerpAPI key not configured. Go to Settings to add it.'

    search_q = f'{query} site:instagram.com {location}'
    params = {
        'api_key': api_key,
        'engine': 'google',
        'q': search_q,
        'num': min(num, 40),
        'hl': 'en',
        'gl': 'in',
    }

    try:
        resp = requests.get('https://serpapi.com/search', params=params, timeout=30)
        data = resp.json()
    except Exception as e:
        logger.error('SerpAPI Instagram search failed: %s', e)
        return [], f'SerpAPI request failed: {e}'

    if 'error' in data:
        return [], data['error']

    results = []
    skip_patterns = ['/p/', '/reel/', '/stories/', '/explore/', '/tv/']
    for item in data.get('organic_results', []):
        link = item.get('link', '')
        if 'instagram.com' not in link:
            continue
        if any(pat in link for pat in skip_patterns):
            continue

        raw_title = item.get('title', '')
        # Instagram titles: "Name (@handle) - Instagram" or "Name (@handle) • Instagram photos and videos"
        name = (raw_title
                .replace(' - Instagram', '')
                .replace(' • Instagram photos and videos', '')
                .replace(' | Instagram', '')
                .strip())
        # Extract handle from parentheses
        handle_match = re.search(r'\(@?(\w+)\)', name)
        handle = handle_match.group(1) if handle_match else ''
        # Remove handle from name
        name = re.sub(r'\s*\(@?\w+\)\s*', '', name).strip()

        parts = [p.strip() for p in name.split(' - ') if p.strip()]
        name = parts[0] if parts else name
        title = parts[1] if len(parts) > 1 else (f'@{handle}' if handle else '')
        company = ''

        snippet = item.get('snippet', '')
        loc = ''
        for loc_keyword in [location.title(), 'India', 'Mumbai', 'Delhi', 'Bangalore',
                            'Chennai', 'Pune', 'Hyderabad', 'Kolkata']:
            if loc_keyword.lower() in snippet.lower():
                loc = loc_keyword
                break

        results.append({
            'name': name,
            'title': title,
            'company': company,
            'location': loc,
            'instagram_url': link.split('?')[0],
            'snippet': snippet,
        })

    return results, None


def find_candidate_contact_generic(name, company='', location=''):
    """Find email/phone for a non-LinkedIn candidate via generic Google search."""
    api_key = AppSetting.get('serpapi_key', '')
    if not api_key:
        return '', '', 'SerpAPI key not configured.'

    parts = [f'"{name}"']
    if company:
        parts.append(f'"{company}"')
    if location:
        parts.append(location)
    parts.append('email OR phone OR contact OR mobile')
    search_q = ' '.join(parts)

    all_emails = []
    all_phones = []

    try:
        params = {
            'api_key': api_key,
            'engine': 'google',
            'q': search_q,
            'num': 5,
            'hl': 'en',
            'gl': 'in',
        }
        resp = requests.get('https://serpapi.com/search', params=params, timeout=20)
        data = resp.json()

        for item in data.get('organic_results', []):
            snippet = item.get('snippet', '')
            all_emails.extend(re.findall(
                r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', snippet))
            all_phones.extend(re.findall(
                r'(?:\+91[\s\-]?)?(?:\d[\s\-]?){10}', snippet))
    except Exception as e:
        logger.warning('Generic contact search failed: %s', e)

    junk_domains = [
        'example.com', 'sentry.', 'wixpress', 'schema.org',
        '.png', '.jpg', '.gif', 'linkedin.com', 'noreply',
    ]
    all_emails = [e for e in all_emails
                  if not any(x in e.lower() for x in junk_domains)]

    email = all_emails[0] if all_emails else ''
    phone_raw = all_phones[0] if all_phones else ''
    phone = re.sub(r'[\s\-]', '', phone_raw) if phone_raw else ''
    if phone and (len(phone) < 10 or len(phone) > 13):
        phone = ''

    return email, phone, None


# ---------------------------------------------------------------------------
#  Candidate Search Routes
# ---------------------------------------------------------------------------

@job_tracker_bp.route('/candidates/')
def candidates_dashboard():
    """List all past candidate searches."""
    searches = (db.session.query(CandidateSearch)
                .order_by(CandidateSearch.created_at.desc())
                .all())
    # Parse platforms JSON for each search so template can display icons
    for s in searches:
        try:
            s.platform_list = json.loads(s.platforms) if s.platforms else ['linkedin']
        except (json.JSONDecodeError, TypeError):
            s.platform_list = ['linkedin']
    return render_template('job_tracker/candidates.html', searches=searches,
                           candidate_platforms=CANDIDATE_PLATFORM_CHOICES)


@job_tracker_bp.route('/candidates/search', methods=['GET', 'POST'])
def candidate_search():
    """Search for candidates on LinkedIn/Facebook/Instagram via SerpAPI."""
    if request.method == 'POST':
        query = request.form.get('search_query', '').strip()
        location = request.form.get('location', 'Mumbai').strip()
        num = int(request.form.get('num_results', '10'))
        platforms = request.form.getlist('platforms') or ['linkedin']

        if not query:
            flash('Search query is required.', 'warning')
            return render_template('job_tracker/candidate_search.html',
                                   candidate_platforms=CANDIDATE_PLATFORM_CHOICES)

        # Create search record
        search = CandidateSearch(
            search_query=query,
            location=location,
            num_results=num,
            platforms=json.dumps(platforms),
            status='pending',
        )
        db.session.add(search)
        db.session.commit()

        # Execute SerpAPI search for each selected platform
        all_errors = []
        saved = 0

        for plat in platforms:
            if plat == 'linkedin':
                results, error = search_serpapi_people(query, location, num)
            elif plat == 'facebook':
                results, error = search_serpapi_facebook_people(query, location, num)
            elif plat == 'instagram':
                results, error = search_serpapi_instagram_people(query, location, num)
            else:
                continue

            if error:
                all_errors.append(f'{plat}: {error}')
                continue

            for item in results:
                # Build the right URL field for dedup
                if plat == 'linkedin':
                    profile_url = item.get('linkedin_url', '')
                elif plat == 'facebook':
                    profile_url = item.get('facebook_url', '')
                elif plat == 'instagram':
                    profile_url = item.get('instagram_url', '')
                else:
                    profile_url = ''

                if not profile_url:
                    continue

                result = CandidateResult(
                    search_id=search.id,
                    name=item.get('name', '')[:300],
                    title=item.get('title', '')[:500],
                    company=item.get('company', '')[:300],
                    location=item.get('location', '')[:200],
                    linkedin_url=item.get('linkedin_url', '')[:1000],
                    facebook_url=item.get('facebook_url', '')[:1000],
                    instagram_url=item.get('instagram_url', '')[:1000],
                    snippet=item.get('snippet', ''),
                    platform=plat,
                )
                db.session.add(result)
                saved += 1

        if all_errors and saved == 0:
            search.status = 'failed'
            search.error_message = '; '.join(all_errors)
            db.session.commit()
            flash(f'Search failed: {"; ".join(all_errors)}', 'danger')
            return redirect(url_for('job_tracker.candidates_dashboard'))

        search.total_found = saved
        search.status = 'completed'
        if all_errors:
            search.error_message = '; '.join(all_errors)
        db.session.commit()

        platform_names = ', '.join(p.title() for p in platforms)
        flash(f'Found {saved} candidate(s) on {platform_names} for "{query}".', 'success')
        return redirect(url_for('job_tracker.candidate_results', search_id=search.id))

    return render_template('job_tracker/candidate_search.html',
                           candidate_platforms=CANDIDATE_PLATFORM_CHOICES)


@job_tracker_bp.route('/candidates/results/<int:search_id>')
def candidate_results(search_id):
    """View results for a candidate search."""
    search = db.session.get(CandidateSearch, search_id)
    if not search:
        flash('Search not found.', 'warning')
        return redirect(url_for('job_tracker.candidates_dashboard'))

    try:
        search.platform_list = json.loads(search.platforms) if search.platforms else ['linkedin']
    except (json.JSONDecodeError, TypeError):
        search.platform_list = ['linkedin']

    results = (search.results
               .order_by(CandidateResult.found_at.desc())
               .all())
    return render_template('job_tracker/candidate_results.html',
                           search=search, results=results,
                           candidate_platforms=CANDIDATE_PLATFORM_CHOICES)


@job_tracker_bp.route('/candidates/delete/<int:search_id>', methods=['POST'])
def delete_candidate_search(search_id):
    """Delete a candidate search and all its results."""
    search = db.session.get(CandidateSearch, search_id)
    if search:
        db.session.delete(search)
        db.session.commit()
        flash('Search deleted.', 'success')
    return redirect(url_for('job_tracker.candidates_dashboard'))


@job_tracker_bp.route('/candidates/save-contact/<int:result_id>', methods=['POST'])
def save_candidate_to_contacts(result_id):
    """Save a single candidate result to the main Contacts table."""
    result = db.session.get(CandidateResult, result_id)
    if not result:
        flash('Candidate not found.', 'warning')
        return redirect(url_for('job_tracker.candidates_dashboard'))

    # Check if already saved
    if result.is_saved:
        flash(f'{result.name} is already saved to Contacts.', 'info')
        return redirect(url_for('job_tracker.candidate_results',
                                search_id=result.search_id))

    # Check duplicate in Contacts by profile URL
    profile_url = result.profile_url
    existing = db.session.query(Contact).filter(
        Contact.website == profile_url
    ).first()
    if existing:
        result.is_saved = True
        db.session.commit()
        flash(f'{result.name} already exists in Contacts.', 'info')
        return redirect(url_for('job_tracker.candidate_results',
                                search_id=result.search_id))

    platform_label = (result.platform or 'linkedin').title()
    contact = Contact(
        company_name=result.company or result.name,
        contact_person=result.name,
        email=result.email or '',
        phone=result.phone or '',
        website=profile_url,
        city=result.location,
        category=f'{platform_label} Candidate',
        source='serpapi',
        status='new',
        notes=f'Title: {result.title}\n{result.snippet}',
    )
    db.session.add(contact)
    result.is_saved = True
    db.session.commit()
    flash(f'{result.name} saved to Contacts.', 'success')
    return redirect(url_for('job_tracker.candidate_results',
                            search_id=result.search_id))


@job_tracker_bp.route('/candidates/save-all/<int:search_id>', methods=['POST'])
def save_all_candidates(search_id):
    """Save all unsaved candidates from a search to Contacts."""
    search = db.session.get(CandidateSearch, search_id)
    if not search:
        flash('Search not found.', 'warning')
        return redirect(url_for('job_tracker.candidates_dashboard'))

    unsaved = search.results.filter_by(is_saved=False).all()
    saved_count = 0
    for result in unsaved:
        profile_url = result.profile_url
        existing = db.session.query(Contact).filter(
            Contact.website == profile_url
        ).first()
        if existing:
            result.is_saved = True
            continue

        platform_label = (result.platform or 'linkedin').title()
        contact = Contact(
            company_name=result.company or result.name,
            contact_person=result.name,
            email=result.email or '',
            phone=result.phone or '',
            website=profile_url,
            city=result.location,
            category=f'{platform_label} Candidate',
            source='serpapi',
            status='new',
            notes=f'Title: {result.title}\n{result.snippet}',
        )
        db.session.add(contact)
        result.is_saved = True
        saved_count += 1

    db.session.commit()
    flash(f'Saved {saved_count} candidate(s) to Contacts.', 'success')
    return redirect(url_for('job_tracker.candidate_results', search_id=search_id))


@job_tracker_bp.route('/candidates/find-contact/<int:result_id>', methods=['POST'])
def find_candidate_contact_details(result_id):
    """Find email/phone for a specific candidate."""
    result = db.session.get(CandidateResult, result_id)
    if not result:
        flash('Candidate not found.', 'warning')
        return redirect(url_for('job_tracker.candidates_dashboard'))

    if result.platform in ('facebook', 'instagram'):
        email, phone, error = find_candidate_contact_generic(
            result.name, result.company, result.location)
    else:
        email, phone, error = find_candidate_contact(
            result.name, result.company, result.location, result.linkedin_url)

    if error:
        flash(f'Could not find contact info: {error}', 'warning')
    elif not email and not phone:
        flash(f'No contact details found for {result.name}. Try visiting their profile directly.', 'info')
    else:
        if email:
            result.email = email
        if phone:
            result.phone = phone
        db.session.commit()
        found = []
        if email:
            found.append(f'email: {email}')
        if phone:
            found.append(f'phone: {phone}')
        flash(f'Found {", ".join(found)} for {result.name}', 'success')

    return redirect(url_for('job_tracker.candidate_results',
                            search_id=result.search_id))


@job_tracker_bp.route('/candidates/find-all-contacts/<int:search_id>', methods=['POST'])
def find_all_candidate_contacts(search_id):
    """Find contact details for all candidates in a search."""
    search = db.session.get(CandidateSearch, search_id)
    if not search:
        flash('Search not found.', 'warning')
        return redirect(url_for('job_tracker.candidates_dashboard'))

    results = search.results.filter(
        (CandidateResult.email == '') | (CandidateResult.email == None)
    ).all()

    found_count = 0
    for result in results:
        if result.platform in ('facebook', 'instagram'):
            email, phone, error = find_candidate_contact_generic(
                result.name, result.company, result.location)
        else:
            email, phone, error = find_candidate_contact(
                result.name, result.company, result.location, result.linkedin_url)
        if email:
            result.email = email
            found_count += 1
        if phone:
            result.phone = phone

    db.session.commit()
    flash(f'Found contact details for {found_count} out of {len(results)} candidate(s).', 'success')
    return redirect(url_for('job_tracker.candidate_results', search_id=search_id))


@job_tracker_bp.route('/candidates/find-facebook/<int:result_id>', methods=['POST'])
def find_candidate_facebook_profile(result_id):
    """Find Facebook profile for a specific candidate."""
    result = db.session.get(CandidateResult, result_id)
    if not result:
        flash('Candidate not found.', 'warning')
        return redirect(url_for('job_tracker.candidates_dashboard'))

    fb_url, error = find_candidate_facebook(
        result.name, result.company, result.location)

    if error:
        flash(f'Could not search Facebook: {error}', 'warning')
    elif not fb_url:
        flash(f'No Facebook profile found for {result.name}.', 'info')
    else:
        result.facebook_url = fb_url
        db.session.commit()
        flash(f'Found Facebook for {result.name}: {fb_url}', 'success')

    return redirect(url_for('job_tracker.candidate_results',
                            search_id=result.search_id))


@job_tracker_bp.route('/candidates/find-all-facebook/<int:search_id>', methods=['POST'])
def find_all_candidate_facebook(search_id):
    """Find Facebook profiles for all candidates in a search."""
    search = db.session.get(CandidateSearch, search_id)
    if not search:
        flash('Search not found.', 'warning')
        return redirect(url_for('job_tracker.candidates_dashboard'))

    results = search.results.filter(
        (CandidateResult.facebook_url == '') | (CandidateResult.facebook_url == None)
    ).all()

    found_count = 0
    for result in results:
        fb_url, error = find_candidate_facebook(
            result.name, result.company, result.location)
        if fb_url:
            result.facebook_url = fb_url
            found_count += 1

    db.session.commit()
    flash(f'Found Facebook profiles for {found_count} out of {len(results)} candidate(s).', 'success')
    return redirect(url_for('job_tracker.candidate_results', search_id=search_id))


@job_tracker_bp.route('/candidates/export/<int:search_id>')
def export_candidates_csv(search_id):
    """Export candidates to CSV."""
    import csv
    import io
    from flask import Response

    search = db.session.get(CandidateSearch, search_id)
    if not search:
        flash('Search not found.', 'warning')
        return redirect(url_for('job_tracker.candidates_dashboard'))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Name', 'Title', 'Company', 'Location', 'Email', 'Phone',
                      'Platform', 'LinkedIn URL', 'Facebook URL', 'Instagram URL', 'Snippet'])

    for r in search.results.all():
        writer.writerow([r.name, r.title, r.company, r.location, r.email or '', r.phone or '',
                         r.platform or 'linkedin', r.linkedin_url, r.facebook_url or '',
                         r.instagram_url or '', r.snippet])

    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = (
        f'attachment; filename=candidates_{search.search_query.replace(" ", "_")}_{search_id}.csv'
    )
    return response
