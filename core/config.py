# backend/app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
import urllib.parse
from dotenv import load_dotenv
import os
load_dotenv()

class Settings(BaseSettings):
    # ---- Mongo connection ----
    DB_HOST:str= os.getenv("DB_HOST")
    DB_PORT:int=os.getenv("DB_PORT")
    DB_USER:str=os.getenv("DB_USER")
    DB_PASSWORD:str=os.getenv("DB_PASSWORD")
    CRAWLER_DB:str=os.getenv("CRAWLER_DB")  # listings data DB name (e.g., "crawler_data")
    USER_DB:str=os.getenv("USER_DB")      # users/subscriptions DB name (e.g., "users")
    ENVIRONMENT:str=os.getenv("ENVIRONMENT")
    DB_AUTH_SOURCE:str=os.getenv("DB_AUTH_SOURCE")   # <— important for root/admin users

    # (pydantic v2 style)
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def database_url(self) -> str:
        user = urllib.parse.quote_plus(self.DB_USER)
        password = urllib.parse.quote_plus(self.DB_PASSWORD)
        # include authSource so Mongo authenticates against admin DB
        return f"mongodb://{user}:{password}@{self.DB_HOST}:{self.DB_PORT}/?authSource={self.DB_AUTH_SOURCE}"
    # ---- JWT ----
    SECRET_KEY: str = os.getenv("SECRET_KEY")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ---- Stripe (optional) ----
    
    
    # ---- Server Configuration ----
    HOST: str = "0.0.0.0"
    PORT: int = 8000


settings = Settings()
