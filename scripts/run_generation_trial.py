"""Run one explicitly identified trial, or export an existing trial without API calls."""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import re
import sys

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.deployment.content import _https_url, validate_semantic_html
from src.generation.trial import get_trial, run_trial


def _positive(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("Debe ser un entero positivo.") from None
    if not 0 < number <= 2**63 - 1:
        raise argparse.ArgumentTypeError("Debe ser un entero positivo.")
    return number


def export_trial(trial, *, title, image_url=None, featured_media=None, output_dir="data/adaptations"):
    """Export review artifacts, preserving the original output separately.

    The supplied image is referenced only in the local preview. This function
    never generates or uploads media and never sends content to WordPress.
    """
    if not isinstance(title, str) or not title.strip() or len(title) > 300 or "\x00" in title:
        raise ValueError("El título debe contener entre 1 y 300 caracteres.")
    title = title.strip()
    if image_url is not None and not _https_url(image_url):
        raise ValueError("La imagen debe usar una URL HTTPS completa sin credenciales.")
    if featured_media is not None and (type(featured_media) is not int or not 0 < featured_media <= 2**63 - 1):
        raise ValueError("La imagen destacada debe tener un identificador positivo.")
    if type(trial.get("id")) is not int or trial["id"] <= 0:
        raise ValueError("El identificador de la prueba no es válido.")
    body = trial.get("html", "")
    if not isinstance(body, str):
        raise ValueError("El contenido de la prueba debe ser texto.")
    validation = validate_semantic_html(body)
    first_paragraph = BeautifulSoup(body, "html.parser").find("p")
    excerpt = first_paragraph.get_text(" ", strip=True) if first_paragraph else ""
    excerpt = " ".join(excerpt.split())
    excerpt = re.sub(r"\s+([,.;:!?])", r"\1", excerpt)
    metadata = {key: trial.get(key) for key in (
        "id", "run_key", "article_id", "source_version_id", "configuration_id",
        "configuration_version_id", "prompt_version_id", "destination_id", "model",
        "response_model", "status", "usage", "created_at", "completed_at",
    )}
    metadata.update({
        "title": title, "excerpt": excerpt, "image_url": image_url,
        "featured_media": featured_media,
        "semantic_validation": validation,
        "cta_pending": validation["cta_markers"],
        "editorial_review": "pending",
        "wordpress_upload_performed_by_export": False,
    })
    response = trial.get("response")
    metadata["service_tier"] = response.get("service_tier") if isinstance(response, dict) else None
    request = trial.get("request")
    metadata["requested_service_tier"] = request.get("service_tier") if isinstance(request, dict) else None

    safe_body = body if validation["valid"] else "<pre>" + escape(body) + "</pre>"
    image = f'<img src="{escape(image_url, quote=True)}" alt="" />' if image_url else ""
    pending = ", ".join(validation["cta_markers"]) or "ninguno presente"
    status = escape(str(trial.get("status", "")))
    model = escape(str(trial.get("model", "")))
    preview = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'" />
<title>{escape(title)}</title>
<style>body{{max-width:800px;margin:2rem auto;padding:0 1rem;font:18px/1.65 system-ui,sans-serif;color:#222}}img{{max-width:100%;height:auto}}aside{{padding:1rem;background:#f4f4f4;font-size:14px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:.5rem}}</style>
</head><body><aside>Vista previa local. Revisión editorial pendiente. Esta exportación no publica en WordPress.
<br />Prueba {trial['id']} · Modelo {model} · Estado {status}
<br />HTML semántico: {"válido" if validation["valid"] else "inválido; se muestra como texto"}.
<br />Enlaces CTA pendientes: {escape(pending)}.</aside>
<main><h1>{escape(title)}</h1>{image}<article>{safe_body}</article></main></body></html>
"""
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "body": directory / f"generation-{trial['id']}.body.html",
        "preview": directory / f"generation-{trial['id']}.preview.html",
        "metadata": directory / f"generation-{trial['id']}.json",
    }
    paths["body"].write_text(body, encoding="utf-8", newline="")
    paths["preview"].write_text(preview, encoding="utf-8")
    paths["metadata"].write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"trial_id": trial["id"], "status": trial["status"], "model": trial["model"],
            "usage": trial.get("usage", {}), "semantic_valid": validation["valid"],
            "artifacts": {key: str(path) for key, path in paths.items()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/source_articles.db")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--article-id", type=_positive, help="Artículo para una única llamada de pago.")
    mode.add_argument("--export-trial", type=_positive, help="Exportar una prueba guardada, sin llamar a OpenAI.")
    parser.add_argument("--configuration-id", type=_positive)
    parser.add_argument("--model")
    parser.add_argument("--run-key")
    parser.add_argument("--title", required=True)
    parser.add_argument("--image-url")
    parser.add_argument("--featured-media", type=_positive)
    parser.add_argument("--output-dir", default="data/adaptations")
    args = parser.parse_args(argv)
    if args.export_trial is not None:
        if any(value is not None for value in (args.configuration_id, args.model, args.run_key)):
            parser.error("--export-trial no admite opciones de generación.")
    elif not all((args.configuration_id, args.model, args.run_key)):
        parser.error("La prueba de pago requiere --article-id, --configuration-id, --model y --run-key.")
    # Validate presentation inputs before a paid request, not only while exporting.
    if not args.title.strip() or len(args.title) > 300 or "\x00" in args.title:
        parser.error("El título debe contener entre 1 y 300 caracteres.")
    if args.image_url is not None and not _https_url(args.image_url):
        parser.error("La imagen debe usar una URL HTTPS completa sin credenciales.")
    try:
        if args.export_trial is not None:
            trial = get_trial(args.db, args.export_trial)
        else:
            trial = run_trial(args.db, args.article_id, args.configuration_id, args.model, args.run_key,
                              max_output_tokens=8000, reasoning_effort="minimal", service_tier="flex")
        result = export_trial(trial, title=args.title, image_url=args.image_url,
                              featured_media=args.featured_media, output_dir=args.output_dir)
    except (ValueError, KeyError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({"error": "No se pudo leer o escribir el archivo local solicitado."}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if trial["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
