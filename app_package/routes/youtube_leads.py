import json
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs

import requests
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

from app_package import db
from app_package.models import AppSetting, Contact

# Gemini models (same fallback chain as ai_assistant)
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
]

youtube_leads_bp = Blueprint('youtube_leads', __name__,
                             template_folder='../templates')

# Business-intent keywords and their scores
BUSINESS_KEYWORDS = {
    'interested': 20, 'price': 20, 'pricing': 20, 'cost': 20, 'rate': 15,
    'bulk': 20, 'supplier': 20, 'supply': 15, 'vendor': 15, 'dealer': 15,
    'contact': 20, 'whatsapp': 20, 'order': 20, 'buy': 15, 'purchase': 15,
    'available': 15, 'stock': 15, 'deliver': 15, 'delivery': 15, 'ship': 15,
    'shipping': 15, 'wholesale': 20, 'distributor': 20, 'quotation': 20,
    'quote': 20, 'sample': 15, 'catalog': 15, 'catalogue': 15,
    'need': 10, 'require': 10, 'looking for': 15, 'want to buy': 20,
    'export': 15, 'import': 15, 'business': 10, 'company': 10,
}

EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
PHONE_PATTERN = re.compile(
    r'(?:\+?\d{1,3}[\s\-]?)?'
    r'(?:\(?\d{2,5}\)?[\s\-]?)?'
    r'\d{5,10}'
    r'(?:[\s\-]?\d{1,5})?'
)


def extract_video_id(url):
    """Parse YouTube URL to get video ID. Handles various formats."""
    if not url:
        return None

    # Direct video ID (11 chars)
    if re.match(r'^[a-zA-Z0-9_\-]{11}$', url.strip()):
        return url.strip()

    parsed = urlparse(url)
    host = parsed.hostname or ''

    # youtube.com/watch?v=VIDEO_ID
    if host in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            qs = parse_qs(parsed.query)
            return qs.get('v', [None])[0]
        # youtube.com/embed/VIDEO_ID or /shorts/VIDEO_ID or /v/VIDEO_ID
        for prefix in ('/embed/', '/shorts/', '/v/'):
            if parsed.path.startswith(prefix):
                return parsed.path[len(prefix):].split('/')[0].split('?')[0]

    # youtu.be/VIDEO_ID
    if host in ('youtu.be', 'www.youtu.be'):
        return parsed.path.lstrip('/').split('/')[0].split('?')[0]

    return None


def search_youtube_videos(query, api_key, max_results=5):
    """Search YouTube for videos matching a query. Returns list of video dicts."""
    url = 'https://www.googleapis.com/youtube/v3/search'
    params = {
        'part': 'snippet',
        'q': query,
        'type': 'video',
        'maxResults': min(max_results, 10),
        'order': 'relevance',
        'key': api_key,
    }
    resp = requests.get(url, params=params, timeout=15)
    if resp.status_code != 200:
        error_data = resp.json().get('error', {})
        raise Exception(error_data.get('message', f'Search API error {resp.status_code}'))

    videos = []
    for item in resp.json().get('items', []):
        vid_id = item['id'].get('videoId')
        if not vid_id:
            continue
        s = item['snippet']
        videos.append({
            'video_id': vid_id,
            'title': s.get('title', ''),
            'channel': s.get('channelTitle', ''),
            'thumbnail': s.get('thumbnails', {}).get('medium', {}).get('url', ''),
            'published': s.get('publishedAt', ''),
        })
    return videos


def ai_generate_search_queries(topic, gemini_key):
    """Use Gemini to generate smart YouTube search queries from a simple topic."""
    prompt = f"""You are a lead generation expert. Given the business topic "{topic}",
generate 3 YouTube search queries that would find videos whose commenters are likely
potential customers or leads (people asking about pricing, suppliers, availability, etc).

Return ONLY a JSON array of strings, no markdown, no explanation.
Example: ["poultry feed supplier India", "poultry farm equipment price", "broiler chicken business India"]

Topic: {topic}"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 256}
    }

    for model in GEMINI_MODELS:
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
        try:
            resp = requests.post(api_url, json=payload, timeout=20)
            if resp.status_code == 429:
                time.sleep(5)
                continue
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            # Strip markdown fences
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                text = "\n".join(lines)
            return json.loads(text)
        except Exception:
            continue

    # Fallback: just use the topic directly with business suffixes
    return [
        f"{topic} supplier India",
        f"{topic} price wholesale",
        f"{topic} business contact",
    ]


def fetch_video_comments(video_id, api_key, max_results=100):
    """Fetch comments from a YouTube video using Data API v3."""
    url = 'https://www.googleapis.com/youtube/v3/commentThreads'
    comments = []
    page_token = None
    fetched = 0
    per_page = min(max_results, 100)

    while fetched < max_results:
        params = {
            'part': 'snippet',
            'videoId': video_id,
            'maxResults': per_page,
            'order': 'relevance',
            'textFormat': 'plainText',
            'key': api_key,
        }
        if page_token:
            params['pageToken'] = page_token

        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            error_data = resp.json().get('error', {})
            error_msg = error_data.get('message', f'API error {resp.status_code}')
            raise Exception(error_msg)

        data = resp.json()
        for item in data.get('items', []):
            snippet = item['snippet']['topLevelComment']['snippet']
            comments.append({
                'author': snippet.get('authorDisplayName', ''),
                'author_channel': snippet.get('authorChannelUrl', ''),
                'text': snippet.get('textDisplay', ''),
                'likes': snippet.get('likeCount', 0),
                'published': snippet.get('publishedAt', ''),
            })
            fetched += 1
            if fetched >= max_results:
                break

        page_token = data.get('nextPageToken')
        if not page_token:
            break

    return comments


def fetch_video_info(video_id, api_key):
    """Fetch basic video metadata."""
    url = 'https://www.googleapis.com/youtube/v3/videos'
    params = {
        'part': 'snippet,statistics',
        'id': video_id,
        'key': api_key,
    }
    resp = requests.get(url, params=params, timeout=10)
    if resp.status_code == 200:
        items = resp.json().get('items', [])
        if items:
            s = items[0]['snippet']
            stats = items[0].get('statistics', {})
            return {
                'title': s.get('title', ''),
                'channel': s.get('channelTitle', ''),
                'thumbnail': s.get('thumbnails', {}).get('medium', {}).get('url', ''),
                'views': int(stats.get('viewCount', 0)),
                'comments': int(stats.get('commentCount', 0)),
            }
    return None


def extract_leads_from_comments(comments, keyword_filter=''):
    """Scan comments for emails, phones, and business keywords. Return scored leads."""
    leads = []
    now = datetime.now(timezone.utc)
    kw_filter = keyword_filter.lower().strip()

    for c in comments:
        text = c['text']
        text_lower = text.lower()

        # If keyword filter set, skip comments that don't contain it
        if kw_filter and kw_filter not in text_lower:
            continue

        emails = EMAIL_PATTERN.findall(text)
        # Filter out common false-positive emails
        emails = [e for e in emails if not e.endswith(('.png', '.jpg', '.gif', '.mp4'))]

        phones = PHONE_PATTERN.findall(text)
        # Filter phone numbers — keep only those with 7+ digits
        phones = [p.strip() for p in phones if len(re.sub(r'\D', '', p)) >= 7]

        # Score calculation
        score = 0
        matched_keywords = []

        if emails:
            score += 50
        if phones:
            score += 40

        for kw, pts in BUSINESS_KEYWORDS.items():
            if kw in text_lower:
                score += pts
                matched_keywords.append(kw)

        if c['likes'] >= 5:
            score += 10

        # Recency bonus
        try:
            pub_date = datetime.fromisoformat(c['published'].replace('Z', '+00:00'))
            if (now - pub_date) < timedelta(days=30):
                score += 10
        except (ValueError, TypeError):
            pass

        leads.append({
            'author': c['author'],
            'author_channel': c['author_channel'],
            'text': text,
            'likes': c['likes'],
            'published': c['published'],
            'emails': emails,
            'phones': phones,
            'score': score,
            'keywords': matched_keywords,
        })

    # Sort by score descending
    leads.sort(key=lambda x: x['score'], reverse=True)
    return leads


@youtube_leads_bp.route('/')
def index():
    return render_template('youtube_leads/index.html')


@youtube_leads_bp.route('/scan', methods=['POST'])
def scan():
    api_key = AppSetting.get('youtube_api_key', '')
    if not api_key:
        flash('YouTube API key not configured. Go to Settings to add it.', 'warning')
        return redirect(url_for('youtube_leads.index'))

    video_url = request.form.get('video_url', '').strip()
    keyword_filter = request.form.get('keyword_filter', '').strip()
    max_results = int(request.form.get('max_results', 100))
    max_results = min(max(max_results, 10), 500)

    video_id = extract_video_id(video_url)
    if not video_id:
        flash('Invalid YouTube URL. Please enter a valid video link.', 'danger')
        return redirect(url_for('youtube_leads.index'))

    try:
        video_info = fetch_video_info(video_id, api_key)
        comments = fetch_video_comments(video_id, api_key, max_results=max_results)
        leads = extract_leads_from_comments(comments, keyword_filter)

        # Stats
        total_comments = len(comments)
        leads_with_contact = [l for l in leads if l['emails'] or l['phones']]
        high_intent = [l for l in leads if l['score'] >= 30]

        return render_template('youtube_leads/results.html',
                               leads=leads,
                               video_info=video_info,
                               video_id=video_id,
                               video_url=video_url,
                               keyword_filter=keyword_filter,
                               total_comments=total_comments,
                               leads_with_contact=len(leads_with_contact),
                               high_intent_count=len(high_intent))
    except Exception as e:
        flash(f'Error fetching comments: {e}', 'danger')
        return redirect(url_for('youtube_leads.index'))


@youtube_leads_bp.route('/save', methods=['POST'])
def save_leads():
    """Save selected leads as Contacts."""
    data = request.get_json()
    if not data or 'leads' not in data:
        return jsonify({'error': 'No leads provided'}), 400

    saved = 0
    for lead in data['leads']:
        name = lead.get('author', 'YouTube Lead')
        email = lead.get('email', '')
        phone = lead.get('phone', '')
        notes = lead.get('notes', '')

        # Skip if no contact info
        if not email and not phone:
            continue

        # Check for duplicate by email
        if email:
            existing = db.session.query(Contact).filter_by(email=email).first()
            if existing:
                continue

        contact = Contact(
            company_name=name,
            contact_person=name,
            email=email,
            phone=phone,
            whatsapp=phone,
            source='youtube',
            status='new',
            notes=notes,
        )
        db.session.add(contact)
        saved += 1

    db.session.commit()
    return jsonify({'saved': saved, 'message': f'{saved} lead(s) saved to Contacts'})


@youtube_leads_bp.route('/smart-scan', methods=['POST'])
def smart_scan():
    """AI-powered: type a topic like 'poultry', auto-find videos and scan comments."""
    yt_key = AppSetting.get('youtube_api_key', '')
    if not yt_key:
        flash('YouTube API key not configured. Go to Settings to add it.', 'warning')
        return redirect(url_for('youtube_leads.index'))

    topic = request.form.get('topic', '').strip()
    if not topic:
        flash('Enter a topic to search.', 'danger')
        return redirect(url_for('youtube_leads.index'))

    max_videos = int(request.form.get('max_videos', 3))
    max_videos = min(max(max_videos, 1), 10)
    comments_per_video = int(request.form.get('comments_per_video', 50))
    comments_per_video = min(max(comments_per_video, 10), 200)

    # Step 1: Generate smart search queries (AI if Gemini key available, else fallback)
    gemini_key = AppSetting.get('gemini_api_key', '')
    if gemini_key:
        queries = ai_generate_search_queries(topic, gemini_key)
    else:
        queries = [
            f"{topic} supplier India",
            f"{topic} price wholesale",
            f"{topic} business contact",
        ]

    # Step 2: Search YouTube for videos from each query
    all_videos = []
    seen_ids = set()
    for q in queries:
        try:
            videos = search_youtube_videos(q, yt_key, max_results=max_videos)
            for v in videos:
                if v['video_id'] not in seen_ids:
                    v['search_query'] = q
                    all_videos.append(v)
                    seen_ids.add(v['video_id'])
        except Exception:
            continue

    if not all_videos:
        flash(f'No YouTube videos found for "{topic}". Try a different topic.', 'warning')
        return redirect(url_for('youtube_leads.index'))

    # Step 3: Fetch comments from each video and extract leads
    all_leads = []
    video_infos = []
    total_comments = 0

    for v in all_videos[:max_videos * len(queries)]:
        try:
            comments = fetch_video_comments(v['video_id'], yt_key, max_results=comments_per_video)
            total_comments += len(comments)
            leads = extract_leads_from_comments(comments)
            for lead in leads:
                lead['video_title'] = v['title']
                lead['video_id'] = v['video_id']
            all_leads.extend(leads)
            video_infos.append({
                'video_id': v['video_id'],
                'title': v['title'],
                'channel': v['channel'],
                'thumbnail': v['thumbnail'],
                'search_query': v.get('search_query', ''),
                'comments_scanned': len(comments),
            })
        except Exception:
            continue

    # Sort all leads by score
    all_leads.sort(key=lambda x: x['score'], reverse=True)

    leads_with_contact = [l for l in all_leads if l['emails'] or l['phones']]
    high_intent = [l for l in all_leads if l['score'] >= 30]

    return render_template('youtube_leads/smart_results.html',
                           topic=topic,
                           queries=queries,
                           leads=all_leads,
                           video_infos=video_infos,
                           total_comments=total_comments,
                           total_videos=len(video_infos),
                           leads_with_contact=len(leads_with_contact),
                           high_intent_count=len(high_intent))
