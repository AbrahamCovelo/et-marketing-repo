# Base de contenido para localización

Extracción de guías de Moving to Spain, limpieza determinista, almacenamiento local en SQLite y un panel para revisar el contenido y preparar sus futuras adaptaciones. Los documentos originales están en [extras/Idea general del proyecto.txt](<extras/Idea general del proyecto.txt>) y [extras/posible_estructura_del_proyecto.txt](extras/posible_estructura_del_proyecto.txt). La ejecución de generación y el envío a WordPress quedan para etapas posteriores.

El [diseño de extracción](docs/diseno-extraccion.md) explica los hallazgos de 15 guías, qué se conserva, el modelo de datos y cómo se registran las omisiones sin inventar contenido.

La [entrega inicial](docs/estado-etapa1.md) deja 107 artículos locales y un ejemplo de exportación, con el resultado de las comprobaciones y los casos que requieren revisión antes de regionalizar.

## Instalación en PowerShell

Requiere Python 3.12 o posterior. Ejecutar desde la raíz del repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python main.py init
```

Para ejecutar sin pruebas basta instalar `requirements.txt`. La extracción, la revisión y la preparación local no necesitan claves de API.

## Panel local

En Windows puede abrir **`iniciar-panel.cmd` con doble clic**. También puede iniciar el panel desde PowerShell:

```powershell
.\.venv\Scripts\python main.py dashboard --open-browser
```

El navegador abre `http://127.0.0.1:8765`. Mantenga abierta la consola mientras trabaja; `Ctrl+C` detiene el servidor. Para usar otro puerto, agregue `--port 8766`. Si ya tenía instalado el proyecto, actualice primero las dependencias con `pip install -r requirements.txt` usando el Python del entorno virtual.

El panel permite:

- Buscar en título, texto limpio o URL, sin distinguir tildes ni mayúsculas; filtrar por publicación, modificación o extracción de versión.
- Abrir un artículo y revisar el texto limpio, original, exclusiones, bloques, enlaces y versiones. El texto se muestra sin reproducir el diseño del sitio fuente.
- Guardar una selección entre sesiones, actualizar los seleccionados o volver a limpiar sus respaldos locales.
- Descubrir URLs, extraer nuevos artículos y actualizar existentes desde el catálogo o una lista de URLs, con rangos de fecha del origen.
- Consultar el progreso y el historial de operaciones, o descargar el JSON estructurado de un artículo.
- Gestionar destinos, versiones de prompts y configuraciones para las futuras adaptaciones.
- Seleccionar artículos, pulsar «Adaptar seleccionados…» y previsualizar o guardar una preparación local para una o varias configuraciones.
- Consultar las preparaciones de cada artículo y cancelarlas sin confundirlas con adaptaciones ya generadas.

Los 107 artículos iniciales tienen fechas de publicación y modificación originales. El filtro de fechas del origen incluye ambos extremos. Para comprobar fechas nuevas durante una extracción hay que descargar las páginas candidatas; únicamente se almacenan las que cumplen el rango. El modo «solo nuevos» omite los artículos ya guardados. Consulte los detalles en [Panel local](docs/panel-local.md).

## Preparación de adaptaciones

Hay tres variantes iniciales para dos sitios: Canadians In Spain en inglés (`en-CA`) y francés (`fr-CA`), y Colombians in Spain en español (`es-CO`). Los destinos y sus URLs son editables. Cada variante empieza con un prompt vacío en borrador y una configuración sin modelo elegido, pendientes de las plantillas y decisiones de los socios.

La nueva sección «Preparación de adaptaciones» permite crear y editar destinos, prompts y configuraciones. Una configuración conserva la versión de prompt elegida; editar el prompt no la sustituye automáticamente. Desde Revisión de datos puede preparar hasta 500 combinaciones de artículo y configuración por lote. La vista previa se calcula localmente y guardar conserva las versiones exactas del origen y de la configuración.

**La generación está deshabilitada en el servidor.** Ninguna acción de preparación llama a proveedores, valida credenciales ni consume saldo de API. Tener una clave o cambiar una variable de entorno no habilita la ejecución. Las preparaciones tampoco forman una cola que vaya a ejecutarse automáticamente. El historial sigue indicando «Todavía no hubo adaptaciones.» hasta una fase futura con resultados reales.

Las claves se configuran en el servidor mediante `.env`, excluido de Git, o variables de entorno. [.env.example](.env.example) contiene los nombres de variables, sin secretos; `extras/EntreTramites.txt` también está excluido. El navegador recibe solo si existe una credencial configurada, nunca su valor. Los detalles del flujo, las variables de plantilla y el modelo de almacenamiento están en [Preparación de generación](docs/preparacion-generacion.md).

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
src/dashboard/                panel local, consultas, operaciones y presentación
src/generation/               catálogo, versiones y preparación local sin ejecución
iniciar-panel.cmd             acceso al panel en Windows
extras/                       documentos iniciales del proyecto
.env.example                  nombres de variables, sin claves
.env                          credenciales locales, excluidas de Git
data/source_articles.db       base local (se crea al ejecutar)
data/                         respaldos HTML comprimidos y exportaciones
data/research/                muestra HTML local y manifiesto de investigación
docs/                         decisiones y evidencia de las 15 lecturas
tests/                        regresiones de extracción, panel y preparación local
scripts/capture_research.py   captura reproducible de las primeras 3 páginas
```

Se usa `src/extraction` en lugar de `src/1_extraccion` para tener un paquete Python importable. Los datos locales, respaldos y entorno virtual están excluidos de Git. No se modifican ni publican contenidos de los sitios.

La cuarta responsabilidad propuesta es `dashboard`: coordina la revisión y las acciones sobre los módulos de extracción, generación y despliegue. `generation` ya conserva las decisiones editoriales y las preparaciones locales; su ejecución y el módulo de despliegue siguen pendientes.

El JSON de exportación separa el contenido reutilizable de la auditoría. Las exclusiones de autoría, los huecos informativos y las limitaciones de la fuente requieren revisión antes de regionalizar; no son órdenes para rellenar con información inventada.
