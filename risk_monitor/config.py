from __future__ import annotations

from pathlib import Path
from typing import List, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Provider: "openrouter" or "groq"
    provider: Literal["openrouter", "groq"] = "groq"

    openrouter_api_key: str = ""
    groq_api_key: str = ""

    model_triage: str = "llama-3.1-8b-instant"
    model_specialist: str = "llama-3.3-70b-versatile"
    model_judge: str = "llama-3.3-70b-versatile"

    triage_threshold: int = 20
    max_diff_tokens: int = 12000
    db_path: Path = Path("./risk_monitor.db")
    sensitive_paths: str = "auth,payments,migrations,secrets,billing"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RISK_MONITOR_",
        extra="ignore",
    )

    @property
    def sensitive_path_list(self) -> List[str]:
        return [p.strip() for p in self.sensitive_paths.split(",") if p.strip()]

    @property
    def active_api_key(self) -> str:
        return self.groq_api_key if self.provider == "groq" else self.openrouter_api_key

    @property
    def base_url(self) -> str:
        return {
            "groq": "https://api.groq.com/openai/v1",
            "openrouter": "https://openrouter.ai/api/v1",
        }[self.provider]

    @property
    def rate_limit_per_minute(self) -> int:
        return {"groq": 30, "openrouter": 14}[self.provider]


def _load() -> Settings:
    import os
    from dotenv import load_dotenv

    load_dotenv()
    s = Settings()
    # fallback: read raw env vars if pydantic-settings missed them
    if not s.groq_api_key:
        s.groq_api_key = os.getenv("GROQ_API_KEY", "")
    if not s.openrouter_api_key:
        s.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "")
    return s


settings = _load()
