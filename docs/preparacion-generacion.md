# Preparación y generación de adaptaciones

La preparación de lotes continúa siendo local y no consume API. Desde el 6 de noviembre de 2026, el panel también permite iniciar expresamente una generación con **«Generar con API»**. Cada combinación de artículo y configuración obtiene una decisión de aplicabilidad mediante la API, con búsqueda en fuentes oficiales, y continúa a redacción solo si el modelo decide `adapt`. No requiere que una persona escoja exclusiones, corrija el artículo o elija una fotografía. El formulario de revisión manual se conserva como herramienta editorial opcional; guardar un lote o marcar un prompt como revisado no inicia una generación.

El resultado se guarda localmente por defecto. La casilla **«Crear también un borrador en WordPress con imagen seleccionada automáticamente»** activa la importación de portada y el envío como borrador. Evaluar y redactar consume API; las búsquedas de la evaluación pueden añadir coste. Consultar el catálogo de modelos y su documentación no genera contenido ni consume tokens de inferencia. No se muestran precios fijos ni se promete que una generación sea gratuita.

## Primera prueba individual: Barcelona → Colombia

El 4 de noviembre de 2026 se autorizó una única adaptación real del artículo 1, versión de origen 429, con la configuración colombiana 3 (versión 3, ID de revisión 9). Se usó la plantilla vigente sin corregirla después de ver la salida. El registro `generation_trials.id=1` conserva el texto exacto enviado, las versiones, el HTML original, los identificadores de la respuesta, el modelo y el consumo.

- Modelo solicitado y devuelto: `gpt-5-nano-2025-08-07`, modalidad devuelta `flex`, razonamiento `minimal`.
- Consumo: 3543 tokens de entrada, 2121 de salida, 0 cacheados y 0 de razonamiento; total 5664.
- Estimación: `(3543 × 0.025 + 2121 × 0.20) / 1000000 = US$0.000512775`, según las [tarifas oficiales](https://developers.openai.com/api/docs/pricing). Es una estimación del consumo informado, no una factura.
- Una llamada completada. No hubo regeneración ni cambio automático de modelo. Después de corregir la credencial de WordPress, se reutilizó este resultado para crear el borrador 1546.
- La imagen indicada por el usuario corresponde al medio 1369 en Colombia; se usa en la vista previa, separada del cuerpo.

El HTML supera el validador semántico, pero el artículo necesita revisión: contiene una sección «Público objetivo y cultura», cifras COP sin tasa ni fecha de conversión, una pregunta de Brexit sin utilidad para este público, términos como «hours» y «Catalonia», «garante municipal» sin respaldo en el original, y afirmaciones fiscales que la fuente no sustenta. La salida se conserva sin correcciones para evaluar el prompt y el modelo. Los CTA todavía necesitan destinos reales. No debe confundirse estado de generación `completed` con aprobación editorial.

`src/generation/trial.py` reserva la clave de prueba en SQLite antes de hacer el POST a Responses. Volver a usar la misma clave devuelve el registro guardado; ni siquiera un timeout o una respuesta incompleta provoca otra llamada. Tras una interrupción, un estado `running` o `unknown` necesita comprobación manual, no otra clave automática. [Flex](https://developers.openai.com/api/docs/guides/flex-processing) puede demorar; se permite hasta diez minutos de lectura y se desactivan reintentos y redirecciones. Las credenciales no se archivan.

`scripts/run_generation_trial.py` permite una prueba con artículo, configuración, modelo y clave explícitos. El modo `--export-trial 1` solo lee el resultado existente y produce cuerpo HTML, vista previa con título e imagen separados, y metadatos JSON bajo `data/adaptations/`. Usa el HTML original si no hay ediciones, o la revisión editorial vigente si se guardó una. No consume API. El panel ofrece `GET /api/generation/trials/<id>` como consulta limitada a metadatos y HTML; no expone la petición ni la respuesta completa del proveedor.

GPT-5 nano figura en retirada. La elección se hizo para esta prueba económica y no cambia los modelos generales de las configuraciones. Su disponibilidad debe revisarse de nuevo para otra prueba. [Modelo y condiciones](https://developers.openai.com/api/docs/models/gpt-5-nano).

La primera prueba, descrita arriba, es anterior al control de aplicabilidad y se conserva como hito histórico. El flujo actual del panel utiliza el mismo servicio automático que la consola y registra entrada, respuesta, consumo y decisiones por combinación. La ejecución solo empieza con la acción explícita de generar; no publica artículos automáticamente.

## Destinos iniciales

Son dos sitios con tres variantes de idioma:

| Destino | URL inicial editable | Idioma |
|---|---|---|
| Canadians In Spain (English) | `https://canadiansinspain.com` | `en-CA` |
| Canadians In Spain (French) | `https://canadiansinspain.com` | `fr-CA` |
| Colombians in Spain (Español) | `https://colombianosespana.com` | `es-CO` |

Estas URLs son valores iniciales que se pueden confirmar o cambiar en Destinos. Guardarlas no conecta con el sitio. Cada destino tiene nombre, URL, idioma, notas y una opción para activarlo o desactivarlo.

En **Preparación de adaptaciones → Destinos**, cada destino tiene un editor de CTA con los marcadores de su idioma, URL y texto del enlace. Los campos pueden quedar vacíos mientras no existan las páginas de servicios. Los ajustes pertenecen al destino y se comparten con sus configuraciones y con WordPress; no es necesario duplicarlos dentro de cada prompt. Guardarlos no genera texto ni modifica entradas remotas. Python sustituye los marcadores conocidos por enlaces validados antes del envío. Una adaptación local puede conservar marcadores pendientes; un borrador de prueba puede conservarlos con el aviso correspondiente, pero no se inventan URLs.

Las nuevas claves de Canadá inglés y francés se importaron de `extras/Sitios.txt` a `.env` y se comprobaron mediante lectura autenticada. Comparten el sitio y la contraseña de aplicación. La sección de guías y la integración de idioma francesa siguen pendientes: una clave válida no resuelve esa configuración y el envío francés se bloquea mientras falte.

Una base nueva empieza con un prompt vacío en borrador por variante y una configuración asociada. En la base de trabajo ya se cargaron las tres plantillas suministradas el 4 de noviembre de 2026, conservando las revisiones vacías anteriores. El proveedor inicial es OpenAI; el modelo general queda pendiente y puede elegirse al preparar. También puede elegir Gemini como dato de configuración; ninguna de las dos opciones ejecuta solicitudes.

Los archivos reproducibles están en `extras/prompts/`. Las plantillas actuales refuerzan las instrucciones suministradas y conservan sus marcadores CTA; `{source_text}` se tradujo a `{{article_text}}`. La instrucción de rol del propio texto se reutiliza como instrucción de sistema. Se cargan en estado Borrador porque la revisión vigente aún necesita evaluación con salidas reales. Los marcadores CTA permanecen literales: no se insertan enlaces ni se publican resultados.

La revisión 3 incorporó el cuerpo semántico sin estilos, clases, identificadores, código del maquetador, imágenes ni H1. Título e imagen destacada se preparan como campos separados de WordPress. El límite colombiano de tres líneas se sustituyó por 2–3 frases, porque el número de líneas depende del dispositivo y del tema. La revisión 4, cargada el 5 de noviembre en las tres plantillas y configuraciones iniciales, incorpora las reglas siguientes sin regenerar la prueba histórica. La validación estructural no garantiza calidad editorial.

### Reglas reforzadas de la revisión 4

- Redactar solo el alcance aprobado, conservando secciones útiles, condiciones y excepciones. Una mención estadounidense no obliga a descartar toda una guía; un beneficio exclusivamente ajeno al público necesita la decisión previa correspondiente.
- No inventar equivalencias entre IRS, FBI, pensiones, visados o trámites y organismos de otro país. Una sustitución jurídica, fiscal o administrativa necesita evidencia oficial suministrada para ese público.
- Conservar los importes aplicables en EUR, con rangos, periodicidad y alcance originales. Añadir CAD para Canadá o COP para Colombia solo con conversiones calculadas, fuente y fecha suministradas. Sin esos datos, conservar EUR; no hay un servicio automático de cotizaciones en esta fase.
- Mantener idioma y tratamiento coherentes. Apostillas, traducciones y consulados se mencionan cuando correspondan al trámite y al lector, sin añadir requisitos por costumbre.
- Retirar autoría y credenciales sin perder advertencias sustantivas ni atribuir revisiones profesionales al sitio de destino. No rellenar exclusiones con citas o hechos inventados.
- Entregar únicamente el cuerpo HTML semántico, sin encabezados del prompt, imágenes ni decisiones editoriales. Colocar cada CTA solo en una sección pertinente, sin forzar ambos marcadores en cualquier artículo.

Estos cambios corrigen instrucciones que favorecían conversiones sin respaldo, equivalencias automáticas y añadidos irrelevantes. Son reglas editoriales para la próxima prueba; todavía no prueban que un modelo las cumpla.

La actualización inicial a revisión 4 y las primeras pruebas de imágenes del 5 de noviembre no hicieron llamadas a OpenAI ni añadieron adaptaciones: en ese hito se conservó la única prueba de generación histórica. Las pruebas posteriores con ejecución explícita son operaciones distintas. Importar una fotografía a WordPress no regenera su artículo.

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
3. Elija una o varias configuraciones activas. Para preparar, puede conservar los modelos de cada configuración; para generar, seleccione un modelo de la cuenta y su esfuerzo. También deben estar activos sus destinos y sin archivar sus prompts.
4. Pulse «Previsualizar preparación». Verá el número de combinaciones y una entrada de ejemplo para la primera combinación, con las instrucciones y plantilla resueltas localmente. No es un artículo generado y no guarda un lote.
5. Pulse «Guardar preparación» para conservar el lote. Puede hacerlo con borradores o sin modelo: los avisos dejan constancia de lo pendiente.
6. Si desea ejecutar la prueba, pulse **«Generar con API»**. Esta acción consume API y utiliza la selección actual; no ejecuta silenciosamente las preparaciones guardadas. La opción de WordPress está desmarcada inicialmente y el resultado queda local.
7. Siga el progreso en el diálogo o en **Generaciones desde el panel**. Cada combinación informa si se adaptó, se omitió o necesita verificación. Los artículos generados aparecen en Revisión de datos → Adaptaciones.
8. Puede editar y guardar el HTML local o borrar la adaptación. Guardar una edición no vuelve a generar ni despliega el resultado. Las preparaciones guardadas se pueden consultar y cancelar por separado.

### Modelos disponibles y esfuerzos

`GET /api/generation/models` consulta `GET /v1/models` con la clave del servidor y devuelve los candidatos GPT de texto disponibles para esa cuenta. No realiza una petición de generación. La lista se guarda una hora en caché, separada por huella de credencial; **«Actualizar modelos · sin generar»** solicita una lista nueva. Si falla la consulta y existe una lista anterior, el panel la identifica como desactualizada. La clave nunca se devuelve al navegador ni se guarda en la caché.

La [Models API oficial](https://developers.openai.com/api/reference/resources/models/methods/list) informa identificadores y disponibilidad, pero no enumera los esfuerzos. `GET /api/generation/models/<id>` consulta la versión Markdown de la ficha oficial de ese modelo para leer esfuerzos, valor predeterminado y compatibilidad con texto, Responses, salida estructurada y búsqueda web. No hace sondeos de generación. Una ficha de familia solo sirve para un snapshot si enumera su identificador exacto.

La documentación confirmada se guarda 24 horas; una consulta sin datos suficientes se conserva cinco minutos para evitar solicitudes repetidas. Si no se pueden determinar los esfuerzos, se ofrece únicamente **«Predeterminado del modelo»**, que omite `reasoning` en la solicitud. No se deduce el esfuerzo por el nombre del modelo ni se sustituye el modelo elegido. Antes de iniciar la ejecución, el servidor rechaza opciones de esfuerzo no documentadas y capacidades conocidas incompatibles; la información desconocida permanece indicada como tal. Véanse la [guía oficial de razonamiento](https://developers.openai.com/api/docs/guides/reasoning) y, como ejemplo de ficha, [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).

El catálogo es dinámico y no contiene una lista fija de modelos o precios. La disponibilidad de una cuenta y las características documentadas pueden cambiar. La consulta gratuita de metadatos no implica que la evaluación, la redacción o sus herramientas sean gratuitas.

### Ejecución explícita y recuperación

El botón utiliza `POST /api/generation/runs`; las consultas de progreso usan `GET /api/generation/runs` y `GET /api/generation/runs/<id>`. `src/dashboard/generation_jobs.py` valida toda la selección y registra `panel_generation_runs` antes de iniciar el trabajo. Guarda las opciones, una clave de solicitud, las huellas del contexto y el estado de cada combinación. Admite como máximo 500 combinaciones y un lote activo a la vez; las procesa secuencialmente. El panel utiliza el nivel de servicio `default`, sin cambiar automáticamente a otro modelo o modalidad.

Cada combinación invoca `run_automatic_trial` con claves estables para su evaluación y redacción. Repetir la misma solicitud recupera el lote registrado; cambiar su contenido conservando la clave se rechaza. Si el artículo, prompt, configuración o contexto cambia antes de ejecutarse, esa combinación se detiene. Un resultado incierto detiene las combinaciones restantes y requiere comprobar los registros; no se reenvía automáticamente una llamada ni se reanuda un trabajo por abrir el panel de nuevo. Las reservas y las respuestas guardadas permiten revisar lo sucedido sin generar otra vez.

La ruta anterior `POST /api/generation/execute` sigue respondiendo con ejecución deshabilitada. Esto no afecta al nuevo punto de entrada explícito. Previsualizar, guardar o cancelar preparaciones sigue siendo local y no constituye una programación de trabajos futuros.

### Contrato HTML y recuperación de formato

La prueba 4 del artículo 60, **Retire in Spain**, produjo HTML completo pero dejó dos marcadores CTA entre bloques, fuera de `<p>`. El validador rechazaba ese texto suelto, aunque no era necesario cambiar el artículo. Este caso se resuelve con dos medidas comunes a todos los destinos y modelos:

1. `output_contract.py` añade a cada solicitud de redacción un contrato de serialización versionado, separado de los prompts editoriales editables. Exige que los CTA independientes se devuelvan como `<p>[CTA_…]</p>`, especifica la estructura de listas y tablas y limita etiquetas y atributos a los admitidos por WordPress. La solicitud y `snapshot.output_contract_version` conservan qué contrato se utilizó. No modifica las versiones de prompt o configuración ni invalida evaluaciones antiguas.
2. `html_normalization.py` corrige localmente los casos de formato inequívocos: texto o CTA suelto y elementos en línea en la raíz se agrupan en párrafos; se eliminan envoltorios simples sin atributos y cercas completas de código HTML; `<b>`/`<i>` se convierten en sus equivalentes semánticos. Solo acepta el resultado si conserva el texto, los destinos de enlaces, la secuencia de CTA y las relaciones semánticas comprobadas, y si supera el mismo validador estricto. Una entrada ya válida se devuelve sin cambiar sus bytes.

El normalizador no descarta scripts, imágenes, comentarios o enlaces inseguros para hacer pasar la salida; esos casos se mantienen bloqueados. Tampoco adivina una estructura ambigua ni corrige hechos. El contrato reduce fallos de salida, pero las instrucciones de lenguaje natural no garantizan cualquier respuesta posible de una API: la validación sigue siendo necesaria. Esto coincide con la [guía oficial de prompting](https://developers.openai.com/api/docs/guides/prompt-engineering), que describe la generación como no determinista.

`formatting.ensure_trial_format` guarda las transformaciones aprobadas en `generation_html_formats`, con política, hashes y operaciones realizadas. `generation_trials.html`, petición, respuesta y consumo originales permanecen intactos. La lectura efectiva usa la versión normalizada (`html_source=normalized`); una edición manual guardada después sigue teniendo prioridad. La normalización no consume API y borrar la adaptación también elimina su copia normalizada.

Para lotes antiguos detenidos por formato, **Continuar con contenido guardado** llama a `POST /api/generation/runs/<id>/resume`. Solo son elegibles las combinaciones con evaluación y redacción completas correspondientes a sus claves y contexto originales. La ejecución utiliza `reuse_only=True`: lee los registros guardados directamente, sin invocar los clientes de evaluación ni de redacción. Mantiene el modelo, esfuerzo y opción de borrador originales; no repite combinaciones ya completadas. Conserva la causa anterior en `recovery_history` y vuelve a validar antes de continuar a portada/exportación/despliegue. Una operación incierta o un resultado eliminado no se transforma en una nueva generación.

Recuperación real del 6 de noviembre de 2026: el lote 1 terminó usando la evaluación 2 y la adaptación 4 existentes, sin nuevas llamadas a OpenAI. Se envolvieron únicamente los dos CTA sueltos; el texto visible y el HTML original guardado permanecieron intactos. La selección automática de Openverse produjo el medio 1664 y se creó el [borrador colombiano 1665](https://colombianosespana.com/wp-admin/post.php?post=1665&action=edit). Los resultados anteriores válidos de las adaptaciones 1 y 3 permanecen idénticos byte por byte al pasar por el normalizador.

### Aplicabilidad antes de redactar

En **Aplicabilidad antes de redactar**, puede consultar la evaluación de la API o guardar una revisión editorial manual adicional. Esta última es opcional: no tiene que rellenar el formulario para usar «Generar con API». Para una revisión manual, seleccione artículo y configuración y guarde la pregunta central, el motivo, el revisor, los apartados a excluir y las fuentes consultadas, cada una con URL, conclusión y fecha. Las comprobaciones permiten distinguir información confirmada de desconocida.

| Decisión | Criterio | ¿Autoriza una nueva prueba? |
|---|---|---|
| `adapt` · Adaptar | El núcleo es útil y lo esencial está comprobado; las exclusiones concretas se trasladan a redacción. Si el núcleo exige una alternativa local, debe estar respaldada por evidencia. | Sí, mientras la revisión siga vigente y el destino esté habilitado |
| `omit` · Omitir | Todo el artículo depende de una exclusividad ajena al público y se confirmó, con evidencia, que no hay alternativa aplicable. | No |
| `needs_verification` · Pendiente | Falta información esencial para decidir o confirmar una alternativa. | No |

Una guía general de alquiler puede adaptarse omitiendo un apartado exclusivo estadounidense. Una guía dedicada por completo a un beneficio exclusivo exige resolver primero si existe una alternativa. El sistema no descarta por palabras clave. En el formulario manual, las fuentes son declaraciones del revisor; en el flujo automático, la API debe consultar fuentes oficiales y devolver las URLs obtenidas por sus herramientas. El código comprueba esa procedencia, pero no puede garantizar que la interpretación del modelo sea correcta.

Cada guardado incorpora una fila inmutable en `generation_applicability_reviews`. La última decisión reemplaza a la anterior para autorizar nuevas pruebas, conservando el historial. Cambiar la versión fuente, la revisión de configuración/prompt fijada o los datos editables del destino invalida la huella y muestra la revisión como desactualizada. Guardar una revisión desde un formulario con contexto antiguo también se rechaza.

`run_trial` comprueba la revisión antes de leer credenciales o reservar una llamada y vuelve a comprobarla al reservar. Sus exclusiones y evidencia se incluyen en las instrucciones, fuera del HTML, y en `snapshot.applicability_review`. No hay una opción para saltarse esta comprobación. Una prueba antigua con la misma clave se devuelve desde la base sin generar otra vez; una clave eliminada permanece bloqueada.

`build_assessment_prompt` y `parse_assessment_candidate`, en `src/generation/applicability.py`, siguen siendo auxiliares locales sin llamadas. La ejecución automática está en `src/generation/assessment.py`: guarda la respuesta del proveedor en `generation_assessments` y crea una revisión con `source=model`, `assessment_id` y revisor `OpenAI: <modelo>`. La decisión guardada no puede sustituirse por otra escrita por quien invoque el servicio. La preparación de lotes sigue disponible sin revisión, pero su conservación no autoriza redacción.

### Prueba automática individual

Prueba real del 5 de noviembre de 2026: el artículo DNV 2 se evaluó mediante `generation_assessments.id=1` y se redactó en `generation_trials.id=3`, con `gpt-6-luna`, `reasoning.effort=high` y Flex. La decisión fue `adapt`, con título y exclusiones propuestos por la API. El HTML se envió sin edición manual al borrador colombiano 1599, con portada elegida automáticamente. El ensayo asistido anterior quedó como prueba 2, sin desplegar. Consulte [la verificación de WordPress](preparacion-wordpress.md#prueba-automática-dnv--colombia-5-de-noviembre-de-2026).

`scripts/run_automatic_trial.py` ejecuta una secuencia identificada por una clave estable:

1. La API recibe el artículo extraído, el público y la plantilla vigente. No recibe las exclusiones ni las fuentes de una revisión manual anterior. Consulta fuentes oficiales mediante `web_search` y devuelve decisión, motivo, exclusiones/correcciones respaldadas y título propuesto.
2. `omit` y `needs_verification` detienen el flujo antes de redactar o importar una portada. Un JSON inválido, una respuesta incompleta o un resultado incierto también detienen la ejecución.
3. Una decisión `adapt` válida se registra como revisión del modelo, fijada al contexto exacto. La segunda llamada redacta el HTML con esas instrucciones y la plantilla. El modelo y el razonamiento solicitados se conservan en ambas fases.
4. El normalizador resuelve el formato recuperable y el validador comprueba la estructura final. El texto editorial no se reescribe; la salida original queda guardada. Si no puede normalizarse de forma segura, se detiene esa combinación con el diagnóstico concreto.
5. Si se activa la portada, el servicio escoge e importa una fotografía CC0 automáticamente. El título de la API, el cuerpo y la portada se exportan como artefactos separados. Con `--send-draft`, el resultado se envía a WordPress como borrador; no se publica. La consola mantiene la importación de portada por defecto; el servicio acepta `import_cover=False` para conservar únicamente la adaptación local. El panel usa ese modo sin importar imágenes salvo que se marque la casilla de borrador.

La evaluación y la redacción son dos llamadas distintas, cada una con reserva durable y consumo guardado. Las búsquedas del proveedor pueden tener coste adicional. El servicio limita las llamadas a herramientas y no reintenta automáticamente respuestas incompletas, fallidas o inciertas. Repetir la misma clave recupera sus resultados; no crea otra evaluación ni otra redacción. Cambiar las versiones mientras se ejecuta impide usar la aprobación anterior, y una decisión editorial posterior no se revoca al recuperar una evaluación antigua.

El modo automático exige una revisión `source=model` vinculada a su propia evaluación: una aprobación manual previa no sustituye ese paso. El estado `completed` acredita una respuesta válida registrada, no una revisión profesional ni una garantía de exactitud factual. La evidencia y las llamadas de búsqueda permiten auditar lo que la API decidió.

Opciones principales: `--article-id`, `--configuration-id`, `--target-id`, `--model`, `--reasoning-effort`, `--service-tier` y `--run-key`. `--send-draft` activa el envío; `--category-id` puede repetirse. `--allow-pending-ctas` permite conservar los marcadores del idioma sin resolver únicamente en el borrador de prueba. El panel conserva los controles de preparación local y solo ejecuta los artículos seleccionados cuando se pulsa «Generar con API».

El límite es de **500 combinaciones por lote**. Por ejemplo, 100 artículos con 3 configuraciones producen 300 combinaciones. La creación es atómica: si alguna selección es inválida, no se guarda un lote parcial.

La selección de todos los resultados usa los mismos filtros que la lista, incluye todas las páginas y añade a la selección existente, con un máximo de 5000 resultados por acción. El límite del lote se comprueba después: seleccionar muchos artículos no los genera ni crea preparaciones automáticamente.

El modelo común del lote se guarda como opción de generación separada de la configuración. Solo admite identificadores GPT y configuraciones OpenAI; no cambia los valores generales. Guardar una preparación no exige comprobar el acceso al modelo; iniciar una generación sí requiere que aparezca en el catálogo consultado para la cuenta.

Referencia histórica de OpenAI Docs consultada para la prueba del 4 de noviembre de 2026: [precios oficiales](https://developers.openai.com/api/docs/pricing), [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) y [GPT-5 nano](https://developers.openai.com/api/docs/models/gpt-5-nano). Esa comparación explica la elección de la primera prueba; no se usa como catálogo actual ni como presupuesto de futuras ejecuciones. La tarifa por token no determina por sí sola el coste final ni garantiza acceso a un modelo.

Al guardar se fijan las versiones vigentes en ese momento. La vista previa no bloquea los datos: si otra operación cambia un artículo o una configuración antes de guardar, la preparación guarda las versiones vigentes al guardar. Las versiones del lote son la referencia para su posterior revisión.

## Historial, deduplicación y cancelación

Cada preparación conserva los identificadores de las versiones del artículo, prompt y configuración; las fechas del origen; las copias de los metadatos editoriales y del destino; los avisos y la fecha de preparación. El texto fuente sigue referenciado a su versión histórica de extracción. No se guarda ningún resultado generado.

Repetir una combinación con la misma versión fuente, configuración, datos del destino y modelo previsto reutiliza la preparación activa. Cambiar únicamente el modelo crea otra preparación. Los lotes distinguen cuántas combinaciones se crearon y cuántas se reutilizaron. La deduplicación se protege en una transacción SQLite para evitar duplicados por dos clics simultáneos.

Una preparación puede pertenecer a varios lotes. Cancelar un lote cancela únicamente sus preparaciones que ya no estén compartidas por otros lotes activos. El resto sigue disponible para esos otros lotes. Después de cancelar todos los lotes que utilizan una preparación, la misma combinación puede prepararse de nuevo y conserva el registro anterior cancelado.

El historial avisa si el artículo, la configuración o el destino cambiaron desde la preparación, y si hay una revisión más reciente del prompt. Un cambio en el origen o la configuración, o en los datos del destino, permite crear una preparación distinta. Un prompt editado por sí solo no cambia una configuración que todavía usa la revisión anterior.

**Preparación y adaptación real son estados distintos.** Las preparaciones tienen estado `prepared` o `cancelled`; no pasan a completadas como si ya existiera texto. El historial muestra los resultados activos y las preparaciones. Las adaptaciones eliminadas desaparecen de las tarjetas, los contadores y el resumen, incluso si se borraron antes de esta actualización. El mensaje «Todavía no hubo adaptaciones» corresponde a artículos sin resultados guardados activos.

**Editar texto → Guardar texto** conserva una versión local en `adaptation_edits`, después de validar HTML semántico y marcadores del idioma. La respuesta original en `generation_trials` se mantiene como evidencia del modelo; `src/generation/content_edits.py` resuelve la versión vigente para lectura, exportación y reenvío. El guardado comprueba la revisión abierta para impedir que una pestaña sobrescriba cambios de otra. No llama a OpenAI ni modifica WordPress.

El borrado local elimina el HTML y las revisiones editadas y oculta por completo la adaptación en el historial. Conserva los identificadores técnicos, consumo y claves de deduplicación necesarios para impedir que una solicitud antigua vuelva a generar o desplegar. Esos restos técnicos no se presentan como una adaptación visible. WordPress, el original extraído y los archivos exportados se gestionan aparte.

## Credenciales y acciones que consumen API

La configuración privada reside en `.env`, excluido de Git. El archivo [.env.example](../.env.example) contiene únicamente los nombres `OPENAI_API_KEY` y `GEMINI_API_KEY`, con valores vacíos. `extras/EntreTramites.txt` también está excluido de Git. Las credenciales no se almacenan en las tablas de prompts, configuraciones o preparaciones ni se devuelven al navegador.

El estado de credenciales solo informa si existe una clave, sin exponerla. La consulta de modelos comprueba acceso a ese recurso mediante lectura; no solicita saldo ni hace una llamada de generación para probar capacidades. «Generar con API» requiere clave, modelo disponible, selección válida y compatibilidad de opciones. Su evaluación y redacción sí consumen API. Las claves o variables de entorno por sí solas no ejecutan trabajos.

Las preparaciones no son trabajos programados para ejecutarse automáticamente más adelante. La ejecución requiere el botón del panel o una invocación explícita del script y sus parámetros. Las operaciones de scraping existentes siguen teniendo su propio acceso al origen cuando se solicitan.

## Organización y persistencia

```text
src/generation/
  repository.py          destinos, revisiones, lotes e historial en SQLite
  preparation.py         variables, vista previa local y bloqueo de ejecución
  settings.py            comprobación local de presencia de credenciales
  model_options.py       validación local del identificador y opciones del lote
  model_catalog.py       modelos de la cuenta y capacidades oficiales, consultas GET y caché
  presets.py             importación explícita de las plantillas recibidas
  applicability.py       decisiones manuales o del modelo, vigencia e historial inmutable
  assessment.py          evaluación de la API con búsquedas oficiales y registro de consumo
  trial.py               prueba individual explícita, sujeta a aplicabilidad
  content_edits.py       contenido vigente, versión y procedencia modelo/editor
scripts/
  run_automatic_trial.py  evaluación → redacción → portada automática → borrador opcional
src/dashboard/
  generation_routes.py   API local para gestionar y preparar
  generation_jobs.py     lotes explícitos, contexto fijado, progreso y reservas durables
  execution_routes.py    inicio y consulta de /api/generation/runs
  model_routes.py        catálogo y capacidades sin peticiones de generación
  adaptation_routes.py   guardado local de ediciones
  applicability_routes.py consulta y guardado local de revisiones previas
  static/generation.js   interfaz editorial y preparación de seleccionados
  static/execution.js    selector dinámico, esfuerzo e inicio/progreso de ejecuciones
```

Las tablas `generation_*` viven junto a los artículos en la base elegida mediante `--db`; por defecto, `data/source_articles.db`. La creación del esquema adicional y del catálogo inicial es transaccional e idempotente. No se cambia `PRAGMA user_version=1` del esquema de extracción ni se sustituyen los artículos, sus versiones o la selección del panel.

Los datos principales se separan en `generation_destinations`, `generation_prompts` y `generation_prompt_versions`, `generation_configurations` y `generation_configuration_versions`, `generation_plans`, `generation_preparations` y `generation_plan_items`. Las versiones de prompt y configuración no admiten actualizaciones ni borrados directos. Las relaciones con las versiones de extracción se protegen con claves foráneas.

`generation_preparations.generation_options_json` conserva el proveedor y modelo efectivos del lote. La actualización añade esta columna de forma idempotente a las bases existentes. Para las preparaciones antiguas, se toma el modelo de su copia histórica de configuración y se conserva la deduplicación anterior.

Las pruebas locales iniciales están en `tests/test_generation_repository.py`, `tests/test_generation_preparation.py` y `tests/test_generation_api.py`. Comprueban, entre otros casos, versiones fijadas, rollback de inicialización, creación atómica, deduplicación concurrente, cancelación compartida, preservación del historial, sustitución literal, respuestas sin secretos y rechazo de la ruta de ejecución antigua. Las nuevas pruebas de catálogo, esfuerzo, ejecuciones del panel y ediciones usan transportes simulados: no necesitan credenciales reales ni llamadas de pago.

La primera comprobación de navegador sobre una copia de los 107 artículos verificó creación y edición de destinos, prompts y configuraciones; versiones fijadas; selección, vista previa, deduplicación, historial y cancelación; y presentación en escritorio y móvil. No registró errores de JavaScript ni solicitudes externas. La base principal conserva sus 107 artículos y 535 versiones; las pruebas se realizan en una copia y no dejan preparaciones de ensayo en ella.

Actualización del 4 de noviembre de 2026: 225 pruebas y 8 subpruebas aprobadas. El navegador comprobó selección filtrada y de los 107 artículos, preparación de 321 combinaciones con cada uno de dos modelos, reutilización de combinaciones repetidas, historial separado por modelo, las tres plantillas cargadas y presentación móvil. No hubo llamadas a proveedores. La base de trabajo quedó con prompts y configuraciones en versión 2, conservando las versiones 1 y sin lotes de prueba.

```powershell
.\.venv\Scripts\python -m pytest tests/test_generation_repository.py tests/test_generation_preparation.py tests/test_generation_api.py -q
```
