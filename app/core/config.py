from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    """Runtime settings; every value must be supplied through the environment."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: str = Field(min_length=1, max_length=64)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

    db_host: str = Field(min_length=1)
    db_port: int = Field(ge=1, le=65535)
    db_name: str = Field(min_length=1)
    db_user: str = Field(min_length=1)
    db_password: SecretStr
    redis_url: str = Field(min_length=1)

    jwt_secret_key: SecretStr
    jwt_algorithm: Literal["HS256"]
    access_token_expire_minutes: int = Field(gt=0)

    llm_base_url: str = Field(min_length=1)
    llm_api_key: SecretStr
    llm_model: str = Field(min_length=1, max_length=255)
    llm_timeout_seconds: int = Field(gt=0)
    llm_output_mode: Literal["json_schema", "json_object", "text"]
    llm_max_output_tokens: int = Field(gt=0)
    llm_max_response_bytes: int = Field(gt=0)

    catalog_path: Path
    max_region_name_length: int = Field(ge=2, le=150)
    max_geographic_description_length: int = Field(ge=10, le=2000)
    max_selection_reason_length: int = Field(ge=1, le=1000)

    recommendation_minute_limit: int = Field(gt=0)
    recommendation_long_window_limit: int = Field(gt=0)
    recommendation_long_window_seconds: int = Field(gt=60)
    recommendation_lock_ttl_seconds: int = Field(gt=0)

    register_limit: int = Field(gt=0)
    register_window_seconds: int = Field(gt=0)
    login_limit: int = Field(gt=0)
    login_window_seconds: int = Field(gt=0)
    change_password_limit: int = Field(gt=0)
    change_password_window_seconds: int = Field(gt=0)
    me_limit: int = Field(gt=0)
    me_window_seconds: int = Field(gt=0)
    graphql_limit: int = Field(gt=0)
    graphql_window_seconds: int = Field(gt=0)

    graphql_max_depth: int = Field(gt=0)
    graphql_max_complexity: int = Field(gt=0)
    graphql_max_page_size: int = Field(ge=20, le=100)
    graphql_max_query_length: int = Field(gt=0)
    graphql_ide_enabled: bool

    cors_allowed_origins: list[str]
    trusted_proxy_ips: list[str]

    @field_validator(
        "app_env",
        "db_host",
        "db_name",
        "db_user",
        "redis_url",
        "llm_base_url",
        "llm_model",
    )
    @classmethod
    def strip_non_secret_values(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("catalog_path", mode="after")
    @classmethod
    def make_catalog_path_absolute(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return (PROJECT_ROOT / value).resolve()

    @field_validator("cors_allowed_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            origin = value.strip().rstrip("/")
            if not origin:
                raise ValueError("CORS origins must not be blank")
            if origin == "*":
                raise ValueError("wildcard CORS origins are not supported")
            cleaned.append(origin)
        return cleaned

    @field_validator("trusted_proxy_ips")
    @classmethod
    def validate_proxy_ips(cls, values: list[str]) -> list[str]:
        return [str(ip_address(value.strip())) for value in values]

    @model_validator(mode="after")
    def validate_cross_field_constraints(self) -> "Settings":
        if not self.db_password.get_secret_value():
            raise ValueError("DB_PASSWORD must not be empty")
        if not self.llm_api_key.get_secret_value().strip():
            raise ValueError("LLM_API_KEY must not be blank")
        if len(self.jwt_secret_key.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET_KEY must contain at least 32 bytes")
        if self.recommendation_lock_ttl_seconds <= self.llm_timeout_seconds:
            raise ValueError(
                "RECOMMENDATION_LOCK_TTL_SECONDS must exceed LLM_TIMEOUT_SECONDS"
            )
        return self

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
