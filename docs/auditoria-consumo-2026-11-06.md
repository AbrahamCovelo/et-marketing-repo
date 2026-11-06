# Auditoría del consumo de adaptaciones · 6 de noviembre de 2026

## Actualización después de la prueba #5

La segunda prueba de jubilación usó **GPT-6 Luna, esfuerzo high, servicio estándar**, a las 11:29–11:30 de Bogotá. Su evaluación consumió 38.857 tokens de entrada y 4.796 de salida; la redacción, 7.609 de entrada y 3.922 de salida. Total: **46.466 de entrada, 8.718 de salida y 55.184 tokens en dos solicitudes**. Incluye dos búsquedas completas, con USD 0,02 de tarifa de herramientas.

| Prueba | Artículo / modelo | Solicitudes | Coste total estimado, dólares USD |
|---|---|---:|---:|
| #1 | Barcelona · GPT-5 nano Flex | 1 | 0,000512775 |
| #2 | Renovación DNV, contenido borrado · Luna Flex | 1 | 0,001542900 |
| #3 | Renovación DNV, flujo automático · Luna Flex | 2 | 0,024632725 |
| #4 | Jubilación · Sol estándar | 2 | 0,376090000–0,386090000 |
| #5 | Jubilación · Luna estándar | 2 | 0,029465550 |
| **Total** | | **8** | **0,432243950–0,442243950** |

Son importes en dólares, no en centavos: por ejemplo, la prueba #5 cuesta aproximadamente **2,95 centavos de dólar**. Están calculados sobre el consumo real almacenado y las tarifas públicas; no equivalen a una factura consultada. El intervalo de la prueba #4 corresponde a una búsqueda cuyo cobro no se puede confirmar con la respuesta local. La tarifa Flex de #2 está confirmada en su exportación original aunque esa respuesta se borró de la base.

La ficha de cada adaptación ahora suma evaluación y redacción, muestra ambas etapas y el coste estimado total. La tabla general también agrupa por prueba y conserva el gasto de contenido borrado. Un historial que carezca de su evaluación asociada se identifica como incompleto, sin tratar el consumo ausente como gratuito.

La prueba #5 se ejecutó desde una instancia antigua del panel: su lote carecía de presupuesto aceptado y su solicitud WordPress incluía `categories: []`. Se corrigió solo la categoría de la entrada 1701 a «Jubilarse en España», conservando texto, portada y publicación. El registro de esa corrección queda en `data/adaptations/generation-5.category-correction.json`. Las categorías se eligieron manualmente para reparar esta entrada ya generada; las nuevas adaptaciones usan la clasificación dinámica de la API.

Se incorporaron restricciones persistentes para rechazar futuras reservas antiguas sin presupuesto o sin categorías permitidas y un bloqueo de instancia para evitar dos paneles nuevos simultáneos sobre la misma base. El servidor debe reiniciarse para cargar cambios de código; refrescar el navegador por sí solo no actualiza Python.

## Registro inicial, anterior a la prueba #5

La base local conserva **seis respuestas con consumo: cuatro generaciones y dos comprobaciones de aplicabilidad**. Incluye la adaptación #2, cuyo contenido se borró. El último artículo, «Retire in Spain», utilizó dos llamadas facturables: comprobación con investigación web y generación del HTML. La estimación conjunta es **USD 0,37609–0,38609**. Es compatible con aproximadamente USD 0,38, pero no demuestra que el importe del panel de OpenAI corresponda exactamente a estas llamadas.

Esta revisión usa las respuestas ya guardadas y documentación pública. No ejecuta generaciones ni consulta la factura de OpenAI. No puede descartar llamadas hechas con la misma clave fuera de este registro, ni reconstruir consumo antiguo que no se almacenó. La consulta gratuita de modelos mediante GET no genera artículos.

## Llamadas conservadas

Horas de Bogotá (UTC−5). «Caché» significa lectura de caché; «Escritura» es creación de caché. El razonamiento ya está incluido en «Salida».

| Fecha y hora | Etapa | Modelo / servicio | Entrada | Caché | Escritura | Salida | Razonamiento | Total tokens | Coste estimado USD |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 4 nov., 15:15 | Generación #1, Barcelona | gpt-5-nano-2025-08-07 / Flex | 3.543 | 0 | 0 | 2.121 | 0 | 5.664 | 0,000512775 |
| 5 nov., 15:11 | Generación #2, renovación DNV, borrada | gpt-6-luna / Flex | 5.531 | 0 | 5.528 | 4.789 | 3.103 | 10.320 | 0,001542900 |
| 5 nov., 15:20 | Comprobación #1, renovación DNV | gpt-6-luna / Flex | 34.210 | 0 | 8.401 | 4.998 | 3.390 | 39.208 | 0,023065013 |
| 5 nov., 15:21 | Generación #3, renovación DNV | gpt-6-luna / Flex | 4.868 | 0 | 4.865 | 5.054 | 4.034 | 9.922 | 0,001567713 |
| 6 nov., 10:32 | Comprobación #2, jubilación | gpt-5.6-sol / Standard | 35.984 | 0 | 10.792 | 3.974 | 990 | 39.958 | 0,254208–0,264208 |
| 6 nov., 10:33 | Generación #4, jubilación | gpt-5.6-sol / Standard | 8.445 | 0 | 8.442 | 3.983 | 511 | 12.428 | 0,121882 |
| **Total** | **6 respuestas** | | **92.581** | **0** | **38.028** | **24.919** | **12.028** | **117.500** | **0,4027784–0,4127784** |

La respuesta original de la adaptación borrada #2 ya no conserva su nivel de servicio en la base. El archivo local `data/adaptations/generation-2.json` sí conserva `service_tier: flex`. La consulta general de consumo, que solo lee la base, señala explícitamente esta incertidumbre y utiliza el nivel solicitado como supuesto.

Hay tres llamadas registradas el 5 de noviembre: una generación inicial y, después, la comprobación y segunda generación del mismo artículo. Borrar la primera adaptación no revierte su consumo. El 6 de noviembre hay dos llamadas asociadas al lote #1: `panel-1-0.assessment` y `panel-1-0.generation`. Por tanto, «generar una adaptación» ejecutaba dos solicitudes pagadas; ese coste debía hacerse visible antes de iniciar el lote. La normalización local del HTML no figura como otra llamada en esta base.

## Cómo se calcula

Tarifas públicas verificadas el 6 de noviembre de 2026, expresadas en USD por millón de tokens. Se conservan en `config/openai-pricing-2026-11-06.json`, separadas del código. Las tarifas de Sol son promocionales hasta el 30 de noviembre. La referencia incluye los modelos auditados; los modelos y niveles de servicio sin tarifa documentada se muestran sin estimación, nunca como gratuitos. [Precios oficiales](https://developers.openai.com/api/docs/pricing), [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol), [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).

| Modelo / servicio | Entrada ordinaria | Lectura de caché | Escritura 30 min. | Salida |
|---|---:|---:|---:|---:|
| GPT-5.6 Sol / Standard | 4 | 0,4 | 5 | 20 |
| GPT-6 Luna / Standard | 0,1 | 0,01 | 0,125 | 0,5 |
| GPT-6 Luna / Flex | 0,05 | 0,005 | 0,0625 | 0,25 |
| GPT-5 nano / Flex | 0,025 | 0,0025 | No tiene recargo de escritura | 0,20 |

La entrada se divide en categorías excluyentes:

```text
entrada_ordinaria = input_tokens - cached_tokens - cache_write_tokens
coste_tokens = (
  entrada_ordinaria × tarifa_entrada
  + cached_tokens × tarifa_lectura_cache
  + escrituras_30m × tarifa_escritura_30m
  + escrituras_12h × tarifa_escritura_12h
  + output_tokens × tarifa_salida
) / 1.000.000
```

La escritura de caché tiene su tarifa total propia: no se suma otra vez el precio normal de entrada para esos mismos tokens. Las escrituras de 30 minutos y 12 horas tampoco se superponen. Las respuestas históricas no traen ese desglose y las solicitudes no fijaron otra duración, por lo que se aplica el valor predeterminado de 30 minutos. Ninguna de las seis respuestas reportó lecturas de caché. [Contadores y cálculo de caché](https://developers.openai.com/api/docs/guides/prompt-caching).

`reasoning_tokens` es parte de `output_tokens`: se muestra para explicar el consumo, sin facturarlo dos veces. El contenido recuperado por búsquedas forma parte de la entrada del modelo. Además, cada acción de búsqueda tiene una tarifa de USD 0,01, que no se reduce al usar Flex. Abrir una página o buscar dentro de ella no se cuenta como una nueva acción `search`. [Funcionamiento de búsqueda web](https://developers.openai.com/api/docs/guides/tools-web-search), [precios de herramientas](https://developers.openai.com/api/docs/pricing).

La comprobación #1 contiene dos acciones `search` completas. La comprobación #2 también contiene dos completas, además de una en estado `searching`. La respuesta local no permite confirmar la facturación de esa última acción; por eso se muestra un intervalo de USD 0,01. Los intervalos no incluyen impuestos, créditos, descuentos particulares ni diferencias de facturación que solo puede confirmar OpenAI.

## Comparación para 300 adaptaciones

Se reutilizan exactamente los tokens y las búsquedas del artículo de jubilación y se aplican otras tarifas. Es una comparación, no una predicción de calidad, longitud ni un límite garantizado. Un modelo o artículo distinto puede consumir otra cantidad de razonamiento, salida e investigación.

| Modelo / servicio | Una adaptación, ambas etapas | 300 adaptaciones |
|---|---:|---:|
| GPT-5.6 Sol / Standard | USD 0,37609–0,38609 | **USD 112,827–115,827** |
| GPT-6 Luna / Standard | USD 0,02890225–0,03890225 | **USD 8,670675–11,670675** |
| GPT-6 Luna / Flex | USD 0,024451125–0,034451125 | **USD 7,3353375–10,3353375** |

Con esta muestra, Luna encaja en la referencia de USD 15 para 300 adaptaciones; Sol Standard no. El factor principal es la tarifa del modelo y del servicio: la generación de jubilación se hizo con Sol Standard, mientras las pruebas de renovación usaron Luna Flex. La comprobación web es la parte más costosa de la última adaptación; comparar solo los tokens del HTML oculta esa etapa. Las búsquedas supondrían USD 6–9 de esos lotes de 300 incluso con un modelo más económico.

Una adaptación equivale a **un artículo para un destino**. Adaptar 300 artículos a los tres destinos son 900 adaptaciones. Estos escenarios no incluyen repeticiones, nuevas pruebas ni el gasto ya registrado.

## Consulta local

`src/generation/usage.py` abre SQLite con `mode=ro` y `query_only`, sin inicializar tablas, conectarse a OpenAI ni modificar respuestas. Devuelve las seis métricas solicitadas, el coste de las dos etapas, búsquedas, nivel de servicio solicitado o confirmado y los supuestos de cálculo. Incluye registros borrados para no hacer desaparecer gasto real de los totales, sin devolver sus textos. No devuelve credenciales, solicitudes completas ni respuestas completas.

`project_adaptation_cost` toma la última pareja completa de comprobación y generación como referencia. Solo asocia etapas mediante su identificador de ejecución compartido, nunca por coincidir el artículo. Si no hay una pareja válida o una tarifa documentada, informa que no hay estimación.
