import os
import secrets

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv optional
    pass

ENV = os.getenv("APP_ENV", "development")
DB_PATH = os.getenv("DB_PATH", "meet.db")
TOKEN_MINUTES = int(os.getenv("TOKEN_MINUTES", "120"))
MAX_PEERS = int(os.getenv("MAX_PEERS", "6"))
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY or SECRET_KEY == "change-me":
    if ENV == "production":
        raise RuntimeError("SECRET_KEY must be set to a strong value in production")
    SECRET_KEY = secrets.token_urlsafe(48)  # dev only: tokens reset on restart
elif ENV == "production" and len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY must be at least 32 characters in production")
