"""Explicitly import the supplied WP application passwords into local .env."""
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from .settings import CONNECTIONS


def import_site_credentials(source_file, username, env_path):
    if not isinstance(username, str) or not username.strip() or any(char in username for char in ('\n','\r','\x00','"',"'")):
        raise ValueError("Indique un nombre de usuario WordPress válido.")
    content = Path(source_file).read_text(encoding="utf-8-sig")
    values = {}
    seen = set()
    for block in re.split(r"\n\s*\n", content):
        if not block.strip():
            continue
        urls = re.findall(r"https?://[^\s<>]+", block)
        if len(urls) != 1:
            raise ValueError("Cada bloque de sitios debe tener una única URL.")
        url = urlsplit(urls[0])
        if url.scheme != "https" or url.username or url.password or url.query:
            raise ValueError("La URL de conexión debe ser HTTPS y no incluir credenciales ni parámetros.")
        if url.netloc == "colombianosespana.com":
            key = "colombians_es"
        elif url.netloc == "canadiansinspain.com":
            key = "canadians_fr" if url.path.startswith("/fr/") else "canadians_en"
        else:
            raise ValueError("El archivo contiene un sitio no admitido.")
        if key in seen:
            raise ValueError("El archivo contiene una conexión duplicada.")
        seen.add(key)
        matches = re.findall(r"(?im)^\s*API key\s*:\s*(.+)$", block)
        if len(matches) != 1 or not re.fullmatch(r"(?:[A-Za-z0-9]{4}\s+){5}[A-Za-z0-9]{4}", matches[0].strip()):
            raise ValueError("Cada sitio necesita una contraseña de aplicación con el formato esperado.")
        prefix = CONNECTIONS[key][0]
        values[prefix + "_USERNAME"] = username.strip()
        values[prefix + "_APPLICATION_PASSWORD"] = matches[0].strip()
    if seen != set(CONNECTIONS):
        raise ValueError("Faltan conexiones: se esperan Canadá inglés, Canadá francés y Colombia.")
    env_path = Path(env_path)
    existing = env_path.read_text(encoding="utf-8-sig") if env_path.exists() else ""
    lines = [line for line in existing.splitlines() if not any(re.match(r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*=", line) for key in values)]
    lines.extend(f'{key}="{value}"' for key, value in values.items())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=env_path.parent, prefix=".env.", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write("\n".join(lines) + "\n")
        temporary.replace(env_path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return {"connections_imported": len(seen), "tested": False, "execution_enabled": False}
