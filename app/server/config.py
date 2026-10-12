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

    # LLM gateway (server/llm_gateway_service.py, docs/llm-gateway.md). frankencompute is
    # the self-hosted Ollama box, reached over Tailscale -- never exposed to pilots.
    frankencompute_url: str = "http://localhost:11434"
    frankencompute_chat_model: str = "gemma4"
    frankencompute_classify_model: str = "gemma4"
    # The digest the role table was evaluated at. Empty = don't check. When set, a box
    # reporting a different digest for the model is treated as down (falls back) rather
    # than silently serving an unevaluated build.
    frankencompute_model_digest: str = ""
    # Requests already running on frankencompute before new ones overflow to fallback.
    # Matches the box's OLLAMA_NUM_PARALLEL=3 (docs/frankencompute-bench.md): beyond 3,
    # a request would only queue there. 0 disables overflow (everything queues on the box).
    llm_max_inflight: int = 3

    # Fallback when frankencompute can't answer: "anthropic" or "openai". No key for the
    # chosen provider = no fallback (503 instead). Empty model = that provider's default
    # (server/llm_gateway_service.DEFAULT_FALLBACK_MODELS).
    fallback_provider: str = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    fallback_chat_model: str = ""
    fallback_classify_model: str = ""

    # TTS gateway (server/tts_service.py). Kokoro on frankencompute over Tailscale
    # (frankencompute/kokoro_server.py), e.g. http://100.x.y.z:8880. bf_isabella was
    # picked by ear over ElevenLabs and Chatterbox (docs/frankencompute-bench.md).
    frankencompute_tts_url: str = "http://localhost:8880"
    tts_voice: str = "bf_isabella"
    # Fallback when frankencompute can't speak. ALICE then sounds different (a different
    # voice), so it's optional: no key = no fallback (503, ALICE stays quiet but still
    # answers in the overlay). The rate prices tts_usage rows; 0 while on the free tier,
    # set it to the plan's per-1k-character rate once paying.
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_turbo_v2_5"
    elevenlabs_usd_per_1k_chars: float = 0.0

    # Runaway guards (server/limits.py, docs/llm-gateway.md Limits) -- set well above any
    # real session; they exist to stop a broken client or a loop, not to ration use.
    # A normal turn is 2-3 LLM calls and 1 TTS call.
    llm_requests_per_minute: int = 30
    tts_requests_per_minute: int = 20
    # ~250 spoken replies a day per pilot; a 3-hour session is ~20k.
    tts_daily_chars_per_user: int = 50_000
    # Hosted-fallback spend across ALL pilots per UTC day, from llm_usage. Past it, an
    # outage means 503 instead of open-ended billing; a merely-busy frankencompute just
    # queues instead of overflowing. 0 = no cap.
    fallback_daily_usd_cap: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
