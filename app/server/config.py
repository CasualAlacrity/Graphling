from functools import lru_cache

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # No database_url field here — db/session.py already reads TRADE_DB_URL directly
    # (same env var this project has always used) and the server just imports its
    # SessionLocal like every DB-touching module already does. Duplicating that into a
    # second config surface would just be two places that could disagree.

    # OAuth — Discord. Requires "Public Client" OFF in the Developer Portal (the old
    # client-side PKCE flow needed it on; a real server can hold a secret).
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = "http://localhost:8000/auth/discord/callback"

    # JWT
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days

    # UEX Corp — needed here now that the UEX cache's read-through-or-fetch logic lives
    # server-side (uex_cache_service.py). Every other UEXCorpClient method (live,
    # uncached lookups) stays client-side and never reads this.
    uexcorp_api_key: str = ""
    uexcorp_bearer_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
