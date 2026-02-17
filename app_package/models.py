from datetime import datetime, timezone
from app_package import db


class Contact(db.Model):
    __tablename__ = 'contacts'

    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(200), nullable=False)
    contact_person = db.Column(db.String(200), default='')
    email = db.Column(db.String(200), default='')
    phone = db.Column(db.String(50), default='')
    whatsapp = db.Column(db.String(50), default='')
    website = db.Column(db.String(300), default='')
    city = db.Column(db.String(100), default='')
    state = db.Column(db.String(100), default='')
    country = db.Column(db.String(100), default='India')
    category = db.Column(db.String(100), default='Other')
    notes = db.Column(db.Text, default='')
    source = db.Column(db.String(100), default='manual')  # manual, csv, google_search
    status = db.Column(db.String(50), default='new')  # new, contacted, responded, converted, inactive
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    messages = db.relationship('MessageLog', backref='contact', lazy='dynamic',
                               cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Contact {self.company_name}>'


class MessageLog(db.Model):
    __tablename__ = 'message_logs'

    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contacts.id'), nullable=False)
    channel = db.Column(db.String(20), nullable=False)  # email, whatsapp
    status = db.Column(db.String(20), default='pending')  # pending, sent, failed, delivered
    subject = db.Column(db.String(300), default='')
    body_preview = db.Column(db.Text, default='')
    brochure_id = db.Column(db.Integer, db.ForeignKey('brochures.id'), nullable=True)
    error_message = db.Column(db.Text, default='')
    sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    brochure = db.relationship('Brochure', backref='message_logs')

    def __repr__(self):
        return f'<MessageLog {self.channel} to contact {self.contact_id}>'


class Brochure(db.Model):
    __tablename__ = 'brochures'

    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(300), nullable=False)
    stored_filename = db.Column(db.String(300), nullable=False, unique=True)
    file_type = db.Column(db.String(10), nullable=False)  # pdf, png, jpg
    file_size = db.Column(db.Integer, default=0)
    is_default = db.Column(db.Boolean, default=False)
    description = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<Brochure {self.original_filename}>'


class SearchLog(db.Model):
    __tablename__ = 'search_logs'

    id = db.Column(db.Integer, primary_key=True)
    query = db.Column(db.String(500), nullable=False)
    source = db.Column(db.String(50), default='google_api')  # google_api, scraping
    results_count = db.Column(db.Integer, default=0)
    contacts_saved = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<SearchLog "{self.query}">'


class AppSetting(db.Model):
    __tablename__ = 'app_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, default='')
    is_encrypted = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    @staticmethod
    def get(key, default=''):
        setting = db.session.query(AppSetting).filter_by(key=key).first()
        if setting is None:
            return default
        if setting.is_encrypted and setting.value:
            from config import Config
            try:
                fernet = Config.get_fernet()
                return fernet.decrypt(setting.value.encode()).decode()
            except Exception:
                return default
        return setting.value

    @staticmethod
    def set(key, value, encrypted=False):
        from app_package import db as _db
        setting = db.session.query(AppSetting).filter_by(key=key).first()
        store_value = value
        if encrypted and value:
            from config import Config
            fernet = Config.get_fernet()
            store_value = fernet.encrypt(value.encode()).decode()
        if setting is None:
            setting = AppSetting(key=key, value=store_value, is_encrypted=encrypted)
            _db.session.add(setting)
        else:
            setting.value = store_value
            setting.is_encrypted = encrypted
        _db.session.commit()

    def __repr__(self):
        return f'<AppSetting {self.key}>'


class FacebookSource(db.Model):
    __tablename__ = 'facebook_sources'

    id = db.Column(db.Integer, primary_key=True)
    fb_id = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(300), default='')
    source_type = db.Column(db.String(50), default='page')  # page, group
    category = db.Column(db.String(200), default='')
    description = db.Column(db.Text, default='')
    member_count = db.Column(db.Integer, default=0)
    fan_count = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    last_scanned = db.Column(db.DateTime, nullable=True)
    leads_found = db.Column(db.Integer, default=0)
    added_keyword = db.Column(db.String(200), default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<FacebookSource {self.source_type}:{self.name}>'


class ScheduledJob(db.Model):
    __tablename__ = 'scheduled_jobs'

    id = db.Column(db.Integer, primary_key=True)
    job_name = db.Column(db.String(100), unique=True, nullable=False)
    job_type = db.Column(db.String(50), nullable=False)  # search, email_blast, whatsapp_blast
    is_enabled = db.Column(db.Boolean, default=False)
    schedule_hour = db.Column(db.Integer, default=9)
    schedule_minute = db.Column(db.Integer, default=0)
    last_run = db.Column(db.DateTime, nullable=True)
    last_status = db.Column(db.String(50), default='never_run')
    last_result = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<ScheduledJob {self.job_name}>'


class DealTracker(db.Model):
    __tablename__ = 'deal_trackers'

    id = db.Column(db.Integer, primary_key=True)
    search_query = db.Column(db.String(300), nullable=False)
    category = db.Column(db.String(50), default='other')  # cars/bikes/electronics/furniture/phones/other
    city = db.Column(db.String(100), default='Mumbai')
    min_price = db.Column(db.Integer, nullable=True)
    max_price = db.Column(db.Integer, nullable=True)
    platforms = db.Column(db.Text, default='["olx"]')  # JSON list e.g. '["olx","quikr"]'
    whatsapp_number = db.Column(db.String(20), default='')
    is_active = db.Column(db.Boolean, default=True)
    last_checked = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    listings = db.relationship('DealListing', backref='tracker', lazy='dynamic',
                               cascade='all, delete-orphan')

    def __repr__(self):
        return f'<DealTracker "{self.search_query}">'


class DealListing(db.Model):
    __tablename__ = 'deal_listings'

    id = db.Column(db.Integer, primary_key=True)
    tracker_id = db.Column(db.Integer, db.ForeignKey('deal_trackers.id'), nullable=False)
    title = db.Column(db.String(500), default='')
    price = db.Column(db.String(100), default='')
    location = db.Column(db.String(200), default='')
    url = db.Column(db.String(1000), default='')
    image_url = db.Column(db.String(1000), default='')
    platform = db.Column(db.String(50), default='olx')  # olx/quikr/facebook
    description = db.Column(db.Text, default='')
    is_new = db.Column(db.Boolean, default=True)
    found_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<DealListing "{self.title}">'


class JobTracker(db.Model):
    __tablename__ = 'job_trackers'

    id = db.Column(db.Integer, primary_key=True)
    search_query = db.Column(db.String(300), nullable=False)
    category = db.Column(db.String(50), default='it')  # it/marketing/sales/finance/hr/design/other
    city = db.Column(db.String(100), default='Mumbai')
    experience = db.Column(db.String(50), default='')  # e.g. "2-5 years"
    job_type = db.Column(db.String(50), default='')  # full-time/part-time/remote/internship
    platforms = db.Column(db.Text, default='["linkedin"]')  # JSON list
    whatsapp_number = db.Column(db.String(20), default='')
    is_active = db.Column(db.Boolean, default=True)
    last_checked = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    listings = db.relationship('JobListing', backref='tracker', lazy='dynamic',
                               cascade='all, delete-orphan')

    def __repr__(self):
        return f'<JobTracker "{self.search_query}">'


class JobListing(db.Model):
    __tablename__ = 'job_listings'

    id = db.Column(db.Integer, primary_key=True)
    tracker_id = db.Column(db.Integer, db.ForeignKey('job_trackers.id'), nullable=False)
    title = db.Column(db.String(500), default='')
    company = db.Column(db.String(300), default='')
    location = db.Column(db.String(200), default='')
    salary = db.Column(db.String(200), default='')
    experience = db.Column(db.String(100), default='')
    url = db.Column(db.String(1000), default='')
    platform = db.Column(db.String(50), default='linkedin')
    description = db.Column(db.Text, default='')
    posted_date = db.Column(db.String(100), default='')
    is_new = db.Column(db.Boolean, default=True)
    found_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<JobListing "{self.title}">'


class HotelTracker(db.Model):
    __tablename__ = 'hotel_trackers'

    id = db.Column(db.Integer, primary_key=True)
    destination = db.Column(db.String(200), nullable=False)  # e.g. "Goa", "Manali"
    checkin = db.Column(db.String(20), default='')  # YYYY-MM-DD
    checkout = db.Column(db.String(20), default='')
    guests = db.Column(db.Integer, default=2)
    rooms = db.Column(db.Integer, default=1)
    min_price = db.Column(db.Integer, nullable=True)
    max_price = db.Column(db.Integer, nullable=True)
    platforms = db.Column(db.Text, default='["booking"]')  # JSON list
    whatsapp_number = db.Column(db.String(20), default='')
    is_active = db.Column(db.Boolean, default=True)
    last_checked = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    listings = db.relationship('HotelListing', backref='tracker', lazy='dynamic',
                               cascade='all, delete-orphan')

    def __repr__(self):
        return f'<HotelTracker "{self.destination}">'


class HotelListing(db.Model):
    __tablename__ = 'hotel_listings'

    id = db.Column(db.Integer, primary_key=True)
    tracker_id = db.Column(db.Integer, db.ForeignKey('hotel_trackers.id'), nullable=False)
    name = db.Column(db.String(500), default='')
    price = db.Column(db.String(100), default='')
    rating = db.Column(db.String(50), default='')
    location = db.Column(db.String(200), default='')
    url = db.Column(db.String(1000), default='')
    image_url = db.Column(db.String(1000), default='')
    platform = db.Column(db.String(50), default='booking')
    description = db.Column(db.Text, default='')
    is_new = db.Column(db.Boolean, default=True)
    found_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<HotelListing "{self.name}">'


class CandidateSearch(db.Model):
    __tablename__ = 'candidate_searches'

    id = db.Column(db.Integer, primary_key=True)
    search_query = db.Column(db.String(300), nullable=False)  # e.g. "electrical diploma"
    location = db.Column(db.String(200), default='Mumbai')
    num_results = db.Column(db.Integer, default=10)
    total_found = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), default='pending')  # pending, completed, failed
    error_message = db.Column(db.Text, default='')
    platforms = db.Column(db.Text, default='["linkedin"]')  # JSON list of platforms searched
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    results = db.relationship('CandidateResult', backref='search', lazy='dynamic',
                              cascade='all, delete-orphan')

    def __repr__(self):
        return f'<CandidateSearch "{self.search_query}">'


class CandidateResult(db.Model):
    __tablename__ = 'candidate_results'

    id = db.Column(db.Integer, primary_key=True)
    search_id = db.Column(db.Integer, db.ForeignKey('candidate_searches.id'), nullable=False)
    name = db.Column(db.String(300), default='')
    title = db.Column(db.String(500), default='')  # role / designation
    company = db.Column(db.String(300), default='')
    location = db.Column(db.String(200), default='')
    linkedin_url = db.Column(db.String(1000), default='')
    snippet = db.Column(db.Text, default='')  # Google snippet with keywords
    email = db.Column(db.String(200), default='')
    phone = db.Column(db.String(50), default='')
    facebook_url = db.Column(db.String(1000), default='')
    instagram_url = db.Column(db.String(1000), default='')
    platform = db.Column(db.String(50), default='linkedin')  # linkedin, facebook, instagram
    is_contacted = db.Column(db.Boolean, default=False)
    is_saved = db.Column(db.Boolean, default=False)  # saved to main Contacts
    found_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def profile_url(self):
        """Return the primary profile URL based on the candidate's source platform."""
        if self.platform == 'facebook':
            return self.facebook_url or self.linkedin_url
        elif self.platform == 'instagram':
            return self.instagram_url or self.linkedin_url
        return self.linkedin_url

    def __repr__(self):
        return f'<CandidateResult "{self.name}">'


class EnquirySearch(db.Model):
    __tablename__ = 'enquiry_searches'

    id = db.Column(db.Integer, primary_key=True)
    search_query = db.Column(db.String(300), nullable=False)
    platforms = db.Column(db.Text, default='["facebook","instagram","twitter"]')  # JSON list
    location = db.Column(db.String(200), default='')
    total_found = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), default='pending')  # pending, completed, failed
    error_message = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    results = db.relationship('EnquiryResult', backref='search', lazy='dynamic',
                              cascade='all, delete-orphan')

    def __repr__(self):
        return f'<EnquirySearch "{self.search_query}">'


class EnquiryResult(db.Model):
    __tablename__ = 'enquiry_results'

    id = db.Column(db.Integer, primary_key=True)
    search_id = db.Column(db.Integer, db.ForeignKey('enquiry_searches.id'), nullable=False)
    title = db.Column(db.String(500), default='')
    snippet = db.Column(db.Text, default='')
    url = db.Column(db.String(1000), default='')
    platform = db.Column(db.String(50), default='')  # facebook, instagram, twitter
    author = db.Column(db.String(300), default='')
    posted_date = db.Column(db.String(100), default='')
    image_url = db.Column(db.String(1000), default='')
    is_saved = db.Column(db.Boolean, default=False)
    found_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<EnquiryResult "{self.title[:50]}">'
