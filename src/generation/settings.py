"""Private credential presence checks; this phase has no provider client."""
from __future__ import annotations

import os
from pathlib import Path
import re


_CREDENTIALS = (("openai", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY"))
_ENV_LINE = re.compile(r"^(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$")


def _file_credentials(env_path: Path) -> dict[str, str]:
    """Read just the supported names, without interpolation or shell execution."""
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return {}
    allowed = {name for _, name in _CREDENTIALS}
    values = {}
    for line in lines:
        match = _ENV_LINE.fullmatch(line.strip())
        if not match or match[1] not in allowed:
            continue
        value = match[2].strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            closing = value.find(quote, 1)
            if closing == -1 or (value[closing + 1:].strip() and not value[closing + 1:].lstrip().startswith("#")):
                continue
            value = value[1:closing]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[match[1]] = value
    return values


def public_status(env_path: str | Path | None = None) -> dict:
    """Only expose presence flags, never values, paths or credential fragments."""
    path = Path(env_path) if env_path is not None else Path(__file__).resolve().parents[2] / ".env"
    file_values = _file_credentials(path)
    return {
        "mode": "preparation",
        "execution_enabled": False,
        "credentials": [
            {"provider": provider, "configured": bool(os.environ.get(name, file_values.get(name, "")).strip())}
            for provider, name in _CREDENTIALS
        ],
    }
