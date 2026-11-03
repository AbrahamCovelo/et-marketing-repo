# Base de contenido para localización

Primera etapa del proyecto: extracción de guías de Moving to Spain, limpieza determinista y almacenamiento local en SQLite. Los documentos de idea originales se mantienen como referencia. La generación y el envío a WordPress quedan para etapas posteriores.

El [diseño de extracción](docs/diseno-extraccion.md) explica los hallazgos de 15 guías, qué se conserva, el modelo de datos y cómo se registran las omisiones sin inventar contenido.

La [entrega inicial](docs/estado-etapa1.md) deja 107 artículos locales y un ejemplo de exportación, con el resultado de las comprobaciones y los casos que requieren revisión antes de regionalizar.

## Instalación en PowerShell

Requiere Python 3.12 o posterior. Ejecutar desde la raíz del repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python main.py init
```

Para ejecutar sin pruebas basta instalar `requirements.txt`. No hacen falta claves de API para esta etapa.

## Uso

```powershell
# Ver los artículos de las primeras tres páginas del índice
.\.venv\Scripts\python main.py discover --output data/urls.json

# Extraer esos artículos y guardar su texto; omitir los ya almacenados
.\.venv\Scripts\python main.py scrape

# Recorrer toda la paginación de guías
.\.venv\Scripts\python main.py scrape --all-pages

# Actualizar un artículo concreto y conservar su historial
.\.venv\Scripts\python main.py scrape --url https://movingtospain.com/avoid-spain-digital-nomad-visa-renewal-problems/ --refresh

# Resumen y listado
.\.venv\Scripts\python main.py stats
.\.venv\Scripts\python main.py list

# Exportación estructurada para preparar la futura adaptación
.\.venv\Scripts\python main.py export --id 1 --output data/exports/article-1.json

# Lectura de texto plano (sin jerarquía ni advertencias)
.\.venv\Scripts\python main.py export --id 1 --text --output data/exports/article-1.txt

# Volver a limpiar la muestra local sin descargarla de nuevo
.\.venv\Scripts\python main.py import-cache data/research/manifest.json --refresh

# Reprocesar todos los artículos desde sus respaldos comprimidos, sin red
.\.venv\Scripts\python main.py reclean

# Pruebas locales; no necesitan red
.\.venv\Scripts\python -m pytest -q
```

`--db` se escribe antes del comando, por ejemplo `main.py --db data/prueba.db init`. `scrape` acepta `--url` varias veces o `--url-file` con JSON de URLs / una URL por línea. `--limit` limita artículos y `--max-pages` limita páginas del índice. El modo `--all-pages` sigue su paginación hasta terminar.

Los artículos existentes se omiten salvo que se indique `--refresh`. Una captura sin cambios de extracción no duplica la versión. Un fallo en un artículo queda registrado y no impide procesar los siguientes; el comando termina con código 1 si hubo fallos. El cliente aplica pausas y reintentos limitados, y valida las respuestas antes de limpiar.

## Archivos

```text
main.py                       entrada de consola
src/extraction/               scraper, cleaner, modelos, base de datos y CLI
data/source_articles.db       base local (se crea al ejecutar)
data/                         respaldos HTML comprimidos y exportaciones
data/research/                muestra HTML local y manifiesto de investigación
docs/                         decisiones y evidencia de las 15 lecturas
tests/                        regresiones de limpieza, persistencia y descarga
scripts/capture_research.py   captura reproducible de las primeras 3 páginas
```

Se usa `src/extraction` en lugar de `src/1_extraccion` para tener un paquete Python importable. Los datos locales, respaldos y entorno virtual están excluidos de Git. No se modifican ni publican contenidos de los sitios.

El JSON de exportación separa el contenido reutilizable de la auditoría. Las exclusiones de autoría, los huecos informativos y las limitaciones de la fuente requieren revisión antes de regionalizar; no son órdenes para rellenar con información inventada.
