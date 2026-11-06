"""Editable routing/CTA settings kept apart from credentials and source content."""
from datetime import datetime, timezone
import json
from urllib.parse import urlsplit

from src.extraction.db_manager import Database
from src.generation.repository import GenerationRepository
from .settings import CONNECTIONS, credentials_status

CTA_NAMES = {
    "en-CA": ("CTA_IMMIGRATION_EN", "CTA_TAXES_EN"),
    "fr-CA": ("CTA_IMMIGRATION_FR", "CTA_TAXES_FR"),
    "es-CO": ("CTA_INMIGRACION_ES", "CTA_IMPUESTOS_ES"),
}
GUIDES = {
    "canadians_en": "https://canadiansinspain.com/guides-for-canadians-in-spain/",
    "canadians_fr": "",
    "colombians_es": "https://colombianosespana.com/guias-colombianos-en-espana",
}
LOCALES = {"canadians_en": "en-CA", "canadians_fr": "fr-CA", "colombians_es": "es-CO"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value, label, limit=4000):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError(f"{label} debe ser texto de hasta {limit} caracteres.")
    return value.strip()


def _id(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError("El identificador debe ser un entero positivo.")
    return value


def _url(value, base_url, label):
    value = _text(value, label, 2000)
    if not value:
        return ""
    parsed, base = urlsplit(value), urlsplit(base_url)
    if (parsed.scheme != "https" or parsed.netloc != base.netloc or parsed.username or parsed.password
            or parsed.query or any(char.isspace() for char in value) or "\\" in value):
        raise ValueError(f"{label} debe ser una URL HTTPS del mismo sitio, sin credenciales ni parámetros.")
    return value


def _clean_ctas(ctas, locale, base_url):
    if not isinstance(ctas, dict) or set(ctas) != set(CTA_NAMES[locale]):
        raise ValueError("Los enlaces CTA deben corresponder al idioma del destino.")
    clean = {}
    for name, link in ctas.items():
        if not isinstance(link, dict) or set(link) != {"url", "text"}:
            raise ValueError("Cada CTA necesita los campos url y text.")
        clean[name] = {"url": _url(link["url"], base_url, "El enlace CTA"),
                       "text": _text(link["text"], "El texto CTA", 300)}
    return clean


class DeploymentRepository:
    def __init__(self, db_path, env_path=None):
        self.db_path = db_path
        self.env_path = env_path
        GenerationRepository(db_path)
        with Database(db_path) as db, db.connection:
            db.connection.execute("BEGIN IMMEDIATE")
            db.connection.execute("""CREATE TABLE IF NOT EXISTS deployment_targets (
                id INTEGER PRIMARY KEY, destination_id INTEGER NOT NULL UNIQUE REFERENCES generation_destinations(id),
                connection_key TEXT NOT NULL UNIQUE, guide_url TEXT NOT NULL,
                category_ids_json TEXT NOT NULL CHECK(json_valid(category_ids_json)),
                language_mode TEXT NOT NULL CHECK(language_mode IN ('site_default','pending')),
                cta_links_json TEXT NOT NULL CHECK(json_valid(cta_links_json)),
                notes TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            for key in CONNECTIONS:
                destination = db.connection.execute("SELECT id,locale FROM generation_destinations WHERE seed_key=?", (key,)).fetchone()
                if not destination:
                    continue
                db.connection.execute("""INSERT OR IGNORE INTO deployment_targets
                    (destination_id,connection_key,guide_url,category_ids_json,language_mode,cta_links_json,notes,updated_at)
                    VALUES (?,?,?,'[]',?,?,?,?)""", (destination["id"], key, GUIDES[key],
                    "pending" if key == "canadians_fr" else "site_default",
                    json.dumps({name: {"url": "", "text": ""} for name in CTA_NAMES[LOCALES[key]]}),
                    "Ruta temporal. Pendiente comprobar categorías, consulta del listado e imagen destacada en ambas plantillas.", _now()))

    def _target(self, row):
        item = dict(row)
        item["destination_locale"] = item["locale"]
        item["locale"] = LOCALES[item["connection_key"]]
        item["base_url"] = CONNECTIONS[item["connection_key"]][1]
        item["rest_posts_url"] = item["base_url"] + "/wp-json/wp/v2/posts"
        item["category_ids"] = json.loads(item.pop("category_ids_json"))
        item["cta_links"] = json.loads(item.pop("cta_links_json"))
        item["credentials"] = credentials_status(item["connection_key"], self.env_path)
        item["blockers"] = []
        if item["destination_locale"] != item["locale"]:
            item["blockers"].append("El idioma del destino cambió y no coincide con esta conexión WordPress.")
        if urlsplit(item.pop("destination_site_url")).netloc != urlsplit(item["base_url"]).netloc:
            item["blockers"].append("El sitio del destino cambió y no coincide con esta conexión WordPress.")
        if not item["credentials"]["configured"]:
            item["blockers"].append("Falta configurar el usuario o la contraseña de aplicación de WordPress.")
        if not item["guide_url"]:
            item["blockers"].append("Falta definir la ruta del catálogo de guías.")
        # The legacy fixed list is retained for old records, never used as an
        # automatic classification or as evidence that taxonomy is configured.
        from .categories import get_category_catalog
        try:
            taxonomy = get_category_catalog(self.db_path, item["id"])
            item["category_policy"] = taxonomy["policy"]
            item["category_catalog_revision"] = taxonomy["catalog_revision"]
            item["category_policy_configured"] = taxonomy["configured"]
            item["blockers"].extend(taxonomy["blockers"])
        except ValueError as error:
            item["category_policy"] = None
            item["category_catalog_revision"] = None
            item["category_policy_configured"] = False
            item["blockers"].append(str(error))
        if item["language_mode"] == "pending" or item["connection_key"] == "canadians_fr":
            item["blockers"].append("Pendiente definir y verificar la integración del idioma en WordPress.")
        item["verification_status"] = "not_tested"
        item["execution_enabled"] = False
        return item

    def catalog(self):
        with Database(self.db_path) as db:
            rows = db.connection.execute("""SELECT t.*,d.name,d.locale,d.site_url AS destination_site_url FROM deployment_targets t
                JOIN generation_destinations d ON d.id=t.destination_id ORDER BY t.id""").fetchall()
            return {"mode": "preparation", "execution_enabled": False, "targets": [self._target(row) for row in rows]}

    def get_target(self, target_id):
        _id(target_id)
        with Database(self.db_path) as db:
            row = db.connection.execute("""SELECT t.*,d.name,d.locale,d.site_url AS destination_site_url FROM deployment_targets t
                JOIN generation_destinations d ON d.id=t.destination_id WHERE t.id=?""", (target_id,)).fetchone()
            if row is None:
                raise KeyError("No existe el destino WordPress.")
            return self._target(row)

    @staticmethod
    def _destination_ctas(connection, destination_id):
        row = connection.execute("""SELECT d.id,d.locale,d.site_url,t.id AS target_id,
            t.connection_key,t.cta_links_json,t.updated_at
            FROM generation_destinations d LEFT JOIN deployment_targets t ON t.destination_id=d.id
            WHERE d.id=?""", (destination_id,)).fetchone()
        if row is None:
            raise KeyError("No existe el destino.")
        locale = LOCALES[row["connection_key"]] if row["connection_key"] else row["locale"]
        names = CTA_NAMES.get(locale, ())
        links = json.loads(row["cta_links_json"]) if row["cta_links_json"] else {}
        blockers = []
        if row["target_id"] is None:
            blockers.append("Este destino todavía no tiene una conexión WordPress configurada.")
        elif (locale != row["locale"] or
              urlsplit(row["site_url"]).netloc != urlsplit(CONNECTIONS[row["connection_key"]][1]).netloc):
            blockers.append("El sitio o el idioma del destino no coincide con su conexión WordPress.")
        return {"destination_id": destination_id, "target_id": row["target_id"], "locale": locale,
                "base_url": CONNECTIONS[row["connection_key"]][1] if row["connection_key"] else row["site_url"],
                "cta_links": {name: links.get(name, {"url": "", "text": ""}) for name in names},
                "editable": not blockers, "blockers": blockers, "updated_at": row["updated_at"]}

    def get_destination_ctas(self, destination_id):
        _id(destination_id)
        with Database(self.db_path) as db:
            return self._destination_ctas(db.connection, destination_id)

    def save_destination_ctas(self, destination_id, payload):
        """Update only shared CTA settings; routing, credentials and articles stay intact."""
        _id(destination_id)
        if not isinstance(payload, dict) or set(payload) != {"cta_links"}:
            raise ValueError("Se requiere únicamente cta_links.")
        with Database(self.db_path) as db, db.connection:
            db.connection.execute("BEGIN IMMEDIATE")
            item = self._destination_ctas(db.connection, destination_id)
            if not item["editable"]:
                raise ValueError(" ".join(item["blockers"]))
            links = _clean_ctas(payload["cta_links"], item["locale"], item["base_url"])
            db.connection.execute("UPDATE deployment_targets SET cta_links_json=?,updated_at=? WHERE id=?",
                                  (json.dumps(links, ensure_ascii=False), _now(), item["target_id"]))
            return self._destination_ctas(db.connection, destination_id)

    def save_target(self, target_id, payload):
        if not isinstance(payload, dict) or set(payload) != {"guide_url", "category_ids", "language_mode", "cta_links", "notes"}:
            raise ValueError("Se requieren ruta, categorías, modo de idioma, enlaces CTA y notas.")
        target = self.get_target(target_id)
        guide = _url(payload["guide_url"], target["base_url"], "La ruta de guías")
        categories = payload["category_ids"]
        if not isinstance(categories, list) or len(categories) > 50:
            raise ValueError("Las categorías deben ser una lista de hasta 50 identificadores.")
        categories = sorted({_id(value) for value in categories})
        language = payload["language_mode"]
        if not isinstance(language, str) or language not in {"site_default", "pending"}:
            raise ValueError("El modo de idioma no es válido.")
        if target["connection_key"] == "canadians_fr" and language != "pending":
            raise ValueError("La integración del francés sigue pendiente; no puede enviarse como idioma predeterminado.")
        clean_ctas = _clean_ctas(payload["cta_links"], target["locale"], target["base_url"])
        notes = _text(payload["notes"], "Las notas")
        with Database(self.db_path) as db, db.connection:
            db.connection.execute("""UPDATE deployment_targets SET guide_url=?,category_ids_json=?,language_mode=?,
                cta_links_json=?,notes=?,updated_at=? WHERE id=?""", (guide,json.dumps(categories),language,
                json.dumps(clean_ctas, ensure_ascii=False),notes,_now(),target_id))
        return self.get_target(target_id)
