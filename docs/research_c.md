# Inspección de guías: muestra C

Inspección del 3 de noviembre de 2026. Se leyó el cuerpo completo de cinco guías y se inspeccionó el HTML descargado en `data/research/`. Las observaciones describen la estructura y las necesidades de extracción; no verifican la vigencia de afirmaciones fiscales, legales o migratorias.

## Muestra y hallazgos

| Guía | Estructura observada dentro del cuerpo | Decisión de extracción |
|---|---|---|
| [Metodología de la calculadora de patrimonio](https://movingtospain.com/methodology-spain-wealth-tax-calculator/) | 16 H2; 3 tablas; 42 párrafos; sin FAQ. Las tablas están dentro de `figure.wp-block-table`. | Conservar tablas, unidades, años, supuestos y limitaciones. Separar referencias institucionales de frases sobre revisión por socios. Los enlaces de consulta se mezclan con texto útil: limpiar por oración o enviar a revisión, no borrar ciegamente el párrafo. |
| [Enviar un automóvil a España](https://movingtospain.com/ship-a-car-to-spain/) | 15 H2, 26 H3, 2 H4; 4 tablas; 4 citas; listas y FAQ con encabezados normales. | Una cita es una anécdota firmada; otra es una advertencia técnica útil; otra anuncia una calculadora futura. Cada una exige una decisión distinta. El tramo final contiene promoción, una lista de otras guías y después fuentes externas: retirar la promoción y conservar las fuentes posteriores. |
| [Ayuda para nómadas de Extremadura](https://movingtospain.com/extremadura-digital-nomad-grant/) | 10 H2, 1 H3, 2 H4; tabla comparativa HTML; 3 citas; FAQ de acordeón; listas anidadas. | Conservar avisos de suspensión, fecha de comprobación y requisitos. Separar experiencia de viaje, testimonio personal y voz institucional. Una cita oficial enlaza a `juntaex.es`; otra contiene una lista de requisitos. La pregunta y respuesta del acordeón necesitan mantenerse juntas. |
| [Potencia del pasaporte español](https://movingtospain.com/spanish-passport-power/) | 6 H2, 5 H3, 4 H4; una cita sin firma; FAQ de acordeón; sin tablas. | Detectar primera persona aunque no haya firma. Quitar la referencia independiente a otra guía y el panel comercial. Mantener previsiones como previsiones, nunca convertirlas en hechos. Hay una respuesta sobre identidad digital cuyo contenido parece corresponder a renovación del pasaporte: advertencia editorial, no reparación automática. |
| [Impuestos estadounidenses para expatriados](https://movingtospain.com/us-expat-taxes-in-spain/) | 12 H2, 8 H3, 7 H4; 4 citas; FAQ de acordeón; sin tablas. | Excluir declaración comercial, notas firmadas del afiliado y CTA. Los enlaces `/go/myexpattaxes/` llevan `rel="nofollow sponsored"`. Conservar IRS, FinCEN, convenio y direcciones postales como referencias. El cuerpo mezcla cifras de distintos ejercicios: almacenar sus años y marcar revisión antes de regionalizar. Su alcance estadounidense requiere una decisión editorial posterior para Canadá/Colombia. |

## Selectores comprobados

- Cuerpo: `article.single-entry .entry-content.single-content`. Hay más elementos `article` y `.entry-content` en las tarjetas relacionadas; elegir el primero sin contexto es frágil.
- Índice automático: `nav.wp-block-kadence-tableofcontents`, también `.kb-table-of-content-nav`. Se elimina entero para no repetir los encabezados.
- Tablas: `table`, incluidos `figure.wp-block-table`. No eliminar todos los `figure` porque varios contienen datos textuales.
- Citas: `blockquote.wp-block-quote`. El selector identifica estructura, **no** el motivo para excluir. Una cita de autor puede incluir `a[data-type="writers"]` y un enlace `/writers/`; una cita institucional puede contener un enlace gubernamental o una lista.
- Pregunta FAQ: `.kt-accordion-pane .kt-blocks-accordion-title`; respuesta en el mismo `.kt-accordion-pane`, dentro de `.kt-accordion-panel` / `.kt-accordion-panel-inner`. El título es un `span` dentro de un `button`: eliminar todos los botones antes de extraer perdería preguntas.
- CTA observados: `.wp-block-kadence-rowlayout` que contiene `.ad-arrow` y un botón `.wp-block-kadence-singlebtn` hacia `/services/` o un afiliado. La clase de filas también sirve para contenido normal: excluir por combinación de señales, no por la clase general.
- La cabecera H1 y la información editorial quedan fuera del cuerpo. Fechas de comentarios usan `time[datetime]`: no usar el primer `time` global como fecha del artículo.

## Qué guardar para evitar huecos sin inventar contenido

Guardar bloques ordenados con tipo, jerarquía, texto original, decisión, motivo y texto limpio. Registrar omisiones en una colección separada con posición y sección. Si una sección queda sin texto, su encabezado también se excluye de la versión de lectura; el historial conserva ambos.

Una omisión comercial normalmente no necesita reemplazo. Una transición rota, primera persona, tabla que depende de una calculadora inexistente o párrafo que combina información y afiliación necesita una advertencia `needs_review` vinculada a bloques vecinos. El texto limpio no debe contener marcadores de relleno ni información generada. El futuro proceso de adaptación recibirá cuerpo estructurado, fuentes, fechas y advertencias; podrá reconstruir la continuidad usando solamente contenido conservado.

Conviene distinguir `editorial_attribution`, `personal_experience`, `commercial_cta`, `article_referral`, `navigation`, `media` y `unsupported_context`. Un enlace informativo dentro de una oración mantiene su texto visible; la URL interna se retira del cuerpo y queda en el inventario de enlaces. Las URL institucionales permanecen disponibles como evidencia. No quitar indiscriminadamente apellidos o nombres de organismos: se perderían autoridades citadas y referencias útiles.

## Límites de esta inspección

El índice cambió entre las respuestas de caché y la consulta actual: la página 1 incluye Barcelona y la metodología pasó a la página 2. Descubrir enlaces desde cada HTML y registrar fecha evita depender de posiciones fijas. Algunas peticiones HTTP iniciales devolvieron una página de verificación con código 403. Esa respuesta debe registrarse como error de descarga, nunca como artículo ni como versión vacía válida.
