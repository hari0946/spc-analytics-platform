from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SPC Analytics Platform"
    app_version: str = "0.1.0"
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"

    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_password: SecretStr = SecretStr("")
    snowflake_warehouse: str = ""
    snowflake_database: str = ""
    snowflake_schema: str = ""
    snowflake_role: str = ""
    snowflake_login_timeout: int = 20

    max_upload_mb: int = 200
    max_upload_rows: int = 5_000_000
    max_upload_columns: int = 500
    upload_part_rows: int = 200_000

    jwt_secret: SecretStr = SecretStr("")
    jwt_expire_minutes: int = 60
    login_max_failures: int = 5
    login_window_seconds: int = 300

    # Project-root .env (local run from backend/) and backend-local .env
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
