"""Explicitly import the supplied WP application passwords into local .env."""
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from .settings import CONNECTIONS


def import_site_credentials(source_file, username, env_path, *, connection_keys=None):
    if not isinstance(username, str) or not username.strip() or any(char in username for char in ('\n','\r','\x00','"',"'")):
        raise ValueError("Indique un nombre de usuario WordPress válido.")
    requested = set(CONNECTIONS) if connection_keys is None else set(connection_keys)
    if not requested or not requested <= set(CONNECTIONS):
        raise ValueError("Seleccione conexiones WordPress admitidas.")
    content = Path(source_file).read_text(encoding="utf-8-sig")
    values = {}
    seen = set()
    for block in re.split(r"\n\s*\n", content):
        if not block.strip():
            continue
        urls = re.findall(r"https?://[^\s<>]+", block)
        if not urls:
            raise ValueError("Cada bloque de sitios necesita una URL.")
        keys = []
        hosts = set()
        for address in urls:
            url = urlsplit(address)
            if url.scheme != "https" or url.username or url.password or url.query or url.fragment:
                raise ValueError("La URL de conexión debe ser HTTPS y no incluir credenciales ni parámetros.")
            hosts.add(url.netloc)
            if url.netloc == "colombianosespana.com":
                key = "colombians_es"
            elif url.netloc == "canadiansinspain.com":
                key = "canadians_fr" if url.path.startswith("/fr/") else "canadians_en"
            else:
                raise ValueError("El archivo contiene un sitio no admitido.")
            if key in seen:
                raise ValueError("El archivo contiene una conexión duplicada.")
            seen.add(key)
            keys.append(key)
        if len(hosts) != 1:
            raise ValueError("Una contraseña compartida solo puede corresponder a idiomas del mismo sitio.")
        matches = re.findall(r"(?im)^\s*API key\s*:\s*(.+)$", block)
        if len(matches) != 1 or not re.fullmatch(r"(?:[A-Za-z0-9]{4}\s+){5}[A-Za-z0-9]{4}", matches[0].strip()):
            raise ValueError("Cada sitio necesita una contraseña de aplicación con el formato esperado.")
        for key in keys:
            if key in requested:
                prefix = CONNECTIONS[key][0]
                values[prefix + "_USERNAME"] = username.strip()
                values[prefix + "_APPLICATION_PASSWORD"] = matches[0].strip()
    if not requested <= seen:
        raise ValueError("Faltan conexiones WordPress solicitadas en el archivo.")
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
    return {"connections_imported": len(requested), "tested": False, "execution_enabled": False}
