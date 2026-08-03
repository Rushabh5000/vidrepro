from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VIDREPRO_", env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+psycopg://vidrepro:vidrepro@localhost:5432/vidrepro"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint: str = "http://localhost:9000"
    s3_public_endpoint: str = ""  # endpoint reachable from the browser; falls back to s3_endpoint
    s3_bucket: str = "vidrepro"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"

    default_org_id: str = "00000000-0000-0000-0000-000000000001"
    db_auto_create: bool = True

    max_upload_bytes: int = 2 * 1024**3
    max_duration_s: int = 45 * 60

    ocr_engine: str = "tesseract"  # tesseract | paddle | none

    # AI is optional and OFF by default. The pipeline is fully deterministic
    # when llm_provider == "none".
    llm_provider: str = "none"  # none | anthropic
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5"

    # Azure DevOps export defaults (org settings may override per-org)
    ado_org: str = ""
    ado_project: str = ""
    ado_pat: str = ""

    cors_origins: str = "http://localhost:3000"

    @property
    def public_s3_endpoint(self) -> str:
        return self.s3_public_endpoint or self.s3_endpoint


@lru_cache
def get_settings() -> Settings:
    return Settings()
