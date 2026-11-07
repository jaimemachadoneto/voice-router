"""The settings of the router service, from the environment.

The service reads one token, the OpenRouter key, the database path, the
optional house words and the optional Kev server at start. The key comes from
a variable or from a file, so a container can mount it as a secret. An error
message names the variable and never the value, because the value is a secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB = "/data/voice-router.db"
DEFAULT_JEV_LIMIT = 0.8
DEFAULT_JEV_MODEL = "~typesafe/jev-latest"
DEFAULT_KEV_LIMIT = 0.25
DEFAULT_KEV_MODEL = "kev"


@dataclass(frozen=True)
class Settings:
    """The configuration of one router service."""

    token: str
    openrouter_key: str
    db: Path
    house_words: Path | None = None
    jev_limit: float = DEFAULT_JEV_LIMIT
    jev_model: str = DEFAULT_JEV_MODEL
    kev_url: str | None = None
    kev_model: str = DEFAULT_KEV_MODEL
    kev_limit: float = DEFAULT_KEV_LIMIT
    local_first: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        """Build the settings of the process environment.

        The router token is required. The OpenRouter key comes from
        `OPENROUTER_API_KEY` or from the file in
        `VOICE_ROUTER_OPENROUTER_KEY_FILE`, and never from both.
        """
        token = os.environ.get("VOICE_ROUTER_TOKEN", "")
        if not token:
            raise RuntimeError("VOICE_ROUTER_TOKEN is not set")
        house = os.environ.get("VOICE_ROUTER_HOUSE_WORDS", "")
        return cls(
            token=token,
            openrouter_key=_openrouter_key(),
            db=Path(os.environ.get("VOICE_ROUTER_DB", DEFAULT_DB)),
            house_words=Path(house) if house else None,
            jev_limit=_jev_limit(),
            jev_model=os.environ.get("VOICE_ROUTER_JEV_MODEL", DEFAULT_JEV_MODEL),
            kev_url=os.environ.get("VOICE_ROUTER_KEV_URL") or None,
            kev_model=os.environ.get("VOICE_ROUTER_KEV_MODEL") or DEFAULT_KEV_MODEL,
            kev_limit=_kev_limit(),
            local_first=_local_first(),
        )


def _openrouter_key() -> str:
    """The key from the environment variable or from the file.

    The two sources are exclusive. The error messages never hold the key.
    """
    direct = os.environ.get("OPENROUTER_API_KEY", "").strip()
    key_file = os.environ.get("VOICE_ROUTER_OPENROUTER_KEY_FILE", "")
    if direct and key_file:
        raise RuntimeError(
            "set only one of OPENROUTER_API_KEY and VOICE_ROUTER_OPENROUTER_KEY_FILE"
        )
    if not direct and not key_file:
        raise RuntimeError("set OPENROUTER_API_KEY or VOICE_ROUTER_OPENROUTER_KEY_FILE")
    if not direct:
        try:
            direct = Path(key_file).read_text().strip()
        except OSError:
            raise RuntimeError(
                "the file in VOICE_ROUTER_OPENROUTER_KEY_FILE is not readable"
            ) from None
    if not direct:
        raise RuntimeError("the OpenRouter key is empty")
    return direct


def _jev_limit() -> float:
    """The Jev limit in seconds, as a number."""
    raw = os.environ.get("VOICE_ROUTER_JEV_LIMIT", "")
    if not raw:
        return DEFAULT_JEV_LIMIT
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError("VOICE_ROUTER_JEV_LIMIT is not a number") from None


def _kev_limit() -> float:
    """The Kev limit in seconds, as a number greater than 0."""
    raw = os.environ.get("VOICE_ROUTER_KEV_LIMIT", "")
    if not raw:
        return DEFAULT_KEV_LIMIT
    try:
        value = float(raw)
    except ValueError:
        raise RuntimeError("VOICE_ROUTER_KEV_LIMIT is not a number") from None
    if value <= 0:
        raise RuntimeError("VOICE_ROUTER_KEV_LIMIT must be greater than 0")
    return value


def _local_first() -> bool:
    """The local-first switch: true for 1, true or yes, in any case."""
    return os.environ.get("VOICE_ROUTER_LOCAL_FIRST", "").lower() in {
        "1",
        "true",
        "yes",
    }
