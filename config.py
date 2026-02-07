from dotenv import load_dotenv
import os

load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-super-insecure-key-please-change")
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/alex_dashboard")
    SESSION_COLLECTION = "sessions"
