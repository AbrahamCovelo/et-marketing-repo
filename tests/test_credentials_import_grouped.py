"""Grouped site credentials remain local, selective and atomically validated."""
import json

import pytest
import requests

from src.deployment.credentials_import import import_site_credentials
from src.deployment.settings import CONNECTIONS, load_credentials


CANADA_SECRET = "abcd EFGH 1234 ijkl MNOP 5678"
COLOMBIA_SECRET = "qrst UVWX 9012 yzab CDEF 3456"
CANADA_BLOCK = f"""Canadians in Spain
https://canadiansinspain.com/
https://canadiansinspain.com/fr/
Nombre de API: canada-articles
API key: {CANADA_SECRET}
"""
COLOMBIA_BLOCK = f"""Colombianos en España
https://colombianosespana.com/
Nombre de API: colombia-articles
API key: {COLOMBIA_SECRET}
"""


@pytest.fixture
def files(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Credential import must not contact WordPress or OpenAI")
    monkeypatch.setattr(requests.Session, "request", no_network)
    for prefix, _ in CONNECTIONS.values():
        for suffix in ("_USERNAME", "_APPLICATION_PASSWORD"):
            monkeypatch.delenv(prefix + suffix, raising=False)
    source, env = tmp_path / "Sitios.txt", tmp_path / ".env"
    return source, env


def test_grouped_canada_password_populates_both_languages(files):
    source, env = files
    source.write_text(CANADA_BLOCK + "\n" + COLOMBIA_BLOCK, encoding="utf-8")
    result = import_site_credentials(source, "David Noguera", env)
    english, french = (load_credentials(connection, env) for connection in ("canadians_en", "canadians_fr"))
    assert english.username == french.username == "David Noguera"
    assert english.application_password == french.application_password == CANADA_SECRET
    assert english.base_url == french.base_url == "https://canadiansinspain.com"
    assert load_credentials("colombians_es", env).application_password == COLOMBIA_SECRET
    assert result == {"connections_imported": 3, "tested": False, "execution_enabled": False}
    assert CANADA_SECRET not in json.dumps(result) and COLOMBIA_SECRET not in json.dumps(result)


def test_selective_canada_update_preserves_colombia_and_other_env_values(files):
    source, env = files
    source.write_text(CANADA_BLOCK + "\n" + COLOMBIA_BLOCK, encoding="utf-8")
    original = ('# Retain user settings\nOPENAI_API_KEY="unrelated-private-value"\n'
                'WP_COLOMBIANS_ES_USERNAME="Original User"\n'
                'WP_COLOMBIANS_ES_APPLICATION_PASSWORD="original-colombia-password"\n'
                'WP_CANADIANS_EN_USERNAME="Old User"\n'
                'WP_CANADIANS_EN_APPLICATION_PASSWORD="old-canada-password"\n')
    env.write_text(original, encoding="utf-8")
    result = import_site_credentials(source, "New User", env, connection_keys=["canadians_en", "canadians_fr"])
    assert result["connections_imported"] == 2
    stored = env.read_text(encoding="utf-8")
    assert '# Retain user settings\nOPENAI_API_KEY="unrelated-private-value"\n' in stored
    assert 'WP_COLOMBIANS_ES_USERNAME="Original User"' in stored
    assert 'WP_COLOMBIANS_ES_APPLICATION_PASSWORD="original-colombia-password"' in stored
    assert "old-canada-password" not in stored and COLOMBIA_SECRET not in stored
    assert stored.count("WP_CANADIANS_EN_APPLICATION_PASSWORD=") == 1
    assert load_credentials("canadians_en", env).application_password == CANADA_SECRET
    assert load_credentials("canadians_fr", env).application_password == CANADA_SECRET


def test_selective_import_accepts_canada_only_file(files):
    source, env = files
    source.write_text(CANADA_BLOCK, encoding="utf-8-sig")
    result = import_site_credentials(source, "David Noguera", env, connection_keys=["canadians_en", "canadians_fr"])
    assert result["connections_imported"] == 2
    assert "WP_COLOMBIANS" not in env.read_text(encoding="utf-8")


@pytest.mark.parametrize("content", [
    f"https://canadiansinspain.com/\nhttps://colombianosespana.com/\nAPI key: {CANADA_SECRET}\n",
    CANADA_BLOCK + "\n" + COLOMBIA_BLOCK.replace(COLOMBIA_SECRET, "malformed-private-password"),
    CANADA_BLOCK.replace("https://canadiansinspain.com/fr/", "https://untrusted.example/fr/"),
    CANADA_BLOCK.replace("https://canadiansinspain.com/fr/", "https://canadiansinspain.com/"),
    CANADA_BLOCK.replace("https://canadiansinspain.com/fr/", "https://private-user:private-pass@canadiansinspain.com/fr/"),
])
def test_invalid_file_leaves_existing_credentials_unchanged_without_leaks(files, content):
    source, env = files
    source.write_text(content, encoding="utf-8")
    original = b'OPENAI_API_KEY="unchanged"\r\nWP_COLOMBIANS_ES_APPLICATION_PASSWORD="old"\r\n'
    env.write_bytes(original)
    with pytest.raises(ValueError) as error:
        import_site_credentials(source, "David Noguera", env, connection_keys=["canadians_en", "canadians_fr"])
    assert env.read_bytes() == original
    assert not list(env.parent.glob(".env.*.tmp"))
    assert CANADA_SECRET not in str(error.value)
    assert "malformed-private-password" not in str(error.value)
    assert "private-pass" not in str(error.value)


def test_failed_import_does_not_create_new_environment_file(files):
    source, env = files
    source.write_text(CANADA_BLOCK.replace("https://canadiansinspain.com/fr/", "https://colombianosespana.com/"), encoding="utf-8")
    with pytest.raises(ValueError):
        import_site_credentials(source, "David Noguera", env)
    assert not env.exists()


def test_missing_requested_french_language_fails_atomically(files):
    source, env = files
    source.write_text(CANADA_BLOCK.replace("https://canadiansinspain.com/fr/\n", ""), encoding="utf-8")
    with pytest.raises(ValueError, match="Faltan conexiones"):
        import_site_credentials(source, "David Noguera", env, connection_keys=["canadians_en", "canadians_fr"])
    assert not env.exists()
