from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LITMEMO_", env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    seekdb_host: str = "127.0.0.1"
    seekdb_port: int = 2881
    seekdb_tenant: str = "sys"
    seekdb_database: str = "test"
    seekdb_user: str = "root"
    seekdb_password: str = ""
    collection_name: str = "litmemo_chunks"

    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimension: int = 512

    powercontext_url: str = "http://127.0.0.1:8000"
    powercontext_token: str = ""
    powercontext_scope_id: str = ""

    host: str = "127.0.0.1"
    port: int = 8080


@lru_cache
def get_settings() -> Settings:
    return Settings()
