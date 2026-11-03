# Preparación local de generación

Esta fase organiza las futuras adaptaciones y permite revisar qué entrada se usaría. No genera artículos, no envía contenido a proveedores y no publica en los sitios de destino. Las plantillas finales, su calidad editorial, los modelos y las decisiones de publicación siguen pendientes de los socios.

## Destinos iniciales

Son dos sitios con tres variantes de idioma:

| Destino | URL inicial editable | Idioma |
|---|---|---|
| Canadians In Spain (English) | `https://canadiansinspain.com` | `en-CA` |
| Canadians In Spain (French) | `https://canadiansinspain.com` | `fr-CA` |
| Colombians in Spain (Español) | `https://colombianosespana.com` | `es-CO` |

Estas URLs son valores iniciales que se pueden confirmar o cambiar en Destinos. Guardarlas no conecta con el sitio. Cada destino tiene nombre, URL, idioma, notas y una opción para activarlo o desactivarlo.

El catálogo inicial incluye un prompt vacío en borrador por variante y una configuración asociada. El proveedor inicial es OpenAI y el campo de modelo está vacío. No se han inventado plantillas definitivas ni seleccionado un modelo de pago. También puede elegir Gemini como dato de configuración; ninguna de las dos opciones ejecuta solicitudes.

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
2. En Revisión de datos, seleccione los artículos y pulse «Adaptar seleccionados…».
3. Elija una o varias configuraciones activas. También deben estar activos sus destinos y sin archivar sus prompts.
4. Pulse «Previsualizar preparación». Verá el número de combinaciones y una entrada de ejemplo para la primera combinación, con las instrucciones y plantilla resueltas localmente. No es un artículo generado y no guarda un lote.
5. Pulse «Guardar preparación» para conservar el lote. Puede hacerlo con borradores o sin modelo: los avisos dejan constancia de lo pendiente.
6. Revise Preparaciones guardadas o la vista Adaptaciones de cada artículo. Cancelar conserva el registro histórico.

El límite es de **500 combinaciones por lote**. Por ejemplo, 100 artículos con 3 configuraciones producen 300 combinaciones. La creación es atómica: si alguna selección es inválida, no se guarda un lote parcial.

Al guardar se fijan las versiones vigentes en ese momento. La vista previa no bloquea los datos: si otra operación cambia un artículo o una configuración antes de guardar, la preparación guarda las versiones vigentes al guardar. Las versiones del lote son la referencia para su posterior revisión.

## Historial, deduplicación y cancelación

Cada preparación conserva los identificadores de las versiones del artículo, prompt y configuración; las fechas del origen; las copias de los metadatos editoriales y del destino; los avisos y la fecha de preparación. El texto fuente sigue referenciado a su versión histórica de extracción. No se guarda ningún resultado generado.

Repetir una combinación con la misma versión fuente, configuración y datos del destino reutiliza la preparación activa. Los lotes distinguen cuántas combinaciones se crearon y cuántas se reutilizaron. La deduplicación se protege en una transacción SQLite para evitar duplicados por dos clics simultáneos.

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
src/dashboard/
  generation_routes.py   API local para gestionar y preparar
  static/generation.js   interfaz editorial y preparación de seleccionados
```

Las tablas `generation_*` viven junto a los artículos en la base elegida mediante `--db`; por defecto, `data/source_articles.db`. La creación del esquema adicional y del catálogo inicial es transaccional e idempotente. No se cambia `PRAGMA user_version=1` del esquema de extracción ni se sustituyen los artículos, sus versiones o la selección del panel.

Los datos principales se separan en `generation_destinations`, `generation_prompts` y `generation_prompt_versions`, `generation_configurations` y `generation_configuration_versions`, `generation_plans`, `generation_preparations` y `generation_plan_items`. Las versiones de prompt y configuración no admiten actualizaciones ni borrados directos. Las relaciones con las versiones de extracción se protegen con claves foráneas.

Las pruebas locales de esta fase están en `tests/test_generation_repository.py`, `tests/test_generation_preparation.py` y `tests/test_generation_api.py`. Comprueban, entre otros casos, versiones fijadas, rollback de inicialización, creación atómica, deduplicación concurrente, cancelación compartida, preservación del historial, sustitución literal, respuestas sin secretos y rechazo de ejecución. No necesitan credenciales ni llamadas de pago.

La comprobación de navegador sobre una copia de los 107 artículos verificó creación y edición de destinos, prompts y configuraciones; versiones fijadas; selección, vista previa, deduplicación, historial y cancelación; y presentación en escritorio y móvil. No registró errores de JavaScript ni solicitudes externas. La base principal conserva sus 107 artículos y 535 versiones, con tres destinos, tres borradores vacíos y tres configuraciones; no se guardaron preparaciones de prueba en ella.

```powershell
.\.venv\Scripts\python -m pytest tests/test_generation_repository.py tests/test_generation_preparation.py tests/test_generation_api.py -q
```
