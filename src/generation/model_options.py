"""Model identifier validation and planned execution settings.

The model picker is populated by ``model_catalog`` from the account's API.
This module keeps preparation local and never guesses account availability.
"""
import re


MODEL_REFERENCE = {
    "checked_at": None,
    "source_url": "https://developers.openai.com/api/reference/resources/models/methods/list",
    "availability_checked": False,
    "models": [],
    "dynamic": True,
    "catalog_url": "/api/generation/models",
}


def validate_model_override(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("El modelo del lote debe ser un identificador GPT en texto.")
    value = value.strip()
    if value and (len(value) > 200 or not re.fullmatch(r"gpt-[A-Za-z0-9][A-Za-z0-9._:-]*", value)):
        raise ValueError("El modelo del lote debe tener un identificador GPT válido, sin espacios ni URL.")
    return value


def generation_options(configuration, model_override=None):
    model = validate_model_override(model_override)
    if model and configuration["provider"] != "openai":
        raise ValueError("El modelo GPT del lote solo se aplica a configuraciones OpenAI. Separe las de otros proveedores.")
    return {"provider": configuration["provider"], "model": model or configuration["model"]}
