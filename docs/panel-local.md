# Panel local de revisión, extracción y preparación

Estado de la sesión del 6 de noviembre de 2026: la corrección de formato y recuperación se sirve en `http://127.0.0.1:8766/`. El proceso anterior del puerto 8765 sigue abierto porque la revisión automática bloqueó su reinicio. Un inicio posterior desde el repositorio carga el código actualizado. La validación completa de esta corrección pasó **881 pruebas y 8 subpruebas**, más la recuperación por navegador sobre una copia aislada.

El módulo `src/dashboard` añade una interfaz de gestión sobre la base local. Incluye Revisión de datos, Scraping, Preparación de adaptaciones y WordPress. Guardar una preparación es local. La acción explícita «Generar con API» evalúa y redacta las combinaciones seleccionadas, con la opción de crear borradores en WordPress con portada automática.

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

La lista y el detalle distinguen las preparaciones guardadas de las adaptaciones reales: preparar un artículo no incrementa su contador de resultados generados.

## Preparación de adaptaciones

La sección contiene cuatro vistas: Configuraciones, Prompts, Destinos y Preparaciones guardadas. Los destinos iniciales representan dos sitios y tres variantes: Canadians In Spain en inglés y francés, y Colombians in Spain en español. Se pueden editar sus URLs e idiomas o añadir otros destinos.

Los tres prompts suministrados el 4 de noviembre de 2026 están cargados en la base de trabajo y en estado Borrador, pendientes de pruebas. Conservan las instrucciones de HTML, localización y etiquetas CTA. Puede guardar borradores, crear prompts y conservar sus versiones; para marcar uno como Revisado hacen falta instrucciones de sistema, plantilla y una variable que incluya el texto o JSON del artículo. Esa marca expresa revisión editorial y no activa generación.

Cada configuración combina un destino con una versión concreta de un prompt, un proveedor y un modelo. Si cambia un prompt, las configuraciones existentes conservan su versión anterior y muestran el aviso correspondiente. Para usar la nueva versión debe seleccionarla y guardar la configuración. La lista de modelos se consulta en la cuenta mediante GET sin generación.

«Seleccionar todos los resultados» añade los artículos de todas las páginas que cumplen los filtros aplicados, hasta 5000 resultados por acción. Para seleccionar toda la base, limpie primero los filtros. La preparación mantiene su límite de 500 combinaciones: los 107 artículos actuales con tres destinos suman 321.

El diálogo permite usar el modelo de cada configuración al guardar una preparación, o elegir un GPT disponible para todo el lote. Para generar se elige un modelo común explícito y su esfuerzo. Los modelos proceden de `GET /v1/models`; los esfuerzos se leen de la documentación oficial y se almacenan en caché. Si no se pueden verificar, solo se ofrece el valor predeterminado del proveedor, omitiendo `reasoning` en la solicitud. Esta elección no edita las configuraciones generales. El flujo de ejecución actual usa OpenAI.

En **Preparación de adaptaciones → Destinos**, los CTA de cada idioma tienen campos independientes de URL y texto. Pueden quedar vacíos hasta que existan las páginas. «Guardar enlaces CTA» actualiza la misma configuración utilizada antes del envío a WordPress; no modifica prompts ni artículos guardados.

La sección «WordPress · Preparación» añade los ajustes de rutas, categorías y CTA del destino. Muestra únicamente si las credenciales privadas están configuradas. Su vista previa comprueba título, HTML semántico, extracto e ID de una imagen destacada existente; no confirma ese ID contra el sitio ni guarda o envía un artículo. Consulte [Preparación de WordPress](preparacion-wordpress.md).

Para preparar artículos:

1. En Revisión de datos, seleccione uno o varios artículos y pulse «Adaptar seleccionados…».
2. Marque una o varias configuraciones. Se crea una combinación por cada artículo y configuración; el máximo es 500 por preparación.
3. Pulse «Previsualizar preparación» para revisar las combinaciones, sus avisos y el ejemplo de entrada de la primera combinación. Todo se calcula localmente y la vista previa no guarda una preparación.
4. Pulse «Guardar preparación» para conservar el lote y las versiones exactas elegidas. Los borradores y modelos pendientes quedan señalados para revisión.
5. Consulte el lote en Preparaciones guardadas o la vista Adaptaciones del artículo. Puede cancelar el lote sin borrar su historial.

Guardar dos veces las mismas combinaciones reutiliza las preparaciones activas, sin duplicarlas. Si dos lotes comparten una preparación, cancelar uno mantiene la preparación disponible para el otro. Un cambio del artículo, de la configuración o del destino permite guardar una preparación nueva; el historial conserva la anterior y señala qué cambió. Una revisión nueva del prompt por sí sola no sustituye la versión fijada en la configuración.

**Guardar una preparación no genera una adaptación.** El historial separa preparaciones y resultados reales. Solo las respuestas completas cuentan como adaptaciones; siguen pendientes de revisión editorial. Las preparaciones guardadas no se ejecutan automáticamente.

### Generar desde el panel

1. Seleccione artículos y pulse «Adaptar seleccionados»; marque las configuraciones que desea probar.
2. Elija un modelo de la lista de la cuenta. «Actualizar modelos» solo consulta disponibilidad, sin consumir tokens de generación. Escoja el esfuerzo documentado o el predeterminado.
3. Deje desmarcada la opción de WordPress para guardar únicamente texto adaptado local. Márquela para importar una portada seleccionada por el algoritmo y crear un borrador. El francés admite generación local; su envío está pendiente de la integración del idioma en WordPress.
4. Pulse **Generar con API**. Esta acción consume saldo: primero la API consulta fuentes y evalúa si el tema es aplicable; solo si decide `adapt`, redacta. Una decisión `omit` o `needs_verification` no produce artículo ni portada.
5. Consulte el progreso en «Generaciones desde el panel», y el resultado en Revisión de datos → Adaptaciones.

La selección está limitada a 500 combinaciones y se ejecuta en serie, con un único lote activo. Se conserva modelo, esfuerzo, selección y contexto de cada combinación en `panel_generation_runs`. Las reservas antes de las llamadas impiden que repetir la misma solicitud vuelva a gastar. Ante un resultado incierto, se detienen los elementos restantes sin reintentar. Mantenga abierto el servidor: tras una interrupción no se reanuda un lote automáticamente; hay que comprobar los registros antes de reconciliar su estado.

Las correcciones editoriales, exclusiones y el título inicial los determina la API. El código puede normalizar el formato sin reescribir su contenido: por ejemplo, encierra un CTA suelto dentro de un párrafo. Se conserva el texto original y se comprueba que el resultado mantiene palabras, enlaces y marcadores antes de aceptarlo. El envío opcional crea únicamente borradores; permite mantener allí CTA pendientes, claramente identificados. No genera imágenes con OpenAI.

### Recuperar una adaptación detenida por formato

En **Generaciones desde el panel**, un lote con una adaptación completa detenida por el validador ofrece **Continuar con contenido guardado**. La acción normaliza el formato recuperable y continúa con la opción de WordPress que se marcó originalmente. No vuelve a evaluar el artículo ni solicita otra redacción a OpenAI. Si se había pedido un borrador, puede importar la portada automática y crear ese borrador usando el resultado guardado.

La recuperación conserva las combinaciones ya terminadas, el modelo, el esfuerzo y la selección. No permite sustituir el artículo, cambiar de configuración ni activar un envío que no se hubiera pedido antes. Se rechaza si falta o se eliminó el resultado, cambió el contexto o hay una operación remota incierta. La causa anterior y cada recuperación se conservan en el historial técnico del lote.

El normalizador cubre texto/CTA fuera de bloques, envoltorios simples sin atributos, cercas completas de código HTML y equivalentes como `<b>`/`<i>`. No borra contenido para aprobar un artículo ni admite scripts, enlaces inseguros o estructuras ambiguas. Lo que no pueda corregir sin alterar el contenido sigue señalado para revisión, con el diagnóstico concreto; las demás combinaciones del lote pueden terminar.

Cuando una prueba se envía explícitamente mediante el servicio individual, ese historial también muestra el estado del envío registrado. Para Barcelona permite abrir la entrada 1546 en el editor del sitio colombiano, con la imagen 1369. Se creó como borrador; una consulta del 5 de noviembre encontró la entrada publicada, sin modificación por estas pruebas. El enlace requiere iniciar sesión en WordPress. El estado del envío local es histórico; la comprobación previa al reenvío consulta el estado remoto actual.

### Acciones sobre adaptaciones guardadas

En **Revisión de datos → Adaptaciones**, cada resultado ofrece:

- **Editar texto**: abre el HTML guardado. «Guardar texto» valida las etiquetas y los CTA, guarda una nueva versión local y conserva la respuesta original de la API. Exportar o volver a desplegar utiliza la última edición. Guardar no llama a OpenAI ni cambia WordPress. Dos editores no pueden sobrescribirse sin recargar la versión vigente.
- **Borrar de la base local**: requiere confirmación. Vacía el HTML, la respuesta y el contexto guardados, las ediciones y las copias de contenido de los envíos. La adaptación desaparece de tarjetas, títulos, historial y contadores del panel. Solo queda una reserva técnica interna con IDs, consumo y claves para impedir repetir intentos de pago. El original extraído, WordPress, las exportaciones y los respaldos no cambian. No hay restauración desde el panel.
- **Volver a desplegar**: disponible para adaptaciones completas con un primer envío y una entrada identificada. Primero consulta WordPress para mostrar si actualizará esa entrada o creará un borrador porque el ID anterior ya no existe. Comprueba imagen y categorías; utiliza el título y las categorías del envío anterior, el HTML local y los CTA actualmente configurados. Puede sustituir cambios hechos directamente en WordPress.

En actualizaciones se conserva el estado remoto y no se modifica el slug. Una entrada publicada o programada exige una casilla adicional de confirmación. Los CTA pendientes requieren confirmación expresa y solo se permiten en borradores. Si la entrada está en la papelera, hay que restaurarla en WordPress antes de reenviar. Un error de permisos o de conexión nunca se interpreta como que falta la entrada.

Antes de enviar se repite la comprobación del destino. Si cambió respecto a la confirmación, el panel pide revisar de nuevo. Cada confirmación tiene una clave única y un registro reservado antes del POST: repetir esa misma solicitud no repite el envío. Los resultados inciertos o interrumpidos bloquean otro envío y el borrado hasta comprobar manualmente el intento; no se reintentan automáticamente. Ninguna de estas acciones llama a OpenAI.

Rutas protegidas por el token y el origen del panel:

| Ruta | Acción |
|---|---|
| `PUT /api/generation/trials/<id>/content` | Guarda HTML local con la revisión esperada |
| `DELETE /api/generation/trials/<id>` | Borrado local con `confirm: true` |
| `POST /api/generation/trials/<id>/redeploy-preview` | Comprobaciones de lectura en WordPress y resumen de confirmación |
| `POST /api/generation/trials/<id>/redeploy` | Envío explícito con clave única, revisión comprobada y confirmaciones |

Las rutas genéricas antiguas `/api/generation/execute` y `/api/deployment/execute` siguen bloqueadas. Los lotes explícitos usan `POST /api/generation/runs`; la recuperación de formato usa `POST /api/generation/runs/<id>/resume` con `{}` y reutiliza exclusivamente resultados ya guardados. Las consultas GET solo muestran estado. Los nuevos controles se cargan al reiniciar el panel. Las pruebas usan bases aisladas y proveedores simulados, sin generar artículos ni modificar entradas reales.

La configuración privada usa `.env`, ignorado por Git, con [.env.example](../.env.example) como referencia sin secretos. `extras/EntreTramites.txt` y `extras/Sitios.txt` también se ignoran. El navegador nunca recibe credenciales. La consulta de modelos sí verifica acceso de lectura con la clave de OpenAI. Consulte [Preparación de generación](preparacion-generacion.md) para el contrato de las plantillas y la persistencia.

## Aplicabilidad y portadas externas

En **Preparación de adaptaciones → Aplicabilidad antes de redactar**, indique un artículo y una configuración o tome el artículo abierto/seleccionado. Consulte el contexto antes de registrar una decisión:

- **Adaptar**: el tema principal es útil para ese público y la información esencial fue verificada. Los apartados ajenos pueden anotarse como exclusiones.
- **Omitir**: el artículo completo trata una exclusividad ajena y se confirmó, con evidencia fechada, que no hay alternativa aplicable.
- **Necesita verificación**: faltan comprobaciones para decidir; no se redacta todavía.

El formulario conserva motivo, pregunta central, responsable, comprobaciones, fuentes y exclusiones. La aplicación no verifica automáticamente las URLs que alguien introduce en este formulario. Las revisiones son históricas e inmutables; para corregir una se registra otra. Cambiar el artículo, el destino o la configuración invalida la decisión anterior. Preparar un lote sigue siendo posible, pero `run_trial` bloquea la nueva redacción sin una decisión vigente de adaptar. El formulario manual no llama a OpenAI; también muestra la última revisión guardada por el servicio automático con el responsable `OpenAI: <modelo>`.

El flujo automático se ejecuta explícitamente desde el panel o mediante `scripts/run_automatic_trial.py`. La API evalúa el original con herramientas de búsqueda, decide las exclusiones y las correcciones, propone el título y, si corresponde adaptar, redacta el cuerpo. El código normaliza el formato recuperable, lo valida y, al activar el envío, selecciona e importa una portada y crea el borrador. No hay una etapa de selección manual de imagen ni de edición del texto por el asistente en ese flujo.

En **WordPress · Preparación**, el buscador de portadas consulta **WordPress Photos** o **Openverse CC0**, conserva las candidatas y permite ver su origen. La sugerencia por artículo usa ciudad o tema presentes en el título; la puntuación no verifica por sí sola la pertinencia visual. Si no encuentra una imagen válida, deja el caso pendiente.

Después de revisar una candidata, puede importarla al sitio seleccionado. El archivo se valida y se sube a la biblioteca automáticamente, con autor, licencia y origen registrados. El ID devuelto rellena la imagen destacada de la vista previa. Esta acción solo sube medios: asignar esa portada a una entrada requiere el envío correspondiente. Los reenvíos de adaptaciones anteriores conservan su imagen previa.

Una imagen ya importada se reutiliza por sitio, también entre Canadá inglés y francés. Si un envío queda incierto, se bloquea su repetición hasta comprobarlo; no se crean duplicados mediante reintentos automáticos. El plugin All Sources Images no se instaló: véase el análisis y las limitaciones en [Preparación de WordPress](preparacion-wordpress.md).

Un rechazo definitivo permite confirmar un nuevo intento después de corregir la causa; queda registrado el fallo anterior. Esa opción no se ofrece para resultados inciertos ni operaciones todavía reservadas.

Reinicie el panel después de actualizar e instale `requirements.txt` para incorporar el validador de imágenes Pillow. Los controles nuevos conservan la protección local de token y origen.

Validación del 5 de noviembre de 2026: **485 pruebas y 8 subpruebas aprobadas**. La comprobación en Edge, con bases y servicios simulados, verificó las revisiones de aplicabilidad y 15 flujos de portadas, incluidos recuperación de errores, cambios de destino y presentación móvil; no hubo errores JavaScript. Las pruebas reales de catálogo e importación del medio 1573 se documentan por separado en [Preparación de WordPress](preparacion-wordpress.md).

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

### Categorías y control de gasto (6 de noviembre de 2026)

La ficha de cada adaptación muestra **evaluación e investigación**, **redacción**, la suma de tokens y el **coste total estimado en USD**. La sección general de consumo añade una tabla de totales por prueba, incluido el gasto de las borradas. «Último envío registrado» es un dato histórico local; no implica que el borrador siga existiendo o conserve ese estado en WordPress.

En **Revisión de datos**, el orden **Adaptación más reciente** coloca primero los artículos cuya última adaptación completa es más reciente. El filtro **Adaptaciones** permite ver todos, con adaptaciones o sin adaptaciones. **Fecha a consultar → Última adaptación · Bogotá** aplica el rango a esa fecha. Se excluyen intentos fallidos, incompletos y adaptaciones eliminadas; «Seleccionar todos los resultados» respeta los mismos filtros en todas las páginas.

El comando de inicio mantiene un bloqueo del sistema operativo por base de datos, para evitar paneles simultáneos en puertos distintos. Cerrar el proceso libera el bloqueo incluso si hubo un fallo; no se necesita borrar el archivo `.panel.lock`. Al iniciar también se instalan restricciones de reservas para impedir que un proceso antiguo cree lotes sin presupuesto o envíos sin categorías permitidas. La instancia activa quedó unificada en **http://127.0.0.1:8765/**; recargue las pestañas abiertas antes de generar.

En **WordPress → Destino y enlaces → Categorías para la clasificación automática**, consulte las categorías reales del sitio y guarde las permitidas. La categoría predeterminada se excluye explícitamente. Cuando la cuenta no puede leer Ajustes → Escritura, el panel pide identificarla; no supone que tenga un ID o nombre concreto. Colombia ya tiene habilitadas las ocho categorías indicadas por el usuario. Otros destinos necesitan su propia configuración.

La evaluación de aplicabilidad elige categorías entre ese catálogo en la misma llamada a OpenAI. Se validan antes de importar la portada y antes de enviar. Sin una selección válida no se envía el artículo. **Revisar categorías**, junto a cada adaptación, permite corregir la selección local sin regenerar texto. **Volver a desplegar** aplica el cambio a WordPress; si una entrada está en la papelera, no se restaura automáticamente. Las categorías legítimas cambiadas directamente en WordPress se conservan cuando no hay una selección local explícita.

Antes de **Generar con API**, pulse **Calcular coste estimado** y revise el importe. Una adaptación automática usa hasta dos solicitudes facturables: evaluación con búsquedas y redacción. Si cambia modelo, esfuerzo, selección, nivel de servicio o envío, debe recalcular. El presupuesto aceptado caduca a los 30 minutos para iniciar el lote; no causa reintentos pagados.

`config/generation-budget.json` recoge la meta acordada: USD 15 y 300 adaptaciones. Las tarifas fechadas están en `config/openai-pricing-2026-11-06.json`, separadas del código. El panel verifica el presupuesto al iniciar y antes de cada adaptación, incluye el gasto local histórico y se detiene ante precios o consumo desconocidos. La estimación usa una muestra real; **no limita la factura del proveedor ni puede detener retroactivamente una petición que consuma más de lo previsto**. No cambia automáticamente el modelo ni el nivel de servicio elegidos. Una combinación artículo/destino cuenta como una adaptación: 300 artículos en tres destinos son 900 adaptaciones.

**Consumo registrado de OpenAI** muestra evaluación y redacción por separado, incluidos intentos cuyo contenido se borró. Presenta los seis contadores solicitados, búsquedas y coste estimado. La explicación y las cifras históricas están en [la auditoría de consumo](auditoria-consumo-2026-11-06.md).

```text
src/dashboard/
  app.py                  HTTP local y exportación
  generation_routes.py    catálogo editorial, CTA, vista previa y preparaciones
  model_routes.py         modelos disponibles y esfuerzos documentados
  execution_routes.py     inicio explícito y consulta de lotes
  generation_jobs.py      lotes duraderos y ejecución secuencial
  adaptation_routes.py    edición local de HTML con control de revisión
  repository.py           consultas, fechas y selección persistente
  jobs.py                 operaciones y progreso
  templates/index.html    estructura del panel
  static/app.js           interacción
  static/generation.js    gestión editorial y preparaciones
  static/execution.js     selección de modelos/esfuerzos y generación explícita
  static/style.css        presentación básica y adaptable
```

Se reutilizan el limpiador, cliente HTTP, descubrimiento, snapshots y persistencia de `src/extraction`. La tabla de selección y las tablas `generation_*` son extensiones independientes; conservan el esquema de extracción y los registros históricos de los artículos. Cada consulta y operación abre su propia conexión SQLite. El catálogo y las preparaciones se almacenan en la misma base elegida al iniciar el panel.

Validación histórica del hito anterior, dedicado a revisión y extracción: 137 pruebas aprobadas, además de 8 subpruebas. Su prueba de navegador, sobre una copia del corpus, comprobó búsqueda, rangos inclusivos, paginación, detalle, auditoría, exportación, persistencia de selección, limpieza real de un respaldo, historial, errores de entrada, texto sin ejecución de HTML y presentación móvil. No se ejecutaron extracciones de red sobre la base principal durante esas pruebas. Esas cifras corresponden a aquel hito, no al total actual.

Las pruebas de preparación se encuentran en `tests/test_generation_repository.py`, `tests/test_generation_preparation.py` y `tests/test_generation_api.py`; cubren persistencia, versiones, deduplicación, cancelación, plantillas, credenciales y bloqueo de ejecución. Para ejecutar la suite actual use `.\.venv\Scripts\python -m pytest -q` desde la raíz.

Actualización del 6 de noviembre de 2026: **724 pruebas y 8 subpruebas aprobadas**. Playwright verificó CTA, edición, borrado sin remanentes y generación local/borrador con proveedores simulados sobre una copia aislada. La base principal conserva tres intentos históricos y una evaluación, sin nuevas generaciones, ediciones de texto ni envíos reales durante esta actualización. La comprobación de lectura confirmó las dos conexiones de Canadá y el catálogo dinámico; el panel se reinició en el puerto 8765. La adaptación eliminada 2 ya no aparece en su historial público.
