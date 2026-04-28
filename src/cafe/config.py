from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dimensions: int = 384
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_product_collection: str = "btb_product_menu"
    qdrant_support_collection: str = "btb_company_policies"
    log_level: str = "INFO"
    memory_max_prompt_tokens: int = 90000
    memory_compression_trigger_tokens: int = 60000
    memory_keep_recent_messages: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()
