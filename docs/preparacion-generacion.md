# Preparación local de generación

## Primera prueba individual: Barcelona → Colombia

El 4 de noviembre de 2026 se autorizó una única adaptación real del artículo 1, versión de origen 429, con la configuración colombiana 3 (versión 3, ID de revisión 9). Se usó la plantilla vigente sin corregirla después de ver la salida. El registro `generation_trials.id=1` conserva el texto exacto enviado, las versiones, el HTML original, los identificadores de la respuesta, el modelo y el consumo.

- Modelo solicitado y devuelto: `gpt-5-nano-2025-08-07`, modalidad devuelta `flex`, razonamiento `minimal`.
- Consumo: 3543 tokens de entrada, 2121 de salida, 0 cacheados y 0 de razonamiento; total 5664.
- Estimación: `(3543 × 0.025 + 2121 × 0.20) / 1000000 = US$0.000512775`, según las [tarifas oficiales](https://developers.openai.com/api/docs/pricing). Es una estimación del consumo informado, no una factura.
- Una llamada completada. No hubo regeneración ni cambio automático de modelo. Después de corregir la credencial de WordPress, se reutilizó este resultado para crear el borrador 1546.
- La imagen indicada por el usuario corresponde al medio 1369 en Colombia; se usa en la vista previa, separada del cuerpo.

El HTML supera el validador semántico, pero el artículo necesita revisión: contiene una sección «Público objetivo y cultura», cifras COP sin tasa ni fecha de conversión, una pregunta de Brexit sin utilidad para este público, términos como «hours» y «Catalonia», «garante municipal» sin respaldo en el original, y afirmaciones fiscales que la fuente no sustenta. La salida se conserva sin correcciones para evaluar el prompt y el modelo. Los CTA todavía necesitan destinos reales. No debe confundirse estado de generación `completed` con aprobación editorial.

`src/generation/trial.py` reserva la clave de prueba en SQLite antes de hacer el POST a Responses. Volver a usar la misma clave devuelve el registro guardado; ni siquiera un timeout o una respuesta incompleta provoca otra llamada. Tras una interrupción, un estado `running` o `unknown` necesita comprobación manual, no otra clave automática. [Flex](https://developers.openai.com/api/docs/guides/flex-processing) puede demorar; se permite hasta diez minutos de lectura y se desactivan reintentos y redirecciones. Las credenciales no se archivan.

`scripts/run_generation_trial.py` permite una prueba con artículo, configuración, modelo y clave explícitos. El modo `--export-trial 1` solo lee el resultado existente y produce cuerpo HTML original, vista previa con título e imagen separados, y metadatos JSON bajo `data/adaptations/`. No consume API. El panel ofrece `GET /api/generation/trials/<id>` como consulta limitada a metadatos y HTML; no expone la petición ni la respuesta completa del proveedor.

GPT-5 nano figura en retirada. La elección se hizo para esta prueba económica y no cambia los modelos generales de las configuraciones. Su disponibilidad debe revisarse de nuevo para otra prueba. [Modelo y condiciones](https://developers.openai.com/api/docs/models/gpt-5-nano).

El panel organiza las adaptaciones y permite revisar qué entrada se usaría, sin ejecutar proveedores ni publicar. Las tres plantillas recibidas están cargadas. Además existe un servicio separado para pruebas individuales explícitas: guarda la entrada, respuesta y consumo, y alimenta el historial del panel. La primera prueba se documenta al final de esta página.

## Destinos iniciales

Son dos sitios con tres variantes de idioma:

| Destino | URL inicial editable | Idioma |
|---|---|---|
| Canadians In Spain (English) | `https://canadiansinspain.com` | `en-CA` |
| Canadians In Spain (French) | `https://canadiansinspain.com` | `fr-CA` |
| Colombians in Spain (Español) | `https://colombianosespana.com` | `es-CO` |

Estas URLs son valores iniciales que se pueden confirmar o cambiar en Destinos. Guardarlas no conecta con el sitio. Cada destino tiene nombre, URL, idioma, notas y una opción para activarlo o desactivarlo.

Una base nueva empieza con un prompt vacío en borrador por variante y una configuración asociada. En la base de trabajo ya se cargaron las tres plantillas suministradas el 4 de noviembre de 2026, conservando las revisiones vacías anteriores. El proveedor inicial es OpenAI; el modelo general queda pendiente y puede elegirse al preparar. También puede elegir Gemini como dato de configuración; ninguna de las dos opciones ejecuta solicitudes.

Los archivos reproducibles están en `extras/prompts/`. Conservan el texto suministrado y sus CTAs; `{source_text}` se tradujo a `{{article_text}}`. La instrucción de rol del propio texto se reutiliza como instrucción de sistema. Se cargan en estado Borrador porque aún no se han probado. Los marcadores CTA permanecen literales: no se insertan enlaces ni se publican resultados.

La revisión posterior para WordPress añade una regla 6 en cada idioma: cuerpo semántico sin estilos, clases, identificadores, código del maquetador, imágenes ni H1. Título e imagen destacada se preparan como campos separados de WordPress. El límite colombiano de tres líneas se sustituyó por 2–3 frases, porque el número de líneas depende del dispositivo y del tema. Las tres plantillas y configuraciones de la base de trabajo están ahora en versión 3, conservando las revisiones anteriores. La validación estructural no garantiza calidad editorial.

La importación explícita se ejecuta con `.\.venv\Scripts\python scripts/load_editorial_prompts.py --db data/source_articles.db`. Crea revisiones de los prompts y vuelve a vincular las tres configuraciones iniciales con esas revisiones. No altera otros prompts, no crea preparaciones y nunca se ejecuta automáticamente al abrir el panel. Repetir los mismos valores no duplica versiones; ejecutarla después de editar esos prompts en el panel vuelve a cargar el contenido de los archivos como una revisión nueva.

## Prompts y configuraciones

Un prompt tiene nombre, descripción, instrucciones de sistema, plantilla del mensaje, estado editorial y opción de archivo. Puede guardar borradores vacíos. Los cambios de contenido o estado crean una versión nueva y conservan las anteriores; volver a guardar los mismos valores no crea otra versión.

Para marcar un prompt como Revisado debe tener instrucciones, plantilla y al menos una variable `{{article_text}}` o `{{article_json}}`. Esa validación comprueba que haya una entrada de artículo, pero no evalúa la calidad del prompt. El archivo impide utilizar el prompt en nuevas preparaciones y conserva el historial existente.

Variables admitidas tanto en las instrucciones de sistema como en la plantilla:

| Variable | Valor local |
|---|---|
| `{{article_text}}` | Texto limpio del artículo |
| `{{article_json}}` | Exportación estructurada del artículo, con bloques, referencias permitidas, huecos y avisos |
| `{{article_title}}` | Título del artículo fuente |
| `{{article_url}}` | URL del artículo fuente |
| `{{destination_name}}` | Nombre del destino |
| `{{destination_url}}` | URL configurada del destino |
| `{{locale}}` | Idioma y variante regional del destino |

La sustitución es literal y se hace una sola vez; el contenido del artículo no se interpreta como código ni como una segunda plantilla. Las variables desconocidas y la sintaxis de ejecución se rechazan. Se admite texto JSON normal en las instrucciones. Cada texto resultante admite hasta 5.000.000 de caracteres; su tamaño se comprueba antes de construirlo. La exportación usada para la vista previa conserva los avisos y huecos del limpiador, y excluye las firmas y los fragmentos descartados que solo pertenecen a la auditoría.

Una configuración tiene nombre, destino, **versión concreta de prompt**, proveedor, modelo, notas y estado activo. Sus cambios crean una revisión inmutable. Cuando aparece una revisión nueva del prompt, la configuración avisa pero sigue utilizando la versión guardada. Para actualizarla hay que seleccionar expresamente la revisión nueva y guardar la configuración.

Los destinos se pueden editar directamente. Cada preparación conserva una copia de los datos del destino tal como estaban al guardarla, por lo que cambiar un nombre, idioma o URL no reescribe preparaciones anteriores.

## Flujo en el panel

1. Abra «Preparación de adaptaciones» y revise Destinos, Prompts y Configuraciones.
2. En Revisión de datos, seleccione artículos, una página o todos los resultados filtrados y pulse «Adaptar seleccionados…». Para seleccionar la base completa, limpie los filtros primero.
3. Elija una o varias configuraciones activas y el modelo GPT del lote, o conserve los modelos de cada configuración. También deben estar activos sus destinos y sin archivar sus prompts.
4. Pulse «Previsualizar preparación». Verá el número de combinaciones y una entrada de ejemplo para la primera combinación, con las instrucciones y plantilla resueltas localmente. No es un artículo generado y no guarda un lote.
5. Pulse «Guardar preparación» para conservar el lote. Puede hacerlo con borradores o sin modelo: los avisos dejan constancia de lo pendiente.
6. Revise Preparaciones guardadas o la vista Adaptaciones de cada artículo. Cancelar conserva el registro histórico.

El límite es de **500 combinaciones por lote**. Por ejemplo, 100 artículos con 3 configuraciones producen 300 combinaciones. La creación es atómica: si alguna selección es inválida, no se guarda un lote parcial.

La selección de todos los resultados usa los mismos filtros que la lista, incluye todas las páginas y añade a la selección existente, con un máximo de 5000 resultados por acción. El límite del lote se comprueba después: seleccionar muchos artículos no los genera ni crea preparaciones automáticamente.

El modelo común del lote se guarda como opción de generación separada de la configuración. Solo admite identificadores GPT y configuraciones OpenAI; no cambia los valores generales. La lista de modelos es una referencia local, no una consulta a la cuenta. Se puede escribir otro identificador manualmente, cuya disponibilidad deberá comprobarse al habilitar la ejecución.

Referencia de OpenAI Docs consultada el 4 de noviembre de 2026: [precios oficiales](https://developers.openai.com/api/docs/pricing), [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) y [GPT-5 nano](https://developers.openai.com/api/docs/models/gpt-5-nano). Luna figura como opción económica actual; GPT-5 nano tiene una tarifa por token inferior y está marcado como deprecated, por lo que la interfaz indica «en retirada». La tarifa por token no determina por sí sola el coste final ni garantiza acceso a un modelo. No se fija un modelo definitivo para las pruebas futuras.

Al guardar se fijan las versiones vigentes en ese momento. La vista previa no bloquea los datos: si otra operación cambia un artículo o una configuración antes de guardar, la preparación guarda las versiones vigentes al guardar. Las versiones del lote son la referencia para su posterior revisión.

## Historial, deduplicación y cancelación

Cada preparación conserva los identificadores de las versiones del artículo, prompt y configuración; las fechas del origen; las copias de los metadatos editoriales y del destino; los avisos y la fecha de preparación. El texto fuente sigue referenciado a su versión histórica de extracción. No se guarda ningún resultado generado.

Repetir una combinación con la misma versión fuente, configuración, datos del destino y modelo previsto reutiliza la preparación activa. Cambiar únicamente el modelo crea otra preparación. Los lotes distinguen cuántas combinaciones se crearon y cuántas se reutilizaron. La deduplicación se protege en una transacción SQLite para evitar duplicados por dos clics simultáneos.

Una preparación puede pertenecer a varios lotes. Cancelar un lote cancela únicamente sus preparaciones que ya no estén compartidas por otros lotes activos. El resto sigue disponible para esos otros lotes. Después de cancelar todos los lotes que utilizan una preparación, la misma combinación puede prepararse de nuevo y conserva el registro anterior cancelado.

El historial avisa si el artículo, la configuración o el destino cambiaron desde la preparación, y si hay una revisión más reciente del prompt. Un cambio en el origen o la configuración, o en los datos del destino, permite crear una preparación distinta. Un prompt editado por sí solo no cambia una configuración que todavía usa la revisión anterior.

**Preparación y adaptación real son estados distintos.** Hoy los contadores de adaptaciones generadas permanecen en cero y el mensaje es «Todavía no hubo adaptaciones.». Las preparaciones pueden tener estado `prepared` o `cancelled`. No existe una transición a completado ni una salida ficticia que sugiera que el artículo ya fue adaptado.

## Credenciales y ejecución deshabilitada

La configuración privada reside en `.env`, excluido de Git. El archivo [.env.example](../.env.example) contiene únicamente los nombres `OPENAI_API_KEY` y `GEMINI_API_KEY`, con valores vacíos. `extras/EntreTramites.txt` también está excluido de Git. Las credenciales no se almacenan en las tablas de prompts, configuraciones o preparaciones ni se devuelven al navegador.

El panel solo informa si hay una credencial configurada para cada proveedor. No comprueba que sea válida, no consulta modelos, no solicita saldo y no realiza ninguna llamada de prueba. La generación se rechaza en el servidor con independencia de las claves o variables de entorno, además de mostrar los controles deshabilitados en la interfaz.

Las preparaciones no son trabajos programados para ejecutarse automáticamente más adelante. La fase de ejecución requerirá implementación y una decisión explícita sobre qué generar, con qué modelo y con qué criterios editoriales. Las operaciones de scraping existentes siguen teniendo su propio acceso al origen cuando se solicitan.

## Organización y persistencia

```text
src/generation/
  repository.py          destinos, revisiones, lotes e historial en SQLite
  preparation.py         variables, vista previa local y bloqueo de ejecución
  settings.py            comprobación local de presencia de credenciales
  model_options.py       opciones GPT locales y validación del modelo por lote
  presets.py             importación explícita de las plantillas recibidas
src/dashboard/
  generation_routes.py   API local para gestionar y preparar
  static/generation.js   interfaz editorial y preparación de seleccionados
```

Las tablas `generation_*` viven junto a los artículos en la base elegida mediante `--db`; por defecto, `data/source_articles.db`. La creación del esquema adicional y del catálogo inicial es transaccional e idempotente. No se cambia `PRAGMA user_version=1` del esquema de extracción ni se sustituyen los artículos, sus versiones o la selección del panel.

Los datos principales se separan en `generation_destinations`, `generation_prompts` y `generation_prompt_versions`, `generation_configurations` y `generation_configuration_versions`, `generation_plans`, `generation_preparations` y `generation_plan_items`. Las versiones de prompt y configuración no admiten actualizaciones ni borrados directos. Las relaciones con las versiones de extracción se protegen con claves foráneas.

`generation_preparations.generation_options_json` conserva el proveedor y modelo efectivos del lote. La actualización añade esta columna de forma idempotente a las bases existentes. Para las preparaciones antiguas, se toma el modelo de su copia histórica de configuración y se conserva la deduplicación anterior.

Las pruebas locales de esta fase están en `tests/test_generation_repository.py`, `tests/test_generation_preparation.py` y `tests/test_generation_api.py`. Comprueban, entre otros casos, versiones fijadas, rollback de inicialización, creación atómica, deduplicación concurrente, cancelación compartida, preservación del historial, sustitución literal, respuestas sin secretos y rechazo de ejecución. No necesitan credenciales ni llamadas de pago.

La primera comprobación de navegador sobre una copia de los 107 artículos verificó creación y edición de destinos, prompts y configuraciones; versiones fijadas; selección, vista previa, deduplicación, historial y cancelación; y presentación en escritorio y móvil. No registró errores de JavaScript ni solicitudes externas. La base principal conserva sus 107 artículos y 535 versiones; las pruebas se realizan en una copia y no dejan preparaciones de ensayo en ella.

Actualización del 4 de noviembre de 2026: 225 pruebas y 8 subpruebas aprobadas. El navegador comprobó selección filtrada y de los 107 artículos, preparación de 321 combinaciones con cada uno de dos modelos, reutilización de combinaciones repetidas, historial separado por modelo, las tres plantillas cargadas y presentación móvil. No hubo llamadas a proveedores. La base de trabajo quedó con prompts y configuraciones en versión 2, conservando las versiones 1 y sin lotes de prueba.

```powershell
.\.venv\Scripts\python -m pytest tests/test_generation_repository.py tests/test_generation_preparation.py tests/test_generation_api.py -q
```
