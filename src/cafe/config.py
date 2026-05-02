from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    llm_provider: str = Field(
        default="openai",
        validation_alias=AliasChoices("LLM_PROVIDER", "OPENAI_PROVIDER"),
    )
    llm_base_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "LLM_BASE_URL",
            "OPENAI_BASE_URL",
            "OPENROUTER_BASE_URL",
            "GROQ_BASE_URL",
            "OLLAMA_HOST",
        ),
    )
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "LLM_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "OPENROUTER_API_KEY",
            "DEEPSEEK_API_KEY",
            "DASHSCOPE_API_KEY",
        ),
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("LLM_MODEL", "OPENAI_MODEL"),
    )
    embedding_model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        validation_alias=AliasChoices("EMBEDDING_MODEL", "EMBEDDING_MODEL_NAME"),
    )
    embedding_dimensions: int = 384
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_product_collection: str = "btb_product_menu"
    qdrant_menu_attributes_collection: str = "btb_menu_attributes"
    qdrant_support_collection: str = "btb_company_policies"
    log_level: str = "INFO"
    memory_database_url: str = "sqlite+aiosqlite:///./data/memory/milo_memory.sqlite3"
    memory_max_prompt_tokens: int = 90000
    memory_compression_trigger_tokens: int = 60000
    memory_keep_recent_messages: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()
