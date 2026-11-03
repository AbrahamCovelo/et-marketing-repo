# Estado de la etapa 1

Entrega del 3 de noviembre de 2026. Se implementaron descubrimiento, extracción textual, limpieza auditable, almacenamiento SQLite, exportación estructurada y reprocesamiento sin red. No se ejecutaron llamadas a una API generativa ni publicaciones en WordPress.

## Corpus y validación

- Lectura completa e inspección inicial de **15 guías**, documentadas con sus URL en [A](research_a.md), [B](research_b.md) y [C](research_c.md). La muestra descargada contiene 18 artículos.
- El recorrido de las 18 páginas de `/guides/` encontró **108 URL**, que corresponden a **107 artículos canónicos**. La guía con URL `how-to-move-to-spain-from-the-us-in-6-months-or-less` redirige a `moving-to-spain-from-the-usa` y no crea un artículo duplicado.
- Los **107 artículos** están guardados en `data/source_articles.db`, con sus respaldos HTML comprimidos en `data/raw/`. La versión final del limpiador es **1.2.0**; se mantienen las extracciones anteriores como historial.
- La carga y los reprocesamientos terminaron sin errores. Se comprobaron integridad SQLite, claves foráneas, repetición sin duplicar versiones y omisión de las 108 URL ya almacenadas.
- **61 pruebas aprobadas**, además de 8 subpruebas: limpieza, tablas, FAQ, enlaces, exclusiones, paginación, descargas, errores aislados, exportación, snapshots, versiones y flujo completo de consola.
- La auditoría adicional comparó casos de artículos antiguos y descubrió otro formato de FAQ. El [informe de validación](validation-review.md) conserva los hallazgos y su resolución.

## Qué recibe la futura API

El ejemplo `data/exports/dnv-renewal.json` corresponde al artículo de renovación DNV que motivó la consulta. Contiene metadatos de origen, bloques de texto en orden, relaciones de sección, referencias externas y avisos de omisiones. Su versión de lectura está en `data/exports/dnv-renewal.txt`.

Las firmas, promociones y testimonios descartados no se envían dentro de ese JSON. Permanecen en la auditoría de la base y en el respaldo, para poder recuperar información cuando una exclusión resulte demasiado conservadora.

No hay rellenos inventados ni espacios reservados visibles en el texto. Las omisiones informativas señalan sus bloques vecinos. Cuando se retira un encabezado, se conserva el límite de sección mediante identificadores y avisos, sin trasladar el título descartado a la exportación.

## Revisión pendiente para la siguiente etapa

**101 artículos tienen alguna señal de revisión editorial.** Esta cifra no equivale a 101 fallos de descarga: incluye testimonios retirados, encabezados sin contenido, referencias a herramientas, tablas puestas en cuarentena y datos disponibles solo en medios. Están almacenados para que esas decisiones sean trazables antes de regionalizar.

Casos concretos: el ranking de jubilación existe en una imagen; dos tablas de scouting quedan fuera por mezclar experiencia o servicio propio; algunas respuestas y cifras presentan inconsistencias en el original. El limpiador no inventa el contenido de las imágenes ni verifica o corrige esas afirmaciones.

Para trabajar con los datos, consulte el [diseño y las consultas SQL](diseno-extraccion.md) y los [comandos de instalación y uso](../README.md).
