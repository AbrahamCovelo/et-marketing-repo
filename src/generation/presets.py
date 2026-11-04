"""Explicit import of the supplied templates. Never runs at panel startup."""
from pathlib import Path

from src.extraction.db_manager import Database
from .preparation import PLACEHOLDERS, render_template
from .repository import GenerationRepository

PRESETS = (
    ("canadians_en", "canadian_english.txt", "Canadá · Inglés · HTML y CTAs", "Role:"),
    ("canadians_fr", "canadian_french.txt", "Canadá · Francés · HTML y CTAs", "Rôle :"),
    ("colombians_es", "colombian_spanish.txt", "Colombia · Español · HTML y CTAs", "Rol:"),
)


def load_editorial_prompts(db_path, prompts_dir=None):
    directory = Path(prompts_dir) if prompts_dir else Path(__file__).resolve().parents[2] / "extras/prompts"
    payloads = []
    # Read and validate every file before touching the editorial catalog.
    for key, filename, name, role_prefix in PRESETS:
        template = (directory / filename).read_text(encoding="utf-8-sig").strip()
        if not template or len(template) > 100000 or "{{article_text}}" not in template or "\x00" in template:
            raise ValueError(f"Plantilla de importación inválida: {filename}")
        role = next((line[len(role_prefix):].strip() for line in template.splitlines() if line.startswith(role_prefix)), "")
        if not role:
            raise ValueError(f"Falta la instrucción de rol en {filename}")
        render_template(template, dict.fromkeys(PLACEHOLDERS, ""))
        payloads.append((key, {"name": name, "description": "Plantilla suministrada el 2026-11-04; pendiente de pruebas editoriales.",
                               "system_prompt": role, "template": template, "status": "draft", "archived": False}))
    repo = GenerationRepository(db_path)
    targets = []
    with Database(db_path) as db:
        for key, payload in payloads:
            prompt = db.connection.execute("SELECT id FROM generation_prompts WHERE seed_key=?", (key,)).fetchone()
            configuration = db.connection.execute("SELECT id FROM generation_configurations WHERE seed_key=?", (key,)).fetchone()
            if not prompt or not configuration:
                raise ValueError(f"No se encuentra el catálogo inicial de {key}.")
            context = repo.configuration_context(configuration["id"])
            targets.append((prompt["id"], context["configuration"], payload))
    loaded = []
    for prompt_id, configuration, payload in targets:
        prompt = repo.save_prompt(payload, prompt_id)
        fields = {key: configuration[key] for key in ("name", "destination_id", "provider", "model", "notes", "enabled")}
        fields.update(prompt_version_id=prompt["version_id"], notes="Plantilla cargada; pendiente de pruebas, modelo definitivo y conexión con los sitios.")
        saved = repo.save_configuration(fields, configuration["id"])
        loaded.append({"configuration_id": saved["id"], "prompt_id": prompt["id"], "prompt_version": prompt["version"],
                       "configuration_version": saved["current_version"], "locale": saved["locale"]})
    return loaded
