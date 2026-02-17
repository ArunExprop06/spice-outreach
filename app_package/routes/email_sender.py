import os
import smtplib
import threading
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timezone, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, current_app, jsonify)
from jinja2 import Template
from app_package import db
from app_package.models import Contact, MessageLog, Brochure, AppSetting

email_bp = Blueprint('email', __name__)

EMAIL_TEMPLATES = {
    'brochure_intro': {
        'name': 'Product Introduction',
        'subject': 'Business Inquiry - {{ sender_company }} to {{ company_name }}',
        'body': '''<html><body style="font-family:Arial,sans-serif;color:#333">
<p>Dear {{ contact_person or "Sir/Madam" }},</p>

<p>Greetings from <strong>{{ sender_company }}</strong>!</p>

<p>We would like to introduce our products and services to <strong>{{ company_name }}</strong>.</p>

<p>Please find our product brochure attached for your reference. We offer a wide range of quality products with competitive pricing.</p>

<p>We would love to explore a business association with your esteemed organization. Please let us know a convenient time to discuss further.</p>

<p>Best Regards,<br>
{{ sender_name }}<br>
{{ sender_company }}<br>
{{ sender_phone }}</p>
</body></html>'''
    },
    'follow_up': {
        'name': 'Follow-up Email',
        'subject': 'Following up - {{ sender_company }} to {{ company_name }}',
        'body': '''<html><body style="font-family:Arial,sans-serif;color:#333">
<p>Dear {{ contact_person or "Sir/Madam" }},</p>

<p>I hope this email finds you well. I am following up on my previous email regarding our products.</p>

<p>We recently sent you our product brochure and would love to hear your thoughts. We offer competitive pricing and can customize as per your specific requirements.</p>

<p>Would you be available for a quick call this week to discuss potential collaboration?</p>

<p>Looking forward to hearing from you.</p>

<p>Best Regards,<br>
{{ sender_name }}<br>
{{ sender_company }}<br>
{{ sender_phone }}</p>
</body></html>'''
    }
}


def get_smtp_config():
    return {
        'host': AppSetting.get('smtp_host', 'smtp.gmail.com'),
        'port': int(AppSetting.get('smtp_port', '587')),
        'username': AppSetting.get('smtp_username', ''),
        'password': AppSetting.get('smtp_password', ''),
        'from_email': AppSetting.get('smtp_from_email', ''),
        'from_name': AppSetting.get('smtp_from_name', ''),
    }


def send_single_email(contact, subject, html_body, brochure=None, smtp_config=None):
    """Send a single email. Returns (success, error_message)."""
    if not smtp_config:
        smtp_config = get_smtp_config()

    if not smtp_config['username'] or not smtp_config['password']:
        return False, 'SMTP not configured'

    if not contact.email:
        return False, 'Contact has no email'

    try:
        msg = MIMEMultipart()
        msg['From'] = f"{smtp_config['from_name']} <{smtp_config['from_email']}>" if smtp_config['from_name'] else smtp_config['from_email']
        msg['To'] = contact.email
        msg['Subject'] = subject

        msg.attach(MIMEText(html_body, 'html'))

        # Attach brochure if provided
        if brochure:
            brochure_path = os.path.join(
                current_app.config['BROCHURE_FOLDER'], brochure.stored_filename
            )
            if os.path.exists(brochure_path):
                with open(brochure_path, 'rb') as f:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition',
                                    f'attachment; filename="{brochure.original_filename}"')
                    msg.attach(part)

        server = smtplib.SMTP(smtp_config['host'], smtp_config['port'], timeout=30)
        server.starttls()
        server.login(smtp_config['username'], smtp_config['password'])
        server.send_message(msg)
        server.quit()
        return True, ''
    except Exception as e:
        return False, str(e)


def render_email_template(template_key, context):
    """Render an email template with given context."""
    tpl = EMAIL_TEMPLATES.get(template_key, {})
    subject = Template(tpl.get('subject', '')).render(**context)
    body = Template(tpl.get('body', '')).render(**context)
    return subject, body


def get_rate_limit_status():
    """Check current email rate limits."""
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    sent_this_hour = db.session.query(MessageLog).filter(
        MessageLog.channel == 'email',
        MessageLog.status == 'sent',
        MessageLog.sent_at >= hour_ago
    ).count()

    sent_today = db.session.query(MessageLog).filter(
        MessageLog.channel == 'email',
        MessageLog.status == 'sent',
        MessageLog.sent_at >= day_start
    ).count()

    return sent_this_hour, sent_today


@email_bp.route('/')
@email_bp.route('/compose')
def compose():
    contacts = db.session.query(Contact).filter(Contact.email != '', Contact.email.isnot(None)).order_by(Contact.company_name).all()
    brochures = db.session.query(Brochure).order_by(Brochure.created_at.desc()).all()
    templates = {k: v['name'] for k, v in EMAIL_TEMPLATES.items()}
    sent_hour, sent_day = get_rate_limit_status()
    return render_template('email/compose.html',
                           contacts=contacts, brochures=brochures,
                           templates=templates,
                           sent_hour=sent_hour, sent_day=sent_day,
                           rate_hour=current_app.config['EMAIL_RATE_PER_HOUR'],
                           rate_day=current_app.config['EMAIL_RATE_PER_DAY'])


@email_bp.route('/send', methods=['POST'])
def send():
    contact_ids = request.form.getlist('contact_ids')
    template_key = request.form.get('template', 'brochure_intro')
    brochure_id = request.form.get('brochure_id')
    custom_subject = request.form.get('custom_subject', '').strip()
    custom_body = request.form.get('custom_body', '').strip()

    if not contact_ids:
        flash('Select at least one contact.', 'error')
        return redirect(url_for('email.compose'))

    brochure = None
    if brochure_id:
        brochure = db.session.get(Brochure, int(brochure_id))

    contacts = db.session.query(Contact).filter(Contact.id.in_([int(i) for i in contact_ids])).all()

    # Check rate limits
    sent_hour, sent_day = get_rate_limit_status()
    remaining_hour = max(0, current_app.config['EMAIL_RATE_PER_HOUR'] - sent_hour)
    remaining_day = max(0, current_app.config['EMAIL_RATE_PER_DAY'] - sent_day)
    max_sendable = min(remaining_hour, remaining_day, len(contacts))

    if max_sendable == 0:
        flash('Rate limit reached. Try again later.', 'error')
        return redirect(url_for('email.compose'))

    contacts = contacts[:max_sendable]
    smtp_config = get_smtp_config()

    context_base = {
        'sender_name': AppSetting.get('sender_name', ''),
        'sender_company': AppSetting.get('sender_company', ''),
        'sender_phone': AppSetting.get('sender_phone', ''),
    }

    # Send in background thread for batch
    app = current_app._get_current_object()

    def send_batch():
        with app.app_context():
            for contact in contacts:
                ctx = {**context_base,
                       'company_name': contact.company_name,
                       'contact_person': contact.contact_person}

                if custom_subject and custom_body:
                    subject = Template(custom_subject).render(**ctx)
                    body = custom_body
                else:
                    subject, body = render_email_template(template_key, ctx)

                log = MessageLog(
                    contact_id=contact.id,
                    channel='email',
                    subject=subject,
                    body_preview=body[:200],
                    brochure_id=brochure.id if brochure else None,
                    status='pending'
                )
                db.session.add(log)
                db.session.commit()

                success, error = send_single_email(contact, subject, body, brochure, smtp_config)
                log.status = 'sent' if success else 'failed'
                log.error_message = error
                log.sent_at = datetime.now(timezone.utc) if success else None

                # Update contact status
                if success and contact.status == 'new':
                    contact.status = 'contacted'

                db.session.commit()
                time.sleep(3)  # Rate limiting delay between sends

    thread = threading.Thread(target=send_batch)
    thread.start()

    flash(f'Sending emails to {len(contacts)} contacts in background...', 'success')
    return redirect(url_for('email.compose'))


@email_bp.route('/test-smtp', methods=['POST'])
def test_smtp():
    """Test SMTP connection."""
    config = get_smtp_config()
    if not config['username'] or not config['password']:
        return jsonify({'success': False, 'error': 'SMTP credentials not configured. Go to Settings first.'})

    try:
        server = smtplib.SMTP(config['host'], config['port'], timeout=15)
        server.starttls()
        server.login(config['username'], config['password'])
        server.quit()
        return jsonify({'success': True, 'message': 'SMTP connection successful!'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@email_bp.route('/preview-template', methods=['POST'])
def preview_template():
    template_key = request.form.get('template', 'brochure_intro')
    ctx = {
        'company_name': 'Sample Spice Co.',
        'contact_person': 'Mr. Sharma',
        'sender_name': AppSetting.get('sender_name', 'Your Name'),
        'sender_company': AppSetting.get('sender_company', 'Your Company'),
        'sender_phone': AppSetting.get('sender_phone', '+91-XXXXXXXXXX'),
    }
    subject, body = render_email_template(template_key, ctx)
    return jsonify({'subject': subject, 'body': body})
