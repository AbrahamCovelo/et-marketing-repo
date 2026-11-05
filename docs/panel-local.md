# Panel local de revisión, extracción y preparación

El módulo `src/dashboard` añade la cuarta responsabilidad del proyecto: una interfaz de gestión que utiliza los datos y servicios de extracción existentes. La selección sirve para operaciones por lote y para preparar futuras adaptaciones. El panel tiene las secciones Revisión de datos, Scraping y Preparación de adaptaciones. La preparación es local: no hay ejecución generativa ni envío a WordPress.

## Abrir el panel

Desde la raíz del repositorio:

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python main.py dashboard --open-browser
```

En Windows, `iniciar-panel.cmd` ejecuta el mismo comando. El panel escucha únicamente en `127.0.0.1`, puerto 8765. La consola debe permanecer abierta. El servidor y el navegador son locales; no se despliega ni se copia la base a un servicio externo.

Una base alternativa puede elegirse al iniciar:

```powershell
.\.venv\Scripts\python main.py --db data/otra_base.db dashboard --port 8766
```

## Revisión de datos

La lista muestra la versión actual de cada artículo canónico. La búsqueda recorre título, URL y texto limpio; trata las tildes y mayúsculas como equivalentes. Los filtros de texto, fecha, avisos y selección se combinan. Los resultados están paginados en grupos de 25.

Al abrir un artículo, las vistas ofrecen:

| Vista | Contenido |
|---|---|
| Texto limpio | Texto que conserva la extracción, sin diseño del sitio original |
| Original | Cuerpo textual antes de limpiar |
| Exclusiones | Motivo, texto retirado y bloques vecinos de cada omisión |
| Bloques | Estructura conservada: secciones, listas, tablas y preguntas frecuentes |
| Enlaces | Inventario de enlaces y decisión de conservación |
| Versiones | Fechas y metadatos de las versiones históricas; la lectura corresponde a la actual |
| Adaptaciones | Preparaciones guardadas, configuraciones y versiones elegidas, estado y cambios posteriores |

El botón «Descargar JSON» utiliza el contrato de exportación existente: mantiene los avisos, pero no incluye las firmas ni el texto descartado. La vista Original y la auditoría sí muestran ese material para poder revisarlo.

La selección se guarda en `dashboard_selection`, asociada al identificador del artículo. Persiste al recargar, cerrar el navegador o reiniciar el panel, y no altera versiones ni marcas de calidad. Los avisos de revisión siguen siendo resultados del limpiador: seleccionar un artículo no equivale a aprobarlo.

La lista y el detalle distinguen las preparaciones guardadas de las adaptaciones reales. En esta fase todos los artículos mantienen cero adaptaciones, aunque tengan una o varias preparaciones.

## Preparación de adaptaciones

La sección contiene cuatro vistas: Configuraciones, Prompts, Destinos y Preparaciones guardadas. Los destinos iniciales representan dos sitios y tres variantes: Canadians In Spain en inglés y francés, y Colombians in Spain en español. Se pueden editar sus URLs e idiomas o añadir otros destinos.

Los tres prompts suministrados el 4 de noviembre de 2026 están cargados en la base de trabajo y en estado Borrador, pendientes de pruebas. Conservan las instrucciones de HTML, localización y etiquetas CTA. Puede guardar borradores, crear prompts y conservar sus versiones; para marcar uno como Revisado hacen falta instrucciones de sistema, plantilla y una variable que incluya el texto o JSON del artículo. Esa marca expresa revisión editorial y no activa generación.

Cada configuración combina un destino con una versión concreta de un prompt, un proveedor y un modelo. Si cambia un prompt, las configuraciones existentes conservan su versión anterior y muestran el aviso correspondiente. Para usar la nueva versión debe seleccionarla y guardar la configuración. Los proveedores y modelos son datos locales, sin consulta ni validación contra una API.

«Seleccionar todos los resultados» añade los artículos de todas las páginas que cumplen los filtros aplicados, hasta 5000 resultados por acción. Para seleccionar toda la base, limpie primero los filtros. La preparación mantiene su límite de 500 combinaciones: los 107 artículos actuales con tres destinos suman 321.

El diálogo de preparación permite usar el modelo de cada configuración o elegir un GPT para todo el lote, con una opción manual. El modelo queda guardado en cada preparación y visible en el historial. Esta elección no edita las configuraciones generales; una combinación con otro modelo se registra por separado. Un modelo GPT común no se aplica a configuraciones Gemini.

La sección «WordPress · Preparación» añade los ajustes de rutas, categorías y CTA del destino. Muestra únicamente si las credenciales privadas están configuradas. Su vista previa comprueba título, HTML semántico, extracto e ID de una imagen destacada existente; no confirma ese ID contra el sitio ni guarda o envía un artículo. Consulte [Preparación de WordPress](preparacion-wordpress.md).

Para preparar artículos:

1. En Revisión de datos, seleccione uno o varios artículos y pulse «Adaptar seleccionados…».
2. Marque una o varias configuraciones. Se crea una combinación por cada artículo y configuración; el máximo es 500 por preparación.
3. Pulse «Previsualizar preparación» para revisar las combinaciones, sus avisos y el ejemplo de entrada de la primera combinación. Todo se calcula localmente y la vista previa no guarda una preparación.
4. Pulse «Guardar preparación» para conservar el lote y las versiones exactas elegidas. Los borradores y modelos pendientes quedan señalados para revisión.
5. Consulte el lote en Preparaciones guardadas o la vista Adaptaciones del artículo. Puede cancelar el lote sin borrar su historial.

Guardar dos veces las mismas combinaciones reutiliza las preparaciones activas, sin duplicarlas. Si dos lotes comparten una preparación, cancelar uno mantiene la preparación disponible para el otro. Un cambio del artículo, de la configuración o del destino permite guardar una preparación nueva; el historial conserva la anterior y señala qué cambió. Una revisión nueva del prompt por sí sola no sustituye la versión fijada en la configuración.

**Guardar una preparación no genera una adaptación.** El historial separa preparaciones y resultados reales de pruebas individuales. En el artículo de Barcelona, la pestaña Adaptaciones muestra la prueba para Colombia, su modelo, consumo y un desplegable con el HTML guardado como texto seguro. Solo las respuestas completas cuentan como adaptaciones; siguen pendientes de revisión editorial. Los botones de generación masiva están deshabilitados y ninguna preparación se ejecutará automáticamente.

Cuando una prueba se envía explícitamente mediante el servicio individual, ese historial también muestra su estado en WordPress. Para Barcelona permite abrir el borrador 1546 en el editor del sitio colombiano, con la imagen 1369. El enlace requiere iniciar sesión en WordPress y no publica la entrada; el texto y los CTA siguen pendientes de revisión.

### Acciones sobre adaptaciones guardadas

En **Revisión de datos → Adaptaciones**, cada resultado ofrece:

- **Borrar de la base local**: requiere confirmación. Vacía el HTML, la respuesta y el contexto guardados, así como las copias de contenido de los envíos. Conserva IDs, consumo y claves que impiden repetir intentos accidentalmente. El original extraído, WordPress, las exportaciones y los respaldos no cambian. El historial muestra que se eliminó el contenido; deja de contar como adaptación activa y ya no permite reenviarla. No hay restauración desde el panel.
- **Volver a desplegar**: disponible para adaptaciones completas con un primer envío y una entrada identificada. Primero consulta WordPress para mostrar si actualizará esa entrada o creará un borrador porque el ID anterior ya no existe. Comprueba imagen y categorías; utiliza el título y las categorías del envío anterior, el HTML local y los CTA actualmente configurados. Puede sustituir cambios hechos directamente en WordPress.

En actualizaciones se conserva el estado remoto y no se modifica el slug. Una entrada publicada o programada exige una casilla adicional de confirmación. Los CTA pendientes requieren confirmación expresa y solo se permiten en borradores. Si la entrada está en la papelera, hay que restaurarla en WordPress antes de reenviar. Un error de permisos o de conexión nunca se interpreta como que falta la entrada.

Antes de enviar se repite la comprobación del destino. Si cambió respecto a la confirmación, el panel pide revisar de nuevo. Cada confirmación tiene una clave única y un registro reservado antes del POST: repetir esa misma solicitud no repite el envío. Los resultados inciertos o interrumpidos bloquean otro envío y el borrado hasta comprobar manualmente el intento; no se reintentan automáticamente. Ninguna de estas acciones llama a OpenAI.

Rutas protegidas por el token y el origen del panel:

| Ruta | Acción |
|---|---|
| `DELETE /api/generation/trials/<id>` | Borrado local con `confirm: true` |
| `POST /api/generation/trials/<id>/redeploy-preview` | Comprobaciones de lectura en WordPress y resumen de confirmación |
| `POST /api/generation/trials/<id>/redeploy` | Envío explícito con clave única, revisión comprobada y confirmaciones |

La generación masiva y las rutas genéricas `/api/generation/execute` y `/api/deployment/execute` siguen bloqueadas. Los nuevos controles se cargan al reiniciar el panel. Se verificaron con una base temporal y respuestas WordPress simuladas; su desarrollo no borró ni reenvió la adaptación real de Barcelona.

La configuración privada usa `.env`, ignorado por Git, con [.env.example](../.env.example) como referencia sin secretos. `extras/EntreTramites.txt` también se ignora. El panel muestra únicamente la presencia de una credencial; no la expone ni comprueba su validez. Consulte [Preparación de generación](preparacion-generacion.md) para el contrato de las plantillas y la persistencia.

## Fechas

| Campo | Significado y filtro |
|---|---|
| `published_at` | Publicación original; se usa el día que figura en la fecha del origen |
| `modified_at` | Última modificación informada por el origen; se usa su día original |
| `captured_at` | Creación de la versión extraída o reprocesada; se consulta y muestra en Bogotá |

Los rangos son inclusivos y pueden tener un solo extremo. Una fecha ausente o inválida no se sustituye por otra; queda fuera de un rango sobre ese campo. Los 107 artículos iniciales tienen publicación y modificación. La fecha de creación de versión no equivale a la última comprobación HTTP: una descarga idéntica puede conservar una versión previa.

## Operaciones

- **Descubrir URLs:** recorre el índice con el límite de páginas elegido. No descarga los artículos ni aplica filtros de fecha, porque el índice no proporciona esas fechas. Las URLs encontradas pueden pasarse al formulario de extracción.
- **Traer solo nuevos:** omite los artículos ya almacenados. Descarga los candidatos desconocidos y guarda los que pasan el filtro de fecha, si existe.
- **Volver a descargar y actualizar:** consulta el origen aunque el artículo ya esté almacenado. Compara las fechas recién obtenidas con el rango, no las fechas antiguas de SQLite. Una extracción idéntica no crea otra versión.
- **Actualizar seleccionados:** vuelve a descargar solo los artículos marcados en la lista.
- **Limpiar copias locales:** repite la limpieza de los seleccionados sin HTTP.
- **Limpiar todos los respaldos:** repite la limpieza de todos los artículos almacenados sin HTTP.

El límite de artículos cuenta candidatos del listado, incluidos los que después se omiten. Para revisar fechas antiguas en todo el sitio, seleccione «Recorrer todo el catálogo»; limitarse a las primeras páginas no cubre artículos que estén en páginas posteriores.

Las operaciones se ejecutan en segundo plano mientras el panel sigue disponible. Se admite una operación activa por proceso del panel. El progreso distingue guardados, omitidos, errores, fuera del rango y sin fecha requerida; estas dos últimas categorías forman parte de los omitidos. Los errores individuales se registran y no detienen los demás artículos.

El progreso reciente vive en memoria; el historial de ejecuciones y descargas permanece en SQLite. Mantenga abierto el servidor hasta terminar una operación. Si se fuerza su cierre, no hay reanudación automática del trabajo pendiente; los artículos ya confirmados conservan sus transacciones.

## Organización técnica y validación

```text
src/dashboard/
  app.py                  HTTP local y exportación
  generation_routes.py    catálogo, vista previa, preparaciones y bloqueo de ejecución
  repository.py           consultas, fechas y selección persistente
  jobs.py                 operaciones y progreso
  templates/index.html    estructura del panel
  static/app.js           interacción
  static/generation.js    gestión editorial y preparaciones
  static/style.css        presentación básica y adaptable
```

Se reutilizan el limpiador, cliente HTTP, descubrimiento, snapshots y persistencia de `src/extraction`. La tabla de selección y las tablas `generation_*` son extensiones independientes; conservan el esquema de extracción y los registros históricos de los artículos. Cada consulta y operación abre su propia conexión SQLite. El catálogo y las preparaciones se almacenan en la misma base elegida al iniciar el panel.

Validación histórica del hito anterior, dedicado a revisión y extracción: 137 pruebas aprobadas, además de 8 subpruebas. Su prueba de navegador, sobre una copia del corpus, comprobó búsqueda, rangos inclusivos, paginación, detalle, auditoría, exportación, persistencia de selección, limpieza real de un respaldo, historial, errores de entrada, texto sin ejecución de HTML y presentación móvil. No se ejecutaron extracciones de red sobre la base principal durante esas pruebas. Esas cifras corresponden a aquel hito, no al total actual.

Las pruebas de preparación se encuentran en `tests/test_generation_repository.py`, `tests/test_generation_preparation.py` y `tests/test_generation_api.py`; cubren persistencia, versiones, deduplicación, cancelación, plantillas, credenciales y bloqueo de ejecución. Para ejecutar la suite actual use `.\.venv\Scripts\python -m pytest -q` desde la raíz.
