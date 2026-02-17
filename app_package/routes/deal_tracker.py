"""Deal Tracker — monitors classifieds (CarDekho, OLX, Quikr) for user-defined searches."""
import json
import re
import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, current_app)

from app_package import db
from app_package.models import (DealTracker, DealListing, AppSetting,
                                EnquirySearch, EnquiryResult)

deal_tracker_bp = Blueprint('deal_tracker', __name__,
                            template_folder='../templates')

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Accept-Language': 'en-IN,en;q=0.9',
}

CATEGORY_CHOICES = [
    ('cars', 'Cars'),
    ('bikes', 'Bikes'),
    ('electronics', 'Electronics'),
    ('furniture', 'Furniture'),
    ('phones', 'Phones'),
    ('other', 'Other'),
]

PLATFORM_CHOICES = [
    ('cardekho', 'CarDekho'),
    ('olx', 'OLX'),
    ('quikr', 'Quikr'),
    ('serpapi', 'SerpAPI (Recommended)'),
]

ENQUIRY_PLATFORM_CHOICES = [
    ('facebook', 'Facebook'),
    ('instagram', 'Instagram'),
    ('twitter', 'Twitter / X'),
]

# CarDekho URL slug mappings
CARDEKHO_CITY_SLUGS = {
    'mumbai': 'mumbai', 'delhi': 'new-delhi', 'new delhi': 'new-delhi',
    'bangalore': 'bangalore', 'bengaluru': 'bangalore',
    'chennai': 'chennai', 'hyderabad': 'hyderabad', 'pune': 'pune',
    'kolkata': 'kolkata', 'ahmedabad': 'ahmedabad', 'jaipur': 'jaipur',
    'lucknow': 'lucknow', 'kochi': 'kochi', 'trivandrum': 'trivandrum',
    'chandigarh': 'chandigarh', 'indore': 'indore', 'nagpur': 'nagpur',
    'coimbatore': 'coimbatore', 'goa': 'goa', 'surat': 'surat',
    'thane': 'thane', 'noida': 'noida', 'gurgaon': 'gurgaon',
    # States
    'kerala': 'kerala', 'maharashtra': 'maharashtra', 'karnataka': 'karnataka',
    'tamil nadu': 'tamil-nadu', 'tamilnadu': 'tamil-nadu',
    'rajasthan': 'rajasthan', 'gujarat': 'gujarat', 'punjab': 'punjab',
    'uttar pradesh': 'uttar-pradesh', 'madhya pradesh': 'madhya-pradesh',
    'west bengal': 'west-bengal', 'telangana': 'telangana',
    'andhra pradesh': 'andhra-pradesh',
}

# Kerala cities for location filtering
KERALA_CITIES = {
    'thiruvananthapuram', 'trivandrum', 'kochi', 'ernakulam', 'kozhikode',
    'calicut', 'thrissur', 'kollam', 'palakkad', 'alappuzha', 'kannur',
    'kottayam', 'malappuram', 'pathanamthitta', 'idukki', 'wayanad',
    'kasaragod', 'kattappana', 'perumbavoor', 'angamaly', 'aluva',
    'changanassery', 'thodupuzha', 'munnar', 'guruvayur', 'kodungallur',
}

# Map state names to their known cities (for filtering)
STATE_CITIES = {
    'kerala': KERALA_CITIES,
}


# ---------------------------------------------------------------------------
#  Scraping helpers
# ---------------------------------------------------------------------------

def _is_location_match(location, city_input):
    """Check if a listing's location matches the user's requested city/state."""
    if not location:
        return True  # no location info, include by default
    loc_lower = location.lower().replace('-', ' ')
    city_lower = city_input.lower().strip()

    # Direct city match
    if city_lower in loc_lower or loc_lower in city_lower:
        return True

    # State-level: check if listing city is within the requested state
    state_cities = STATE_CITIES.get(city_lower)
    if state_cities:
        return loc_lower in state_cities or any(c in loc_lower for c in state_cities)

    return False


def scrape_cardekho(query, city='mumbai', category='cars'):
    """Scrape CarDekho used-car/bike search results."""
    listings = []
    city_slug = CARDEKHO_CITY_SLUGS.get(city.lower(), city.lower().replace(' ', '-'))
    query_slug = query.lower().replace(' ', '-')

    # Build URL based on category
    if category in ('cars', 'other'):
        url = f'https://www.cardekho.com/used-{query_slug}-cars+in+{city_slug}'
    elif category == 'bikes':
        url = f'https://www.cardekho.com/used-bikes/{query_slug}+in+{city_slug}'
    else:
        # For non-vehicle categories, CarDekho won't help
        return listings

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 404:
            # Try generic search URL
            encoded = quote_plus(query)
            url = f'https://www.cardekho.com/used-cars+in+{city_slug}/{encoded}'
            resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning('CarDekho request failed: %s', e)
        return listings

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Parse listing links
    link_els = soup.select('a[href*="/used-car-details/"]')
    if not link_els:
        link_els = soup.select('a[href*="/used-bike-details/"]')

    for a in link_els[:30]:
        title = a.get_text(strip=True)
        if not title or len(title) < 5:
            continue

        href = a.get('href', '')
        if href.startswith('/'):
            href = 'https://www.cardekho.com' + href

        # Walk up to find enclosing card for price/image
        card = a.parent
        for _ in range(4):
            if card and card.parent and card.parent.name not in ('body', 'html', '[document]'):
                card = card.parent

        # Extract price from card text
        card_text = card.get_text(' ', strip=True) if card else ''
        price = ''
        price_match = re.search(r'(?:Rs?\s*|Rs\.\s*|\u20b9\s*)[\d,.]+\s*(?:Lakh|lakh)?', card_text)
        if price_match:
            price = price_match.group(0)

        # Extract location from URL
        location = ''
        loc_match = re.search(r'cars-([A-Za-z-]+?)_', href)
        if loc_match:
            location = loc_match.group(1).replace('-', ' ').title()

        # Filter: only include if location matches requested city/state
        if not _is_location_match(location, city):
            continue

        # Get image
        img = a.find('img') or (card.find('img') if card else None)
        image_url = ''
        if img:
            image_url = img.get('src', '') or img.get('data-src', '')

        listings.append({
            'title': title,
            'price': price,
            'location': location or city.title(),
            'url': href,
            'image_url': image_url,
            'description': '',
        })

    return listings


def scrape_olx(query, city='mumbai'):
    """Scrape OLX India search results."""
    listings = []
    encoded = quote_plus(query)
    url = f'https://www.olx.in/items/q-{encoded}'
    params = {}
    if city:
        params['city'] = city.lower()

    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning('OLX request failed: %s', e)
        return listings

    soup = BeautifulSoup(resp.text, 'html.parser')

    # OLX renders listing cards as <li> with data-aut-id="itemBox"
    cards = soup.select('[data-aut-id="itemBox"]')
    if not cards:
        # Fallback: try parsing embedded JSON __NEXT_DATA__
        script = soup.find('script', id='__NEXT_DATA__')
        if script:
            try:
                data = json.loads(script.string)
                items = (data.get('props', {})
                             .get('pageProps', {})
                             .get('initialData', {})
                             .get('data', []))
                for item in items[:30]:
                    title = item.get('title', '')
                    price_obj = item.get('price', {})
                    price_val = price_obj.get('value', {}).get('display', '') if isinstance(price_obj, dict) else ''
                    loc = item.get('locations_resolved', {})
                    location_name = ''
                    if isinstance(loc, dict):
                        location_name = loc.get('ADMIN_LEVEL_3_name', '') or loc.get('ADMIN_LEVEL_1_name', '')
                    item_url = 'https://www.olx.in/item/' + item.get('id', '') + '-' + re.sub(r'\s+', '-', title.lower()[:60])
                    image = ''
                    images = item.get('images', [])
                    if images:
                        image = images[0].get('url', '') if isinstance(images[0], dict) else str(images[0])
                    listings.append({
                        'title': title,
                        'price': price_val,
                        'location': location_name,
                        'url': item_url,
                        'image_url': image,
                        'description': item.get('description', ''),
                    })
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        return listings

    for card in cards[:30]:
        title_el = card.select_one('[data-aut-id="itemTitle"]')
        price_el = card.select_one('[data-aut-id="itemPrice"]')
        loc_el = card.select_one('[data-aut-id="item-location"]')
        link_el = card.find('a', href=True)
        img_el = card.find('img', src=True)

        title = title_el.get_text(strip=True) if title_el else ''
        price = price_el.get_text(strip=True) if price_el else ''
        location = loc_el.get_text(strip=True) if loc_el else ''
        item_url = ('https://www.olx.in' + link_el['href']) if link_el else ''
        image = img_el['src'] if img_el else ''

        if title:
            listings.append({
                'title': title,
                'price': price,
                'location': location,
                'url': item_url,
                'image_url': image,
                'description': '',
            })

    return listings


def scrape_quikr(query, city='mumbai', category='other'):
    """Scrape Quikr search results."""
    listings = []
    encoded = quote_plus(query)
    cat_map = {
        'cars': 'Cars', 'bikes': 'Bikes', 'electronics': 'Electronics',
        'furniture': 'Furniture', 'phones': 'Mobiles', 'other': '',
    }
    cat_slug = cat_map.get(category, '')
    # Quikr's working URL pattern: /Category/w-city?q=search
    if cat_slug:
        url = f'https://www.quikr.com/{cat_slug}/w-{city.lower()}?q={encoded}'
    else:
        url = f'https://www.quikr.com/search?city={city.lower()}&q={encoded}'

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning('Quikr request failed: %s', e)
        return listings

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Quikr listing cards
    cards = soup.select('.snb-tile, .list-view-item, [data-testid="listing-card"]')
    for card in cards[:30]:
        title_el = card.select_one('.prod-title, .snb-tile-title, h2 a, .title a')
        price_el = card.select_one('.price, .snb-tile-price, .product-price')
        link_el = card.find('a', href=True)
        img_el = card.find('img')
        loc_el = card.select_one('.location, .snb-tile-location, .loc')

        title = title_el.get_text(strip=True) if title_el else ''
        price = price_el.get_text(strip=True) if price_el else ''
        location = loc_el.get_text(strip=True) if loc_el else ''
        item_url = link_el['href'] if link_el else ''
        if item_url and not item_url.startswith('http'):
            item_url = 'https://www.quikr.com' + item_url
        image = ''
        if img_el:
            image = img_el.get('src', '') or img_el.get('data-src', '')

        if title:
            listings.append({
                'title': title,
                'price': price,
                'location': location,
                'url': item_url,
                'image_url': image,
                'description': '',
            })

    return listings


def scrape_serpapi(query, city='mumbai', category='other'):
    """Use SerpAPI Google Search to find OLX/Quikr/classifieds listings.

    This is the most reliable method because Google already indexes these sites,
    so we bypass JavaScript rendering and bot-blocking entirely.
    """
    serpapi_key = AppSetting.get('serpapi_key', '')
    if not serpapi_key:
        logger.warning('SerpAPI key not configured')
        return []

    listings = []

    # Build search queries — include city in query text (NOT in location param,
    # SerpAPI location param requires exact IDs and causes 400 errors)
    site_queries = [
        f'{query} {city} used buy site:olx.in OR site:quikr.com',
        f'{query} {city} used price buy sell',
    ]

    for sq in site_queries:
        try:
            params = {
                'engine': 'google',
                'q': sq,
                'gl': 'in',
                'hl': 'en',
                'num': 15,
                'api_key': serpapi_key,
            }
            resp = requests.get('https://serpapi.com/search', params=params, timeout=20)
            if resp.status_code != 200:
                logger.warning('SerpAPI returned %s for query: %s', resp.status_code, sq)
                continue

            data = resp.json()
            if 'error' in data:
                logger.warning('SerpAPI error: %s', data['error'])
                continue

            # Parse organic results
            for result in data.get('organic_results', []):
                title = result.get('title', '')
                link = result.get('link', '')
                snippet = result.get('snippet', '')

                if not title or not link:
                    continue

                # Extract price from title or snippet
                price = ''
                combined = f'{title} {snippet}'
                price_match = re.search(
                    r'(?:Rs?\s*\.?\s*|INR\s*|\u20b9\s*)[\d,]+(?:\s*(?:Lakh|lakh|L|Cr|cr))?',
                    combined
                )
                if price_match:
                    price = price_match.group(0).strip()

                # Extract location from snippet
                location = ''
                if city.lower() in snippet.lower() or city.lower() in title.lower():
                    location = city.title()

                # Detect platform from URL
                platform_tag = 'serpapi'
                if 'olx.in' in link:
                    platform_tag = 'olx'
                elif 'quikr.com' in link:
                    platform_tag = 'quikr'
                elif 'cardekho.com' in link:
                    platform_tag = 'cardekho'

                # Get thumbnail
                image_url = result.get('thumbnail', '')

                listings.append({
                    'title': title[:500],
                    'price': price,
                    'location': location or city.title(),
                    'url': link,
                    'image_url': image_url,
                    'description': snippet[:500] if snippet else '',
                    'platform_override': platform_tag,
                })

        except Exception as e:
            logger.warning('SerpAPI scrape failed for "%s": %s', sq, e)
            continue

    # Deduplicate by URL
    seen_urls = set()
    unique = []
    for item in listings:
        if item['url'] not in seen_urls:
            seen_urls.add(item['url'])
            unique.append(item)

    return unique


def check_tracker(tracker):
    """Run a scrape for a single DealTracker. Returns list of newly saved DealListings."""
    platforms = json.loads(tracker.platforms) if tracker.platforms else ['serpapi']
    city = (tracker.city or 'Mumbai').strip().lower()
    new_listings = []

    for platform in platforms:
        if platform == 'serpapi':
            raw = scrape_serpapi(tracker.search_query, city, tracker.category)
        elif platform == 'cardekho':
            raw = scrape_cardekho(tracker.search_query, city, tracker.category)
        elif platform == 'olx':
            raw = scrape_olx(tracker.search_query, city)
        elif platform == 'quikr':
            raw = scrape_quikr(tracker.search_query, city, tracker.category)
        else:
            continue

        for item in raw:
            item_url = item.get('url', '').strip()
            if not item_url:
                continue

            # Duplicate check
            exists = db.session.query(DealListing).filter_by(
                tracker_id=tracker.id, url=item_url
            ).first()
            if exists:
                continue

            # Price filter (extract numeric value in lakhs or absolute)
            if tracker.min_price or tracker.max_price:
                price_text = item.get('price', '')
                numeric = re.sub(r'[^\d.]', '', price_text)
                if numeric:
                    val = float(numeric)
                    # If price mentions "Lakh", convert to absolute
                    if 'lakh' in price_text.lower():
                        val = val * 100000
                    val = int(val)
                    if tracker.min_price and val < tracker.min_price:
                        continue
                    if tracker.max_price and val > tracker.max_price:
                        continue

            # SerpAPI results may override platform based on actual URL
            actual_platform = item.get('platform_override', platform)

            listing = DealListing(
                tracker_id=tracker.id,
                title=item.get('title', '')[:500],
                price=item.get('price', '')[:100],
                location=item.get('location', '')[:200],
                url=item_url[:1000],
                image_url=item.get('image_url', '')[:1000],
                platform=actual_platform,
                description=item.get('description', ''),
                is_new=True,
            )
            db.session.add(listing)
            new_listings.append(listing)

    tracker.last_checked = datetime.now(timezone.utc)
    db.session.commit()
    return new_listings


def send_deal_alert(tracker, new_listings):
    """Send WhatsApp alert for new deal listings."""
    if not tracker.whatsapp_number or not new_listings:
        return

    phone = tracker.whatsapp_number.strip()
    if not phone.startswith('+'):
        phone = '+91' + phone.lstrip('0')

    lines = [f'\U0001f514 Deal Alert: {tracker.search_query}\n']
    lines.append(f'{len(new_listings)} new listing(s) found in {tracker.city}:\n')
    for i, listing in enumerate(new_listings[:10], 1):
        lines.append(f'{i}. {listing.title} - {listing.price}')
        if listing.location:
            lines.append(f'   \U0001f4cd {listing.location}')
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
        logger.error('Deal alert WhatsApp failed: %s', e)


# ---------------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------------

@deal_tracker_bp.route('/')
def dashboard():
    trackers = db.session.query(DealTracker).order_by(DealTracker.created_at.desc()).all()
    # Attach stats
    for t in trackers:
        t.total_listings = t.listings.count()
        t.new_listings = t.listings.filter_by(is_new=True).count()
    return render_template('deal_tracker/dashboard.html', trackers=trackers)


@deal_tracker_bp.route('/add', methods=['GET', 'POST'])
def add_tracker():
    if request.method == 'POST':
        platforms = request.form.getlist('platforms') or ['serpapi']
        tracker = DealTracker(
            search_query=request.form.get('search_query', '').strip(),
            category=request.form.get('category', 'other'),
            city=request.form.get('city', 'Mumbai').strip(),
            min_price=int(request.form['min_price']) if request.form.get('min_price') else None,
            max_price=int(request.form['max_price']) if request.form.get('max_price') else None,
            platforms=json.dumps(platforms),
            whatsapp_number=request.form.get('whatsapp_number', '').strip(),
            is_active=True,
        )
        if not tracker.search_query:
            flash('Search query is required.', 'warning')
            return render_template('deal_tracker/form.html', tracker=None,
                                   categories=CATEGORY_CHOICES, platforms=PLATFORM_CHOICES)
        db.session.add(tracker)
        db.session.commit()
        flash(f'Tracker "{tracker.search_query}" created.', 'success')
        return redirect(url_for('deal_tracker.dashboard'))

    return render_template('deal_tracker/form.html', tracker=None,
                           categories=CATEGORY_CHOICES, platforms=PLATFORM_CHOICES)


@deal_tracker_bp.route('/edit/<int:tracker_id>', methods=['GET', 'POST'])
def edit_tracker(tracker_id):
    tracker = db.session.get(DealTracker, tracker_id)
    if not tracker:
        flash('Tracker not found.', 'warning')
        return redirect(url_for('deal_tracker.dashboard'))

    if request.method == 'POST':
        platforms = request.form.getlist('platforms') or ['cardekho']
        tracker.search_query = request.form.get('search_query', '').strip()
        tracker.category = request.form.get('category', 'other')
        tracker.city = request.form.get('city', 'Mumbai').strip()
        tracker.min_price = int(request.form['min_price']) if request.form.get('min_price') else None
        tracker.max_price = int(request.form['max_price']) if request.form.get('max_price') else None
        tracker.platforms = json.dumps(platforms)
        tracker.whatsapp_number = request.form.get('whatsapp_number', '').strip()
        tracker.is_active = 'is_active' in request.form
        db.session.commit()
        flash(f'Tracker "{tracker.search_query}" updated.', 'success')
        return redirect(url_for('deal_tracker.dashboard'))

    return render_template('deal_tracker/form.html', tracker=tracker,
                           categories=CATEGORY_CHOICES, platforms=PLATFORM_CHOICES)


@deal_tracker_bp.route('/delete/<int:tracker_id>', methods=['POST'])
def delete_tracker(tracker_id):
    tracker = db.session.get(DealTracker, tracker_id)
    if tracker:
        name = tracker.search_query
        db.session.delete(tracker)
        db.session.commit()
        flash(f'Tracker "{name}" deleted.', 'success')
    return redirect(url_for('deal_tracker.dashboard'))


@deal_tracker_bp.route('/results/<int:tracker_id>')
def results(tracker_id):
    tracker = db.session.get(DealTracker, tracker_id)
    if not tracker:
        flash('Tracker not found.', 'warning')
        return redirect(url_for('deal_tracker.dashboard'))

    page = request.args.get('page', 1, type=int)
    listings = (tracker.listings
                .order_by(DealListing.found_at.desc())
                .paginate(page=page, per_page=20, error_out=False))

    # Mark viewed listings as not new
    db.session.query(DealListing).filter_by(
        tracker_id=tracker_id, is_new=True
    ).update({'is_new': False})
    db.session.commit()

    return render_template('deal_tracker/results.html',
                           tracker=tracker, listings=listings)


@deal_tracker_bp.route('/check/<int:tracker_id>', methods=['POST'])
def check_single(tracker_id):
    tracker = db.session.get(DealTracker, tracker_id)
    if not tracker:
        flash('Tracker not found.', 'warning')
        return redirect(url_for('deal_tracker.dashboard'))

    new = check_tracker(tracker)
    if new:
        send_deal_alert(tracker, new)
        flash(f'Found {len(new)} new listing(s) for "{tracker.search_query}".', 'success')
    else:
        flash(f'No new listings found for "{tracker.search_query}".', 'info')

    return redirect(url_for('deal_tracker.results', tracker_id=tracker_id))


@deal_tracker_bp.route('/check-all', methods=['POST'])
def check_all():
    trackers = db.session.query(DealTracker).filter_by(is_active=True).all()
    total_new = 0
    for tracker in trackers:
        new = check_tracker(tracker)
        if new:
            send_deal_alert(tracker, new)
            total_new += len(new)

    flash(f'Checked {len(trackers)} tracker(s). Found {total_new} new listing(s) total.', 'success')
    return redirect(url_for('deal_tracker.dashboard'))


# ---------------------------------------------------------------------------
#  Product Enquiries — social media buying-intent search via SerpAPI
# ---------------------------------------------------------------------------

def search_social_enquiries(query, platforms=None, location=''):
    """Search Facebook, Instagram, Twitter for buying-intent posts via SerpAPI.

    Builds site-specific Google queries to find posts where people are looking
    to buy a product / looking for suppliers.
    """
    serpapi_key = AppSetting.get('serpapi_key', '')
    if not serpapi_key:
        logger.warning('SerpAPI key not configured')
        return []

    if platforms is None:
        platforms = ['facebook', 'instagram', 'twitter']

    site_map = {
        'facebook': 'site:facebook.com',
        'instagram': 'site:instagram.com',
        'twitter': 'site:twitter.com OR site:x.com',
    }

    # Build site filter string
    site_parts = [site_map[p] for p in platforms if p in site_map]
    if not site_parts:
        return []
    site_filter = ' OR '.join(site_parts)

    buying_keywords = 'looking for OR need OR want to buy OR supplier OR where to buy OR recommend'
    search_query = f'{query} ({buying_keywords}) ({site_filter})'
    if location:
        search_query = f'{query} {location} ({buying_keywords}) ({site_filter})'

    results = []

    try:
        params = {
            'engine': 'google',
            'q': search_query,
            'gl': 'in',
            'hl': 'en',
            'num': 30,
            'api_key': serpapi_key,
        }
        resp = requests.get('https://serpapi.com/search', params=params, timeout=25)
        if resp.status_code != 200:
            logger.warning('SerpAPI returned %s for enquiry query', resp.status_code)
            return []

        data = resp.json()
        if 'error' in data:
            logger.warning('SerpAPI error: %s', data['error'])
            return []

        for item in data.get('organic_results', []):
            title = item.get('title', '')
            link = item.get('link', '')
            snippet = item.get('snippet', '')

            if not title or not link:
                continue

            # Detect platform from URL
            platform = 'other'
            link_lower = link.lower()
            if 'facebook.com' in link_lower:
                platform = 'facebook'
            elif 'instagram.com' in link_lower:
                platform = 'instagram'
            elif 'twitter.com' in link_lower or 'x.com' in link_lower:
                platform = 'twitter'

            # Only keep results from requested platforms
            if platform not in platforms and platform != 'other':
                continue

            # Extract author from title (social posts often have "Name - Post")
            author = ''
            if ' - ' in title:
                author = title.split(' - ')[0].strip()
            elif ' | ' in title:
                author = title.split(' | ')[0].strip()
            elif ' on ' in title.lower():
                parts = title.lower().split(' on ')
                if len(parts) >= 2:
                    author = title[:title.lower().index(' on ')].strip()

            # Extract date from snippet if present
            posted_date = ''
            date_match = re.search(
                r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4})',
                snippet, re.IGNORECASE
            )
            if date_match:
                posted_date = date_match.group(1)

            results.append({
                'title': title[:500],
                'snippet': snippet[:1000] if snippet else '',
                'url': link,
                'platform': platform,
                'author': author[:300],
                'posted_date': posted_date,
                'image_url': item.get('thumbnail', ''),
            })

    except Exception as e:
        logger.warning('Social enquiry search failed: %s', e)

    # Deduplicate by URL
    seen = set()
    unique = []
    for r in results:
        if r['url'] not in seen:
            seen.add(r['url'])
            unique.append(r)

    return unique


@deal_tracker_bp.route('/enquiries')
def enquiries():
    searches = db.session.query(EnquirySearch).order_by(
        EnquirySearch.created_at.desc()
    ).all()
    for s in searches:
        s.result_count = s.results.count()
    return render_template('deal_tracker/enquiries.html', searches=searches,
                           platforms=ENQUIRY_PLATFORM_CHOICES)


@deal_tracker_bp.route('/enquiries/search', methods=['GET', 'POST'])
def enquiry_search():
    if request.method == 'POST':
        query = request.form.get('search_query', '').strip()
        if not query:
            flash('Search query is required.', 'warning')
            return render_template('deal_tracker/enquiry_form.html',
                                   platforms=ENQUIRY_PLATFORM_CHOICES)

        platforms = request.form.getlist('platforms') or ['facebook', 'instagram', 'twitter']
        location = request.form.get('location', '').strip()

        # Create search record
        search = EnquirySearch(
            search_query=query,
            platforms=json.dumps(platforms),
            location=location,
            status='pending',
        )
        db.session.add(search)
        db.session.commit()

        # Run the search
        try:
            raw_results = search_social_enquiries(query, platforms, location)

            for item in raw_results:
                # Duplicate check within this search
                exists = db.session.query(EnquiryResult).filter_by(
                    search_id=search.id, url=item['url']
                ).first()
                if exists:
                    continue

                result = EnquiryResult(
                    search_id=search.id,
                    title=item['title'],
                    snippet=item['snippet'],
                    url=item['url'],
                    platform=item['platform'],
                    author=item['author'],
                    posted_date=item['posted_date'],
                    image_url=item.get('image_url', ''),
                )
                db.session.add(result)

            search.total_found = len(raw_results)
            search.status = 'completed'
            db.session.commit()

            flash(f'Found {len(raw_results)} social media post(s) for "{query}".', 'success')

        except Exception as e:
            search.status = 'failed'
            search.error_message = str(e)[:500]
            db.session.commit()
            logger.error('Enquiry search failed: %s', e)
            flash(f'Search failed: {e}', 'danger')

        return redirect(url_for('deal_tracker.enquiry_results', search_id=search.id))

    return render_template('deal_tracker/enquiry_form.html',
                           platforms=ENQUIRY_PLATFORM_CHOICES)


@deal_tracker_bp.route('/enquiries/<int:search_id>')
def enquiry_results(search_id):
    search = db.session.get(EnquirySearch, search_id)
    if not search:
        flash('Search not found.', 'warning')
        return redirect(url_for('deal_tracker.enquiries'))

    page = request.args.get('page', 1, type=int)
    platform_filter = request.args.get('platform', '')

    query = search.results.order_by(EnquiryResult.found_at.desc())
    if platform_filter:
        query = query.filter_by(platform=platform_filter)

    results = query.paginate(page=page, per_page=20, error_out=False)

    return render_template('deal_tracker/enquiry_results.html',
                           search=search, results=results,
                           platform_filter=platform_filter)


@deal_tracker_bp.route('/enquiries/delete/<int:search_id>', methods=['POST'])
def delete_enquiry(search_id):
    search = db.session.get(EnquirySearch, search_id)
    if search:
        db.session.delete(search)
        db.session.commit()
        flash('Enquiry search deleted.', 'success')
    return redirect(url_for('deal_tracker.enquiries'))
