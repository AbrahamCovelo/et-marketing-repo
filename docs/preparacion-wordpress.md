# Preparación local de WordPress

`src/deployment` y la sección **WordPress** del panel permiten revisar conexiones, destinos, enlaces CTA y el contenido previsto. El formulario general de WordPress prepara una vista previa local. La generación se inicia por separado desde **Adaptar seleccionados**, con la opción explícita de enviar el resultado como borrador y seleccionar su portada automáticamente. Los primeros envíos, reenvíos e importaciones conservan sus propios registros. Guardar configuraciones, CTA o ediciones locales no genera contenido ni modifica entradas remotas.

## Conexiones y rutas de destino

Las credenciales entregadas en `extras/Sitios.txt` se importan a la configuración privada `.env`. Ambos archivos deben quedar excluidos de Git. El panel muestra únicamente si la configuración está presente: no devuelve usuarios, contraseñas, fragmentos de credenciales ni su contenido. Que una conexión figure como configurada no significa que se haya autenticado o que tenga permisos de escritura.

La conexión utiliza un usuario de WordPress y su **contraseña de aplicación**, mediante autenticación Basic sobre HTTPS. Una contraseña de aplicación no se envía como token Bearer. Guardar ajustes locales no verifica la conexión; las operaciones remotas explícitas comprueban lo necesario para su acción. [Autenticación oficial de la REST API](https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/).

El **6 de noviembre de 2026** se importaron las credenciales actualizadas de Canadá para las conexiones inglesa y francesa y se verificó su autenticación mediante consultas de lectura. El importador admite que un bloque de `Sitios.txt` incluya ambas URLs del mismo WordPress y una contraseña de aplicación compartida. La opción `connection_keys` permite actualizar únicamente esas conexiones, conservando Colombia y el resto de `.env`. La validación del archivo se completa antes de sustituir `.env` de forma atómica. La contraseña nunca se copia a SQLite ni se devuelve al navegador.

| Destino | Base de WordPress | Ruta provisional del catálogo |
|---|---|---|
| Canadians In Spain · Inglés | `https://canadiansinspain.com` | `https://canadiansinspain.com/guides-for-canadians-in-spain/` |
| Canadians In Spain · Francés | `https://canadiansinspain.com` | Pendiente |
| Colombians in Spain · Español | `https://colombianosespana.com` | `https://colombianosespana.com/guias-colombianos-en-espana` |

La ruta del catálogo se guarda separada de la base y del endpoint REST. Por ejemplo, el endpoint previsto para entradas es `https://canadiansinspain.com/wp-json/wp/v2/posts`. La página de guías es el lugar donde se muestra un listado; su URL no es un endpoint de publicación ni un `parent` que deba asignarse a cada entrada.

Los campos de título, cuerpo, extracto, categorías e imagen destacada pertenecen a la entrada. La categoría o consulta que utiliza el listado debe comprobarse en WordPress antes de decidir qué IDs asignar. Guardar un ID local no crea una categoría ni verifica que exista. [Referencia de entradas y sus campos](https://developer.wordpress.org/rest-api/reference/posts/).

Para francés quedan pendientes tanto la ruta de guías como la integración del idioma en WordPress. `fr-CA` identifica nuestro destino editorial; no configura por sí mismo un plugin multilingüe. Tener credenciales válidas tampoco resuelve esa integración. El panel permite preparar el contenido para ese público, pero mantiene bloqueado su envío a WordPress mientras siga pendiente y evita tratarlo como una entrada del idioma predeterminado.

## Imagen destacada: tarjeta y artículo

La imagen de portada se representa mediante `featured_media`: el ID de una imagen que existe en la biblioteca del WordPress de destino. La misma imagen destacada del artículo puede usarse para su tarjeta en el listado y para su cabecera al abrirlo. No hace falta incrustar otra copia de esa portada en el HTML del cuerpo. Los IDs son propios de cada instalación de WordPress y no deben copiarse entre sitios como si fueran equivalentes. [Campos de medios](https://developer.wordpress.org/rest-api/reference/media/).

**WordPress core no crea la fotografía original ni elige una imagen adecuada automáticamente.** Puede recibir un archivo existente, gestionar el adjunto y generar miniaturas y tamaños derivados. Si el archivo ya está en la biblioteca, se podrá reutilizar; si no, será necesario aportar una imagen existente antes de publicar. No se incorpora generación de imágenes con OpenAI ni con otro proveedor. [Creación de metadatos y tamaños derivados](https://developer.wordpress.org/reference/functions/wp_generate_attachment_metadata/).

El tema o la plantilla decide dónde aparece la imagen, su tamaño y su recorte. Para lograr las dos vistas solicitadas, habrá que comprobar que la plantilla de la tarjeta y la del artículo completo muestran la imagen destacada. Una configuración de Elementor puede intervenir en esas vistas, pero su funcionamiento concreto no quedó verificado. Asignar `featured_media` no garantiza por sí solo que una plantilla lo muestre. [Imágenes destacadas y plantillas](https://developer.wordpress.org/themes/classic-themes/functionality/featured-images-post-thumbnails/).

El formulario local acepta el ID de la imagen prevista para preparar el documento de envío. No consulta ni modifica la biblioteca y, por tanto, no confirma todavía que ese ID exista, corresponda a una imagen o esté disponible para esa cuenta.

## Búsqueda externa e importación de portadas

`src/deployment/images.py` implementa la alternativa que evita depender de imágenes subidas manualmente. El catálogo principal es el **Directorio de fotos de WordPress.org**, que es externo a las bibliotecas de nuestros sitios. Su consulta pública no necesita la contraseña de WordPress. El directorio ofrece fotografías bajo CC0, con uso comercial permitido; conserva la procedencia y revise cualquier duda sobre el contenido. «Encontrada gratis» no equivale por sí solo a una licencia adecuada. [Licencia del directorio](https://wordpress.org/photos/license/).

El flujo disponible es:

1. Sugerir términos visibles desde el título o las categorías, como `Barcelona`, `classroom` o `paperwork`, y consultar `search_candidates`. Esta acción guarda candidatos en `cover_candidates` y no sube archivos.
2. Comparar las fotografías con el tema. La puntuación solo combina palabras y formato; el primer resultado no queda aprobado automáticamente. Una foto de papeles no debe describirse como una TIE si no muestra ese documento.
3. Elegir una candidata y ejecutar expresamente `import_candidate` para el destino. El servicio descarga y valida los bytes, reserva la operación en `cover_imports` y sube únicamente el archivo a `/wp/v2/media`.
4. Usar el `wp_media_id` confirmado como `featured_media` al preparar o enviar el artículo. Importar una foto no crea, publica ni modifica una entrada y tampoco asigna por sí mismo la portada de una adaptación.

Se admiten fotografías CC0 JPEG, PNG o WebP, horizontales, de al menos 960 × 540, hasta 10 MB y 30 megapíxeles. El servicio revisa tipo real, dimensiones y contenido del archivo, y rechaza SVG, animaciones y respuestas no válidas. La búsqueda conserva página de origen, autor, licencia y descripción; la importación fija además tamaño, tipo y SHA-256. El texto alternativo inicial procede de la descripción del catálogo y debe revisarse para el idioma y el contenido real de la foto.

Openverse es un proveedor alternativo **seleccionado expresamente**, con `license=cc0` y validación adicional de cada resultado. No hay cambio silencioso de proveedor. Puede devolver pocos resultados compatibles o fallar; si no hay candidata adecuada, el estado es `needs_image`. La portada genérica por categoría propuesta en la guía aún no está configurada como un mecanismo automático de respaldo.

La deduplicación se basa en `(site_base_url, provider, external_id)`. Canadá inglés y francés comparten instalación y pueden reutilizar una importación; Colombia necesita un ID de su propio sitio. Una importación existente se devuelve sin otra subida, incluidos sus estados `failed`, `importing` o `unknown`. Si WordPress pudo recibir el archivo pero se pierde la respuesta, queda `unknown` y exige comprobación manual: no se intenta otra vez automáticamente.

Estas funciones no usan OpenAI ni otro generador de imágenes. La selección visual, la importación del medio y la revisión del artículo son pasos separados. Los detalles de campos y estados están en [la estructura de la base](estructura-base-de-datos.md).

## Alternativa mediante All Sources Images

Se revisaron la ficha y el código oficial de **All Sources Images 1.1.0**; esto es una inspección de implementación y consultas públicas de su proveedor, no una prueba instalada de extremo a extremo. El plugin permite buscar proveedores externos y automatizar la imagen destacada, pero también incorpora proveedores de IA, que no se han activado para este proyecto. [Ficha oficial](https://wordpress.org/plugins/all-sources-images/), [código de la versión examinada](https://downloads.wordpress.org/plugin/all-sources-images.1.1.0.zip).

En esa versión, la automatización está desactivada por defecto. Los hooks de publicación atienden entradas en `publish` y programan el trabajo mediante WP-Cron cinco segundos después; crear un borrador por REST no prueba ese recorrido. Existe además una capacidad `allsi/auto-generate-for-post` que puede actuar sobre un borrador, pero su elección aleatoria requiere revisar el resultado y no equivale al recorrido de publicación.

El método `build_query_args` de `admin/sources/class-allsi-source-openverse.php` no envía un filtro de licencia. La reproducción pública de esa consulta para Barcelona devolvió, entre otras, licencias `by-nc-nd`; por eso no debe asumirse que el conjunto sirve para nuestro uso comercial ni que sea CC0. Nuestro servicio de Openverse sí pide y comprueba CC0. La consulta reproducida y sus parámetros están registrados en `data/image-options-research.json`; no se instaló ni ejecutó el plugin para obtener ese resultado.

El usuario WordPress disponible no dispone de `install_plugins` ni `activate_plugins`. Por tanto, no se ha instalado ni activado el plugin y queda pendiente probar su integración en un entorno donde un administrador pueda configurarlo. Antes de usarlo habría que fijar un proveedor y licencias adecuados, desactivar IA y definir si la portada la asigna el plugin o el script. No deben competir ambos por la misma imagen destacada.

La inspección pública comprobó búsquedas en el Directorio y en Openverse. La importación posterior del medio se documenta por separado abajo; ninguna de esas operaciones prueba el plugin instalado ni la presentación final de una portada en el tema.

### Prueba real de búsqueda e importación: 5 de noviembre de 2026

La búsqueda de Barcelona en el Directorio produjo **17 candidatas válidas**. Después de revisar visualmente la foto externa `192650`, se importó a Colombianos en España como medio **1573**. Una lectura posterior respondió HTTP200 y confirmó el medio. Repetir la importación devolvió el mismo registro y no realizó solicitudes de red. La [fotografía importada](https://colombianosespana.com/wp-content/uploads/2026/11/et-cover-wordpress_photos-192650-scaled.jpg) queda disponible en la biblioteca; no se asignó a ninguna entrada durante esta prueba.

Openverse devolvió **5 candidatas compatibles** al consultar `license=cc0` con 20 resultados solicitados y filtrar localmente formato, origen y dimensiones. Los filtros adicionales de formato y proporción de la API se retiraron de la consulta tras un HTTP504; se conservan las validaciones locales. Una búsqueda sin coincidencias en el Directorio produjo `needs_image`, sin inventar ni subir una portada de respaldo.

La lectura de la entrada **1546** mostró que actualmente está **publicada** y conserva `featured_media=1369`. El estado de borrador documentado más abajo corresponde a su creación histórica; no describe su estado actual. Esta sesión no hizo POST de entradas ni cambió su portada. Solo se subió el medio 1573: no hubo llamadas a OpenAI, nuevas adaptaciones ni instalación del plugin. La visualización de esa nueva foto como tarjeta o cabecera queda pendiente de asignarla en una prueba explícita posterior.

## Selección automática para las pruebas

`src/deployment/automatic_images.py` expone `auto_import_cover(db_path, target_id, article_id)`. Obtiene términos del título guardado: para teletrabajo o nómadas digitales prioriza `remote work`, `laptop`, `workspace` y `paperwork`. Consulta WordPress Photos primero y Openverse como respaldo. Filtra por licencia CC0, origen, formato, dimensiones y relación entre descripción y búsqueda; ordena por coincidencia textual/formato y desempata por identificador externo.

La función elige e importa sin selector humano ni modelo visual. La pertinencia depende de los metadatos del catálogo, no de una inspección visual. Si no encuentra una candidata apta, devuelve `needs_image` y la prueba no envía un artículo sin portada. No utiliza generación de imágenes.

Para evitar problemas de transferencia o procesamiento, el importador reduce proporcionalmente los archivos que superan 1.800.000 bytes, con un lado máximo inicial de 2048 píxeles. Conserva formato y transparencia y no recorta la foto. `original_file` y `processing` conservan el hash, tamaño y dimensiones originales, así como la transformación aplicada; el hash principal corresponde al archivo subido. Un archivo que no pueda reducirse dentro de los límites queda rechazado antes del POST.

`cover_auto_selections` conserva artículo, versión fuente, destino, versión de política, consultas, candidata escogida, importación e ID remoto. Repetir la operación reutiliza esa decisión; una operación incierta solo consulta el historial del importador y no repite un POST. Las pruebas automáticas la invocan después de obtener un HTML válido; el ID confirmado pasa a `featured_media` al crear el borrador.

El script `scripts/run_automatic_trial.py` conecta evaluación de la API, redacción, selección de portada, exportación y envío explícito como borrador. El título procede de la evaluación de la API y el cuerpo se conserva sin modificaciones editoriales del asistente. Las versiones y decisiones de cada etapa quedan registradas por separado.

### Prueba automática DNV → Colombia, 5 de noviembre de 2026

El artículo fuente 2, versión 430, se procesó con la configuración colombiana 3 y prompt 12 (revisión 4), usando **GPT-6 Luna, razonamiento high y Flex**. La evaluación automática 1 decidió `adapt`; generó sus propias exclusiones, fuentes y título, sin recibir las observaciones asistidas anteriores. La redacción quedó en la prueba **3**.

El algoritmo eligió en WordPress Photos la candidata externa **111292** mediante la búsqueda `laptop`, sin elección del asistente. Dos conexiones de subida fallaron; las comprobaciones autenticadas y de archivos confirmaron su ausencia antes de reintentar. La optimización automática redujo la misma imagen de 3.230.513 a 235.760 bytes, manteniendo su proporción. WordPress confirmó el medio **1598**.

El envío 2 creó el [borrador 1599](https://colombianosespana.com/wp-admin/post.php?post=1599&action=edit), con categorías Inmigración (20) y Trabajo Remoto (26). Un GET autenticado posterior confirmó `draft`, `featured_media=1598` y que `content.raw` coincidía exactamente con el HTML devuelto por la API. No se editó ese texto; los CTA permanecen como marcadores pendientes en el borrador.

La prueba **2** correspondía al ensayo asistido inicial y no tuvo envío WordPress; posteriormente el usuario eliminó su contenido local. Desde la actualización del 6 de noviembre tampoco se muestra su encabezado en el historial. La recuperación técnica de la imagen reutilizó la evaluación y la redacción de la prueba 3, sin repetir llamadas a OpenAI. La evidencia y el cálculo aproximado de consumo están en `data/adaptations/generation-3.review.json`. Antes del ajuste de archivos pasaron 569 pruebas y 8 subpruebas; después se verificaron las 83 pruebas de importación, selección y orquestación.

## HTML que adopta el diseño de WordPress

Las tres plantillas editoriales añaden una regla de portabilidad para devolver el cuerpo del artículo con etiquetas semánticas. Se mantienen las instrucciones de localización y las etiquetas CTA proporcionadas.

La regla exige:

- Párrafos, encabezados `h2` y `h3`, listas y énfasis semántico; tablas cuando correspondan al contenido.
- Título principal separado del cuerpo. El `h1`, encabezado, pie de página y estructura general de la página corresponden a WordPress.
- Imagen destacada separada del cuerpo, mediante `featured_media`.
- Ausencia de estilos inline, CSS, JavaScript, clases o IDs de presentación, medidas fijas, shortcodes y estructuras específicas de temas o maquetadores.
- Los marcadores CTA como texto literal, para resolverlos después con enlaces configurados.

Así, los estilos del tema pueden aplicarse al cuerpo sin competir con colores, fuentes o dimensiones fijados por el contenido. Sigue siendo necesario que la plantilla incluya el título, el contenido y la imagen destacada. Cambiar de tema no actualiza los hechos o textos del artículo, y una plantilla que omita alguno de esos elementos no lo mostrará por el simple hecho de existir en la entrada.

El validador local comprueba el HTML recibido antes de construir la vista previa. Rechaza etiquetas o atributos incompatibles, estructura inválida, scripts, estilos, imágenes incrustadas, shortcodes y Markdown. No elimina silenciosamente partes del artículo para hacer pasar una validación. Esta comprobación es estructural: no garantiza por sí sola la calidad editorial, la exactitud del contenido ni su apariencia en un tema concreto.

El flujo automático de generación normaliza primero los problemas de formato que puede corregir conservando el contenido: por ejemplo, envuelve en párrafos los CTA que la API haya dejado sueltos entre secciones. La política `semantic-format-v1` conserva texto, enlaces y marcadores y vuelve a ejecutar este mismo validador. Los formatos ambiguos o inseguros siguen rechazados; no se rebaja la validación ni se eliminan partes editoriales. La salida original se conserva en `generation_trials`; la copia normalizada y sus operaciones quedan en `generation_html_formats`. Esta fase no consume API ni altera una edición manual posterior.

## Enlaces CTA

Cada destino tiene los dos marcadores de su idioma:

| Idioma | Marcadores |
|---|---|
| Inglés | `[CTA_IMMIGRATION_EN]`, `[CTA_TAXES_EN]` |
| Francés | `[CTA_IMMIGRATION_FR]`, `[CTA_TAXES_FR]` |
| Español | `[CTA_INMIGRACION_ES]`, `[CTA_IMPUESTOS_ES]` |

En **Preparación de adaptaciones → Destinos**, seleccione el destino y complete los campos de **Enlaces CTA del destino**. El botón **Guardar enlaces CTA** guarda únicamente esos enlaces. Todas las configuraciones del mismo destino los comparten, aunque utilicen prompts o modelos distintos. Esta edición y la sección WordPress consultan la misma columna `deployment_targets.cta_links_json`.

La configuración local guarda, por marcador, un objeto con `url` y `text`. La clave del diccionario no lleva corchetes. Por ejemplo, la estructura inicial es `{"CTA_IMMIGRATION_EN": {"url": "", "text": ""}}`. Ambos campos pueden permanecer vacíos mientras se crean las páginas; también se puede guardar solo uno como avance. No se crean URLs de ejemplo ni se inventa el texto visible.

Las URLs que se completen deben ser HTTPS, pertenecer al sitio de destino y no incluir credenciales ni parámetros. Para reemplazar un marcador hacen falta una URL y un texto válidos. Python convierte solamente ese marcador de texto en un enlace `<a>` semántico, escapando el texto para impedir que se interprete como HTML. No añade clases, estilos ni un contenedor dependiente del tema.

Si falta URL o texto, la vista previa estricta indica el problema y no produce un cuerpo listo. El flujo de prueba permite conservar esos marcadores únicamente en un borrador de revisión cuando se solicita expresamente esa modalidad; en el panel corresponde a la opción de enviar un borrador con CTA pendientes. Guardar o cambiar enlaces no modifica entradas existentes: se aplican al siguiente envío o reenvío explícito. Los marcadores de otro idioma o no reconocidos no se admiten.

## Uso de la sección WordPress

1. Abra **WordPress** y elija uno de los tres destinos.
2. Revise la base, el endpoint previsto y el indicador de credenciales configuradas.
3. Ajuste la ruta provisional de guías, las categorías previstas, las notas y los enlaces CTA. El idioma francés permanece pendiente de integración.
4. Guarde los cambios locales. Las categorías y la consulta del listado necesitan verificación posterior en el sitio.
5. En la vista previa, introduzca título, cuerpo HTML, extracto opcional e ID de imagen destacada. Este formulario sirve para revisar una entrada de prueba existente; no genera un artículo.
6. Consulte la validación y, cuando corresponda, el documento JSON previsto para una entrada con `status: "draft"`.

La vista previa no ejecuta una solicitud a WordPress ni crea un registro remoto. Un documento válido estructuralmente puede tener cuestiones pendientes del sitio, como categorías, idioma, permisos o imagen. El botón de envío permanece deshabilitado y el servidor rechaza cualquier intento de ejecutar el envío, incluso si se elude la interfaz.

## API y organización local

| Ruta local | Propósito |
|---|---|
| `GET /api/deployment/catalog` | Destinos, rutas, categorías, modo de idioma, CTAs, indicadores de credenciales y cuestiones pendientes |
| `PUT /api/deployment/targets/<id>` | Guardar la configuración local del destino |
| `GET /api/generation/destinations/<id>/ctas` | Consultar los dos CTA y si el destino tiene una conexión compatible |
| `PUT /api/generation/destinations/<id>/ctas` | Guardar `{cta_links: ...}` sin cambiar categorías, rutas ni contenido |
| `PUT /api/generation/trials/<id>/content` | Guardar `{html, revision}` como edición local, sin envío remoto |
| `POST /api/deployment/preview` | Validar `target_id`, `title`, `html`, `excerpt` y `featured_media`, y preparar el documento local |
| `POST /api/deployment/execute` | Rechazo de ejecución con HTTP409 |
| `POST /api/deployment/images/search` | Buscar candidatas externas mediante `query` y `provider` |
| `GET /api/deployment/images/suggestions?article_id=<id>` | Proponer búsquedas basadas en el artículo guardado |
| `POST /api/deployment/images/import` | Importación confirmada con `target_id`, `candidate_id` y `confirm: true` |
| `GET /api/deployment/images/import?target_id=<id>&candidate_id=<id>` | Consultar el resultado guardado sin repetir la importación |

La importación nunca se reintenta automáticamente. Tras un rechazo definitivo, como un 401, el panel permite confirmar otra importación después de corregir la causa (`retry_failed: true`). El historial conserva el fallo anterior. Un resultado incierto o todavía reservado no admite esta recuperación, para evitar duplicar medios.

Los destinos incluyen `id`, `name`, `locale`, `guide_url`, `category_ids`, `language_mode` y `cta_links`. `credentials` contiene indicadores de presencia, nunca secretos; `blockers` explica qué falta verificar o configurar. El catálogo y formulario general de WordPress conservan `mode=preparation` y `execution_enabled=false`; esto no bloquea los servicios separados de pruebas y reenvíos explícitos.

```text
src/deployment/
  settings.py          configuración privada y estado público sin secretos
  credentials_import.py importación privada y explícita de contraseñas de aplicación
  repository.py        destinos, rutas, categorías y CTAs en SQLite
  content.py           validación semántica y sustitución local de CTAs
  preparation.py       documento de borrador local y comprobación de campos
  images.py            búsqueda CC0 e importación explícita de medios con procedencia
  wp_publisher.py      bloqueo de envío remoto
src/dashboard/
  deployment_routes.py API local de preparación
  image_routes.py      búsqueda, sugerencias e importación confirmada de medios
  static/deployment.js interfaz de preparación de WordPress
  static/images.js     comparación visual de candidatas y confirmación de importación
```

La tabla `deployment_targets` se relaciona con los destinos editoriales. Guarda los ajustes de despliegue, no las credenciales. La preparación de WordPress no modifica las versiones de los artículos fuente ni marca una adaptación como generada o publicada. `cover_candidates` y `cover_imports` conservan candidatos e importaciones de imágenes, no artículos publicados.

## Prueba de Colombia del 4 de noviembre de 2026

La ruta alternativa estándar `/?rest_route=/wp/v2/...`, con un agente de cliente identificable, permitió consultar medios, categorías y la página de guías. Se verificó que la imagen `https://colombianosespana.com/wp-content/uploads/2026/10/1.webp` existe y es el medio **1369**, tipo `image/webp`; puede reutilizarse sin generar ni subir otra imagen. La página del catálogo tiene ID **1504**. Entre las categorías existentes están Destinos (19), Inmigración (20), Mudarse a España (23) y Vivir en España (24); no se han asignado automáticamente ni se ha confirmado el filtro de tarjetas.

Las consultas autenticadas a `/users/me` devolvieron **401 `rest_not_logged_in`** tanto con el usuario como con el correo facilitados. Se probaron la ruta alternativa y `index.php`, HTTPS, contraseña de aplicación y consultas sin caché. La ruta convencional de endpoints específicos devolvió 403 HTML. No se creó una entrada, no se subió una imagen y no se modificó el sitio.

La incidencia quedó resuelta al probar la nueva contraseña de aplicación suministrada después. WordPress respondió **HTTP200**, reconoció al usuario 5 y confirmó permisos de edición y medios. El endpoint de introspección confirmó la aplicación **api-articulos-colombianos**. Se actualizaron únicamente las credenciales colombianas en `.env` y su bloque de `extras/Sitios.txt`; las demás conexiones se conservaron. El resultado anterior no permite concluir que hubiera un fallo de cabeceras o de Wordfence: esas eran hipótesis previas a disponer de la nueva contraseña.

Con la nueva conexión se creó el [borrador 1546](https://colombianosespana.com/wp-admin/post.php?post=1546&action=edit) a partir de la prueba 1, sin otra llamada al modelo. Una lectura autenticada posterior confirmó `status: draft`, imagen destacada 1369, categorías Destinos (19) y Mudarse a España (23), y el mismo HTML original guardado en SQLite. Su salida conserva las observaciones editoriales y CTA pendientes, descritos en [Preparación de generación](preparacion-generacion.md). No se publicó ni se alteró la imagen existente.

`src/deployment/trial.py` ofrece `send_trial_draft` para una prueba individual guardada. Comprueba identidad, permisos, imagen y categorías; fija siempre `status: draft`. La tabla `deployment_trials` reserva el envío antes del POST y vincula cada prueba de generación con un único intento, su documento, advertencias y resultado. Repetir la llamada devuelve el registro guardado sin hacer solicitudes; un resultado incierto necesita comprobación manual. La conservación de marcadores CTA sin URL requiere `allow_pending_ctas=True` explícito y solo se admite en este borrador de revisión. El flujo de generación del panel usa ese servicio cuando el usuario selecciona el envío como borrador; el endpoint genérico `/api/deployment/execute` sigue bloqueado.

El historial del panel muestra el estado de WordPress y un enlace al editor construido con el dominio autorizado y el ID confirmado. La comprobación de conexión de este envío no convierte los indicadores de mera presencia de credenciales del formulario en una monitorización continua.

### Editar, reenviar o borrar una adaptación

Desde una adaptación completa, **Editar texto** abre el cuerpo HTML semántico para corregirlo y **Guardar texto** crea una revisión local. El guardado valida estructura y marcadores del idioma y rechaza scripts, estilos o etiquetas incompatibles. Si el texto cambió desde que se abrió, solicita recargarlo antes de guardar; también bloquea la edición mientras haya un envío en curso o incierto.

La respuesta original de la API permanece en `generation_trials.html` y `response_json`; las revisiones del usuario quedan en `adaptation_edits`. La última revisión manual es la que muestra el panel, exporta el script y usa un envío posterior; si no existe, se usa la normalización válida de formato o la salida original. No se reescribe la respuesta del modelo ni se llama a OpenAI para guardar. WordPress solo recibe esos cambios al ejecutar el envío o reenvío correspondiente.

Desde el 5 de noviembre, `src/deployment/management.py` añade acciones explícitas del panel sobre adaptaciones previamente enviadas. Conserva el primer registro de `deployment_trials` y añade cada intento posterior en `adaptation_actions`. La entrada usada en la siguiente comprobación procede del último intento confirmado, incluido un borrador recreado con otro ID.

El reenvío actualiza `POST /wp/v2/posts/<id>` conservando el estado y el slug de la entrada existente. Solo ante un 404 con código `rest_post_invalid_id` propone crear un borrador con `POST /wp/v2/posts`. Un 401, 403, error de servidor, respuesta ilegible o entrada en papelera no activa creación automática. El diálogo muestra el destino y las condiciones antes de confirmar. [Contrato de entradas de WordPress](https://developer.wordpress.org/rest-api/reference/posts/).

**Borrar de la base local** retira el cuerpo, sus ediciones y los documentos locales de envío que podían contenerlo. También desaparecen la tarjeta, el título histórico y los contadores de esa adaptación en el panel, incluida una adaptación eliminada antes de esta actualización. Solo queda un registro técnico interno con la clave del intento para evitar repetir accidentalmente una llamada pagada; no es una adaptación visible ni una papelera para restaurar contenido.

El borrado no realiza ninguna llamada DELETE a WordPress. La entrada y la imagen remotas permanecen intactas, al igual que archivos exportados o respaldos anteriores. Consulte [acciones del panel](panel-local.md#acciones-sobre-adaptaciones-guardadas).

## Inspección pública anterior y límites de esa verificación

La inspección de lectura del 4 de noviembre de 2026 encontró un listado paginado de artículos en la página inglesa mediante la herramienta web. Las consultas directas públicas a ambas páginas y a endpoints específicos de entradas, páginas, categorías y tipos devolvieron HTTP403 desde este entorno. La raíz REST de Colombianos respondió correctamente en una consulta y declaró rutas estándar y namespaces de Elementor; eso no permite confirmar permisos ni comportamientos automáticos.

En esa inspección pública inicial no se hicieron pruebas autenticadas. Los 403 observados no demostraban que las credenciales fueran inválidas. La prueba colombiana posterior, descrita arriba, resolvió acceso, imagen y categorías para ese envío; no verificó los filtros de tarjetas, la integración multilingüe ni ambas plantillas. [Catálogo inglés inspeccionado](https://canadiansinspain.com/guides-for-canadians-in-spain/).

La validación utiliza pruebas locales y datos simulados para comprobar HTML, CTAs, configuración sin exposición de secretos y rechazo del envío. Pasaron 305 pruebas y 8 subpruebas. La comprobación de navegador en una copia de los 107 artículos verificó los tres destinos, guardado de categorías y CTA, advertencia de imagen pendiente, documento con `featured_media` separado, rechazo de estilos, restricciones del francés, persistencia y presentación móvil. No hubo solicitudes externas desde el panel ni escrituras remotas. Los 107 artículos, sus 535 versiones y la selección original permanecen intactos; no se guardaron borradores de prueba en la base principal ni en WordPress.
