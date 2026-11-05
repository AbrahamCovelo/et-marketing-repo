# Preparación local de WordPress

Esta fase añade `src/deployment` y la sección **WordPress** del panel para revisar conexiones, destinos, enlaces CTA y el contenido que se enviaría como borrador. Todavía no envía artículos, no sube imágenes y no crea borradores remotos. La generación de artículos también permanece deshabilitada.

## Conexiones y rutas de destino

Las credenciales entregadas en `extras/Sitios.txt` se importan a la configuración privada `.env`. Ambos archivos deben quedar excluidos de Git. El panel muestra únicamente si la configuración está presente: no devuelve usuarios, contraseñas, fragmentos de credenciales ni su contenido. Que una conexión figure como configurada no significa que se haya autenticado o que tenga permisos de escritura.

La conexión prevista utiliza un usuario de WordPress y su **contraseña de aplicación**, mediante autenticación Basic sobre HTTPS. Una contraseña de aplicación no se envía como token Bearer. Esta fase solo guarda la configuración local y no efectúa una prueba autenticada. [Autenticación oficial de la REST API](https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/).

| Destino | Base de WordPress | Ruta provisional del catálogo |
|---|---|---|
| Canadians In Spain · Inglés | `https://canadiansinspain.com` | `https://canadiansinspain.com/guides-for-canadians-in-spain/` |
| Canadians In Spain · Francés | `https://canadiansinspain.com` | Pendiente |
| Colombians in Spain · Español | `https://colombianosespana.com` | `https://colombianosespana.com/guias-colombianos-en-espana` |

La ruta del catálogo se guarda separada de la base y del endpoint REST. Por ejemplo, el endpoint previsto para entradas es `https://canadiansinspain.com/wp-json/wp/v2/posts`. La página de guías es el lugar donde se muestra un listado; su URL no es un endpoint de publicación ni un `parent` que deba asignarse a cada entrada.

Los campos de título, cuerpo, extracto, categorías e imagen destacada pertenecen a la entrada. La categoría o consulta que utiliza el listado debe comprobarse en WordPress antes de decidir qué IDs asignar. Guardar un ID local no crea una categoría ni verifica que exista. [Referencia de entradas y sus campos](https://developer.wordpress.org/rest-api/reference/posts/).

Para francés quedan pendientes tanto la ruta de guías como la integración del idioma en WordPress. `fr-CA` identifica nuestro destino editorial; no configura por sí mismo un plugin multilingüe. El panel mantiene ese destino con idioma pendiente y evita tratarlo como una entrada del idioma predeterminado.

## Imagen destacada: tarjeta y artículo

La imagen de portada se representa mediante `featured_media`: el ID de una imagen que existe en la biblioteca del WordPress de destino. La misma imagen destacada del artículo puede usarse para su tarjeta en el listado y para su cabecera al abrirlo. No hace falta incrustar otra copia de esa portada en el HTML del cuerpo. Los IDs son propios de cada instalación de WordPress y no deben copiarse entre sitios como si fueran equivalentes. [Campos de medios](https://developer.wordpress.org/rest-api/reference/media/).

**WordPress core no crea la fotografía original ni elige una imagen adecuada automáticamente.** Puede recibir un archivo existente, gestionar el adjunto y generar miniaturas y tamaños derivados. Si el archivo ya está en la biblioteca, se podrá reutilizar; si no, será necesario aportar una imagen existente antes de publicar. No se incorpora generación de imágenes con OpenAI ni con otro proveedor. [Creación de metadatos y tamaños derivados](https://developer.wordpress.org/reference/functions/wp_generate_attachment_metadata/).

El tema o la plantilla decide dónde aparece la imagen, su tamaño y su recorte. Para lograr las dos vistas solicitadas, habrá que comprobar que la plantilla de la tarjeta y la del artículo completo muestran la imagen destacada. Una configuración de Elementor puede intervenir en esas vistas, pero su funcionamiento concreto no quedó verificado. Asignar `featured_media` no garantiza por sí solo que una plantilla lo muestre. [Imágenes destacadas y plantillas](https://developer.wordpress.org/themes/classic-themes/functionality/featured-images-post-thumbnails/).

El formulario local acepta el ID de la imagen prevista para preparar el documento de envío. No consulta ni modifica la biblioteca y, por tanto, no confirma todavía que ese ID exista, corresponda a una imagen o esté disponible para esa cuenta.

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

## Enlaces CTA

Cada destino tiene los dos marcadores de su idioma:

| Idioma | Marcadores |
|---|---|
| Inglés | `[CTA_IMMIGRATION_EN]`, `[CTA_TAXES_EN]` |
| Francés | `[CTA_IMMIGRATION_FR]`, `[CTA_TAXES_FR]` |
| Español | `[CTA_INMIGRACION_ES]`, `[CTA_IMPUESTOS_ES]` |

La configuración local guarda, por marcador, un objeto con `url` y `text`. La clave del diccionario no lleva corchetes. Por ejemplo, la estructura es `{"CTA_IMMIGRATION_EN": {"url": "…", "text": "…"}}`; los valores reales se completan desde el panel.

Las URLs configuradas deben ser HTTPS y pertenecer al sitio de destino. Se exige también el texto visible del enlace. Si el cuerpo contiene un CTA sin URL o texto válido, la preparación indica el problema y no produce un cuerpo listo para enviar. No se inventan destinos ni se convierten marcadores en enlaces vacíos. Los marcadores de otros idiomas o no reconocidos también deben resolverse antes de continuar.

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
| `POST /api/deployment/preview` | Validar `target_id`, `title`, `html`, `excerpt` y `featured_media`, y preparar el documento local |
| `POST /api/deployment/execute` | Rechazo de ejecución con HTTP409 |

Los destinos incluyen `id`, `name`, `locale`, `guide_url`, `category_ids`, `language_mode` y `cta_links`. `credentials` contiene indicadores de presencia, nunca secretos; `blockers` explica qué falta verificar o configurar. El modo de esta fase es `preparation` y `execution_enabled` permanece en `false`.

```text
src/deployment/
  settings.py          configuración privada y estado público sin secretos
  credentials_import.py importación privada y explícita de contraseñas de aplicación
  repository.py        destinos, rutas, categorías y CTAs en SQLite
  content.py           validación semántica y sustitución local de CTAs
  preparation.py       documento de borrador local y comprobación de campos
  wp_publisher.py      bloqueo de envío remoto
src/dashboard/
  deployment_routes.py API local de preparación
  static/deployment.js interfaz de preparación de WordPress
```

La tabla `deployment_targets` se relaciona con los destinos editoriales. Guarda los ajustes de despliegue, no las credenciales. La preparación de WordPress no modifica las versiones de los artículos fuente ni marca una adaptación como generada o publicada.

## Prueba de Colombia del 4 de noviembre de 2026

La ruta alternativa estándar `/?rest_route=/wp/v2/...`, con un agente de cliente identificable, permitió consultar medios, categorías y la página de guías. Se verificó que la imagen `https://colombianosespana.com/wp-content/uploads/2026/10/1.webp` existe y es el medio **1369**, tipo `image/webp`; puede reutilizarse sin generar ni subir otra imagen. La página del catálogo tiene ID **1504**. Entre las categorías existentes están Destinos (19), Inmigración (20), Mudarse a España (23) y Vivir en España (24); no se han asignado automáticamente ni se ha confirmado el filtro de tarjetas.

Las consultas autenticadas a `/users/me` devolvieron **401 `rest_not_logged_in`** tanto con el usuario como con el correo facilitados. Se probaron la ruta alternativa y `index.php`, HTTPS, contraseña de aplicación y consultas sin caché. La ruta convencional de endpoints específicos devolvió 403 HTML. No se creó una entrada, no se subió una imagen y no se modificó el sitio.

La incidencia quedó resuelta al probar la nueva contraseña de aplicación suministrada después. WordPress respondió **HTTP200**, reconoció al usuario 5 y confirmó permisos de edición y medios. El endpoint de introspección confirmó la aplicación **api-articulos-colombianos**. Se actualizaron únicamente las credenciales colombianas en `.env` y su bloque de `extras/Sitios.txt`; las demás conexiones se conservaron. El resultado anterior no permite concluir que hubiera un fallo de cabeceras o de Wordfence: esas eran hipótesis previas a disponer de la nueva contraseña.

Con la nueva conexión se creó el [borrador 1546](https://colombianosespana.com/wp-admin/post.php?post=1546&action=edit) a partir de la prueba 1, sin otra llamada al modelo. Una lectura autenticada posterior confirmó `status: draft`, imagen destacada 1369, categorías Destinos (19) y Mudarse a España (23), y el mismo HTML original guardado en SQLite. Su salida conserva las observaciones editoriales y CTA pendientes, descritos en [Preparación de generación](preparacion-generacion.md). No se publicó ni se alteró la imagen existente.

`src/deployment/trial.py` ofrece `send_trial_draft` para una prueba individual guardada. Comprueba identidad, permisos, imagen y categorías; fija siempre `status: draft`. La tabla `deployment_trials` reserva el envío antes del POST y vincula cada prueba de generación con un único intento, su documento, advertencias y resultado. Repetir la llamada devuelve el registro guardado sin hacer solicitudes; un resultado incierto necesita comprobación manual. La conservación de marcadores CTA sin URL requiere `allow_pending_ctas=True` explícito y solo se admite en este borrador de revisión. Las rutas de ejecución del panel siguen bloqueadas.

El historial del panel muestra el estado de WordPress y un enlace al editor construido con el dominio autorizado y el ID confirmado. La comprobación de conexión de este envío no convierte los indicadores de mera presencia de credenciales del formulario en una monitorización continua.

### Reenvío desde el historial

Desde el 5 de noviembre, `src/deployment/management.py` añade acciones explícitas del panel sobre adaptaciones previamente enviadas. Conserva el primer registro de `deployment_trials` y añade cada intento posterior en `adaptation_actions`. La entrada usada en la siguiente comprobación procede del último intento confirmado, incluido un borrador recreado con otro ID.

El reenvío actualiza `POST /wp/v2/posts/<id>` conservando el estado y el slug de la entrada existente. Solo ante un 404 con código `rest_post_invalid_id` propone crear un borrador con `POST /wp/v2/posts`. Un 401, 403, error de servidor, respuesta ilegible o entrada en papelera no activa creación automática. El diálogo muestra el destino y las condiciones antes de confirmar. [Contrato de entradas de WordPress](https://developer.wordpress.org/rest-api/reference/posts/).

Los controles de borrado solo modifican las tablas locales. No existe una llamada DELETE a WordPress en este flujo. El texto original generado y los documentos de envíos se vacían al borrar una adaptación; los metadatos mínimos y el historial de IDs permanecen. Consulte [acciones del panel](panel-local.md#acciones-sobre-adaptaciones-guardadas).

## Inspección pública anterior y límites de esa verificación

La inspección de lectura del 4 de noviembre de 2026 encontró un listado paginado de artículos en la página inglesa mediante la herramienta web. Las consultas directas públicas a ambas páginas y a endpoints específicos de entradas, páginas, categorías y tipos devolvieron HTTP403 desde este entorno. La raíz REST de Colombianos respondió correctamente en una consulta y declaró rutas estándar y namespaces de Elementor; eso no permite confirmar permisos ni comportamientos automáticos.

No se hicieron pruebas autenticadas. Los 403 observados no demuestran que las credenciales sean inválidas. Tampoco quedaron confirmados los IDs de categorías, los filtros de las tarjetas, la integración multilingüe ni la presentación de la imagen en ambas plantillas. Se revisarán en una prueba posterior autorizada. [Catálogo inglés inspeccionado](https://canadiansinspain.com/guides-for-canadians-in-spain/).

La validación utiliza pruebas locales y datos simulados para comprobar HTML, CTAs, configuración sin exposición de secretos y rechazo del envío. Pasaron 305 pruebas y 8 subpruebas. La comprobación de navegador en una copia de los 107 artículos verificó los tres destinos, guardado de categorías y CTA, advertencia de imagen pendiente, documento con `featured_media` separado, rechazo de estilos, restricciones del francés, persistencia y presentación móvil. No hubo solicitudes externas desde el panel ni escrituras remotas. Los 107 artículos, sus 535 versiones y la selección original permanecen intactos; no se guardaron borradores de prueba en la base principal ni en WordPress.
