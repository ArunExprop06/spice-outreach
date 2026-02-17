import threading
import time
from datetime import datetime, timezone
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, current_app, jsonify)
from jinja2 import Template
from app_package import db
from app_package.models import Contact, MessageLog, Brochure, AppSetting

whatsapp_bp = Blueprint('whatsapp', __name__)

WA_MESSAGE_TEMPLATES = {
    'intro': {
        'name': 'Product Introduction',
        'body': '''Namaste {{ contact_person or "Sir/Madam" }}!

I am {{ sender_name }} from {{ sender_company }}.

We would like to introduce our products and services to {{ company_name }}.

Can we share our product brochure with you? We offer quality products at competitive pricing.

Regards,
{{ sender_name }}
{{ sender_phone }}'''
    },
    'follow_up': {
        'name': 'Follow-up',
        'body': '''Hello {{ contact_person or "Sir/Madam" }},

This is {{ sender_name }} from {{ sender_company }}. I had reached out earlier about our products.

Would you be interested in discussing further? We offer competitive pricing and can customize as per your needs.

Looking forward to hearing from you!

{{ sender_name }}
{{ sender_phone }}'''
    }
}


def send_whatsapp_pywhatkit(phone, message):
    """Send WhatsApp via pywhatkit (requires WhatsApp Web in Chrome)."""
    try:
        import pywhatkit
        # pywhatkit sends at a specific time; use sendwhatmsg_instantly if available
        pywhatkit.sendwhatmsg_instantly(phone, message, wait_time=15, tab_close=True)
        return True, ''
    except Exception as e:
        return False, str(e)


def send_whatsapp_twilio(phone, message, media_url=None):
    """Send WhatsApp via Twilio API."""
    try:
        from twilio.rest import Client
        account_sid = AppSetting.get('twilio_account_sid', '')
        auth_token = AppSetting.get('twilio_auth_token', '')
        from_number = AppSetting.get('twilio_whatsapp_number', '')

        if not all([account_sid, auth_token, from_number]):
            return False, 'Twilio credentials not configured'

        client = Client(account_sid, auth_token)

        kwargs = {
            'body': message,
            'from_': f'whatsapp:{from_number}',
            'to': f'whatsapp:{phone}'
        }
        if media_url:
            kwargs['media_url'] = [media_url]

        msg = client.messages.create(**kwargs)
        return True, msg.sid
    except Exception as e:
        return False, str(e)


@whatsapp_bp.route('/')
@whatsapp_bp.route('/compose')
def compose():
    contacts = db.session.query(Contact).filter(
        ((Contact.whatsapp != '') & (Contact.whatsapp.isnot(None))) |
        ((Contact.phone != '') & (Contact.phone.isnot(None)))
    ).order_by(Contact.company_name).all()
    templates = {k: v['name'] for k, v in WA_MESSAGE_TEMPLATES.items()}
    wa_mode = AppSetting.get('whatsapp_mode', 'pywhatkit')
    return render_template('whatsapp/compose.html',
                           contacts=contacts, templates=templates, wa_mode=wa_mode)


@whatsapp_bp.route('/send', methods=['POST'])
def send():
    contact_ids = request.form.getlist('contact_ids')
    template_key = request.form.get('template', 'intro')
    custom_message = request.form.get('custom_message', '').strip()

    if not contact_ids:
        flash('Select at least one contact.', 'error')
        return redirect(url_for('whatsapp.compose'))

    contacts = db.session.query(Contact).filter(Contact.id.in_([int(i) for i in contact_ids])).all()
    wa_mode = AppSetting.get('whatsapp_mode', 'pywhatkit')

    context_base = {
        'sender_name': AppSetting.get('sender_name', ''),
        'sender_company': AppSetting.get('sender_company', ''),
        'sender_phone': AppSetting.get('sender_phone', ''),
    }

    app = current_app._get_current_object()

    def send_batch():
        with app.app_context():
            for contact in contacts:
                phone = contact.whatsapp or contact.phone
                if not phone:
                    continue

                # Ensure proper format
                phone = phone.strip()
                if not phone.startswith('+'):
                    phone = '+91' + phone.lstrip('0')

                ctx = {**context_base,
                       'company_name': contact.company_name,
                       'contact_person': contact.contact_person}

                if custom_message:
                    message = Template(custom_message).render(**ctx)
                else:
                    tpl = WA_MESSAGE_TEMPLATES.get(template_key, WA_MESSAGE_TEMPLATES['intro'])
                    message = Template(tpl['body']).render(**ctx)

                log = MessageLog(
                    contact_id=contact.id,
                    channel='whatsapp',
                    subject=f'WhatsApp to {phone}',
                    body_preview=message[:200],
                    status='pending'
                )
                db.session.add(log)
                db.session.commit()

                if wa_mode == 'twilio':
                    success, error = send_whatsapp_twilio(phone, message)
                else:
                    success, error = send_whatsapp_pywhatkit(phone, message)

                log.status = 'sent' if success else 'failed'
                log.error_message = error if not success else ''
                log.sent_at = datetime.now(timezone.utc) if success else None

                if success and contact.status == 'new':
                    contact.status = 'contacted'

                db.session.commit()
                time.sleep(10)  # Longer delay for WhatsApp

    thread = threading.Thread(target=send_batch)
    thread.start()

    flash(f'Sending WhatsApp messages to {len(contacts)} contacts in background...', 'success')
    return redirect(url_for('whatsapp.compose'))


@whatsapp_bp.route('/preview-template', methods=['POST'])
def preview_template():
    template_key = request.form.get('template', 'intro')
    ctx = {
        'company_name': 'Sample Spice Co.',
        'contact_person': 'Mr. Sharma',
        'sender_name': AppSetting.get('sender_name', 'Your Name'),
        'sender_company': AppSetting.get('sender_company', 'Your Company'),
        'sender_phone': AppSetting.get('sender_phone', '+91-XXXXXXXXXX'),
    }
    tpl = WA_MESSAGE_TEMPLATES.get(template_key, WA_MESSAGE_TEMPLATES['intro'])
    body = Template(tpl['body']).render(**ctx)
    return jsonify({'body': body})
