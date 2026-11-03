# Inspección de cinco guías: contenido, tablas y preguntas frecuentes

Fecha de revisión: 2026-11-03 (America/Bogota). Se leyó el cuerpo completo y se inspeccionó el HTML obtenido directamente; las copias están en `data/research/`. El índice web y sus cachés no siempre mostraron el mismo orden: dos artículos estaban en `/guides/page/2/` y tres en `/guides/page/3/` durante esta inspección.

## Resultado común

Las cinco guías usan `article.single-entry .entry-content.single-content`. Elegir esta raíz evita el encabezado global, metadatos visibles, comentarios, navegación anterior/siguiente, recomendaciones finales y pie. No usar `.entry-content` sin contexto: aparecen varias instancias vacías en tarjetas de artículos relacionados.

El cuerpo contiene estilos inline, índices Kadence y promociones, además del contenido. Excluir `style`, `script`, `nav.wp-block-kadence-tableofcontents`, separadores y medios. Conservar H2/H3/H4, párrafos, listas con su estructura y tablas. Las fechas, URL, título y taxonomías se guardan como metadatos separados.

Las preguntas frecuentes NO son encabezados HTML normales. Cada `.wp-block-kadence-pane` contiene la pregunta en `.kt-blocks-accordion-title` dentro de un `button`, y la respuesta en `.kt-accordion-panel-inner`. La clase `kt-accordion-panel-hidden` indica un acordeón cerrado, no contenido descartable. Extraer estas unidades antes de cualquier regla genérica que quite botones o elementos ocultos. No duplicarlas leyendo también su copia JSON-LD.

## Guías revisadas

| Guía y fuente | Estructura observada | Decisión de extracción |
|---|---|---|
| [Parental leave](https://movingtospain.com/spain-new-parental-leave-policy/) | 5 H2 de contenido, 1 tabla comparativa; 1 H3 adicional pertenece a una promoción. | Conservar el texto normativo como texto fuente sin validarlo ni actualizarlo; preservar tabla, listas y referencias BOE/Seguridad Social. Excluir bloque Roadmap y párrafo de lectura recomendada. |
| [Dogs & pets](https://movingtospain.com/dogs-pets-spain/) | 11 H2, 17 H3, 1 H4, 17 tablas HTML, 4 blockquotes y FAQ Kadence. | Conservar requisitos, cronología, costes, listas y FAQ. Excluir cita personal firmada del gato Shadow y CTA de alquiler. Párrafos con experiencia de clientes requieren revisión contextual. |
| [International Driving Permit](https://movingtospain.com/idp-for-driving-in-spain/) | 5 H2, 13 H3, 7 H4, 1 tabla por países y FAQ Kadence. | Conservar pasos y diferencias por país. Quitar dos párrafos independientes de recomendaciones de otros artículos. Conservar enlaces a emisores y organismos externos como referencias. |
| [Citizenship by marriage](https://movingtospain.com/spanish-citizenship-by-marriage/) | 9 H2, 18 H3, 5 H4, 3 tablas, 1 blockquote comercial y FAQ Kadence. | Excluir consulta comercial y enlaces de lectura adicional. Expandir `rowspan` en la tabla de documentos. Diferenciar referencias conceptuales enlazadas de frases cuya única función es remitir a otra guía. |
| [Best places to retire](https://movingtospain.com/best-places-to-retire-in-spain/) | 8 H2, 15 H3, 16 imágenes, 1 cita atribuida, FAQ; cero tablas HTML. | El ranking extenso está en una imagen: el alcance «solo texto» no permite recuperarlo. Registrar dependencia de medios. Metodología y relatos de investigación propia requieren revisión; no atribuirlos al futuro sitio. |

## Casos que condicionan el diseño

### Tablas

En ciudadanía, varias celdas tienen `rowspan="2"` o `rowspan="3"`. Un simple `get_text()` mezcla columnas y hace que no se sepa a qué documento corresponde una condición. Guardar una matriz de filas/celdas normalizada y mantener sus encabezados. Las celdas vacías reales siguen siendo vacías; no inventar valores.

En mascotas, la tabla visual de límites de peso de aerolíneas está dividida en siete elementos `figure.wp-block-table`: uno con encabezados y seis con una fila cada uno. Conservar orden y sección permite entenderlos; opcionalmente agrupar únicamente las tablas consecutivas compatibles. No considerar automáticamente la primera fila de cada tabla como encabezado. Los `figcaption` de tablas explican datos y pueden conservarse; los de fotografías se descartan con la imagen.

### Atribuciones y experiencia propia

No basta con quitar las etiquetas de autor. Mascotas tiene experiencias familiares, relatos de clientes, recomendaciones basadas en experiencia y una cita con nombre/enlace. Jubilación usa metodología y datos internos propios. Una regla que elimine cualquier «we» perdería gran parte del artículo; una que quite solo nombres dejaría afirmaciones indebidamente apropiables.

Decisión: excluir citas firmadas y testimonios claramente personales del texto listo para adaptación, conservar evidencia de exclusión y marcar los pasajes mixtos para revisión. No reescribirlos como hechos generales durante esta etapa. El texto original queda disponible para recuperar información útil de forma consciente.

### Promociones y referencias

Promociones observadas: `.wp-block-kadence-rowlayout` con encabezado, texto y enlace `.kb-button` hacia `/services/roadmapv2/` o `/services/spain-immigration-lawyer/`. Eliminar el contenedor comercial completo evita un encabezado huérfano. No todos los contenedores Kadence son anuncios: la regla debe verificar contenido/enlace del bloque.

Los párrafos que comienzan con `READ ALSO`, `Also Read` o `Read More` son casos claros. También hay remisiones mezcladas con información útil, por ejemplo una explicación conceptual seguida por una invitación a leer otra guía. En esos casos se puede retirar solo la oración autónoma, dejando registro; si no se puede delimitar con seguridad, marcar para revisión.

Un enlace interno dentro de una frase explicativa no exige quitar la frase: se conserva el texto ancla y se guarda el destino fuera del cuerpo limpio. Referencias a BOE, Seguridad Social, organismos de tráfico y fuentes estadísticas deben conservarse en una tabla de enlaces de procedencia. No son CTAs del sitio.

### Espacios y dependencias después de excluir

Los huecos de presentación desaparecen al reconstruir el documento con bloques consecutivos. Los huecos de información necesitan un registro distinto. Ejemplo real: un encabezado de ranking seguido por una imagen, o una frase que anuncia una cita que se excluye.

Guardar motivo, sección y posición de la omisión, junto con el bloque anterior y siguiente cuando existan. Podar encabezados sin contenido y marcar dependencias informativas o transiciones rotas. No sustituirlas por párrafos inventados, ni dejar marcadores visibles dentro del texto limpio. La futura IA recibirá estos avisos por separado para decidir si omitir la sección, reparar una transición con los datos existentes o pedir una fuente adicional.

## Límites de esta revisión

Esta inspección verifica la estructura y las decisiones de limpieza. No certifica la vigencia jurídica, fiscal o sanitaria del contenido. Hay tensiones internas visibles en algunas guías —por ejemplo entre texto y tabla sobre emisores británicos de permisos— que la extracción debe conservar y señalar para revisión editorial posterior, sin corregir hechos de forma silenciosa.
