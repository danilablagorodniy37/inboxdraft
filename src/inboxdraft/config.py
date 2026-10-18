"""Settings, read from environment variables and an optional .env file."""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# USD per million tokens: [input, output]. These are configured values, not fetched live.
# Checked against public price lists on 18 Oct 2026; override with MODEL_PRICES (JSON) in .env.
DEFAULT_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "text-embedding-3-small": (0.02, 0.0),
    "fake": (0.0, 0.0),
}
PRICES_NOTE = (
    "Prices are configured values (USD per million tokens), last checked 18 Oct 2026. "
    "Verify at https://platform.claude.com/docs/en/about-claude/pricing and override with "
    "MODEL_PRICES in .env."
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # model provider
    llm_provider: str = "auto"  # anthropic | openai | fake | auto
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    draft_model: str = "claude-sonnet-5-5"
    fast_model: str = "claude-haiku-5-5"
    max_draft_tokens: int = 1024

    # embeddings
    embeddings_provider: str = "fastembed"  # fastembed | openai | hashing
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # storage
    database_url: str = "sqlite:///data/inboxdraft.sqlite"

    # behaviour
    firm_name: str = "Wrenfield Accounting"
    firm_signature: str = "Kind regards,\nThe Wrenfield Accounting team"
    reply_from: str = "hello@wrenfield.example"
    escalation_reply_by: str = "the end of the next working day"
    confidence_threshold: float = 0.6
    top_k: int = 5
    knowledge_dir: str = "knowledge"
    inbox_dir: str = "inbox"
    demo_mode: bool = True

    # mailbox
    imap_host: str | None = None
    imap_port: int = 993
    imap_user: str | None = None
    imap_password: str | None = None
    imap_folder: str = "INBOX"
    imap_mark_seen: bool = True

    # sending
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    outbox_dir: str = "data/outbox"

    # review UI
    app_password: str | None = None

    # prices
    model_prices: dict[str, tuple[float, float]] = DEFAULT_MODEL_PRICES

    @field_validator("firm_signature", mode="before")
    @classmethod
    def _unescape_newlines(cls, value: str) -> str:
        return value.replace("\\n", "\n") if isinstance(value, str) else value

    @field_validator("model_prices", mode="before")
    @classmethod
    def _parse_prices(cls, value: object) -> object:
        if isinstance(value, str):
            parsed = json.loads(value)
            merged = dict(DEFAULT_MODEL_PRICES)
            merged.update({k: (float(v[0]), float(v[1])) for k, v in parsed.items()})
            return merged
        return value

    @property
    def resolved_llm_provider(self) -> str:
        if self.llm_provider != "auto":
            return self.llm_provider
        if self.anthropic_api_key:
            return "anthropic"
        if self.openai_api_key:
            return "openai"
        return "fake"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
