from pydantic import AnyUrl, BeforeValidator, computed_field, field_validator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import os
from typing import Annotated


def parse_cors(value: str) -> list[AnyUrl]:
    """
    Parses the CORS settings from a string to a list of URLs.
    """
    if value == "*":
        return ["*"]
    if "," in value:
        return [url.strip() for url in value.split(",")]
    return [value]


class Settings(BaseSettings):
    ENV: str
    DEEPSEEK_API_KEY: str
    DEEPSEEK_MODEL: str
    DEEPSEEK_BASE_URL: str
    MAX_CHAT_TURNS: int
    MAX_RETRIES: int
    E2B_API_KEY: str
    LOG_LEVEL: str
    DEBUG: bool
    REDIS_URL: str
    REDIS_MAX_CONNECTIONS: int
    CORS_ALLOW_ORIGINS: Annotated[list[AnyUrl] | str, BeforeValidator(parse_cors)]

    model_config = SettingsConfigDict(env_file=".env.dev", env_file_encoding="utf-8")

    def get_deepseek_config(self) -> dict:
        return {
            "api_key": self.DEEPSEEK_API_KEY,
            "model": self.DEEPSEEK_MODEL,
            "base_url": self.DEEPSEEK_BASE_URL,
        }

    @classmethod
    def from_env(cls, env: str = None):
        env = env or os.getenv("ENV", "dev")
        env_file = f".env.{env.lower()}"
        return cls(_env_file=env_file, _env_file_encoding="utf-8")


settings = Settings()
