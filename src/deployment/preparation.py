"""Validate a future WordPress draft locally; never sends or stores an article."""
from .content import prepare_wordpress_content
from .repository import _id, _text


def preview_draft(repository, request):
    if not isinstance(request, dict) or set(request) != {"target_id", "title", "html", "excerpt", "featured_media"}:
        raise ValueError("Se requieren destino, título, HTML, extracto e imagen destacada.")
    target = repository.get_target(_id(request["target_id"]))
    title = _text(request["title"], "El título", 300)
    excerpt = _text(request["excerpt"], "El extracto", 5000)
    errors = list(target["blockers"])
    if not title or any(char in title for char in '<>'):
        errors.append("El título debe ser texto sin etiquetas HTML y no puede quedar vacío.")
    if any(char in excerpt for char in '<>'):
        errors.append("El extracto debe ser texto sin etiquetas HTML.")
    media = request["featured_media"]
    if media is None:
        errors.append("Falta elegir una imagen destacada existente en la biblioteca de este WordPress.")
    else:
        _id(media)
    result = prepare_wordpress_content(request["html"], target["cta_links"])
    errors.extend(result["validation"]["errors"])
    warnings = [*result["validation"]["warnings"],
        "Vista previa local: credenciales, permisos, categorías e ID de imagen todavía no se han comprobado contra WordPress.",
        "Las plantillas del listado y del artículo deben mostrar la imagen destacada y el contenido del post.",
        "La ruta del catálogo no fija la URL del artículo; WordPress decide su enlace permanente."]
    ready = not errors and result["ready"]
    payload = {"status": "draft", "title": title, "content": result["content"], "excerpt": excerpt,
               "featured_media": media, "categories": target["category_ids"]} if ready else None
    return {"ready": ready, "execution_enabled": False, "errors": errors, "warnings": warnings,
            "payload": payload, "target": target}
