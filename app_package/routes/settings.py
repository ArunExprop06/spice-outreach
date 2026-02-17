from flask import Blueprint, render_template, request, redirect, url_for, flash
from app_package import db
from app_package.models import AppSetting, ScheduledJob

settings_bp = Blueprint('settings', __name__)


@settings_bp.route('/', methods=['GET', 'POST'])
def settings_page():
    if request.method == 'POST':
        section = request.form.get('section', '')

        if section == 'sender':
            AppSetting.set('sender_name', request.form.get('sender_name', ''))
            AppSetting.set('sender_company', request.form.get('sender_company', ''))
            AppSetting.set('sender_phone', request.form.get('sender_phone', ''))
            flash('Sender info updated.', 'success')

        elif section == 'smtp':
            AppSetting.set('smtp_host', request.form.get('smtp_host', ''))
            AppSetting.set('smtp_port', request.form.get('smtp_port', '587'))
            AppSetting.set('smtp_username', request.form.get('smtp_username', ''))
            password = request.form.get('smtp_password', '')
            if password:  # Only update if a new password is provided
                AppSetting.set('smtp_password', password, encrypted=True)
            AppSetting.set('smtp_from_email', request.form.get('smtp_from_email', ''))
            AppSetting.set('smtp_from_name', request.form.get('smtp_from_name', ''))
            flash('SMTP settings updated.', 'success')

        elif section == 'whatsapp':
            AppSetting.set('whatsapp_mode', request.form.get('whatsapp_mode', 'pywhatkit'))
            account_sid = request.form.get('twilio_account_sid', '')
            if account_sid:
                AppSetting.set('twilio_account_sid', account_sid, encrypted=True)
            auth_token = request.form.get('twilio_auth_token', '')
            if auth_token:
                AppSetting.set('twilio_auth_token', auth_token, encrypted=True)
            AppSetting.set('twilio_whatsapp_number', request.form.get('twilio_whatsapp_number', ''))
            flash('WhatsApp settings updated.', 'success')

        elif section == 'google':
            api_key = request.form.get('google_api_key', '')
            if api_key:
                AppSetting.set('google_api_key', api_key, encrypted=True)
            AppSetting.set('google_cse_id', request.form.get('google_cse_id', ''))
            flash('Google Search settings updated.', 'success')

        elif section == 'serpapi':
            api_key = request.form.get('serpapi_key', '')
            if api_key:
                AppSetting.set('serpapi_key', api_key, encrypted=True)
            flash('SerpAPI settings updated.', 'success')

        elif section == 'gemini':
            api_key = request.form.get('gemini_api_key', '')
            if api_key:
                AppSetting.set('gemini_api_key', api_key, encrypted=True)
            flash('Gemini AI settings updated.', 'success')

        elif section == 'youtube':
            api_key = request.form.get('youtube_api_key', '')
            if api_key:
                AppSetting.set('youtube_api_key', api_key, encrypted=True)
            flash('YouTube API settings updated.', 'success')

        elif section == 'facebook':
            AppSetting.set('fb_page_id', request.form.get('fb_page_id', ''))
            AppSetting.set('fb_group_ids', request.form.get('fb_group_ids', ''))
            token = request.form.get('fb_access_token', '')
            if token:
                AppSetting.set('fb_access_token', token, encrypted=True)
            flash('Facebook settings updated.', 'success')

        elif section == 'scheduler':
            job_types = ['daily_search', 'daily_email', 'daily_whatsapp', 'daily_facebook']
            for jt in job_types:
                job = db.session.query(ScheduledJob).filter_by(job_name=jt).first()
                if not job:
                    job = ScheduledJob(
                        job_name=jt,
                        job_type=jt.replace('daily_', ''),
                    )
                    db.session.add(job)

                job.is_enabled = request.form.get(f'{jt}_enabled') == 'on'
                hour = request.form.get(f'{jt}_hour', '9')
                minute = request.form.get(f'{jt}_minute', '0')
                job.schedule_hour = int(hour) if hour.isdigit() else 9
                job.schedule_minute = int(minute) if minute.isdigit() else 0

            db.session.commit()
            flash('Scheduler settings updated.', 'success')

        return redirect(url_for('settings.settings_page'))

    # Load current settings
    settings = {
        'sender_name': AppSetting.get('sender_name', ''),
        'sender_company': AppSetting.get('sender_company', ''),
        'sender_phone': AppSetting.get('sender_phone', ''),
        'smtp_host': AppSetting.get('smtp_host', 'smtp.gmail.com'),
        'smtp_port': AppSetting.get('smtp_port', '587'),
        'smtp_username': AppSetting.get('smtp_username', ''),
        'smtp_from_email': AppSetting.get('smtp_from_email', ''),
        'smtp_from_name': AppSetting.get('smtp_from_name', ''),
        'whatsapp_mode': AppSetting.get('whatsapp_mode', 'pywhatkit'),
        'twilio_whatsapp_number': AppSetting.get('twilio_whatsapp_number', ''),
        'google_cse_id': AppSetting.get('google_cse_id', ''),
        'fb_page_id': AppSetting.get('fb_page_id', ''),
        'fb_group_ids': AppSetting.get('fb_group_ids', ''),
    }

    # Has password/keys set?
    smtp_pwd_set = bool(db.session.query(AppSetting).filter_by(key='smtp_password').first()
                        and db.session.query(AppSetting).filter_by(key='smtp_password').first().value)
    google_key_set = bool(db.session.query(AppSetting).filter_by(key='google_api_key').first()
                          and db.session.query(AppSetting).filter_by(key='google_api_key').first().value)
    twilio_set = bool(db.session.query(AppSetting).filter_by(key='twilio_account_sid').first()
                      and db.session.query(AppSetting).filter_by(key='twilio_account_sid').first().value)
    fb_token_set = bool(db.session.query(AppSetting).filter_by(key='fb_access_token').first()
                        and db.session.query(AppSetting).filter_by(key='fb_access_token').first().value)
    serpapi_key_set = bool(db.session.query(AppSetting).filter_by(key='serpapi_key').first()
                           and db.session.query(AppSetting).filter_by(key='serpapi_key').first().value)
    gemini_key_set = bool(db.session.query(AppSetting).filter_by(key='gemini_api_key').first()
                          and db.session.query(AppSetting).filter_by(key='gemini_api_key').first().value)
    youtube_key_set = bool(db.session.query(AppSetting).filter_by(key='youtube_api_key').first()
                           and db.session.query(AppSetting).filter_by(key='youtube_api_key').first().value)

    # Scheduler jobs
    jobs = {j.job_name: j for j in db.session.query(ScheduledJob).all()}

    return render_template('settings/index.html',
                           settings=settings,
                           smtp_pwd_set=smtp_pwd_set,
                           google_key_set=google_key_set,
                           twilio_set=twilio_set,
                           fb_token_set=fb_token_set,
                           serpapi_key_set=serpapi_key_set,
                           gemini_key_set=gemini_key_set,
                           youtube_key_set=youtube_key_set,
                           jobs=jobs)
