import os
from dotenv import load_dotenv
from cryptography.fernet import Fernet

load_dotenv()


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')

    # Render gives postgres:// but SQLAlchemy needs postgresql://
    _db_url = os.getenv('DATABASE_URL', 'sqlite:///spice_outreach.db')
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True

    UPLOAD_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                 'app_package', 'static', 'uploads')
    BROCHURE_FOLDER = os.path.join(UPLOAD_FOLDER, 'brochures')
    CSV_TEMP_FOLDER = os.path.join(UPLOAD_FOLDER, 'csv_temp')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

    ALLOWED_BROCHURE_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
    ALLOWED_BROCHURE_MIMETYPES = {
        'application/pdf', 'image/png', 'image/jpeg'
    }

    # Rate limiting for email
    EMAIL_RATE_PER_HOUR = 20
    EMAIL_RATE_PER_DAY = 200

    # Fernet encryption key for credentials
    FERNET_KEY = os.getenv('FERNET_KEY', '')

    @staticmethod
    def get_fernet():
        key = Config.FERNET_KEY
        if not key:
            key = Fernet.generate_key().decode()
            Config.FERNET_KEY = key
            # Write back to .env for persistence
            env_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), '.env')
            try:
                with open(env_path, 'r') as f:
                    content = f.read()
                if 'FERNET_KEY=' in content:
                    lines = content.split('\n')
                    lines = [f'FERNET_KEY={key}' if l.startswith('FERNET_KEY=') else l for l in lines]
                    content = '\n'.join(lines)
                else:
                    content += f'\nFERNET_KEY={key}\n'
                with open(env_path, 'w') as f:
                    f.write(content)
            except OSError:
                pass
        return Fernet(key.encode() if isinstance(key, str) else key)

    # APScheduler
    SCHEDULER_API_ENABLED = True
