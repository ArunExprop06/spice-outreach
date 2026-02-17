from flask import Blueprint, render_template
from app_package import db
from app_package.models import Contact, MessageLog, Brochure, SearchLog, ScheduledJob
from datetime import datetime, timezone, timedelta
from sqlalchemy import func

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
def index():
    total_contacts = db.session.query(Contact).count()
    new_contacts_today = db.session.query(Contact).filter(
        Contact.created_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
    ).count()

    emails_sent = db.session.query(MessageLog).filter_by(channel='email', status='sent').count()
    emails_today = db.session.query(MessageLog).filter(
        MessageLog.channel == 'email',
        MessageLog.status == 'sent',
        MessageLog.sent_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
    ).count()

    whatsapp_sent = db.session.query(MessageLog).filter_by(channel='whatsapp', status='sent').count()
    whatsapp_today = db.session.query(MessageLog).filter(
        MessageLog.channel == 'whatsapp',
        MessageLog.status == 'sent',
        MessageLog.sent_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
    ).count()

    brochure_count = db.session.query(Brochure).count()

    recent_messages = db.session.query(MessageLog).order_by(MessageLog.created_at.desc()).limit(10).all()
    recent_searches = db.session.query(SearchLog).order_by(SearchLog.created_at.desc()).limit(5).all()
    scheduled_jobs = db.session.query(ScheduledJob).all()

    # Contact status breakdown
    status_counts = db.session.query(
        Contact.status, func.count(Contact.id)
    ).group_by(Contact.status).all()

    return render_template('dashboard.html',
                           total_contacts=total_contacts,
                           new_contacts_today=new_contacts_today,
                           emails_sent=emails_sent,
                           emails_today=emails_today,
                           whatsapp_sent=whatsapp_sent,
                           whatsapp_today=whatsapp_today,
                           brochure_count=brochure_count,
                           recent_messages=recent_messages,
                           recent_searches=recent_searches,
                           scheduled_jobs=scheduled_jobs,
                           status_counts=dict(status_counts))
