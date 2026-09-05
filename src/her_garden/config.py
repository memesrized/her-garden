"""Explicit environment configuration; no usable credentials ship in the repository."""

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or a private .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: SecretStr
    public_url: str = "http://localhost:8002/garden"
    household_password_hash: SecretStr
    port: int = 8002

    @field_validator("public_url")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        """Require trusted HTTPS outside local development."""
        from urllib.parse import urlsplit

        url = urlsplit(value)
        if url.scheme != "https" and not (
            url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1"}
        ):
            raise ValueError("PUBLIC_URL must use HTTPS except on localhost")
        if url.query or url.fragment or url.username or url.password or url.path != "/garden":
            raise ValueError(
                "PUBLIC_URL must end in /garden without credentials, query or fragment"
            )
        return value.rstrip("/")

    @field_validator("household_password_hash")
    @classmethod
    def validate_password_hash(cls, value: SecretStr) -> SecretStr:
        """Reject malformed scrypt hashes at startup."""
        salt, digest = value.get_secret_value().split(":")
        if len(bytes.fromhex(salt)) != 16 or len(bytes.fromhex(digest)) != 64:
            raise ValueError("Invalid household password hash")
        return value
