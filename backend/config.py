"""
BookMate Backend Configuration - Phase 1 Extended
包含 AI 服务配置和缓存配置
"""
from pathlib import Path

from pydantic_settings import BaseSettings
from functools import lru_cache

_BACKEND_ENV_FILE = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    """Application settings with environment variable support"""
    
    # App Settings
    APP_NAME: str = "BookMate API"
    APP_VERSION: str = "1.1.0"  # Phase 1 版本升级
    DEBUG: bool = False
    
    # Server Settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # BookWeaver Phase A — engine root is this repository (see engine_home.py).
    # BOOK_WEAVER_HOME is optional when running the unified repo; set only for overrides.
    BOOK_WEAVER_HOME: str = ""
    PDF_TRANSLATOR_HOME: str = ""
    BOOKMATE_AI_BACKEND: str = "minimax"
    BOOKMATE_JOBS_DIR: str = "~/Desktop/文档/Bookmate/Jobs"
    BOOKMATE_INGEST_TIMEOUT_SECONDS: int = 2400
    BOOKMATE_JOB_EXECUTE_TIMEOUT_SECONDS: int = 2700
    
    # File Upload Settings
    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50MB max file size
    UPLOAD_DIR: str = "./uploads"
    CACHE_DIR: str = "./cache"
    
    # Storage Settings
    BOOKS_STORAGE_PATH: str = "./storage/books"
    
    # AI Cache Settings
    AI_CACHE_DIR: str = "./cache/ai"  # AI 生成结果缓存目录
    AI_CACHE_TTL_HOURS: int = 168  # 缓存有效期 7 天
    AI_MAX_CONCURRENT: int = 5  # 最大并发 AI 请求数
    
    class Config:
        env_file = str(_BACKEND_ENV_FILE)
        env_file_encoding = "utf-8"
        extra = "allow"  # Allow extra fields from env


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
