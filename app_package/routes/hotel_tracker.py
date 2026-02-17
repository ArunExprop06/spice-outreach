"""Hotel Tracker — monitors hotel portals (Booking.com, OYO) for deals."""
import json
import re
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash)

from app_package import db
from app_package.models import HotelTracker, HotelListing, AppSetting

hotel_tracker_bp = Blueprint('hotel_tracker', __name__,
                             template_folder='../templates')

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Accept-Language': 'en-IN,en;q=0.9',
}

PLATFORM_CHOICES = [
    ('booking', 'Booking.com'),
    ('oyo', 'OYO Rooms'),
]


# ---------------------------------------------------------------------------
#  Scraping helpers
# ---------------------------------------------------------------------------

def scrape_booking(destination, checkin='', checkout='', guests=2, rooms=1):
    """Scrape Booking.com search results."""
    listings = []
    encoded = quote_plus(destination)

    # Default dates: tomorrow + day after
    if not checkin:
        tomorrow = datetime.now() + timedelta(days=1)
        checkin = tomorrow.strftime('%Y-%m-%d')
    if not checkout:
        cin = datetime.strptime(checkin, '%Y-%m-%d')
        checkout = (cin + timedelta(days=1)).strftime('%Y-%m-%d')

    url = (f'https://www.booking.com/searchresults.html'
           f'?ss={encoded}&checkin={checkin}&checkout={checkout}'
           f'&group_adults={guests}&no_rooms={rooms}&lang=en-gb')

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning('Booking.com request failed: %s', e)
        return listings

    soup = BeautifulSoup(resp.text, 'html.parser')

    cards = soup.select('[data-testid="property-card"]')
    for card in cards[:30]:
        title_el = card.select_one('[data-testid="title"]')
        price_el = card.select_one('[data-testid="price-and-discounted-price"]')
        rating_el = card.select_one('[data-testid="review-score"]')
        link_el = card.find('a', href=True)
        img_el = card.find('img')

        name = title_el.get_text(strip=True) if title_el else ''
        if not name:
            continue

        price = price_el.get_text(strip=True) if price_el else ''
        # Clean up price
        price = re.sub(r'[^\d₹,.\s]', '', price).strip()

        rating_text = ''
        if rating_el:
            score = re.search(r'[\d.]+', rating_el.get_text())
            if score:
                rating_text = score.group(0) + '/10'

        href = link_el['href'].split('?')[0] if link_el else ''
        if href and not href.startswith('http'):
            href = 'https://www.booking.com' + href

        image = img_el.get('src', '') if img_el else ''

        listings.append({
            'name': name,
            'price': price,
            'rating': rating_text,
            'location': destination.title(),
            'url': href,
            'image_url': image,
            'description': '',
        })

    return listings


def scrape_oyo(destination):
    """Scrape OYO Rooms search results."""
    listings = []
    city_slug = destination.lower().replace(' ', '-')
    url = f'https://www.oyorooms.com/hotels-in-{city_slug}/'

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning('OYO request failed: %s', e)
        return listings

    soup = BeautifulSoup(resp.text, 'html.parser')

    cards = soup.select('.hotelCardListing')
    for card in cards[:30]:
        title_el = card.select_one('.listingHotelDescription__hotelName, h3')
        price_el = card.select_one('.listingPrice__finalPrice, [class*="finalPrice"]')
        rating_el = card.select_one('.hotelRating, [class*="rating"]')
        link_el = card.find('a', href=True)
        img_el = card.find('img')

        name = title_el.get_text(strip=True) if title_el else ''
        if not name:
            continue

        # Extract just the final price
        price_text = price_el.get_text(strip=True) if price_el else ''
        price_match = re.search(r'₹[\d,]+', price_text)
        price = price_match.group(0) if price_match else price_text

        rating_text = ''
        if rating_el:
            score = re.search(r'[\d.]+', rating_el.get_text())
            if score:
                rating_text = score.group(0) + '/5'

        href = link_el['href'] if link_el else ''
        if href and not href.startswith('http'):
            href = 'https://www.oyorooms.com' + href

        image = ''
        if img_el:
            image = img_el.get('src', '') or img_el.get('data-src', '')

        listings.append({
            'name': name,
            'price': price,
            'rating': rating_text,
            'location': destination.title(),
            'url': href,
            'image_url': image,
            'description': '',
        })

    return listings


def check_hotel_tracker(tracker):
    """Run a scrape for a single HotelTracker. Returns list of newly saved HotelListings."""
    platforms = json.loads(tracker.platforms) if tracker.platforms else ['booking']
    new_listings = []

    for platform in platforms:
        if platform == 'booking':
            raw = scrape_booking(
                tracker.destination, tracker.checkin, tracker.checkout,
                tracker.guests, tracker.rooms
            )
        elif platform == 'oyo':
            raw = scrape_oyo(tracker.destination)
        else:
            continue

        for item in raw:
            item_url = item.get('url', '').strip()
            if not item_url:
                continue

            # Duplicate check
            exists = db.session.query(HotelListing).filter_by(
                tracker_id=tracker.id, url=item_url
            ).first()
            if exists:
                continue

            # Price filter
            if tracker.min_price or tracker.max_price:
                numeric = re.sub(r'[^\d]', '', item.get('price', ''))
                if numeric:
                    val = int(numeric)
                    if tracker.min_price and val < tracker.min_price:
                        continue
                    if tracker.max_price and val > tracker.max_price:
                        continue

            listing = HotelListing(
                tracker_id=tracker.id,
                name=item.get('name', '')[:500],
                price=item.get('price', '')[:100],
                rating=item.get('rating', '')[:50],
                location=item.get('location', '')[:200],
                url=item_url[:1000],
                image_url=item.get('image_url', '')[:1000],
                platform=platform,
                description=item.get('description', ''),
                is_new=True,
            )
            db.session.add(listing)
            new_listings.append(listing)

    tracker.last_checked = datetime.now(timezone.utc)
    db.session.commit()
    return new_listings


def send_hotel_alert(tracker, new_listings):
    """Send WhatsApp alert for new hotel listings."""
    if not tracker.whatsapp_number or not new_listings:
        return

    phone = tracker.whatsapp_number.strip()
    if not phone.startswith('+'):
        phone = '+91' + phone.lstrip('0')

    lines = [f'\U0001f3e8 Hotel Alert: {tracker.destination}\n']
    dates = ''
    if tracker.checkin:
        dates = f' ({tracker.checkin} to {tracker.checkout})'
    lines.append(f'{len(new_listings)} new hotel(s) found{dates}:\n')
    for i, listing in enumerate(new_listings[:10], 1):
        lines.append(f'{i}. {listing.name}')
        if listing.price:
            lines.append(f'   \U0001f4b0 {listing.price}/night')
        if listing.rating:
            lines.append(f'   \u2b50 {listing.rating}')
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
        logger.error('Hotel alert WhatsApp failed: %s', e)


# ---------------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------------

@hotel_tracker_bp.route('/')
def dashboard():
    trackers = db.session.query(HotelTracker).order_by(HotelTracker.created_at.desc()).all()
    for t in trackers:
        t.total_listings = t.listings.count()
        t.new_listings = t.listings.filter_by(is_new=True).count()
    return render_template('hotel_tracker/dashboard.html', trackers=trackers)


@hotel_tracker_bp.route('/add', methods=['GET', 'POST'])
def add_tracker():
    if request.method == 'POST':
        platforms = request.form.getlist('platforms') or ['booking']
        tracker = HotelTracker(
            destination=request.form.get('destination', '').strip(),
            checkin=request.form.get('checkin', '').strip(),
            checkout=request.form.get('checkout', '').strip(),
            guests=int(request.form.get('guests', 2) or 2),
            rooms=int(request.form.get('rooms', 1) or 1),
            min_price=int(request.form['min_price']) if request.form.get('min_price') else None,
            max_price=int(request.form['max_price']) if request.form.get('max_price') else None,
            platforms=json.dumps(platforms),
            whatsapp_number=request.form.get('whatsapp_number', '').strip(),
            is_active=True,
        )
        if not tracker.destination:
            flash('Destination is required.', 'warning')
            return render_template('hotel_tracker/form.html', tracker=None,
                                   platforms=PLATFORM_CHOICES)
        db.session.add(tracker)
        db.session.commit()
        flash(f'Hotel tracker for "{tracker.destination}" created.', 'success')
        return redirect(url_for('hotel_tracker.dashboard'))

    return render_template('hotel_tracker/form.html', tracker=None,
                           platforms=PLATFORM_CHOICES)


@hotel_tracker_bp.route('/edit/<int:tracker_id>', methods=['GET', 'POST'])
def edit_tracker(tracker_id):
    tracker = db.session.get(HotelTracker, tracker_id)
    if not tracker:
        flash('Tracker not found.', 'warning')
        return redirect(url_for('hotel_tracker.dashboard'))

    if request.method == 'POST':
        platforms = request.form.getlist('platforms') or ['booking']
        tracker.destination = request.form.get('destination', '').strip()
        tracker.checkin = request.form.get('checkin', '').strip()
        tracker.checkout = request.form.get('checkout', '').strip()
        tracker.guests = int(request.form.get('guests', 2) or 2)
        tracker.rooms = int(request.form.get('rooms', 1) or 1)
        tracker.min_price = int(request.form['min_price']) if request.form.get('min_price') else None
        tracker.max_price = int(request.form['max_price']) if request.form.get('max_price') else None
        tracker.platforms = json.dumps(platforms)
        tracker.whatsapp_number = request.form.get('whatsapp_number', '').strip()
        tracker.is_active = 'is_active' in request.form
        db.session.commit()
        flash(f'Hotel tracker for "{tracker.destination}" updated.', 'success')
        return redirect(url_for('hotel_tracker.dashboard'))

    return render_template('hotel_tracker/form.html', tracker=tracker,
                           platforms=PLATFORM_CHOICES)


@hotel_tracker_bp.route('/delete/<int:tracker_id>', methods=['POST'])
def delete_tracker(tracker_id):
    tracker = db.session.get(HotelTracker, tracker_id)
    if tracker:
        name = tracker.destination
        db.session.delete(tracker)
        db.session.commit()
        flash(f'Hotel tracker for "{name}" deleted.', 'success')
    return redirect(url_for('hotel_tracker.dashboard'))


@hotel_tracker_bp.route('/results/<int:tracker_id>')
def results(tracker_id):
    tracker = db.session.get(HotelTracker, tracker_id)
    if not tracker:
        flash('Tracker not found.', 'warning')
        return redirect(url_for('hotel_tracker.dashboard'))

    page = request.args.get('page', 1, type=int)
    listings = (tracker.listings
                .order_by(HotelListing.found_at.desc())
                .paginate(page=page, per_page=20, error_out=False))

    db.session.query(HotelListing).filter_by(
        tracker_id=tracker_id, is_new=True
    ).update({'is_new': False})
    db.session.commit()

    return render_template('hotel_tracker/results.html',
                           tracker=tracker, listings=listings)


@hotel_tracker_bp.route('/check/<int:tracker_id>', methods=['POST'])
def check_single(tracker_id):
    tracker = db.session.get(HotelTracker, tracker_id)
    if not tracker:
        flash('Tracker not found.', 'warning')
        return redirect(url_for('hotel_tracker.dashboard'))

    new = check_hotel_tracker(tracker)
    if new:
        send_hotel_alert(tracker, new)
        flash(f'Found {len(new)} hotel(s) for "{tracker.destination}".', 'success')
    else:
        flash(f'No new hotels found for "{tracker.destination}".', 'info')

    return redirect(url_for('hotel_tracker.results', tracker_id=tracker_id))


@hotel_tracker_bp.route('/check-all', methods=['POST'])
def check_all():
    trackers = db.session.query(HotelTracker).filter_by(is_active=True).all()
    total_new = 0
    for tracker in trackers:
        new = check_hotel_tracker(tracker)
        if new:
            send_hotel_alert(tracker, new)
            total_new += len(new)

    flash(f'Checked {len(trackers)} tracker(s). Found {total_new} hotel(s) total.', 'success')
    return redirect(url_for('hotel_tracker.dashboard'))
