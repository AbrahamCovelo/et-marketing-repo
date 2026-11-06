"""Run one API-assessed adaptation with an automatic CC0 cover and optional draft.

Every remote stage retains its own durable run/import journal. Formatting is
normalized by deterministic code while preserving wording and original output.
The orchestrator never supplies legal conclusions, chooses a photo manually,
or publishes a WordPress entry.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_generation_trial import export_trial, _positive
from src.deployment.automatic_images import auto_import_cover
from src.deployment.categories import category_context, validate_category_selection
from src.deployment.content import validate_semantic_html
from src.deployment.repository import _id, LOCALES
from src.deployment.settings import CONNECTIONS
from src.deployment.trial import send_trial_draft, _same_origin
from src.generation.applicability import review_status
from src.generation.model_options import validate_model_override
from src.generation.trial import get_trial, run_trial
from src.generation.formatting import ensure_trial_format
from src.generation.assessment import get_assessment


def run_assessment(*args, **kwargs):
    # The assessment owns API access, evidence retrieval and its durable journal.
    from src.generation.assessment import run_assessment as assess
    return assess(*args, **kwargs)


def get_cached_category_context(db_path, target_id):
    return category_context(db_path, target_id, cached_only=True)


def _preflight(db_path, article_id, configuration_id, target_id, send_draft):
    status = review_status(db_path, article_id, configuration_id)
    context = status["context"]
    if not context["configuration_enabled"] or not context["destination_enabled"] or context["prompt_archived"]:
        raise ValueError("El artículo requiere una configuración y un destino habilitados con un prompt disponible.")
    path = Path(db_path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        target = db.execute("SELECT * FROM deployment_targets WHERE id=?", (target_id,)).fetchone()
        configuration = db.execute("SELECT provider FROM generation_configuration_versions WHERE id=?",
                                   (context["configuration_version_id"],)).fetchone()
    if not target or target["destination_id"] != context["destination_id"]:
        raise ValueError("La configuración editorial y el destino de WordPress no corresponden al mismo público.")
    if not configuration or configuration["provider"] != "openai":
        raise ValueError("Esta prueba automática requiere una configuración de OpenAI.")
    target = dict(target)
    connection_key = target["connection_key"]
    if (connection_key not in CONNECTIONS or context["locale"] != LOCALES[connection_key]
            or not _same_origin(context["site_url"], CONNECTIONS[connection_key][1])):
        raise ValueError("El sitio o idioma del destino no coincide con la conexión WordPress.")
    if send_draft and (connection_key == "canadians_fr" or target["language_mode"] != "site_default"):
        raise ValueError("La integración del idioma del destino sigue pendiente; no se iniciará la prueba con envío.")
    return status, target


def _approved(status, assessment):
    review = status.get("current_review") or {}
    return (status.get("allowed") and status.get("context_fingerprint") == assessment.get("context_fingerprint")
            and review.get("source") == "model" and review.get("assessment_id") == assessment.get("id"))


def _saved_results(db_path, article_id, configuration_id, model, run_key, effort, tier):
    """Load both stages by exact run key without invoking any provider wrapper."""
    path = Path(db_path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"generation_assessments", "generation_trials"} <= tables:
            raise ValueError("No hay evaluación y adaptación guardadas para continuar sin generar.")
        assessment_row = db.execute("SELECT id FROM generation_assessments WHERE run_key=?", (run_key + ".assessment",)).fetchone()
        trial_row = db.execute("SELECT id FROM generation_trials WHERE run_key=?", (run_key + ".generation",)).fetchone()
    if not assessment_row or not trial_row:
        raise ValueError("Falta un resultado guardado; continuar no inicia llamadas nuevas a OpenAI.")
    assessment, trial = get_assessment(db_path, assessment_row[0]), get_trial(db_path, trial_row[0])
    if (assessment["article_id"], assessment["configuration_id"], assessment["model"],
            assessment["reasoning_effort"], assessment["service_tier"], assessment["status"],
            assessment.get("request", {}).get("max_output_tokens"), assessment.get("request", {}).get("max_tool_calls")) != (
            article_id, configuration_id, model, effort, tier, "completed", 12000, 3):
        raise ValueError("La evaluación guardada no corresponde a esta solicitud de continuación.")
    if (trial["article_id"], trial["configuration_id"], trial["model"], trial["status"]) != (
            article_id, configuration_id, model, "completed"):
        raise ValueError("La adaptación guardada no corresponde a esta solicitud de continuación.")
    return assessment, trial


def run_automatic_trial(db_path, article_id, configuration_id, target_id, model, run_key, *,
                        reasoning_effort="high", service_tier="flex", send_draft=False,
                        category_ids=None, env_path=None, output_dir="data/adaptations", allow_pending_ctas=False,
                        import_cover=True, reuse_only=False):
    """Execute assessment → generation → automatic image → export → optional draft.

    A rejected or uncertain API assessment stops before drafting. Recoverable
    formatting is normalized without changing editorial text or making API
    calls. Unresolved HTML stops before importing a cover. ``reuse_only`` loads
    saved outputs directly and never invokes the paid assessment/drafting paths.
    """
    for value in (article_id, configuration_id, target_id):
        _id(value)
    model = validate_model_override(model)
    if not model:
        raise ValueError("Seleccione un modelo GPT concreto para la prueba automática.")
    if not isinstance(run_key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,148}", run_key):
        raise ValueError("La prueba necesita una clave estable de hasta 149 caracteres, sin espacios.")
    if not isinstance(reasoning_effort, str) or reasoning_effort not in {"", "minimal", "none", "low", "medium", "high", "xhigh", "max", "ultra"}:
        raise ValueError("Nivel de razonamiento no admitido por esta prueba.")
    if service_tier not in {"flex", "default"}:
        raise ValueError("Nivel de servicio no admitido por esta prueba.")
    if type(send_draft) is not bool or type(allow_pending_ctas) is not bool or type(import_cover) is not bool or type(reuse_only) is not bool:
        raise ValueError("Las opciones de envío y CTA pendientes deben ser booleanas.")
    if send_draft and not import_cover:
        raise ValueError("El envío de borradores requiere una portada automática.")
    before, _ = _preflight(db_path, article_id, configuration_id, target_id, send_draft)
    explicit_categories = category_ids is not None
    if explicit_categories:
        if not isinstance(category_ids, list) or not category_ids or len(category_ids) > 50:
            raise ValueError("Las categorías deben ser una lista no vacía de hasta 50 identificadores.")
        category_ids = sorted({_id(value) for value in category_ids})
    # Fetching WordPress taxonomy is free. A draft must have a configured catalog
    # before paid work begins; local drafting can use an already cached catalog.
    if send_draft or explicit_categories:
        taxonomy = category_context(db_path, target_id, env_path=env_path, refresh=True)
    else:
        try:
            taxonomy = get_cached_category_context(db_path, target_id)
        except (ValueError, KeyError):
            taxonomy = None
    if explicit_categories:
        validate_category_selection(db_path, target_id, category_ids, context=taxonomy,
                                    env_path=env_path, refresh=False)
    # Resolve this before API use so an invalid output directory type cannot
    # silently discard an otherwise successful paid result at the final stage.
    output_dir = str(Path(output_dir).resolve())
    result = {"status": "stopped", "stage": "assessment", "article_id": article_id,
              "configuration_id": configuration_id, "target_id": target_id,
              "model": model, "reasoning_effort": reasoning_effort, "run_key": run_key}
    saved_trial = None
    if reuse_only:
        assessment, saved_trial = _saved_results(db_path, article_id, configuration_id, model, run_key,
                                                reasoning_effort, service_tier)
    else:
        assessment = run_assessment(db_path, article_id, configuration_id, model, run_key + ".assessment",
            env_path=env_path, reasoning_effort=reasoning_effort, service_tier=service_tier,
            max_output_tokens=12000, max_tool_calls=3, category_context=taxonomy)
    decision = assessment.get("decision") or {}
    result.update(assessment_id=assessment["id"], assessment_status=assessment["status"],
                  assessment_usage=assessment.get("usage", {}), decision=decision.get("decision"),
                  reason=decision.get("reason", assessment.get("error", "")))
    selection = assessment.get("category_selection") or {}
    selected_ids = category_ids if explicit_categories else selection.get("selected_category_ids", [])
    selection_context = taxonomy if explicit_categories else selection
    result.update(category_ids=selected_ids, category_selection=selection,
                  category_selection_source="manual" if explicit_categories else selection.get("source", "pending"))
    if assessment["status"] != "completed" or decision.get("decision") != "adapt":
        return result
    if assessment.get("context_fingerprint") != before["context_fingerprint"]:
        return {**result, "reason": "La evaluación guardada corresponde a otro contexto; no se generó de nuevo."}
    status, _ = _preflight(db_path, article_id, configuration_id, target_id, send_draft)
    if not _approved(status, assessment):
        return {**result, "reason": "No hay una revisión automática vigente asociada a esta evaluación."}
    title = assessment.get("suggested_title")
    if not isinstance(title, str) or not title.strip() or len(title) > 300 or any(c in title for c in "<>\x00"):
        return {**result, "reason": "La API no devolvió un título válido para el destino; no se redactará."}
    trial = saved_trial if reuse_only else run_trial(db_path, article_id, configuration_id, model, run_key + ".generation",
        env_path=env_path, reasoning_effort=reasoning_effort, service_tier=service_tier, max_output_tokens=16000)
    result.update(stage="generation", trial_id=trial["id"], generation_status=trial["status"],
                  generation_usage=trial.get("usage", {}), title=title)
    if trial["status"] != "completed":
        return result
    request = trial.get("request") or {}
    if (trial.get("model") != model or request.get("reasoning", {}).get("effort", "") != reasoning_effort
            or request.get("service_tier") != service_tier or request.get("max_output_tokens") != 16000):
        return {**result, "reason": "La prueba guardada usa otros parámetros; no se enviará como esta prueba."}
    trial_review = trial.get("snapshot", {}).get("applicability_review", {})
    if trial_review.get("source") != "model" or trial_review.get("assessment_id") != assessment["id"]:
        return {**result, "reason": "La redacción guardada no utilizó esta evaluación automática; no se enviará."}
    trial = ensure_trial_format(db_path, trial["id"])
    result["normalization"] = trial.get("normalization")
    validation = validate_semantic_html(trial["html"])
    result["semantic_validation"] = validation
    if not validation["valid"]:
        return {**result, "reason": "El formato no se puede corregir automáticamente conservando el contenido: " + " ".join(validation["errors"])}
    status, _ = _preflight(db_path, article_id, configuration_id, target_id, send_draft)
    if (not _approved(status, assessment)
            or any(trial.get(key) != status["context"][key] for key in (
                "article_id", "source_version_id", "configuration_id", "configuration_version_id",
                "prompt_version_id", "destination_id"))):
        return {**result, "reason": "El artículo o la configuración cambió; no se importó una imagen ni se envió el resultado."}
    if not import_cover:
        exported = export_trial(trial, title=title, output_dir=output_dir)
        return {**result, "status": "ready", "stage": "export", "artifacts": exported["artifacts"], "reason": ""}
    if send_draft:
        if not selected_ids:
            return {**result, "stage": "categories", "category_selection_pending": True,
                    "reason": "La adaptación está guardada, pero falta seleccionar categorías permitidas. No se repitió la evaluación ni se importó una imagen."}
        try:
            category_check = validate_category_selection(db_path, target_id, selected_ids,
                context=selection_context, env_path=env_path, refresh=True)
        except (ValueError, KeyError) as error:
            return {**result, "stage": "categories", "category_selection_pending": True, "reason": str(error)}
        selected_ids = category_check["category_ids"]
        result.update(category_ids=selected_ids, category_validation=category_check)
    cover = auto_import_cover(db_path, target_id, article_id, env_path=env_path)
    result.update(stage="image", cover_selection_id=cover["selection_id"], cover_status=cover["status"],
                  featured_media=cover.get("wp_media_id"))
    if cover["status"] != "ready":
        return {**result, "reason": cover.get("error", "La portada automática no está confirmada.")}
    if cover["source_version_id"] != trial["source_version_id"] or cover["target_id"] != target_id:
        return {**result, "reason": "La portada corresponde a otra versión o destino; no se enviará."}
    status, _ = _preflight(db_path, article_id, configuration_id, target_id, send_draft)
    if not _approved(status, assessment):
        return {**result, "reason": "Cambió la revisión después de importar la portada; no se enviará el artículo."}
    exported = export_trial(trial, title=title, image_url=cover["wp_media_url"],
                            featured_media=cover["wp_media_id"], output_dir=output_dir)
    result.update(status="ready", stage="export", artifacts=exported["artifacts"],
                  image_url=cover["wp_media_url"], reason="")
    if not send_draft:
        return result
    deployment = send_trial_draft(db_path, trial["id"], title=title, featured_media=cover["wp_media_id"],
                                  category_ids=selected_ids, env_path=env_path, allow_pending_ctas=allow_pending_ctas)
    return {**result, "stage": "deployment", "status": deployment["status"],
            "deployment_id": deployment["id"], "wordpress": deployment.get("result", {}),
            "warnings": deployment.get("warnings", []), "reason": deployment.get("error", "")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/source_articles.db")
    parser.add_argument("--article-id", required=True, type=_positive)
    parser.add_argument("--configuration-id", required=True, type=_positive)
    parser.add_argument("--target-id", required=True, type=_positive)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-key", required=True)
    parser.add_argument("--reasoning-effort", default="high", choices=["minimal", "none", "low", "medium", "high"])
    parser.add_argument("--service-tier", default="flex", choices=["flex", "default"])
    parser.add_argument("--send-draft", action="store_true")
    parser.add_argument("--category-id", type=_positive, action="append", dest="category_ids")
    parser.add_argument("--allow-pending-ctas", action="store_true")
    parser.add_argument("--env-path")
    parser.add_argument("--output-dir", default="data/adaptations")
    args = parser.parse_args(argv)
    try:
        result = run_automatic_trial(args.db, args.article_id, args.configuration_id, args.target_id,
            args.model, args.run_key, reasoning_effort=args.reasoning_effort, service_tier=args.service_tier,
            send_draft=args.send_draft, category_ids=args.category_ids, env_path=args.env_path,
            output_dir=args.output_dir, allow_pending_ctas=args.allow_pending_ctas)
    except (ValueError, KeyError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({"error": "No se pudo leer o escribir el archivo local solicitado."}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"ready", "draft"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
