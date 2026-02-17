from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from flask_apscheduler import APScheduler
from config import Config

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()
scheduler = APScheduler()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    # Ensure upload directories exist
    import os
    os.makedirs(app.config.get('BROCHURE_FOLDER', ''), exist_ok=True)
    os.makedirs(app.config.get('CSV_TEMP_FOLDER', ''), exist_ok=True)

    # Register blueprints
    from app_package.routes.dashboard import dashboard_bp
    from app_package.routes.contacts import contacts_bp
    from app_package.routes.brochures import brochures_bp
    from app_package.routes.email_sender import email_bp
    from app_package.routes.whatsapp_sender import whatsapp_bp
    from app_package.routes.search import search_bp
    from app_package.routes.settings import settings_bp
    from app_package.routes.facebook import facebook_bp
    from app_package.routes.deal_tracker import deal_tracker_bp
    from app_package.routes.job_tracker import job_tracker_bp
    from app_package.routes.hotel_tracker import hotel_tracker_bp
    from app_package.routes.ai_assistant import ai_assistant_bp
    from app_package.routes.youtube_leads import youtube_leads_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(contacts_bp, url_prefix='/contacts')
    app.register_blueprint(brochures_bp, url_prefix='/brochures')
    app.register_blueprint(email_bp, url_prefix='/email')
    app.register_blueprint(whatsapp_bp, url_prefix='/whatsapp')
    app.register_blueprint(search_bp, url_prefix='/search')
    app.register_blueprint(facebook_bp, url_prefix='/facebook')
    app.register_blueprint(deal_tracker_bp, url_prefix='/deals')
    app.register_blueprint(job_tracker_bp, url_prefix='/jobs')
    app.register_blueprint(hotel_tracker_bp, url_prefix='/hotels')
    app.register_blueprint(settings_bp, url_prefix='/settings')
    app.register_blueprint(ai_assistant_bp, url_prefix='/ai')
    app.register_blueprint(youtube_leads_bp, url_prefix='/youtube')

    with app.app_context():
        from app_package import models  # noqa: F401
        db.create_all()

        # Migrate existing candidate tables — add new columns if missing
        _migrate_candidate_columns(db)

    # Initialize scheduler with jobs
    _setup_scheduler(app)

    return app


def _setup_scheduler(app):
    """Configure APScheduler with daily automation jobs."""
    from app_package.scheduler_jobs import (
        run_daily_search, run_daily_email, run_daily_whatsapp,
        run_daily_deal_check, run_daily_job_check, run_daily_hotel_check
    )
    from app_package.routes.facebook import auto_fetch_fb_enquiries

    # Default jobs - will be overridden by DB settings at runtime
    app.config['JOBS'] = [
        {
            'id': 'daily_search',
            'func': lambda: run_daily_search(app),
            'trigger': 'cron',
            'hour': 9,
            'minute': 0,
        },
        {
            'id': 'daily_email',
            'func': lambda: run_daily_email(app),
            'trigger': 'cron',
            'hour': 10,
            'minute': 0,
        },
        {
            'id': 'daily_whatsapp',
            'func': lambda: run_daily_whatsapp(app),
            'trigger': 'cron',
            'hour': 11,
            'minute': 0,
        },
        {
            'id': 'daily_facebook',
            'func': lambda: auto_fetch_fb_enquiries(app),
            'trigger': 'cron',
            'hour': 8,
            'minute': 30,
        },
        {
            'id': 'daily_deal_check',
            'func': lambda: run_daily_deal_check(app),
            'trigger': 'cron',
            'hour': 8,
            'minute': 0,
        },
        {
            'id': 'daily_job_check',
            'func': lambda: run_daily_job_check(app),
            'trigger': 'cron',
            'hour': 7,
            'minute': 30,
        },
        {
            'id': 'daily_hotel_check',
            'func': lambda: run_daily_hotel_check(app),
            'trigger': 'cron',
            'hour': 7,
            'minute': 0,
        },
    ]

    scheduler.init_app(app)
    scheduler.start()

    # Update job schedules from DB
    with app.app_context():
        from app_package.models import ScheduledJob as SJ
        for job_record in db.session.query(SJ).all():
            try:
                scheduler.modify_job(
                    job_record.job_name,
                    trigger='cron',
                    hour=job_record.schedule_hour,
                    minute=job_record.schedule_minute
                )
                if not job_record.is_enabled:
                    scheduler.pause_job(job_record.job_name)
            except Exception:
                pass


def _migrate_candidate_columns(database):
    """Add new candidate columns to existing SQLite databases."""
    migrations = [
        ('candidate_searches', 'platforms', "ALTER TABLE candidate_searches ADD COLUMN platforms TEXT DEFAULT '[\"linkedin\"]'"),
        ('candidate_results', 'platform', "ALTER TABLE candidate_results ADD COLUMN platform VARCHAR(50) DEFAULT 'linkedin'"),
        ('candidate_results', 'instagram_url', "ALTER TABLE candidate_results ADD COLUMN instagram_url VARCHAR(1000) DEFAULT ''"),
    ]
    for table, column, sql in migrations:
        try:
            database.session.execute(database.text(
                f"SELECT {column} FROM {table} LIMIT 1"))
        except Exception:
            try:
                database.session.execute(database.text(sql))
                database.session.commit()
            except Exception:
                database.session.rollback()
