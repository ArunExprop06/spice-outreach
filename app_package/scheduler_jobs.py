"""Scheduled job definitions for automated daily tasks."""
import logging
from datetime import datetime, timezone
from app_package import db
from app_package.models import (Contact, MessageLog, Brochure, AppSetting,
                                ScheduledJob, SearchLog, DealTracker, JobTracker,
                                HotelTracker)

logger = logging.getLogger(__name__)


def run_daily_search(app):
    """Automated daily search for new contacts."""
    with app.app_context():
        job = db.session.query(ScheduledJob).filter_by(job_name='daily_search').first()
        if not job or not job.is_enabled:
            return

        from app_package.routes.search import google_search, extract_contacts_from_url, check_duplicate

        api_key = AppSetting.get('google_api_key', '')
        cse_id = AppSetting.get('google_cse_id', '')
        if not api_key or not cse_id:
            job.last_run = datetime.now(timezone.utc)
            job.last_status = 'skipped'
            job.last_result = 'Google API not configured'
            db.session.commit()
            return

        queries = [
            'manufacturers India contact email',
            'exporters suppliers India email phone',
            'wholesale distributors India contact',
        ]
        import random
        query = random.choice(queries)

        results, error = google_search(query, api_key, cse_id)
        if error:
            job.last_run = datetime.now(timezone.utc)
            job.last_status = 'failed'
            job.last_result = error
            db.session.commit()
            return

        saved = 0
        for sr in results:
            scraped = extract_contacts_from_url(sr['link'])
            company = sr['title'].split(' - ')[0].split(' | ')[0].strip()[:200]
            email = scraped['emails'][0] if scraped['emails'] else ''

            if check_duplicate(company, email):
                continue

            contact = Contact(
                company_name=company,
                email=email,
                phone=scraped['phones'][0] if scraped['phones'] else '',
                website=sr['link'],
                source='google_search',
                category='Other'
            )
            db.session.add(contact)
            saved += 1

        log = SearchLog(query=query, source='google_api',
                        results_count=len(results), contacts_saved=saved)
        db.session.add(log)

        job.last_run = datetime.now(timezone.utc)
        job.last_status = 'success'
        job.last_result = f'Found {len(results)} results, saved {saved} contacts'
        db.session.commit()


def run_daily_email(app):
    """Send brochure email to new contacts."""
    with app.app_context():
        job = db.session.query(ScheduledJob).filter_by(job_name='daily_email').first()
        if not job or not job.is_enabled:
            return

        from app_package.routes.email_sender import send_single_email, render_email_template, get_smtp_config

        smtp_config = get_smtp_config()
        if not smtp_config['username']:
            job.last_run = datetime.now(timezone.utc)
            job.last_status = 'skipped'
            job.last_result = 'SMTP not configured'
            db.session.commit()
            return

        # Get new contacts with email that haven't been emailed
        contacts = db.session.query(Contact).filter(
            Contact.status == 'new',
            Contact.email != '',
            Contact.email.isnot(None)
        ).limit(20).all()

        brochure = db.session.query(Brochure).filter_by(is_default=True).first()

        context_base = {
            'sender_name': AppSetting.get('sender_name', ''),
            'sender_company': AppSetting.get('sender_company', ''),
            'sender_phone': AppSetting.get('sender_phone', ''),
        }

        sent = 0
        failed = 0
        import time
        for contact in contacts:
            ctx = {**context_base,
                   'company_name': contact.company_name,
                   'contact_person': contact.contact_person}
            subject, body = render_email_template('brochure_intro', ctx)

            log = MessageLog(
                contact_id=contact.id, channel='email',
                subject=subject, body_preview=body[:200],
                brochure_id=brochure.id if brochure else None,
                status='pending'
            )
            db.session.add(log)
            db.session.commit()

            success, error = send_single_email(contact, subject, body, brochure, smtp_config)
            log.status = 'sent' if success else 'failed'
            log.error_message = error
            log.sent_at = datetime.now(timezone.utc) if success else None
            if success:
                contact.status = 'contacted'
                sent += 1
            else:
                failed += 1
            db.session.commit()
            time.sleep(3)

        job.last_run = datetime.now(timezone.utc)
        job.last_status = 'success'
        job.last_result = f'Sent {sent}, failed {failed} out of {len(contacts)}'
        db.session.commit()


def run_daily_whatsapp(app):
    """Send WhatsApp intro to new contacts."""
    with app.app_context():
        job = db.session.query(ScheduledJob).filter_by(job_name='daily_whatsapp').first()
        if not job or not job.is_enabled:
            return

        from app_package.routes.whatsapp_sender import (
            send_whatsapp_pywhatkit, send_whatsapp_twilio, WA_MESSAGE_TEMPLATES
        )
        from jinja2 import Template

        wa_mode = AppSetting.get('whatsapp_mode', 'pywhatkit')

        contacts = db.session.query(Contact).filter(
            Contact.status == 'new',
            ((Contact.whatsapp != '') & (Contact.whatsapp.isnot(None))) |
            ((Contact.phone != '') & (Contact.phone.isnot(None)))
        ).limit(10).all()

        context_base = {
            'sender_name': AppSetting.get('sender_name', ''),
            'sender_company': AppSetting.get('sender_company', ''),
            'sender_phone': AppSetting.get('sender_phone', ''),
        }

        sent = 0
        failed = 0
        import time
        for contact in contacts:
            phone = contact.whatsapp or contact.phone
            if not phone:
                continue
            phone = phone.strip()
            if not phone.startswith('+'):
                phone = '+91' + phone.lstrip('0')

            ctx = {**context_base,
                   'company_name': contact.company_name,
                   'contact_person': contact.contact_person}
            tpl = WA_MESSAGE_TEMPLATES['intro']
            message = Template(tpl['body']).render(**ctx)

            log = MessageLog(
                contact_id=contact.id, channel='whatsapp',
                subject=f'WhatsApp to {phone}', body_preview=message[:200],
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
            if success:
                contact.status = 'contacted'
                sent += 1
            else:
                failed += 1
            db.session.commit()
            time.sleep(10)

        job.last_run = datetime.now(timezone.utc)
        job.last_status = 'success'
        job.last_result = f'Sent {sent}, failed {failed} out of {len(contacts)}'
        db.session.commit()


def run_daily_deal_check(app):
    """Check all active deal trackers for new listings and send alerts."""
    with app.app_context():
        from app_package.routes.deal_tracker import check_tracker, send_deal_alert

        trackers = db.session.query(DealTracker).filter_by(is_active=True).all()
        if not trackers:
            return

        total_new = 0
        for tracker in trackers:
            try:
                new_listings = check_tracker(tracker)
                if new_listings:
                    send_deal_alert(tracker, new_listings)
                    total_new += len(new_listings)
            except Exception as e:
                logger.error('Deal check failed for tracker %s: %s', tracker.id, e)

        logger.info('Daily deal check: %d trackers, %d new listings', len(trackers), total_new)


def run_daily_job_check(app):
    """Check all active job trackers for new listings and send alerts."""
    with app.app_context():
        from app_package.routes.job_tracker import check_job_tracker, send_job_alert

        trackers = db.session.query(JobTracker).filter_by(is_active=True).all()
        if not trackers:
            return

        total_new = 0
        for tracker in trackers:
            try:
                new_listings = check_job_tracker(tracker)
                if new_listings:
                    send_job_alert(tracker, new_listings)
                    total_new += len(new_listings)
            except Exception as e:
                logger.error('Job check failed for tracker %s: %s', tracker.id, e)

        logger.info('Daily job check: %d trackers, %d new jobs', len(trackers), total_new)


def run_daily_hotel_check(app):
    """Check all active hotel trackers for new listings and send alerts."""
    with app.app_context():
        from app_package.routes.hotel_tracker import check_hotel_tracker, send_hotel_alert

        trackers = db.session.query(HotelTracker).filter_by(is_active=True).all()
        if not trackers:
            return

        total_new = 0
        for tracker in trackers:
            try:
                new_listings = check_hotel_tracker(tracker)
                if new_listings:
                    send_hotel_alert(tracker, new_listings)
                    total_new += len(new_listings)
            except Exception as e:
                logger.error('Hotel check failed for tracker %s: %s', tracker.id, e)

        logger.info('Daily hotel check: %d trackers, %d new hotels', len(trackers), total_new)
