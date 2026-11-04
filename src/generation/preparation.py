"""Local, deterministic previews of future inputs. There is no API executor."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from src.extraction.db_manager import Database
from .model_options import generation_options, validate_model_override

PLACEHOLDERS = [
    "article_text", "article_json", "article_title", "article_url",
    "destination_name", "destination_url", "locale",
]
MAX_PAIRS = 500
MAX_RENDERED_CHARACTERS = 5_000_000
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class GenerationDisabledError(RuntimeError):
    """Raised even if someone changes environment flags or supplies credentials."""


def execute_generation(*args: Any, **kwargs: Any) -> None:
    raise GenerationDisabledError("Generación deshabilitada: solo preparación local.")


def _ids(values: Any, label: str) -> list[int]:
    if not isinstance(values, list) or not values or len(values) > MAX_PAIRS:
        raise ValueError(f"Selecciona entre 1 y {MAX_PAIRS} {label}.")
    if any(type(value) is not int or not 0 < value <= 2**63 - 1 for value in values):
        raise ValueError(f"Los identificadores de {label} deben ser enteros positivos.")
    return list(dict.fromkeys(values))


def render_template(template: str, values: dict[str, str]) -> str:
    """Replace explicit placeholders once; JSON braces and source text stay literal."""
    if not isinstance(template, str):
        raise ValueError("La plantilla debe ser texto.")
    matches = list(_PLACEHOLDER.finditer(template))
    remainder = _PLACEHOLDER.sub("", template)
    # Standalone }} is allowed: ordinary nested JSON objects end that way.
    # {{ always starts an interpolation, so an unmatched opener is an error.
    if "{{" in remainder or re.search(r"\{[a-z_]+\}\}", remainder) or "{%" in template or "{#" in template:
        raise ValueError("La plantilla contiene una variable incompleta o una sintaxis no admitida.")
    if any(match[1] not in PLACEHOLDERS for match in matches):
        raise ValueError("La plantilla contiene una variable no admitida.")
    # An extra closing brace may legitimately close JSON surrounding a token,
    # e.g. {"source": {{article_json}}}. Reject extras without an open object.
    for match in matches:
        if match.start() > 0 and template[match.start() - 1] == "{":
            raise ValueError("Usa dos llaves para cada variable de la plantilla.")
        extra_closing = re.match(r"\}*", template[match.end():])[0]
        if extra_closing:
            prefix = _PLACEHOLDER.sub("", template[:match.start()])
            prefix = re.sub(r'"(?:\\.|[^"\\])*"', "", prefix)
            if len(extra_closing) > prefix.count("{") - prefix.count("}"):
                raise ValueError("Usa dos llaves para cada variable de la plantilla.")
    projected_size = len(template) + sum(
        len(values[match[1]]) - (match.end() - match.start()) for match in matches
    )
    if projected_size > MAX_RENDERED_CHARACTERS:
        raise ValueError(
            "La vista previa supera el tamaño máximo de 5.000.000 caracteres. "
            "Reduzca el texto de la plantilla o las repeticiones de las variables del artículo."
        )
    return _PLACEHOLDER.sub(lambda match: values[match[1]], template)


def _warnings(source: dict, context: dict) -> list[str]:
    prompt = context["prompt"]
    configuration = context["configuration"]
    warnings = []
    if prompt["status"] != "reviewed":
        warnings.append("La plantilla está en borrador y pendiente de revisión.")
    if not prompt["template"].strip():
        warnings.append("La plantilla está vacía; falta la plantilla de los socios.")
    if not prompt["system_prompt"].strip():
        warnings.append("No hay instrucciones de sistema configuradas.")
    if not context["generation_options"]["model"]:
        warnings.append("Todavía no se ha seleccionado un modelo.")
    if prompt.get("current_version_id") != prompt["version_id"]:
        warnings.append("Existe una versión más reciente del prompt; se usa la versión fijada en la configuración.")
    if source["quality"]["review_required"]:
        warnings.append("El artículo de origen tiene contenido pendiente de revisión.")
    return warnings


def _values(source: dict, destination: dict) -> dict[str, str]:
    return {
        "article_text": source["content"]["clean_text"],
        "article_json": json.dumps(source, ensure_ascii=False, indent=2),
        "article_title": source["article"]["title"],
        "article_url": source["article"]["source_url"],
        "destination_name": destination["name"],
        "destination_url": destination.get("site_url") or "",
        "locale": destination["locale"],
    }


def preview_preparation(
    db_path: str | Path, article_ids: list[int], configuration_ids: list[int],
    model_override=None,
) -> dict:
    """Describe all pairs and render only the first, without saving any plan."""
    from .repository import GenerationRepository

    articles = _ids(article_ids, "artículos")
    configurations = _ids(configuration_ids, "configuraciones")
    model_override = validate_model_override(model_override)
    pair_count = len(articles) * len(configurations)
    if pair_count > MAX_PAIRS:
        raise ValueError(f"La preparación admite como máximo {MAX_PAIRS} combinaciones de artículo y configuración.")
    repository = GenerationRepository(db_path)
    contexts = []
    for configuration_id in configurations:
        context = repository.configuration_context(configuration_id)
        context["generation_options"] = generation_options(context["configuration"], model_override)
        if not context["configuration"]["enabled"]:
            raise ValueError("La configuración seleccionada está deshabilitada.")
        if not context["destination"]["enabled"]:
            raise ValueError("El destino seleccionado está deshabilitado.")
        if context["prompt"]["archived"]:
            raise ValueError("El prompt seleccionado está archivado.")
        # Validate every selected prompt even though only the first is rendered fully.
        blank_values = dict.fromkeys(PLACEHOLDERS, "")
        render_template(context["prompt"]["template"], blank_values)
        render_template(context["prompt"]["system_prompt"], blank_values)
        contexts.append(context)

    items = []
    example = None
    aggregate_warnings = []
    with Database(db_path) as database:
        # All source exports in one consistent read transaction; only one full
        # export remains in the result, even for a selection of 500 articles.
        database.connection.execute("BEGIN")
        for article_id in articles:
            source = database.get_export(article_id=article_id)
            for context in contexts:
                configuration = context["configuration"]
                destination = context["destination"]
                prompt = context["prompt"]
                warnings = _warnings(source, context)
                aggregate_warnings.extend(warning for warning in warnings if warning not in aggregate_warnings)
                identifiers = {
                    "article_id": article_id,
                    "configuration_id": configuration["id"],
                    "source_version_id": source["article"]["version_id"],
                    "configuration_version_id": configuration["current_version_id"],
                    "prompt_version_id": prompt["version_id"],
                    "destination_id": destination["id"],
                    "generation_options": context["generation_options"],
                }
                items.append({
                    **identifiers,
                    "title": source["article"]["title"],
                    "destination_name": destination["name"],
                    "locale": destination["locale"],
                    "configuration_name": configuration["name"],
                    "word_count": len(source["content"]["clean_text"].split()),
                    "warnings": warnings,
                })
                if example is None:
                    values = _values(source, destination)
                    example = {
                        **identifiers,
                        "system_prompt": render_template(prompt["system_prompt"], values),
                        "rendered_prompt": render_template(prompt["template"], values),
                        "source_context": source,
                        "warnings": warnings,
                    }
    return {
        "mode": "preparation", "execution_enabled": False,
        "article_count": len(articles), "configuration_count": len(configurations),
        "pair_count": pair_count, "items": items, "example": example,
        "warnings": aggregate_warnings,
    }
