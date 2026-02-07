from dotenv import load_dotenv
import os

load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-super-insecure-key-please-change")
    MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://chathan:YzZ1JmcaxpUhVjlz@cluster0.ifjuhfc.mongodb.net/?appName=Cluster0")
    SESSION_COLLECTION = "sessions"
