# Revisión de guías: muestra A

Revisión: 3 de noviembre de 2026. Se leyó el cuerpo completo de estas cinco guías y se inspeccionó su DOM, sobre las descargas directas guardadas en `data/research/`. Las cifras de abajo describen la estructura de origen antes de limpiar; no certifican la vigencia de sus afirmaciones legales o fiscales.

| Guía leída | Estructura observada | Decisión de extracción |
|---|---|---|
| [Renovación DNV](https://movingtospain.com/avoid-spain-digital-nomad-visa-renewal-problems/) | 8 H2, 6 H3, 1 tabla, 5 citas, 6 preguntas Rank Math | Excluir citas personales completas, experiencias autobiográficas y promoción. La tabla también contiene primera persona en una celda. Conservar procedimientos impersonales y FAQ sin convertir testimonios en hechos generales. |
| [DNV frente a NLV](https://movingtospain.com/digital-nomad-visa-vs-non-lucrative-visa-spain/) | 14 H2, 14 H3, 2 tablas comparativas, 3 citas, 7 preguntas Rank Math | Conservar tablas con filas/celdas; retirar citas de colaboradores y llamadas a consulta. Hay navegación mezclada al final del mismo párrafo que contiene información útil. |
| [Residencia fiscal](https://movingtospain.com/becoming-a-tax-resident-in-spain/) | 16 H2, 8 H3, 5 citas, widget interactivo, 6 preguntas Kadence | Excluir widget completo y su título huérfano. Retirar frases que presentan la cita eliminada. Conservar enlaces oficiales como referencias separadas y avisos editoriales generales. |
| [Dónde vivir](https://movingtospain.com/where-to-live-in-spain/) | 20 H2, 78 H3, 133 H4, 17 tablas HTML, 158 UL, 2 OL, 8 preguntas Kadence | Preservar jerarquía y tablas, listas anidadas y preguntas; excluir mapa, fotos y sus leyendas, read-more y promociones. Hay experiencias del autor dentro de párrafos, listas y tablas. |
| [Cómo mudarse](https://movingtospain.com/how-to-move-to-spain/) | 19 H2, 37 H3, 2 tablas, 10 citas, 18 preguntas Kadence | Excluir testimonios y secciones completas de paquetes/servicios. No basta retirar botones: varias secciones de venta son H2 + párrafos normales. Las FAQ contienen respuestas útiles con una invitación final a otra guía. |

## Selectores comprobados

- Cuerpo principal: `article .entry-content.single-content`. La página tiene otras `.entry-content` pertenecientes a tarjetas relacionadas; el selector genérico contamina la extracción.
- Título visible: un `h1.wp-block-kadence-advancedheading`, fuera del cuerpo. El H1 no tiene que llevar la clase `entry-title`.
- Fechas: `meta[property="article:published_time"]` y `meta[property="article:modified_time"]` ofrecen ISO 8601 con zona horaria. Guardar aparte de la fecha de extracción.
- Categorías, autores y revisores: las clases del `article` contienen `category-*`, `writers-*`, `reviewers-*`; son metadatos de procedencia, no texto para reutilizar ni una acreditación trasladable a otro sitio.
- Índice: `nav.wp-block-kadence-tableofcontents` / `.kb-table-of-content-nav`; eliminar entero, porque duplica encabezados.
- Citas: `blockquote.wp-block-quote`. Algunas tienen `cite`; otras tienen atribución en párrafo previo o dentro del texto. Hay citas sin firma interna pero claramente introducidas por el párrafo anterior.
- CTA visual: `.wp-block-kadence-rowlayout` con enlaces/botones de consulta, seguros o paquetes. No excluir todos los rowlayout indiscriminadamente; verificar intención comercial y contenido.
- FAQ Rank Math: `#rank-math-faq .rank-math-list-item`, pregunta `.rank-math-question`, respuesta `.rank-math-answer`.
- FAQ Kadence: `.kt-accordion-pane`, pregunta `.kt-blocks-accordion-title`, respuesta `.kt-accordion-panel-inner`. Las preguntas están dentro de un `button`, y las respuestas llevan clases de panel oculto. Eliminar todos los botones o todo lo oculto antes de procesar destruye las FAQ.
- Tablas: `figure.wp-block-table > table`; la leyenda `figcaption` puede ser información útil, promoción o atribución: se clasifica aparte.
- Widget fiscal: `#mts-residency-check`; tiene preguntas, inputs, botones y resultados. Hay un `<title>` incrustado como hermano dentro del cuerpo. No constituye texto del artículo.
- Mapa: `.mts-spain-map`, con `.mts-map-desktop` y SVG. Sus textos y nombres se repiten y son navegación; eliminar el contenedor.

## Casos que condicionan el diseño

1. **Los hechos y las impurezas se mezclan.** El párrafo de entrada de la comparación DNV/NLV acaba en una navegación «Jump to». Las respuestas FAQ y varios párrafos fiscales acaban con una invitación a leer otra guía. La unidad de decisión debe permitir retirar una frase y conservar las anteriores. El texto de ancla de un enlace contextual se conserva; no se elimina la oración solo porque contenga un enlace interno.
2. **Eliminar citas deja referencias colgantes.** En residencia fiscal hay párrafos que presentan a un experto y terminan en dos puntos justo antes de una cita. La frase introductoria también debe salir o quedar en revisión. No inventar un reemplazo.
3. **No todo lo autobiográfico está en blockquote.** Hay experiencias del autor en encabezados, párrafos, elementos de listas, celdas y respuestas. En DNV renewal gran parte del artículo es un caso personal. Excluir de la salida reutilizable y conservar en auditoría es más seguro que generalizar automáticamente su caso.
4. **La autoría también aparece en tercera persona.** La guía de mudanza cuenta historias de Alastair, Amanda, Rhys, Shannon, Teresa y clientes. Una búsqueda solo de «I/we/my/our» no basta. Los nombres, firmas, enlaces de autores/revisores y contexto testimonial ayudan; los casos ambiguos deben poder quedar pendientes.
5. **Un encabezado puede ser enteramente comercial.** En cómo mudarse, los apartados del Decide Package, de elección entre paquetes de relocation y de Membership son H2 normales seguidos por párrafos. Retirar únicamente links/botones deja el anuncio y un heading sin contenido útil.
6. **No asumir que todo figure es imagen.** Las comparaciones de visas, cronogramas y ciudades son tablas esenciales. Extraer cada celda por separado evita concatenar importes con nombres de columna.
7. **Tablas fragmentadas en la fuente.** La respuesta sobre el tamaño de España en dónde vivir contiene ocho tablas consecutivas de una fila cada una: una de encabezado y siete filas de distancias. Conservar orden y relación con la FAQ; no asignar a cada primera fila el significado de encabezado automáticamente. La muestra tiene nueve tablas regionales más estos ocho fragmentos.
8. **Preservar alcance regional.** Hay ejemplos sobre EE. UU., Reino Unido, UE y Canadá en una misma guía. Guardar esos textos y su sección permite que la futura adaptación distinga un ejemplo estadounidense de una regla general. No convertir monedas ni nacionalidades en esta etapa.
9. **La limpieza no verifica afirmaciones.** La muestra incluye diferencias entre artículos en la formulación del umbral fiscal. Guardar fuente, fechas y revisión evita que una extracción limpia parezca una conciliación legal ya hecha.

## Cómo evitar huecos sin inventar texto

La versión limpia se reconstruye con los bloques conservados en su orden. No se insertan espacios reservados dentro del artículo para cada cita, foto o CTA eliminada. Se eliminan títulos vacíos. En un registro separado se conserva qué bloque/frase salió, por qué, a qué sección pertenecía y sus vecinos. Los vacíos de sentido —por ejemplo, una pregunta sin respuesta después de retirar un testimonio— se marcan como pendientes de revisión o adaptación. La futura IA recibe texto utilizable y avisos estructurados, sin recibir el testimonio descartado como si fuera material aprobado.
