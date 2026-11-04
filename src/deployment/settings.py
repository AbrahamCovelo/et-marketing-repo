"""Private WordPress credentials; the browser only receives presence flags."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re

CONNECTIONS = {
    "canadians_en": ("WP_CANADIANS_EN", "https://canadiansinspain.com"),
    "canadians_fr": ("WP_CANADIANS_FR", "https://canadiansinspain.com"),
    "colombians_es": ("WP_COLOMBIANS_ES", "https://colombianosespana.com"),
}


def _file_values(env_path):
    allowed = {prefix + suffix for prefix, _ in CONNECTIONS.values() for suffix in ("_USERNAME", "_APPLICATION_PASSWORD")}
    path = Path(env_path) if env_path is not None else Path(__file__).resolve().parents[2] / ".env"
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return {}
    result = {}
    for line in lines:
        match = re.fullmatch(r"\s*(?:export\s+)?([A-Z_]+)\s*=\s*(.*?)\s*", line)
        if not match or match[1] not in allowed:
            continue
        value = match[2]
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                continue
            value = value[1:-1]
        result[match[1]] = value
    return result


@dataclass(frozen=True)
class WordPressCredentials:
    base_url: str
    username: str = field(repr=False)
    application_password: str = field(repr=False)


def load_credentials(connection_key, env_path=None):
    if connection_key not in CONNECTIONS:
        raise ValueError("Conexión WordPress no admitida.")
    prefix, base_url = CONNECTIONS[connection_key]
    values = _file_values(env_path)
    username = os.environ.get(prefix + "_USERNAME", values.get(prefix + "_USERNAME", "")).strip()
    password = os.environ.get(prefix + "_APPLICATION_PASSWORD", values.get(prefix + "_APPLICATION_PASSWORD", "")).strip()
    return WordPressCredentials(base_url, username, password)


def credentials_status(connection_key, env_path=None):
    credentials = load_credentials(connection_key, env_path)
    return {"username_configured": bool(credentials.username),
            "application_password_configured": bool(credentials.application_password),
            "configured": bool(credentials.username and credentials.application_password), "tested": False}
