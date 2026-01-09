# config.py
import os

SECRET = os.environ.get("APP_SECRET", "dev-secret-change-me")
DB_PATH = os.environ.get("DB_PATH", "app.db")
