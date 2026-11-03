# Auditoría independiente de extracción y verificación de correcciones

## Entrega final: limpiador 1.2.0

La revisión final añade la exclusión de las dos FAQ que venden el servicio propio de scouting. Se conserva la pregunta informativa que compara viajes grupales y personalizados: nombrar un servicio no convierte una pregunta en publicidad. El artículo conserva **13 FAQ informativas**, frente a 15 en el original. Hay una regresión específica que verifica ambos comportamientos y la conservación de preguntas formuladas desde la perspectiva del lector.

La suite completa pasa: **61 pruebas y 8 subpruebas**. El cierre de la base y los comandos de uso se resumen en [estado de la etapa 1](estado-etapa1.md). Los números de versión de las tablas siguientes son evidencia histórica de las comprobaciones, no los identificadores de las versiones actuales.

## Cierre dirigido de la versión 1.1.0

Verificación de solo lectura después de la ejecución `reclean` 3: **107 artículos actuales con limpiador 1.1.0; 107 procesados y cero errores**. Se revisaron exclusivamente los casos previamente reportados.

| Caso | Resultado comprobado |
|---|---|
| Extremadura, v123 | Importes de `Programme I/II` completos; cita normativa y lista de requisitos conservadas; 1/1 tabla y 9/9 FAQ. |
| Mascotas, v119 | 17/17 tablas y 9/9 FAQ, incluidos los cronogramas. |
| Automóvil, v115 | Recuperado requisito de pagar matriculación; eliminada lista de lecturas recomendadas; apartado de fuentes conservado con 25 referencias externas; 4/4 tablas. |
| Ciudadanía por descendencia, v132 | 7/7 tablas; conservada la celda `Annex form (I–VI)`. |
| Comparación fiscal regional, v133 | 11/11 tablas; recuperada la tabla de herencias con grupos I y II. |
| Metodología y fiscalidad US, v114/v125 | No quedan las menciones promocionales comprobadas al socio fiscal de Moving to Spain ni a MyExpatTaxes. |
| Ciudadanía, v200 | Nombres de países separados correctamente; 4/4 tablas. |
| Testimonios atribuidos | Ausentes los ejemplos reportados de Alastair, Matthew, Sean, Leigh, David y la experiencia personal de la entrevista estadounidense. |
| Scouting, v168 | Recuperadas 15/15 FAQ con respuesta directa. La entrega final excluye las dos preguntas explícitas sobre el servicio comercial y conserva las otras 13. |

Dos tablas de scouting permanecen en cuarentena con evento `table_context_loss` y `requires_review=true`; el original se conserva íntegro. Una contiene preguntas hipotéticas en primera persona y la otra mezcla comparación y oferta comercial. La cita de un arrendador en una lista de alertas también permanece en cuarentena conservadora. Estas limitaciones son trazables y no se sustituyeron por texto inventado.

Siguen requiriendo revisión editorial las inconsistencias originales de IPREM, la respuesta de pasaporte aparentemente desubicada, las referencias a herramientas inexistentes en los sitios destino y la vigencia de datos legales o fiscales. La extracción no los certifica ni los corrige por inferencia.

## Historial de hallazgos iniciales

Revisión de solo lectura del 3 de noviembre de 2026, comparando HTML original, bloques SQLite y exportación para IA. Esta es la auditoría **previa a las correcciones** comunicadas al responsable del limpiador. Las claves corresponden a las versiones iniciales indicadas; una nueva limpieza puede reemplazarlas como versión actual.

## Conservación de estructura comprobada

| Guía | Versión auditada | Tablas original / almacenado | FAQ original / almacenado | Referencias externas conservadas |
|---|---:|---:|---:|---:|
| Metodología de patrimonio | 7 | 3 / 3 | 0 / 0 | 0: las fuentes institucionales son una lista textual sin enlaces |
| Importación de automóvil | 8 | 4 / 4 | 9 / 9 | 24 |
| Ayuda Extremadura | 16 | 1 / 1 | 9 / 9 | 8 |
| Pasaporte español | 17 | 0 / 0 | 5 / 5 | 3 |
| Impuestos estadounidenses | 18 | 0 / 0 | 4 / 4 | 7 |

La exportación mantiene bloques, relaciones pregunta/respuesta, matrices de celdas y fuentes. Excluye metadatos de autor/revisor y texto original de omisiones; los huecos contienen motivos y referencias a bloques vecinos. Las URL internas y comerciales no salen en `references`. La cita oficial enlazada a la Junta de Extremadura y la advertencia técnica sobre automóviles nuevos se conservaron, mientras las citas personales firmadas y los paneles comerciales principales fueron retirados.

## Hallazgos comunicados para corregir

| Prioridad | Guía y bloque | Evidencia | Corrección conservadora |
|---|---|---|---|
| Alta | Extremadura v16, tabla `b000179`; evento posición 211 | La fila de importes perdió la frase que contiene los pagos iniciales y de fase posterior; fue clasificada como primera persona por la numeración `Programme I`. | Reconocer numeración romana en programas, capítulos y anexos; conservar importes íntegros. |
| Alta | Extremadura v16, evento posición 78 | Se excluyó una cita normativa sobre requisitos de `Program II`, incluida su lista de condiciones. | No considerar toda cita sin URL como cita editorial; distinguir normativa de experiencia personal. |
| Alta | Automóvil v8, evento posición 106 | Se retiró el requisito de pagar el impuesto de matriculación por incluir una referencia a los tramos mostrados más abajo. | Distinguir referencias a la misma sección de invitaciones a otro artículo. |
| Media | Metodología v7, `b000057`, `b000078`, `b000126`, `b000213`, `b000250` y otros | Persisten invitaciones a consultar socios y afirmaciones de revisión por socios. | Retirar la oración promocional o credencial; conservar los supuestos y limitaciones que la preceden. |
| Media | Impuestos US v18, `b000177`, `b000184`, `b000192` | Persisten recomendaciones al afiliado MyExpatTaxes mezcladas con contenido informativo. | Excluir las frases promocionales; conservar explicación general de presentación electrónica. |
| Media | Automóvil v8, `b000712` y `b000714` | Persiste la introducción de lectura recomendada y la lista de seis guías internas. | Quitar introducción/lista y encabezado vacío; conservar el siguiente apartado de fuentes externas. |
| Media | Barcelona v1 `b000282`; residencia fiscal v4 `b000072` | Referencia independiente a otras guías y presentación de un revisor, respectivamente. | Excluir esos bloques completos. |
| Media | Automóvil v8 `b000115`, `b000547`, `b000568` | Hay uniones de palabras alrededor de etiquetas en línea: `payIGICinstead`, `pink)and`, `aleft-hand-drive`. | Normalizar espacios entre nodos sin alterar cifras, puntuación o jerarquía. |

## Revisión editorial posterior

En pasaporte, `b000169` mantiene una pregunta sobre identidad digital cuya respuesta enumera plazos de renovación de pasaportes. El problema ya existe en la fuente; requiere una advertencia o revisión, no una respuesta inventada. La metodología depende de una calculadora del sitio original y el artículo fiscal estadounidense tiene un alcance nacional que no se puede trasladar a Canadá o Colombia mediante simple cambio de vocabulario.

El indicador general `needs_editorial_review` está presente. Conviene mantener advertencias específicas para información temporal, dependencias de herramientas y transiciones cuyo antecedente se haya eliminado. La limpieza no constituye una comprobación de exactitud jurídica o fiscal.

## Ampliación al catálogo completo, antes de volver a limpiar

Se examinaron los 89 artículos posteriores a la muestra inicial mediante búsqueda de patrones y comparación de tablas contra los snapshots originales. Además se leyó completa la salida de [transferencias](https://movingtospain.com/transfer-money-to-spain/), [IPREM](https://movingtospain.com/iprem-spain/), [ciudadanía](https://movingtospain.com/spanish-citizenship/) y [alquiler](https://movingtospain.com/renting-property-spain/), contrastando las omisiones con el contenido fuente. Los identificadores siguientes corresponden a la primera importación del catálogo.

- **Atribución narrativa:** aparecen nombres antes o después de las citas, fuera de `cite` y sin enlace a autor: presentaciones de Alastair en `private-health-insurance-spain` (`b000309`, `b000325`), una cita que termina con una atribución a Matthew en `living-in-mallorca` (`b000577`), otra a Sean en `living-in-malaga` (`b000415`) y la recomendación de David en `renting-property-spain` (`b000141`). Se comunicó el patrón para distinguir testimonios de citas institucionales.
- **Tablas normativas retiradas por numeración romana:** la tabla de documentos de `spanish-citizenship-by-descent` contiene `Annex form (I–VI)`; la tabla de herencias de `spain-regional-tax-comparison` contiene `Group I & II`. La regla de primera persona provocó la cuarentena de tablas completas (7→6 y 11→10 tablas respectivamente). Es la misma familia de error detectada en Extremadura, ahora con más variantes.
- **Primera persona de ejemplo:** en `scouting-trip-to-spain`, una tabla contiene preguntas que debe hacerse el lector, introducidas por `Ask:`. En alquiler, una lista de señales de alerta cita la postura de un arrendador. Esas frases no son experiencias del autor y requieren conservar su contexto, no aplicar mecánicamente un filtro de pronombres.
- **FAQ con texto directo:** `scouting-trip-to-spain` tiene 15 elementos `.rank-math-list-item`; sus respuestas son texto directo dentro de `.rank-math-answer`, sin párrafos. La primera extracción guardó cero FAQ y registró preguntas sin respuesta. Se informó el adaptador específico necesario. En las otras 37 guías con Rank Math, las diferencias de conteo son pequeñas y corresponden a respuestas excluidas por las reglas; no se observó el mismo vaciado completo.
- **Saltos de línea en tablas:** la tabla de países de `spanish-citizenship` (`b000140`) contiene varios países separados por `<br>`; la salida inicial unía sus nombres. Debe conservarse al menos el separador textual. Las celdas vacías y repetidas por `colspan` de otras tablas no son por sí mismas pérdida de datos.
- **Promoción residual:** `barcelona-neighborhoods-guide` (`b000468`) inicia una referencia a lecturas adicionales en mitad de una oración. Alquiler conserva una invitación al directorio inmobiliario original. Transferencias conserva un encabezado comercial y un pronombre sin antecedente después de retirar la presentación de su corredor. Se recomendó tratar el bloque promocional completo y marcar dependencias, sin inventar transiciones.

La inspección completa también confirmó inconsistencias ya presentes en la fuente: IPREM ofrece resultados distintos para la misma multiplicación entre cuerpo y FAQ. Se deben mantener fechas, trazabilidad y revisión editorial; el extractor no debe corregir cifras mediante inferencia ni presentar la fuente como verificada.
