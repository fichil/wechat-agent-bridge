"""Explicit local key import for the isolated OpenAI/mock console profile."""

from pathlib import Path
import re

from .console_models import ConsoleError
from .providers import DEFAULT_CONFIG


OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_TEST_MODEL = "gpt-4.1-mini-2025-04-14"


def load_openai_configuration(env_file: str | Path) -> dict:
    """Read one literal assignment; never evaluate shell or inherit a key."""
    path = Path(env_file)
    try:
        if path.is_symlink() or not path.is_file():
            raise ConsoleError("openai_env_file_unavailable")
        with path.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ConsoleError("openai_env_file_invalid")
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise ConsoleError("openai_env_file_unavailable") from None
    assignments = []
    for line in text.splitlines():
        match = re.fullmatch(r"\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*?)\s*", line)
        if match:
            value = match[1]
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            assignments.append(value)
    if not assignments:
        raise ConsoleError("provider_api_key_missing")
    if len(assignments) != 1 or not re.fullmatch(r"sk-[A-Za-z0-9_-]{1,2045}", assignments[0]):
        raise ConsoleError("openai_env_key_invalid")
    return {**DEFAULT_CONFIG, "provider": "compatible", "base_url": OPENAI_BASE_URL,
            "model": OPENAI_TEST_MODEL, "api_key": assignments[0],
            "hourly_limit": 2, "daily_limit": 2,
            "capabilities": {"text": False, "vision": False, "transcription": False}}


def configure_openai(service, env_file: str | Path) -> None:
    """Import into the existing protected store without enabling any grants."""
    if service.adapter.simulation_only is not True and service.acceptance is None:
        raise ConsoleError("openai_test_requires_mock")
    config = load_openai_configuration(env_file)
    if service.acceptance:
        config.update(vision_model=OPENAI_TEST_MODEL, transcription_model="whisper-1", hourly_limit=10, daily_limit=10)
    # An unchanged import preserves successful capability evidence and counters.
    if any(service.config.get(key) != value for key, value in config.items() if key != "capabilities"):
        service.save_configuration(config)
    service.pause()
