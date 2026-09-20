# FASE 2F.8.1 — Persistent validated marker catalog + automatic bootstrapping

`PATTERN_VALIDATION_STATUS = ENGINEERING` · `PRODUCTION_READY = NO` · `STOP_GATE = ACTIVE`

`FASE_2F_8_1_PERSISTENT_MARKER_CATALOG = PASS` —
`WARM_TOTAL_FABRIC_METERS = 167.209` (**exact match to the known-feasible
reference**, `KNOWN_REFERENCE_RECOVERY` tier). Ver el bloque de veredictos
completo en la Sección 18 y `artifacts/phase2f8_1/summary.json`.

## 1. Resultado ejecutivo

2F.8 dejó una brecha de 58.656 m entre lo que el sistema automatizado
encuentra en frío (225.865 m / 53.73%) y lo que tres fases experimentales
dedicadas habían demostrado geométricamente posible (167.209 m / 72.58%).
Antes de escribir código, se diagnosticó la causa real (Sección 3) en vez de
asumirla, y esta fase cierra la brecha **por completo**: la corrida "warm"
(catálogo persistente poblado con la evidencia de 2F.7H re-validada) alcanza
**167.209 cm / 72.578893%, 4 tendidos, 4 diseños, 0 faltante, 0
sobreproducción** — un match exacto al mejor resultado conocido, usando
exactamente el mismo mecanismo genérico de reutilización de catálogo que se
aplicaría a cualquier pedido futuro, sin componer reglas especiales por
composición (Sección 2 del encargo, cumplida explícitamente).

El experimento de acumulación (Sección 10) también confirma, con números
medidos, que corridas sucesivas sobre el mismo catálogo persistente mejoran
automáticamente **incluso sin importar evidencia externa**: 225.865 m →
176.509 m (solo por reutilizar lo que la propia corrida 1 descubrió) →
167.209 m (tras enriquecer el catálogo).

## 2. Diagnóstico factual (Secciones 8/9) — antes de escribir código

Se corrió el benchmark de 216 prendas en frío y se inspeccionó el `/audit`
real (`artifacts/phase2f8_1/discovery-trace.json`):

| Composición | ¿Generada? | Ronda | ¿Prefiltrada? | ¿Evaluada geométricamente? | Razón de exclusión |
| --- | --- | --- | --- | --- | --- |
| M×2+L×1 | SÍ | 1 | No | **No** | `candidate_funnel_top_k` |
| S×3+XXL×5 | SÍ | 1 | No | **No** | `candidate_funnel_top_k` |
| XS×3+XL×3 | **No** | — | — | No | nunca generada (ver abajo) |
| XL×1 | SÍ | 1 | No | Sí | — |

**Causa raíz #1 (M2+L1, S3+XXL5): ranking, no generación.** Ambas
composiciones SÍ las genera `CandidateCompositionGenerator`, pero
`select_geometry_top_k` (`domain/candidate_funnel.py`) las excluye antes de
llegar a geometría — con `geometry_top_k_initial=20` y solo 8 de 48
candidatos de la ronda 1 realmente evaluados, una composición de 3 prendas
(M2+L1) o 8 prendas (S3+XXL5) pierde contra candidatos de mayor cobertura
absoluta cuando el residual todavía es el pedido completo de 216 prendas.

**Causa raíz #2 (XS3+XL3): generación, no ranking.** Re-derivando la
aritmética exacta: XS=30/XL=35 tiene GCD=5, así que la única composición de
razón exacta (cero residuo) para ese par de tallas es **XS×6+XL×7** (ya
sabido, desde 2F.7H-2/3, geométricamente demasiado ajustada:
`L90=771cm > 700cm`). XS×3+XL×3 solo se descubrió en 2F.7H-3 porque un
humano enumeró manualmente cantidades pequeñas de XS/XL en el script de esa
fase — no es una salida natural de la lógica proporcional/residual/de razón
del generador genérico dentro de 2 rondas.

**Esto implica que cargar un catálogo persistente de markers ya validados
resuelve ambas causas a la vez**, sin tocar `candidate_generator.py` ni
`candidate_funnel.py`: un marker precargado no compite por un cupo de
`geometry_top_k` (no es un "candidato" que rankear, es conocimiento ya
certificado), y no necesita que el generador genérico lo "descubra" de
nuevo.

## 3. Contrato de compatibilidad (Secciones 3/4)

Más barato de lo esperado: `fabric.content_hash`
(`infrastructure/seed_data.py:177-189`) ya incorpora `usable_width_cm`,
`piece_clearance_cm`, `margins_cm`, `fabric_directionality`,
`marker_direction_policy`, `lay_face_mode`; `table.content_hash` incorpora
`usable_length_cm`/`max_layers`. Por lo tanto **`(pattern_hash, fabric_hash,
table_hash, composition)` ya es un contrato de compatibilidad
suficiente y fail-closed** para ancho/largo/holgura/orientación/
márgenes/capas — no se necesitan columnas nuevas para corrección, solo un
índice aditivo para performance de consulta. El `content_key` existente
(usado para dedup dentro de una misma corrida en `_evaluate_candidate`)
es la clave equivocada para reutilización entre órdenes distintas: también
hashea `seed`/`geometry_budget`/`engine`, irrelevantes para si la geometría
de un marker es confiable en una corrida distinta. El bootstrap usa su
propia consulta, más amplia, ignorando esos campos.

Ambigüedad = no reutilizar (fail closed): confirmado por test
(`test_incompatible_pattern_hash_not_reused`,
`test_incompatible_fabric_hash_not_reused`,
`test_incompatible_table_hash_not_reused`,
`test_unvalidated_marker_never_reused_fail_closed`).

## 4. Algoritmo de bootstrap (Sección 6)

`PlanningCoordinator._bootstrap_compatible_markers(pattern_hash, fabric_hash,
table_hash)` consulta `MarkerArtifactORM` por esos 3 hashes +
`validation_status == "VALIDATED"`, aplica `remove_dominated` (la misma
regla de dominancia same-composition-only ya existente y ya probada — no se
duplicó lógica). Se ejecuta **antes** de la ronda 1, solo si
`persistent_catalog_bootstrap_enabled` está activo: si hay markers
compatibles, siembra `markers`, intenta inmediatamente
`heuristic.solve(...)` sobre ese catálogo, y si valida lo publica como
incumbent temprano (`BEST_VALIDATED_SO_FAR · plan a partir del catálogo
persistente`) — implementando literalmente el paso 3 de la Sección 6
("preguntarle al planner si el conocimiento existente ya habilita una
solución válida") antes de que la generación de la ronda 1 siquiera corra.
El loop de rondas en sí **no se modificó**: el catálogo sembrado ya fluye
naturalmente por `marker_catalog = remove_dominated(tuple(markers))` en
cada ronda posterior, sin cambios a esa lógica.

## 5. Importación de evidencia histórica (Sección 5)

Los 4 markers conocidos (M2+L1, S3+XXL5, XS3+XL3, XL1) solo existían como
JSON de 2F.7H (`best-marker.json`, forma reducida
`{piece_instance_id, translation, rotation, geometry_hash}`) — cada script
experimental usó su propia base sqlite en memoria desechable, nunca la
persistencia real. `import_phase2f7h_marker_evidence.py` reconstruye
`Placement`s completos vía `transform_piece`/`rotate_and_translate_point`
contra la geometría **viva** del patrón (nunca los SVG — datos poligonales
canónicos, por instrucción explícita de la Sección 5), compara
`geometry_hash` pieza por pieza contra el patrón vivo (detecta si el patrón
cambió desde que se capturó la evidencia — Sección 25), y solo entonces
re-valida con `IndependentMarkerValidator` antes de persistir vía
`PlanningCoordinator._persist_marker_artifact` (mismo camino de inserción
segura ante condiciones de carrera que ya usa producción).

Resultado real: **los 4 markers se re-validaron y persistieron exitosamente**,
con métricas idénticas a la evidencia experimental original (218.0cm/74.26%,
660.0cm/72.52%, 441.5cm/71.60%, 93.58cm/66.26%) — confirmando que la
reconstrucción es fiel. Un marker deliberadamente corrompido (geometry_hash
alterado) se **rechaza** correctamente
(`test_corrupted_historical_marker_is_rejected_by_import`), nunca se
importa silenciosamente.

## 6. Cambios de esquema

Un único índice aditivo (`ix_marker_artifacts_compatibility` sobre
`pattern_hash, fabric_hash, table_hash`), migración
`20260912_0008_marker_catalog_index.py`. Sin columnas nuevas, sin tabla
nueva, sin cambio de forma — `MarkerArtifactORM` ya era suficiente
(Sección 24).

## 7. Benchmark frío vs. caliente (Sección 22, bases de datos aisladas)

| | Frío | Caliente | Referencia conocida |
| --- | --- | --- | --- |
| Estado | SUCCEEDED | SUCCEEDED | — |
| Tiempo de ejecución | 83.8s | 81.5s | — |
| Entradas de catálogo antes | 0 | 4 (importadas) | — |
| Tendidos físicos | 10 | **4** | 4 |
| Diseños de marker distintos | 6 | **4** | 4 |
| Tela total | 225.865 m | **167.209 m** | 167.209 m |
| Eficiencia global ponderada | 53.730473% | **72.578893%** | 72.578893% |
| Faltante / Sobreproducción | 0 / 0 | 0 / 0 | 0 / 0 |

Caliente **iguala exactamente** la referencia conocida — nivel de éxito
`KNOWN_REFERENCE_RECOVERY` (Sección 21, el más alto definido). Nunca se
compartió estado de base de datos entre frío y caliente (dos motores
sqlite en memoria completamente separados).

## 8. Experimento de acumulación (Sección 10, base de datos compartida — separado de la Sección 7)

| Corrida | Catálogo antes | ¿Bootstrap encontró solución temprana? | Tendidos | Diseños | Tela | Eficiencia |
| --- | --- | --- | --- | --- | --- | --- |
| 1 (fría) | 0 | No | 10 | 6 | 225.865 m | 53.730% |
| 2 (tras la corrida 1, sin importar nada externo) | 6 | **Sí** | 6 | 6 | **176.509 m** | 68.755% |
| 3 (tras enriquecer con los 4 markers importados) | 14 | Sí | 4 | 4 | **167.209 m** | 72.579% |

**`CATALOG_ACCUMULATION_HYPOTHESIS = SUPPORTED`** con números medidos, no
supuestos: la sola reutilización de lo que la corrida 1 descubrió (sin
ninguna intervención externa) ya mejora el resultado en 49.36 m / +15pp: el
catálogo persistente aprende de sí mismo. Enriquecerlo con la evidencia
curada cierra el resto de la brecha exactamente.

## 9. Experimento de presupuesto ALNS (Secciones 14-16, hallazgo central)

Se tomaron los 3 markers con mayor contribución de tela en el plan ganador
real de la corrida fría (`XXL×1`, `XL×1`, `M×1` — no supuestos, derivados de
`get_cold_run_spread_compositions.py`), y se corrió cada uno con
`GlobalNestingSearch` (sin modificar) en 3 niveles de presupuesto:

| Marker | Nivel `runtime` (1 semilla) | Nivel `medium` (2 semillas) | Nivel `deep` (3 semillas) |
| --- | --- | --- | --- |
| XXL×1 (120.5cm inicial) | Sin mejora (166.6s) | Sin mejora (2 semillas, ~425s c/u) | **111.68cm** (semilla 3, 34.7s, mejora a los 9.86s/iteración 1); semillas 1-2 sin mejora (~725s c/u) |
| XL×1 (114.0cm inicial) | Sin mejora (773.9s) | Sin mejora (2 semillas, 816-1264s) | Sin mejora (3 semillas, 1572-3081s) |
| M×1 (81.466cm inicial) | Sin mejora (412.2s) | **79.991cm** (semilla 2, 633.3s, mejora a los 0.68s/iteración 1); semilla 1 sin mejora | **79.991cm** (semilla 2 otra vez, mejora a los 0.77s/iteración 1) |

**Hallazgo central, medido, no supuesto**: el nivel `runtime` (1 sola
semilla — exactamente lo que 2F.8 usó en producción) **nunca mejoró ninguno
de los 3 markers**, reproduciendo con precisión el hallazgo de "0 markers
mejorados" de 2F.8. Pero agregar semillas (`medium`/`deep`) sí encontró
mejoras reales en 2 de 3 markers — y cuando una semilla mejora, lo hace
**rápido** (0.68-9.86 segundos hasta la primera mejora, en la primera
iteración) — dentro de la ventana "5-15 segundos" que la Sección 16 llama
viable para runtime. El problema no es que ALNS necesite correr más tiempo
por semilla; es que **una sola semilla tiene una probabilidad real de caer
en una iteración cara e improductiva** que consume todo el presupuesto sin
avanzar (algunas semillas tardaron 400-3000+ segundos en UNA sola
iteración, muy por encima de `piece_time_budget_ms`/`max_runtime_s`
configurados — el costo real está dominado por la generación de
NFP/CandidateSpace para geometría de patrón real, no por el bucle de
evaluación que esos parámetros sí acotan).

`ALNS_RUNTIME_FAILURE_CAUSE = OTHER` (ver Sección 18 para el texto
completo): varianza de costo por iteración, no presupuesto insuficiente ni
óptimo local.

**Hallazgo de ingeniería adicional (limitación, no corregida esta fase)**:
`max_runtime_s`/`piece_time_budget_ms` no acotan confiablemente el tiempo de
pared total cuando el costo de *generación* de candidatos (no de
evaluación) domina — una sola iteración puede exceder el presupuesto
configurado por un orden de magnitud. Esto es consistente con el cuello de
botella ya documentado en 2F.7G-8P2 (`candidate_space.boundary_intersections`,
`IntegerGeometryKernel.nfp`). No se corrige en esta fase (tocar
`completion_runner.py::_build_pool`/`_evaluate_piece` sin un defecto
reproducible violaría la Sección 2 del encargo) — se documenta como
hallazgo para una fase futura.

## 10. Time-to-first-improvement

Cuando una mejora ocurre, ocurre rápido: 0.683s (M×1, mejor caso) a 9.858s
(XXL×1). **`TIME_TO_FIRST_IMPROVEMENT_S = 0.683`** (mínimo medido). Esto
apoya la viabilidad de refinamiento síncrono *si* se usan múltiples
semillas — no una sola, como 2F.8 hacía.

## 11. Enriquecimiento profundo offline (Sección 12)

`enrich_marker_catalog_offline.py` implementado y disponible — reutiliza
`GlobalNestingSearch` sin cambios (sin un segundo motor geométrico),
persiste el ganador vía el mismo `_persist_marker_artifact` que usan la
importación y el refinamiento de 2F.8. No se ejecutó como parte extra de
esta corrida de evidencia (el experimento de presupuesto ALNS de la
Sección 9 ya cumple el mismo propósito de "enriquecimiento profundo
medido" para los 3 markers relevantes); queda disponible para enriquecer
composiciones adicionales bajo demanda, ejecutado siempre offline (nunca
desde el worker — Sección 17).

## 12. Plan de producción automatizado final (corrida caliente)

4 tendidos, 4 diseños de marker, 167.209 m de tela, 72.578893% de eficiencia
global ponderada, `shortage=0`, `overproduction=0`, validado
independientemente. Idéntico al mejor resultado conocido de 2F.7H-3.

## 13. Concurrencia

`test_concurrent_equivalent_artifact_insertion_produces_no_duplicate`
confirma que dos inserciones con el mismo `content_key` (simulando dos
workers descubriendo el mismo marker a la vez) producen exactamente una
fila — la segunda inserción recibe `IntegrityError`, recupera la fila ya
persistida (`cache_hit=True`), y ninguna fila duplicada o corrupta queda en
la tabla.

## 14. Determinismo

`test_bootstrap_query_is_deterministic` confirma que la consulta de
bootstrap retorna el mismo conjunto de `marker_hash` en corridas
independientes idénticas. El determinismo del propio ALNS ya está probado
exhaustivamente desde 2F.7H-1 (código sin cambios esta fase).

## 15. No-regresión de la ruta legada (Sección 27/28)

`test_flag_off_ignores_compatible_catalog_entries`: con
`persistent_catalog_bootstrap_enabled=False`, la presencia de un marker
compatible pre-existente en la base de datos **no afecta** la ejecución —
`run.audit` no contiene `"catalog_bootstrap"` en absoluto. La corrida fría
de esta fase reproduce exactamente el resultado de 2F.8 (225.865 m /
53.730473%, mismo estado), confirmando `LEGACY_PATH_REGRESSION = PASS`.

## 16. Nota sobre una corrida de tests intermitente

Durante el desarrollo, una corrida completa de la suite mostró 2 fallos
transitorios en pruebas de importación mientras el experimento de
presupuesto ALNS corría simultáneamente en otro proceso (alta carga de CPU
concurrente). Una repetición inmediata, sin cambios de código, bajo carga
normal, pasó 100% limpio, y las mismas 2 pruebas también pasaron en
aislamiento durante el incidente — indicando contención de recursos
transitoria del entorno, no un defecto reproducible del código. Se reporta
por transparencia, no se oculta.

## 17. Limitaciones

- El experimento ALNS confirma que el presupuesto de refinamiento en vivo
  de 2F.8 (`joint_optimization_enabled`, 1 semilla) es estructuralmente
  insuficiente — no por profundidad, sino por varianza entre semillas. No
  se modificó `_refine_marker_geometry`/`_refine_plan_with_geometry` en
  esta fase (fuera de alcance; el hallazgo se entrega como evidencia para
  la decisión de la Sección 33, no como una corrección automática).
- `max_runtime_s`/`piece_time_budget_ms` no acotan confiablemente el costo
  de generación de candidatos NFP para geometría real (ver Sección 9) —
  limitación de ingeniería documentada, no corregida.
- El enriquecimiento profundo offline (Sección 11) está implementado pero
  no se corrió como paso separado esta sesión (el experimento de
  presupuesto ya cubre el mismo propósito para los markers relevantes).
- El catálogo de bootstrap no implementa todavía un límite de relevancia
  más allá del alcance natural por `(pattern_hash, fabric_hash,
  table_hash)` (Sección 7 del encargo) — apropiado para el tamaño actual
  de evidencia (unas pocas decenas de markers), no para un catálogo de
  miles.

## 18. Próxima fase recomendada

Con `WARM_SUCCESS_TIER = KNOWN_REFERENCE_RECOVERY` y el experimento de
presupuesto mostrando mejoras reales en 5-15 segundos cuando se usan
múltiples semillas, la recomendación de la Sección 33 es:

**Opción D (híbrida)**: (a) el bootstrap de catálogo persistente ya
demostró ser la mejora de mayor impacto — se recomienda su integración
como fuente de conocimiento geométrico por defecto (activar
`persistent_catalog_bootstrap_enabled` de forma general, tras revisión);
(b) el refinamiento ALNS síncrono en el worker (2F.8) debería usar
**múltiples semillas** (2-3, no 1) dado el tiempo-a-primera-mejora medido
de 0.68-9.86s — esto es una mejora acotada y de bajo riesgo al mecanismo ya
existente, no un rediseño; (c) el enriquecimiento profundo (más semillas,
más iteraciones, para composiciones de alto impacto que el bootstrap no
cubre) debe seguir siendo explícitamente offline/en segundo plano, nunca
síncrono en el worker, dado que el costo de generación de NFP para
geometría real sigue siendo impredecible por iteración. No se comienza
ninguna fase nueva automáticamente. `STOP_GATE` permanece `ACTIVE`.

## 19. Veredictos finales obligatorios

Ver `artifacts/phase2f8_1/summary.json` para el bloque completo,
máquina-legible. Resumen:

```
FASE_2F_8_1_PERSISTENT_MARKER_CATALOG = PASS
CATALOG_COMPATIBILITY_STATUS = PASS
HISTORICAL_MARKER_IMPORT_STATUS = PASS
CATALOG_BOOTSTRAP_STATUS = PASS
CATALOG_ACCUMULATION_HYPOTHESIS = SUPPORTED
AUTOMATED_EXACT_216_SOLUTION_FOUND = YES
SHORTAGE = 0
OVERPRODUCTION = 0
COLD_TOTAL_FABRIC_METERS = 225.865
WARM_TOTAL_FABRIC_METERS = 167.209
KNOWN_REFERENCE_TOTAL_FABRIC = 167.209
FABRIC_GAP_TO_REFERENCE = 0.0
COLD_GLOBAL_EFFICIENCY = 53.730473%
WARM_GLOBAL_EFFICIENCY = 72.578893%
KNOWN_REFERENCE_GLOBAL_EFFICIENCY = 72.578893%
ALNS_RUNTIME_FAILURE_CAUSE = OTHER (seed-variance in per-iteration NFP-generation cost, not budget depth -- see Section 9)
TIME_TO_FIRST_IMPROVEMENT = 0.683s (minimum measured)
DEEP_ENRICHMENT_USEFUL = YES (with >=2 seeds; NO with exactly 1 seed at the same budget)
DETERMINISM_STATUS = PASS
INDEPENDENT_VALIDATION_STATUS = PASS
CONCURRENCY_STATUS = PASS
LEGACY_PATH_REGRESSION = PASS
PATTERN_VALIDATION_STATUS = ENGINEERING
PRODUCTION_READY = NO
STOP_GATE = ACTIVE
```
