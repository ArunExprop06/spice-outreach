import json
import time
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify)
from app_package import db
from app_package.models import AppSetting

ai_assistant_bp = Blueprint('ai_assistant', __name__)

# Models to try in order (fallback chain)
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
]


def get_gemini_response(prompt, api_key):
    """Call Google Gemini API with retry and model fallback."""
    import requests as req

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048,
        }
    }

    last_error = None
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        for attempt in range(3):
            try:
                resp = req.post(url, json=payload, timeout=30)
                if resp.status_code == 429:
                    wait = 5 + attempt * 5  # 5s, 10s, 15s
                    last_error = f"Rate limited on {model} (attempt {attempt + 1}). "
                    time.sleep(wait)
                    continue
                if resp.status_code == 404:
                    last_error = f"Model {model} not available. "
                    break  # Skip to next model
                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return text, None
            except Exception as e:
                last_error = str(e)
                break  # Try next model

    return None, f"{last_error}Please wait a minute and try again. Free tier allows 15 requests/min. If this keeps happening, check your API key billing at aistudio.google.com."


def parse_ai_response(raw_text):
    """Try to parse JSON from AI response, handling markdown code fences."""
    text = raw_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


@ai_assistant_bp.route('/')
def index():
    gemini_key = AppSetting.get('gemini_api_key', '')
    return render_template('ai_assistant/index.html',
                           has_gemini=bool(gemini_key))


@ai_assistant_bp.route('/analyze', methods=['POST'])
def analyze_product():
    product = request.form.get('product', '').strip()
    if not product:
        flash('Enter a product name.', 'error')
        return redirect(url_for('ai_assistant.index'))

    gemini_key = AppSetting.get('gemini_api_key', '')
    if not gemini_key:
        flash('Gemini API key not configured. Go to Settings to add it.', 'warning')
        return redirect(url_for('ai_assistant.index'))

    prompt = f"""You are a business intelligence analyst specializing in Indian B2B markets.

Analyze this product: "{product}"

Return a JSON object (no markdown, just raw JSON) with these keys:

{{
  "product_name": "{product}",
  "market_rating": <number 1-10>,
  "market_summary": "<2-3 sentence summary of market potential in India>",
  "strengths": ["<strength 1>", "<strength 2>", "<strength 3>"],
  "improvements": [
    {{"title": "<suggestion title>", "detail": "<1-2 sentence explanation>"}},
    {{"title": "<suggestion title>", "detail": "<1-2 sentence explanation>"}},
    {{"title": "<suggestion title>", "detail": "<1-2 sentence explanation>"}},
    {{"title": "<suggestion title>", "detail": "<1-2 sentence explanation>"}}
  ],
  "target_industries": ["<industry 1>", "<industry 2>", "<industry 3>", "<industry 4>"],
  "lead_search_queries": [
    "<Google search query to find buyers/suppliers 1>",
    "<Google search query to find buyers/suppliers 2>",
    "<Google search query to find buyers/suppliers 3>",
    "<Google search query to find buyers/suppliers 4>",
    "<Google search query to find buyers/suppliers 5>"
  ],
  "email_pitch": "<Short 3-4 sentence cold email pitch for this product>",
  "pricing_insight": "<1-2 sentence insight about typical pricing or market rates>"
}}

Focus on the Indian market. Make the search queries specific and useful for finding real business contacts (include words like 'email', 'contact', 'supplier', 'buyer', 'India').
Return ONLY valid JSON, no extra text."""

    raw_response, error = get_gemini_response(prompt, gemini_key)
    if error:
        flash(f'AI error: {error}', 'error')
        return redirect(url_for('ai_assistant.index'))

    analysis = parse_ai_response(raw_response)
    if not analysis:
        # If JSON parsing fails, show raw response
        return render_template('ai_assistant/results.html',
                               product=product, analysis=None,
                               raw_response=raw_response)

    return render_template('ai_assistant/results.html',
                           product=product, analysis=analysis,
                           raw_response=None)
