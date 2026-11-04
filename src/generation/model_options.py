"""Local model choices and planned execution settings; never contacts a provider."""
import re


MODEL_REFERENCE = {
    "checked_at": "2026-11-04",
    "source_url": "https://developers.openai.com/api/docs/pricing",
    "availability_checked": False,
    "models": [
        {"id": "gpt-6-luna", "label": "GPT-6 Luna · opción económica actual"},
        {"id": "gpt-5-nano-2025-08-07", "label": "GPT-5 nano · tarifa menor, en retirada"},
        {"id": "gpt-6.1-terra", "label": "GPT-6.1 Terra"},
        {"id": "gpt-6.1-sol", "label": "GPT-6.1 Sol"},
        {"id": "gpt-6-astra", "label": "GPT-6 Astra"},
    ],
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
