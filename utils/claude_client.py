import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

LOG_PATH = Path(__file__).parent.parent / "data" / "api_calls.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("api_calls")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
    return anthropic.Anthropic(api_key=api_key)


def chat(system: str, user: str, model: str, max_tokens: int = 4096) -> str:
    client = get_client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    content = response.content[0].text

    logger.info(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "purpose": "scoring" if "score" in user[:50].lower() else "tailor",
    }))

    return content
