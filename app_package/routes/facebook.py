import re
import requests
from datetime import datetime, timezone
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify)
from app_package import db
from app_package.models import Contact, AppSetting, FacebookSource

facebook_bp = Blueprint('facebook', __name__)

GRAPH_API = 'https://graph.facebook.com/v18.0'


def get_fb_config():
    return {
        'page_id': AppSetting.get('fb_page_id', ''),
        'access_token': AppSetting.get('fb_access_token', ''),
        'group_ids': [g.strip() for g in AppSetting.get('fb_group_ids', '').split(',') if g.strip()],
    }


def fb_api_get(endpoint, params=None):
    """Make a GET request to Facebook Graph API."""
    config = get_fb_config()
    if not config['access_token']:
        return None, 'Facebook Access Token not configured. Go to Settings.'

    url = f"{GRAPH_API}/{endpoint}"
    if params is None:
        params = {}
    params['access_token'] = config['access_token']

    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if 'error' in data:
            return None, data['error'].get('message', 'Unknown Facebook API error')
        return data, None
    except Exception as e:
        return None, str(e)


def extract_contact_info(text):
    """Extract phone numbers and emails from text."""
    phones = []
    emails = []
    if not text:
        return phones, emails

    email_pattern = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    emails = list(set(re.findall(email_pattern, text)))
    emails = [e for e in emails if 'facebook.com' not in e.lower()]

    phone_patterns = [
        r'\+91[\s\-]?\d{5}[\s\-]?\d{5}',
        r'\+91[\s\-]?\d{10}',
        r'(?<!\d)0?\d{10}(?!\d)',
        r'(?<!\d)\d{3}[\s\-]\d{3}[\s\-]\d{4}(?!\d)',
        r'(?<!\d)\d{5}[\s\-]\d{5}(?!\d)',
    ]
    for pattern in phone_patterns:
        found = re.findall(pattern, text)
        for p in found:
            cleaned = re.sub(r'[\s\-]', '', p)
            if len(cleaned) >= 10:
                phones.append(cleaned)

    phones = list(set(phones))
    return phones, emails


def is_enquiry(text, keyword_filter=None):
    """Check if a comment/post looks like a business enquiry."""
    if not text:
        return False
    text_lower = text.lower()

    enquiry_keywords = [
        'need', 'want', 'require', 'looking for', 'interested',
        'price', 'rate', 'cost', 'quote', 'quotation', 'pricing',
        'supply', 'supplier', 'provide', 'available', 'availability',
        'bulk', 'wholesale', 'order', 'buy', 'purchase', 'buying',
        'contact', 'call me', 'whatsapp', 'msg me', 'dm me', 'inbox me',
        'send details', 'send info', 'more info', 'details please',
        'quantity', 'kg', 'ton', 'quintal', 'metric ton',
        'urgent', 'urgently', 'immediately', 'asap',
        'dealer', 'distributor', 'manufacturer', 'exporter',
        'chahiye', 'chaiye', 'mangta', 'dedo', 'bhejo', 'bhejna',
        'kitna', 'kya rate', 'price batao', 'rate batao',
        'kharidna', 'lena hai', 'dena hai', 'mil sakta',
        'contact karo', 'number do', 'phone number',
    ]

    is_enq = any(kw in text_lower for kw in enquiry_keywords)

    if keyword_filter:
        keyword_lower = keyword_filter.lower().replace('#', '')
        return is_enq or (keyword_lower in text_lower)

    return is_enq


def _process_comment_or_post(text, author_name, source_label, keyword_filter=None):
    """Process a single comment/post and return a lead dict or None."""
    if not text:
        return None
    if not is_enquiry(text, keyword_filter):
        return None

    phones, emails = extract_contact_info(text)
    name = author_name or 'Facebook Lead'

    is_dup = db.session.query(Contact).filter(
        Contact.company_name == name,
        Contact.source.in_(['facebook_comment', 'facebook_group', 'facebook_search',
                            'facebook_page', 'facebook_discover'])
    ).first() is not None

    return {
        'name': name,
        'text': text,
        'phones': phones,
        'emails': emails,
        'phone': phones[0] if phones else '',
        'email': emails[0] if emails else '',
        'is_enquiry': True,
        'is_duplicate': is_dup,
        'source': source_label,
    }


def _save_contact_from_lead(name, phone, email, text, source):
    """Save a single lead as contact. Returns True if saved, False if duplicate."""
    if not name:
        return False
    existing = db.session.query(Contact).filter_by(company_name=name).first()
    if existing:
        if phone and not existing.phone:
            existing.phone = phone
            existing.whatsapp = phone
        if email and not existing.email:
            existing.email = email
        if text and len(text) > len(existing.notes or ''):
            existing.notes = f"FB: {text[:500]}"
        db.session.commit()
        return False

    contact = Contact(
        company_name=name,
        contact_person=name,
        phone=phone,
        whatsapp=phone,
        email=email,
        notes=f"FB: {text[:500]}",
        source=source,
        category='Other',
    )
    db.session.add(contact)
    return True


def _scan_source_feed(fb_id, source_type, keyword=None):
    """Scan a page/group feed for enquiries. Returns (leads_list, stats_dict)."""
    endpoint = f"{fb_id}/feed"
    if source_type == 'page':
        endpoint = f"{fb_id}/posts"

    posts_data, error = fb_api_get(
        endpoint,
        params={
            'fields': 'id,message,from,created_time,comments.limit(50){message,from,created_time}',
            'limit': '50'
        }
    )
    if error:
        return [], {'error': error, 'posts': 0, 'comments': 0, 'enquiries': 0, 'saved': 0}

    leads = []
    stats = {'posts': 0, 'comments': 0, 'enquiries': 0, 'saved': 0}
    contact_source = 'facebook_page' if source_type == 'page' else 'facebook_group'

    for post in posts_data.get('data', []):
        stats['posts'] += 1
        post_text = post.get('message', '')
        poster_name = post.get('from', {}).get('name', '')

        # Check post itself
        if post_text and is_enquiry(post_text, keyword):
            stats['enquiries'] += 1
            phones, emails = extract_contact_info(post_text)
            lead = {
                'name': poster_name or 'FB Lead',
                'text': post_text,
                'phone': phones[0] if phones else '',
                'email': emails[0] if emails else '',
                'source': contact_source,
                'date': post.get('created_time', ''),
            }
            leads.append(lead)

        # Check comments
        comments = post.get('comments', {}).get('data', [])
        for comment in comments:
            stats['comments'] += 1
            text = comment.get('message', '')
            commenter = comment.get('from', {}).get('name', '')

            if is_enquiry(text, keyword):
                stats['enquiries'] += 1
                phones, emails = extract_contact_info(text)
                lead = {
                    'name': commenter or 'FB Lead',
                    'text': text,
                    'phone': phones[0] if phones else '',
                    'email': emails[0] if emails else '',
                    'source': contact_source,
                    'date': comment.get('created_time', ''),
                }
                leads.append(lead)

    # If page, also try fetching comments separately for more coverage
    if source_type == 'page':
        posts_data2, _ = fb_api_get(
            f"{fb_id}/posts",
            params={'fields': 'id', 'limit': '25'}
        )
        if posts_data2:
            for post in posts_data2.get('data', []):
                comments_data, _ = fb_api_get(
                    f"{post['id']}/comments",
                    params={'fields': 'id,message,from,created_time', 'limit': '100'}
                )
                if not comments_data:
                    continue
                for comment in comments_data.get('data', []):
                    stats['comments'] += 1
                    text = comment.get('message', '')
                    commenter = comment.get('from', {}).get('name', '')
                    if is_enquiry(text, keyword):
                        # Avoid duplicate leads
                        if not any(l['name'] == commenter and l['text'][:50] == text[:50] for l in leads):
                            stats['enquiries'] += 1
                            phones, emails = extract_contact_info(text)
                            leads.append({
                                'name': commenter or 'FB Lead',
                                'text': text,
                                'phone': phones[0] if phones else '',
                                'email': emails[0] if emails else '',
                                'source': contact_source,
                                'date': comment.get('created_time', ''),
                            })

    return leads, stats


# ─── Dashboard ─────────────────────────────────────────────

@facebook_bp.route('/')
def dashboard():
    config = get_fb_config()
    is_configured = bool(config['access_token'])
    has_groups = len(config['group_ids']) > 0

    fb_contacts = db.session.query(Contact).filter(
        Contact.source.in_(['facebook_comment', 'facebook_group', 'facebook_search',
                            'facebook_page', 'facebook_discover'])
    ).order_by(Contact.created_at.desc()).limit(50).all()

    total_fb = db.session.query(Contact).filter(
        Contact.source.in_(['facebook_comment', 'facebook_group', 'facebook_search',
                            'facebook_page', 'facebook_discover'])
    ).count()
    from_comments = db.session.query(Contact).filter(
        Contact.source.in_(['facebook_comment', 'facebook_page'])
    ).count()
    from_groups = db.session.query(Contact).filter_by(source='facebook_group').count()
    from_search = db.session.query(Contact).filter(
        Contact.source.in_(['facebook_search', 'facebook_discover'])
    ).count()

    # Saved sources count
    saved_sources = db.session.query(FacebookSource).filter_by(is_active=True).count()

    return render_template('facebook/dashboard.html',
                           is_configured=is_configured,
                           has_groups=has_groups,
                           group_ids=config['group_ids'],
                           fb_contacts=fb_contacts,
                           total_fb=total_fb,
                           from_comments=from_comments,
                           from_groups=from_groups,
                           from_search=from_search,
                           saved_sources=saved_sources)


# ─── Fetch Page Comments ──────────────────────────────────

@facebook_bp.route('/fetch-comments', methods=['POST'])
def fetch_comments():
    config = get_fb_config()
    if not config['access_token'] or not config['page_id']:
        flash('Facebook not configured. Go to Settings.', 'error')
        return redirect(url_for('facebook.dashboard'))

    keyword = request.form.get('keyword', '').strip()
    leads, stats = _scan_source_feed(config['page_id'], 'page', keyword or None)

    contacts_saved = 0
    for lead in leads:
        if _save_contact_from_lead(lead['name'], lead['phone'], lead['email'],
                                   lead['text'], 'facebook_comment'):
            contacts_saved += 1

    db.session.commit()

    flash(f'Scanned {stats["posts"]} posts, {stats["comments"]} comments. '
          f'Found {stats["enquiries"]} enquiries, saved {contacts_saved} new contacts.', 'success')
    return redirect(url_for('facebook.dashboard'))


# ─── Fetch Group Posts & Comments ──────────────────────────

@facebook_bp.route('/fetch-groups', methods=['POST'])
def fetch_groups():
    config = get_fb_config()
    if not config['access_token']:
        flash('Facebook not configured. Go to Settings.', 'error')
        return redirect(url_for('facebook.dashboard'))

    if not config['group_ids']:
        flash('No Facebook Groups configured. Add Group IDs in Settings.', 'error')
        return redirect(url_for('facebook.dashboard'))

    keyword = request.form.get('keyword', '').strip()
    total_enquiries = 0
    total_saved = 0
    total_posts = 0
    total_comments = 0

    for group_id in config['group_ids']:
        leads, stats = _scan_source_feed(group_id, 'group', keyword or None)
        total_posts += stats['posts']
        total_comments += stats['comments']
        total_enquiries += stats['enquiries']

        for lead in leads:
            if _save_contact_from_lead(lead['name'], lead['phone'], lead['email'],
                                       lead['text'], 'facebook_group'):
                total_saved += 1

    db.session.commit()

    flash(f'Scanned {len(config["group_ids"])} groups: {total_posts} posts, {total_comments} comments. '
          f'Found {total_enquiries} enquiries, saved {total_saved} new contacts.', 'success')
    return redirect(url_for('facebook.dashboard'))


# ─── Discover Pages & Groups ──────────────────────────────

@facebook_bp.route('/discover')
def discover():
    """Show the discover page with saved sources."""
    saved_sources = db.session.query(FacebookSource).order_by(
        FacebookSource.is_active.desc(), FacebookSource.created_at.desc()
    ).all()

    return render_template('facebook/discover.html', saved_sources=saved_sources)


@facebook_bp.route('/discover-pages', methods=['POST'])
def discover_pages():
    """Search Facebook for pages matching a keyword."""
    keyword = request.form.get('keyword', '').strip()
    if not keyword:
        flash('Enter a keyword to search for pages.', 'error')
        return redirect(url_for('facebook.discover'))

    config = get_fb_config()
    if not config['access_token']:
        flash('Facebook not configured. Go to Settings.', 'error')
        return redirect(url_for('facebook.discover'))

    # Search for pages using Graph API
    data, error = fb_api_get(
        'search',
        params={
            'q': keyword,
            'type': 'page',
            'fields': 'id,name,category,fan_count,link,about,location',
            'limit': '25'
        }
    )

    results = []
    if error:
        # If page search endpoint fails, try alternative approach
        # Search for places/pages via another method
        flash(f'Page search note: {error}. Try searching manually or use "My Groups" to find your groups.', 'warning')
    else:
        existing_ids = {s.fb_id for s in db.session.query(FacebookSource).all()}
        for page in data.get('data', []):
            results.append({
                'fb_id': page.get('id', ''),
                'name': page.get('name', ''),
                'category': page.get('category', ''),
                'fan_count': page.get('fan_count', 0),
                'about': page.get('about', '')[:200] if page.get('about') else '',
                'link': page.get('link', ''),
                'location': page.get('location', {}).get('city', ''),
                'type': 'page',
                'already_saved': page.get('id', '') in existing_ids,
            })

    saved_sources = db.session.query(FacebookSource).order_by(
        FacebookSource.is_active.desc(), FacebookSource.created_at.desc()
    ).all()

    return render_template('facebook/discover.html',
                           saved_sources=saved_sources,
                           results=results,
                           search_keyword=keyword,
                           search_type='pages')


@facebook_bp.route('/discover-groups', methods=['POST'])
def discover_groups():
    """Search Facebook for groups matching a keyword."""
    keyword = request.form.get('keyword', '').strip()
    if not keyword:
        flash('Enter a keyword to search for groups.', 'error')
        return redirect(url_for('facebook.discover'))

    config = get_fb_config()
    if not config['access_token']:
        flash('Facebook not configured. Go to Settings.', 'error')
        return redirect(url_for('facebook.discover'))

    data, error = fb_api_get(
        'search',
        params={
            'q': keyword,
            'type': 'group',
            'fields': 'id,name,description,member_count,privacy',
            'limit': '25'
        }
    )

    results = []
    if error:
        flash(f'Group search note: {error}. Try "My Groups" to find groups you\'re already in.', 'warning')
    else:
        existing_ids = {s.fb_id for s in db.session.query(FacebookSource).all()}
        for group in data.get('data', []):
            results.append({
                'fb_id': group.get('id', ''),
                'name': group.get('name', ''),
                'category': group.get('privacy', 'Unknown'),
                'member_count': group.get('member_count', 0),
                'about': group.get('description', '')[:200] if group.get('description') else '',
                'type': 'group',
                'already_saved': group.get('id', '') in existing_ids,
            })

    saved_sources = db.session.query(FacebookSource).order_by(
        FacebookSource.is_active.desc(), FacebookSource.created_at.desc()
    ).all()

    return render_template('facebook/discover.html',
                           saved_sources=saved_sources,
                           results=results,
                           search_keyword=keyword,
                           search_type='groups')


@facebook_bp.route('/my-groups')
def my_groups():
    """Fetch all groups the user is a member of via /me/groups."""
    config = get_fb_config()
    if not config['access_token']:
        flash('Facebook not configured. Go to Settings.', 'error')
        return redirect(url_for('facebook.discover'))

    data, error = fb_api_get(
        'me/groups',
        params={
            'fields': 'id,name,description,member_count,privacy',
            'limit': '100'
        }
    )

    results = []
    if error:
        flash(f'Could not fetch groups: {error}', 'warning')
    else:
        existing_ids = {s.fb_id for s in db.session.query(FacebookSource).all()}
        for group in data.get('data', []):
            results.append({
                'fb_id': group.get('id', ''),
                'name': group.get('name', ''),
                'category': group.get('privacy', 'Unknown'),
                'member_count': group.get('member_count', 0),
                'about': group.get('description', '')[:200] if group.get('description') else '',
                'type': 'group',
                'already_saved': group.get('id', '') in existing_ids,
            })

    if not error and not results:
        flash('No groups found. Make sure your token has the right permissions.', 'info')

    saved_sources = db.session.query(FacebookSource).order_by(
        FacebookSource.is_active.desc(), FacebookSource.created_at.desc()
    ).all()

    return render_template('facebook/discover.html',
                           saved_sources=saved_sources,
                           results=results,
                           search_keyword='My Groups',
                           search_type='my_groups')


@facebook_bp.route('/save-source', methods=['POST'])
def save_source():
    """Save a discovered page/group as a source for scanning."""
    fb_id = request.form.get('fb_id', '').strip()
    name = request.form.get('name', '').strip()
    source_type = request.form.get('source_type', 'page')
    category = request.form.get('category', '')
    member_count = request.form.get('member_count', '0')
    fan_count = request.form.get('fan_count', '0')
    about = request.form.get('about', '')
    keyword = request.form.get('keyword', '')

    if not fb_id:
        flash('Invalid source.', 'error')
        return redirect(url_for('facebook.discover'))

    existing = db.session.query(FacebookSource).filter_by(fb_id=fb_id).first()
    if existing:
        existing.is_active = True
        flash(f'"{name}" is already saved. Re-activated.', 'info')
    else:
        source = FacebookSource(
            fb_id=fb_id,
            name=name,
            source_type=source_type,
            category=category,
            description=about[:500],
            member_count=int(member_count) if str(member_count).isdigit() else 0,
            fan_count=int(fan_count) if str(fan_count).isdigit() else 0,
            added_keyword=keyword,
        )
        db.session.add(source)
        flash(f'Saved "{name}" ({source_type}) for scanning.', 'success')

    db.session.commit()
    return redirect(url_for('facebook.discover'))


@facebook_bp.route('/save-multiple-sources', methods=['POST'])
def save_multiple_sources():
    """Save multiple discovered pages/groups at once."""
    selected = request.form.getlist('selected')
    if not selected:
        flash('No sources selected.', 'error')
        return redirect(url_for('facebook.discover'))

    saved = 0
    for idx in selected:
        fb_id = request.form.get(f'fb_id_{idx}', '').strip()
        name = request.form.get(f'name_{idx}', '').strip()
        source_type = request.form.get(f'type_{idx}', 'page')
        category = request.form.get(f'category_{idx}', '')
        member_count = request.form.get(f'member_count_{idx}', '0')
        fan_count = request.form.get(f'fan_count_{idx}', '0')
        about = request.form.get(f'about_{idx}', '')
        keyword = request.form.get('search_keyword', '')

        if not fb_id:
            continue

        existing = db.session.query(FacebookSource).filter_by(fb_id=fb_id).first()
        if existing:
            existing.is_active = True
        else:
            source = FacebookSource(
                fb_id=fb_id,
                name=name,
                source_type=source_type,
                category=category,
                description=about[:500],
                member_count=int(member_count) if str(member_count).isdigit() else 0,
                fan_count=int(fan_count) if str(fan_count).isdigit() else 0,
                added_keyword=keyword,
            )
            db.session.add(source)
            saved += 1

    db.session.commit()
    flash(f'Saved {saved} sources for scanning.', 'success')
    return redirect(url_for('facebook.discover'))


@facebook_bp.route('/remove-source/<int:source_id>', methods=['POST'])
def remove_source(source_id):
    """Remove (deactivate) a saved source."""
    source = db.session.get(FacebookSource, source_id)
    if source:
        db.session.delete(source)
        db.session.commit()
        flash(f'Removed "{source.name}".', 'success')
    return redirect(url_for('facebook.discover'))


@facebook_bp.route('/toggle-source/<int:source_id>', methods=['POST'])
def toggle_source(source_id):
    """Toggle active/inactive for a saved source."""
    source = db.session.get(FacebookSource, source_id)
    if source:
        source.is_active = not source.is_active
        db.session.commit()
        status = 'activated' if source.is_active else 'paused'
        flash(f'"{source.name}" {status}.', 'success')
    return redirect(url_for('facebook.discover'))


# ─── Scan Saved Sources ───────────────────────────────────

@facebook_bp.route('/scan-source/<int:source_id>', methods=['POST'])
def scan_source(source_id):
    """Scan a single saved source for enquiries."""
    source = db.session.get(FacebookSource, source_id)
    if not source:
        flash('Source not found.', 'error')
        return redirect(url_for('facebook.discover'))

    keyword = request.form.get('keyword', '').strip() or None
    leads, stats = _scan_source_feed(source.fb_id, source.source_type, keyword)

    if 'error' in stats:
        flash(f'Error scanning "{source.name}": {stats["error"]}', 'error')
        return redirect(url_for('facebook.discover'))

    contacts_saved = 0
    contact_source = 'facebook_page' if source.source_type == 'page' else 'facebook_group'
    for lead in leads:
        if _save_contact_from_lead(lead['name'], lead['phone'], lead['email'],
                                   lead['text'], contact_source):
            contacts_saved += 1

    source.last_scanned = datetime.now(timezone.utc)
    source.leads_found += contacts_saved
    db.session.commit()

    flash(f'Scanned "{source.name}": {stats["posts"]} posts, {stats["comments"]} comments. '
          f'Found {stats["enquiries"]} enquiries, saved {contacts_saved} new contacts.', 'success')
    return redirect(url_for('facebook.discover'))


@facebook_bp.route('/scan-all-sources', methods=['POST'])
def scan_all_sources():
    """Scan all active saved sources for enquiries."""
    sources = db.session.query(FacebookSource).filter_by(is_active=True).all()
    if not sources:
        flash('No active sources to scan. Discover and save some pages/groups first.', 'warning')
        return redirect(url_for('facebook.discover'))

    keyword = request.form.get('keyword', '').strip() or None
    total_saved = 0
    total_enquiries = 0
    scanned = 0
    errors = []

    for source in sources:
        leads, stats = _scan_source_feed(source.fb_id, source.source_type, keyword)

        if 'error' in stats:
            errors.append(f'{source.name}: {stats["error"]}')
            continue

        scanned += 1
        total_enquiries += stats['enquiries']
        contact_source = 'facebook_page' if source.source_type == 'page' else 'facebook_group'

        saved_this = 0
        for lead in leads:
            if _save_contact_from_lead(lead['name'], lead['phone'], lead['email'],
                                       lead['text'], contact_source):
                saved_this += 1
                total_saved += 1

        source.last_scanned = datetime.now(timezone.utc)
        source.leads_found += saved_this

    db.session.commit()

    msg = f'Scanned {scanned}/{len(sources)} sources. Found {total_enquiries} enquiries, saved {total_saved} new contacts.'
    if errors:
        msg += f' Errors: {len(errors)} sources failed.'
    flash(msg, 'success' if not errors else 'warning')
    return redirect(url_for('facebook.discover'))


# ─── Hashtag / Keyword Search ─────────────────────────────

@facebook_bp.route('/search', methods=['GET', 'POST'])
def search():
    if request.method == 'POST':
        query = request.form.get('query', '').strip()
        if not query:
            flash('Enter a search keyword or hashtag.', 'error')
            return redirect(url_for('facebook.search'))

        config = get_fb_config()
        if not config['access_token']:
            flash('Facebook not configured. Go to Settings.', 'error')
            return redirect(url_for('facebook.search'))

        results = []
        query_lower = query.lower().replace('#', '')

        # 1. Search own page feed for keyword
        if config['page_id']:
            posts_data, _ = fb_api_get(
                f"{config['page_id']}/feed",
                params={
                    'fields': 'id,message,from,comments.limit(50){message,from,created_time},created_time',
                    'limit': '50'
                }
            )
            if posts_data:
                for post in posts_data.get('data', []):
                    post_text = post.get('message', '')
                    if post_text and query_lower in post_text.lower():
                        poster = post.get('from', {})
                        lead = _process_comment_or_post(post_text, poster.get('name', ''),
                                                        'page_post', query)
                        if lead:
                            lead['date'] = post.get('created_time', '')
                            results.append(lead)

                    for comment in post.get('comments', {}).get('data', []):
                        text = comment.get('message', '')
                        if query_lower in text.lower():
                            commenter = comment.get('from', {})
                            lead = _process_comment_or_post(text, commenter.get('name', ''),
                                                            'page_comment', query)
                            if lead:
                                lead['date'] = comment.get('created_time', '')
                                results.append(lead)

        # 2. Search configured groups for keyword
        for group_id in config['group_ids']:
            posts_data, _ = fb_api_get(
                f"{group_id}/feed",
                params={
                    'fields': 'id,message,from,comments.limit(30){message,from,created_time},created_time',
                    'limit': '50'
                }
            )
            if not posts_data:
                continue
            for post in posts_data.get('data', []):
                post_text = post.get('message', '')
                if post_text and query_lower in post_text.lower():
                    poster = post.get('from', {})
                    lead = _process_comment_or_post(post_text, poster.get('name', ''),
                                                    f'group_{group_id}', query)
                    if lead:
                        lead['date'] = post.get('created_time', '')
                        results.append(lead)
                for comment in post.get('comments', {}).get('data', []):
                    text = comment.get('message', '')
                    if query_lower in text.lower():
                        commenter = comment.get('from', {})
                        lead = _process_comment_or_post(text, commenter.get('name', ''),
                                                        f'group_{group_id}', query)
                        if lead:
                            lead['date'] = comment.get('created_time', '')
                            results.append(lead)

        # 3. Search ALL saved sources for keyword
        saved_sources = db.session.query(FacebookSource).filter_by(is_active=True).all()
        scanned_ids = set(config['group_ids'])
        if config['page_id']:
            scanned_ids.add(config['page_id'])

        for source in saved_sources:
            if source.fb_id in scanned_ids:
                continue
            scanned_ids.add(source.fb_id)

            posts_data, _ = fb_api_get(
                f"{source.fb_id}/feed" if source.source_type == 'group' else f"{source.fb_id}/posts",
                params={
                    'fields': 'id,message,from,comments.limit(30){message,from,created_time},created_time',
                    'limit': '30'
                }
            )
            if not posts_data:
                continue
            for post in posts_data.get('data', []):
                post_text = post.get('message', '')
                if post_text and query_lower in post_text.lower():
                    poster = post.get('from', {})
                    lead = _process_comment_or_post(post_text, poster.get('name', ''),
                                                    f'discover_{source.source_type}', query)
                    if lead:
                        lead['date'] = post.get('created_time', '')
                        lead['source_name'] = source.name
                        results.append(lead)
                for comment in post.get('comments', {}).get('data', []):
                    text = comment.get('message', '')
                    if query_lower in text.lower():
                        commenter = comment.get('from', {})
                        lead = _process_comment_or_post(text, commenter.get('name', ''),
                                                        f'discover_{source.source_type}', query)
                        if lead:
                            lead['date'] = comment.get('created_time', '')
                            lead['source_name'] = source.name
                            results.append(lead)

        # 4. Try public post search
        search_data, _ = fb_api_get(
            'search',
            params={'q': query, 'type': 'post', 'fields': 'id,message,from,created_time', 'limit': '25'}
        )
        if search_data:
            for post in search_data.get('data', []):
                text = post.get('message', '')
                poster = post.get('from', {})
                lead = _process_comment_or_post(text, poster.get('name', ''),
                                                'public_search', query)
                if lead:
                    lead['date'] = post.get('created_time', '')
                    results.append(lead)

        # Deduplicate by name
        seen = set()
        unique_results = []
        for r in results:
            key = (r['name'], r['text'][:50])
            if key not in seen:
                seen.add(key)
                unique_results.append(r)

        return render_template('facebook/search_results.html',
                               query=query, results=unique_results)

    return render_template('facebook/search.html')


@facebook_bp.route('/save-leads', methods=['POST'])
def save_leads():
    selected = request.form.getlist('selected')
    if not selected:
        flash('No leads selected.', 'error')
        return redirect(url_for('facebook.dashboard'))

    saved = 0
    for idx in selected:
        name = request.form.get(f'name_{idx}', '').strip()
        phone = request.form.get(f'phone_{idx}', '').strip()
        email = request.form.get(f'email_{idx}', '').strip()
        text = request.form.get(f'text_{idx}', '').strip()
        source = request.form.get(f'source_{idx}', 'facebook_search')

        contact_source = 'facebook_search'
        if 'group' in source:
            contact_source = 'facebook_group'
        elif 'comment' in source or 'page' in source:
            contact_source = 'facebook_comment'
        elif 'discover' in source:
            contact_source = 'facebook_discover'

        if _save_contact_from_lead(name, phone, email, text, contact_source):
            saved += 1

    db.session.commit()
    flash(f'Saved {saved} new contacts from Facebook.', 'success')
    return redirect(url_for('facebook.dashboard'))


# ─── Auto-Monitor (for scheduler) ─────────────────────────

def auto_fetch_fb_enquiries(app):
    """Called by scheduler — scans page + groups + saved sources for new enquiries."""
    with app.app_context():
        config = get_fb_config()
        if not config['access_token']:
            return 0

        saved = 0

        # Scan own page
        if config['page_id']:
            leads, _ = _scan_source_feed(config['page_id'], 'page')
            for lead in leads:
                if _save_contact_from_lead(lead['name'], lead['phone'], lead['email'],
                                           lead['text'], 'facebook_comment'):
                    saved += 1

        # Scan configured groups
        for group_id in config['group_ids']:
            leads, _ = _scan_source_feed(group_id, 'group')
            for lead in leads:
                if _save_contact_from_lead(lead['name'], lead['phone'], lead['email'],
                                           lead['text'], 'facebook_group'):
                    saved += 1

        # Scan all saved sources
        scanned_ids = set(config['group_ids'])
        if config['page_id']:
            scanned_ids.add(config['page_id'])

        sources = db.session.query(FacebookSource).filter_by(is_active=True).all()
        for source in sources:
            if source.fb_id in scanned_ids:
                continue
            scanned_ids.add(source.fb_id)

            contact_source = 'facebook_page' if source.source_type == 'page' else 'facebook_group'
            leads, _ = _scan_source_feed(source.fb_id, source.source_type)
            saved_this = 0
            for lead in leads:
                if _save_contact_from_lead(lead['name'], lead['phone'], lead['email'],
                                           lead['text'], contact_source):
                    saved_this += 1
                    saved += 1

            source.last_scanned = datetime.now(timezone.utc)
            source.leads_found += saved_this

        db.session.commit()
        return saved
