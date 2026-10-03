"""Application configuration management."""

from pydantic import Field, PostgresDsn, RedisDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        allow_inf_nan=False,
    )

    # Application
    app_name: str = "Tempoledger"
    app_version: str = "1.0.0"
    debug: bool = False
    environment: str = "development"

    # Database
    database_url: PostgresDsn = Field(
        default="postgresql://localhost:5432/tempoledger",
        description="PostgreSQL connection string",
    )

    # Redis
    redis_url: RedisDsn = Field(
        default="redis://localhost:6379/0",
        description="Redis connection string",
    )

    # Twilio
    twilio_account_sid: str | None = Field(default=None, description="Twilio Account SID")
    twilio_auth_token: str | None = Field(default=None, description="Twilio Auth Token")
    twilio_phone_number: str | None = Field(default=None, description="Twilio phone number")

    # LLM (for AI Agent)
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    enable_live_agent: bool = False
    enable_live_delivery: bool = False
    rate_limit_storage_uri: str = "memory://"

    # OpenTelemetry
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None, description="OpenTelemetry OTLP endpoint"
    )
    otel_service_name: str = Field(default="tempoledger", description="OpenTelemetry service name")

    # Scheduling Parameters
    default_wpm_mean: float = Field(default=50.0, gt=0, description="Mean words per minute")
    default_wpm_std: float = Field(default=15.0, ge=0, description="WPM standard deviation")
    default_wpm_min: float = Field(default=30.0, gt=0, description="Minimum WPM")
    default_wpm_max: float = Field(default=80.0, gt=0, description="Maximum WPM")
    pause_probability: float = Field(default=0.4, ge=0, le=1, description="Pause probability")
    pause_lambda: float = Field(
        default=0.08, gt=0, description="Exponential distribution lambda for pauses"
    )

    # Business Hours
    business_hours_start: int = Field(default=9, ge=0, le=23, description="Opening hour")
    business_hours_end: int = Field(default=17, ge=0, le=23, description="Closing hour")

    # Pattern Detection
    min_interval_variance: float = Field(
        default=100.0, ge=0, description="Minimum interval variance (seconds^2)"
    )
    min_coefficient_of_variation: float = Field(
        default=0.4, ge=0, description="Minimum coefficient of variation"
    )
    max_burst_messages: int = Field(default=3, gt=0, description="Maximum messages in burst")
    burst_window_seconds: int = Field(default=90, gt=0, description="Burst window (seconds)")

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.business_hours_start >= self.business_hours_end:
            raise ValueError(
                "Opening hour must precede closing hour; overnight windows unsupported"
            )
        if not self.default_wpm_min <= self.default_wpm_mean <= self.default_wpm_max:
            raise ValueError("WPM mean must lie between minimum and maximum")
        return self


settings = Settings()
