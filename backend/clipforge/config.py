"""Environment-driven settings. Secrets are never logged (SecretStr)."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    scan_model: str = Field(default="claude-haiku-4-5-20251001", alias="CLIPFORGE_SCAN_MODEL")
    curate_model: str = Field(default="claude-sonnet-5", alias="CLIPFORGE_CURATE_MODEL")
    max_scan_model: str = Field(default="claude-sonnet-5", alias="CLIPFORGE_MAX_SCAN_MODEL")
    max_curate_model: str = Field(default="claude-opus-5", alias="CLIPFORGE_MAX_CURATE_MODEL")
    max_job_cost_usd: float = Field(default=1.00, alias="MAX_JOB_COST_USD", gt=0)
    hf_token: SecretStr | None = Field(default=None, alias="HF_TOKEN")
    asr_backend: Literal["auto", "faster-whisper", "mlx-whisper"] = Field(
        default="auto", alias="ASR_BACKEND"
    )
    asr_model: str = Field(default="large-v3-turbo", alias="ASR_MODEL")
    asr_model_non_english: str = Field(
        default="large-v3", alias="ASR_MODEL_NON_ENGLISH"
    )  # "same" = use ASR_MODEL
    max_source_height: int = Field(default=1080, alias="MAX_SOURCE_HEIGHT", ge=360, le=2160)
    ytdlp_cookies_from_browser: str | None = Field(default=None, alias="YTDLP_COOKIES_FROM_BROWSER")
    google_client_id: str | None = Field(default=None, alias="GOOGLE_CLIENT_ID")
    google_client_secret: SecretStr | None = Field(default=None, alias="GOOGLE_CLIENT_SECRET")
    data_dir: Path = Field(default=Path("~/Clipforge"), alias="DATA_DIR")
    render_workers: int | None = Field(default=None, alias="RENDER_WORKERS", ge=1)

    @property
    def projects_dir(self) -> Path:
        return self.data_dir.expanduser() / "projects"


def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
