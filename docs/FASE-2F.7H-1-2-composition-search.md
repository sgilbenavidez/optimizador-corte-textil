# FASE 2F.7H-1 + 2F.7H-2 — Local compaction, operator expansion y CompositionExplorer

`PATTERN_VALIDATION_STATUS = ENGINEERING` · `PRODUCTION_READY = NO` · `STOP_GATE = ACTIVE`

Ver el bloque de veredictos completo en la Sección 21 y el JSON máquina-legible
en `artifacts/phase2f7h12/summary.json`.

## 1. Resultado ejecutivo

2F.7H-1 completó los operadores destroy/repair y la compactación local que
2F.7H había dejado explícitamente pendientes (3/6 → 6/6 operadores destroy,
2/6 → 3/6 estrategias de reinserción, SHIFT_LEFT local, instrumentación por
operador, checkpoint/resume del propio loop ALNS). Sobre el mismo fixture
M2+L1 del incumbent legado, esta infraestructura ampliada bajó el mejor
resultado validado de **227.483 cm / 71.159748%** (2F.7H) a **218.0 cm /
74.255197%** — una mejora real, reproducible e independientemente validada,
no solo una ampliación de alcance.

2F.7H-2 implementó `CompositionExplorer` como puente entre
`CandidateCompositionGenerator` (generación/filtrado arítmético, ya existente
y sin modificar) y `GlobalNestingSearch` (evaluación geométrica real). La
hipótesis central del usuario — *¿prendas repetidas mejoran el
anidado?* — se puso a prueba con evidencia geométrica real (no solo
aritmética) sobre M×1 → M×2 → M×3 → M×4:

| Composición | Eficiencia validada |
| --- | --- |
| M×1 | 68.005145% |
| M×2 | 71.885820% |
| M×3 | 73.429349% |
| M×4 | 74.722858% |

Monótonamente creciente, con retornos decrecientes (+3.88pp, +1.54pp,
+1.29pp) — la hipótesis queda **SUPPORTED** dentro de este alcance.

Además, S×3+XXL×5 (8 prendas, 40 piezas) — que 2F.7H había dejado
explícitamente sin autorizar por falta de evidencia geométrica — validó en
**660.0 cm / 72.524126%**, con ambas semillas convergiendo al mismo
resultado exacto: cabe cómodamente en la mesa de 700cm y supera el piso duro
del incumbent legado. El análisis de producción exacta para las 216 prendas
del benchmark resultó `INFEASIBLE`, pero por una razón estructural honesta y
esperada: el catálogo evaluado en esta sesión nunca incluyó una composición
que cubra las tallas XS o XL (ver Sección 14), no por una limitación del
mecanismo `ProductionPlanner`/CP-SAT en sí (que ya está probado y sin
cambios).

## 2. Delta de arquitectura

Ninguno. `docs/architecture/costura-optima.architecture.json` no se modifica:
todo el trabajo ocurre dentro del componente "Optimization Domain" ya
modelado (`pipeline`/`completion-runner`/`validator`), sin nuevos límites,
colas, datastores ni flujos runtime API/worker — exactamente el mismo
razonamiento que `c8d04ee` ya documentó para 2F.7G-8/8P/8P2. `GLOBAL_SEARCH_PRODUCT_INTEGRATED`
sigue en `NO`; nada de este trabajo toca `planning_coordinator.py` ni el
engine que la API/worker usan en producción (`MARKER_ENGINE` /
`DeterministicNestingEngine`, invocado sin cambios como *cold start* — ver
Sección 4).

## 3. Archivos modificados

- `apps/api/src/costura_optima/domain/global_nesting_search.py` (modificado,
  no reescrito desde cero: +315/-45 líneas) — operadores destroy/repair
  nuevos, `compact()`, instrumentación, checkpoint/resume.
- `apps/api/src/costura_optima/domain/composition_explorer.py` (nuevo) —
  `CompositionExplorer`, `compute_attainability`, `protected_length`,
  `to_validated_marker_candidate`.
- `apps/api/scripts/run_phase2f7h2_composition_search.py` (nuevo) — harness
  de evaluación geométrica por composición.
- `apps/api/scripts/run_phase2f7h2_exact_production.py` (nuevo) — puente a
  `ProductionPlanner` en modo exacto para el benchmark de 216 prendas.
- `apps/api/scripts/finalize_phase2f7h12.py` (nuevo) — agregación de
  veredictos, mismo formato que `finalize_phase2f7h.py`.
- `apps/api/tests/test_global_nesting_search.py`,
  `apps/api/tests/test_local_compaction.py`,
  `apps/api/tests/test_composition_explorer.py` (nuevos, 32 tests).

Sin cambios en `completion_runner.py`, `candidate_space.py`,
`marker_validator.py`, `integer_kernel.py`, `candidate_generator.py`,
`divisibility.py`, `production_planner.py` ni `production_models.py` — toda
la geometría/scoring/CP-SAT cerrados se reutiliza sin modificar, por diseño
(Sección 3 y 32 del encargo original).

## 4. Nuevos algoritmos/operadores

**Destroy** (`global_nesting_search.py::GlobalNestingSearch.destroy`):
`REGION_REMOVAL` (ventana contigua en x, perturba una vecindad espacial como
bloque), `SLEEVE_CLUSTER_REMOVAL` (familia SLEEVE/NECKBAND, gap-fillers que
se remueven juntos), `SMALL_PIECE_REMOVAL` (las k piezas de menor área,
baratas de reinsertar). Se suman a `RANDOM_K_REMOVAL`/`TAIL_REMOVAL`/
`WORST_CONTRIBUTOR_REMOVAL` (2F.7H) sin tocarlos.

**Reinsert**: `CONTACT_POTENTIAL_FIRST` — ordena por distancia bbox mínima
(sin llamadas NFP) a la posición pre-remoción de cada pieza removida respecto
a las piezas conservadas, priorizando reinsertar primero lo que ya tenía una
oportunidad geométrica cercana.

**Compactación local (`compact`)**: `SHIFT_LEFT` procesa piezas en orden
ascendente de x, busca binariamente la x mínima legal contra un
`IncrementalCandidateValidator` fresco de las demás piezas, y solo publica el
resultado si (a) revalida completo con `IndependentMarkerValidator` y (b) el
largo no aumentó — si cualquiera falla, retorna el estado original sin
cambios. `GAP_CLOSING` no se implementó como operador separado: el orden
ascendente de `SHIFT_LEFT` ya produce su efecto práctico (cada pieza se
compacta contra el hueco que deja la anterior). `SMALL_PIECE_REFILL` queda
diferido, documentado, no implementado — necesita detección de cavidades que
no reutiliza limpiamente ningún primitivo existente.

**Instrumentación por operador**: contadores `proposed/repair_succeeded/
validated/accepted/incumbent_improvements` por destroy y por reinsert, más
`compaction.applied/iterations_improved/length_reduction_units_total`,
escritos en `operator-statistics.json` por corrida.

**Checkpoint/resume del loop ALNS**: nuevo (el checkpoint existente de
`completion_runner.py` es write-only, por pieza, de la fase greedy lineal —
no relacionado). Serializa config, iteración, RNG state
(`Random.getstate()`), placements actual/mejor, estadísticas de operador e
historial. `test_resumed_run_matches_an_uninterrupted_run` confirma que una
corrida interrumpida y reanudada produce el mismo `best_layout_hash` que una
corrida ininterrumpida con la misma semilla.

## 5. Diseño de CompositionExplorer

Deliberadamente un puente, no una reimplementación (ver hallazgos de
investigación previos a la implementación): `generate_candidates` llama a
`CandidateCompositionGenerator.generate(...)` **sin modificarlo** y añade
niveles de attainability; `evaluate_geometrically` recibe `build_request`/
`cold_start` como funciones inyectadas (mantiene el módulo libre de
dependencias de `application`/`infrastructure`, coherente con el límite de
capas del dominio) y delega toda la geometría a `GlobalNestingSearch`/
`IndependentMarkerValidator` sin cambios. El *cold start* llama directamente
a `MARKER_ENGINE.nest(request)` — el mismo motor legado que la API/worker de
producción ya usa y confía — en vez de depender de un checkpoint de fase
anterior hardcodeado (la limitación exacta que bloqueaba evaluar
composiciones nuevas en 2F.7H).

## 6. Fórmulas de prefiltro

Para cada composición, `compute_attainability(total_area_units2,
usable_width_units, max_length_units)`:

```
L_tier = ceil(total_area_units2 / (usable_width_units * tier))   tier ∈ {1.00, 0.95, 0.90, 0.85, 0.80}
```

Clasificación (explícita, documentada, umbral por comparación directa contra
`max_length_units`):

- `AREA_INFEASIBLE` si `L100 > max_length` (ni al 100% de eficiencia cabe).
- `VERY_TIGHT` si `L90 > max_length ≥ L100`.
- `CHALLENGING` si `L85 > max_length ≥ L90`.
- `PROMISING` si `max_length ≥ L85`.

Son cotas de screening, nunca prueba de factibilidad geométrica (Secciones
10/11 del encargo). `CandidateCompositionGenerator` ya calculaba un único
bound equivalente a su `expected_marker_efficiency` (0.72 por defecto); este
módulo añade los niveles L95/L90/L85/L80 explícitos encima, sin tocar ese
archivo de producción.

## 7. Configuración de búsqueda

SMALL-tier ALNS (no LARGE): `candidate_budget=5000` (3000 para S3+XXL5, 40
piezas), `top_k=1`, `beam_width=1` (mismos valores que 2F.7H canónico), 2
semillas por composición (`seeds=(1,2)`), 4-6 iteraciones según cardinalidad
de piezas (M1:6, M2:6, M3:5, M4:4, M2+L1:5, S3+XXL5:3). Alcance reducido
explícitamente frente a las 5 semillas sugeridas por el encargo — ver
Sección 18 (Limitaciones).

## 8. Evidencia de determinismo

Dos corridas independientes, mismo seed=1, mismo config, sobre M2+L1 con el
código 2F.7H-1 ya integrado (`artifacts/phase2f7h1_check/det{1,2}/summary.json`):

| Campo | det1 | det2 | Igual |
| --- | --- | --- | --- |
| best_length_cm | 233.296 | 233.296 | ✅ |
| best_efficiency_pct | 69.386672 | 69.386672 | ✅ |
| accepted_states | 5 | 5 | ✅ |
| incumbent_improvements | 1 | 1 | ✅ |
| best_layout_hash | `8db613ce...` | `8db613ce...` | ✅ (idéntico byte a byte) |
| orientation_counts | {0:13,180:2} | {0:13,180:2} | ✅ |
| final_validation_status | VALIDATED | VALIDATED | ✅ |

`DETERMINISM_STATUS = PASS`. No se compara contra el artefacto congelado de
2F.7H (`small/summary.json`, 233.296cm también — coincidencia esperable en
la primera mejora temprana, no una comparación de determinismo formal): con
6 operadores destroy en vez de 3, la secuencia de consumo del RNG cambia
legítimamente: lo que se exige es "misma semilla+config → mismo resultado"
en el código actual, no paridad byte a byte con una corrida de un código
anterior.

## 9. Evidencia de validación

Cada estado aceptado por `GlobalNestingSearch.run()` pasa
`IndependentMarkerValidator` antes de convertirse en `current`/`best`
(sin cambios respecto a 2F.7H); `compact()` añade una segunda validación
completa antes de publicar cualquier compactación. Cada *cold start* del
motor legado (`MARKER_ENGINE.nest`) se revalida independientemente antes de
usarse como semilla de ALNS (`evaluate_geometrically`, guard explícito). El
mejor resultado final de cada composición se revalida una tercera vez de
forma defensiva (`INVALID_FINAL_MARKER` si fallara — no ocurrió en ninguna
corrida de este alcance). Los 32 tests nuevos cubren invariantes de
operador, validez de repair, compactación (nunca empeora, siempre válida,
idempotente, demuestra mejora real en un fixture flojo) y CompositionExplorer
(determinismo, bounds, exclusión de tallas en cero, monotonicidad de niveles
de attainability, protección de incumbent).

## 10. Performance

| Composición | Piezas | Iteraciones×semillas | Runtime total |
| --- | --- | --- | --- |
| M×1 | 5 | 6×2 | 104.6 s |
| M×2 | 10 | 6×2 | 261.0 s |
| M×3 | 15 | 5×2 | 402.4 s |
| M×4 | 20 | 4×2 | 509.5 s |
| M2+L1 | 15 | 5×2 | 490.4 s |
| S3+XXL5 | 40 | 3×2 | 1012.3 s |

El costo por iteración escala con el número de piezas ya colocadas (más
candidatos NFP a evaluar por posición), consistente con el cuello de botella
ya documentado en 2F.7G-8P2 (`candidate_space.boundary_intersections`,
`IntegerGeometryKernel.nfp`). No se realizó optimización de performance
adicional en esta fase (fuera de alcance explícito, igual que 2F.7G-8P2).

## 11. Tabla comparativa

| Composición | Prendas | Piezas | L100 (cm) | L90 (cm) | Largo real (cm) | Eficiencia | Validado | Runtime |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M×1 | 1 | 5 | 52.38 | 58.20 | 77.023 | 68.005145% | YES | 104.6s |
| M×2 | 2 | 10 | 104.76 | 116.40 | 145.730 | 71.885820% | YES | 261.0s |
| M×3 | 3 | 15 | 157.14 | 174.60 | 214.000 | 73.429349% | YES | 402.4s |
| M×4 | 4 | 20 | 209.52 | 232.80 | 280.394 | 74.722858% | YES | 509.5s |
| M×2+L×1 (nuevo) | 3 | 15 | 161.88 | 179.86 | 218.000 | 74.255197% | YES | 490.4s |
| S×3+XXL×5 | 8 | 40 | 478.66 | 531.84 | 660.000 | 72.524126% | YES | 1012.3s |
| XS×6+XL×7 | 13 | — | 694.24 | 771.38 | — | — | PREFILTER_REJECTED (VERY_TIGHT) | 0s |

## 12. Experimento de prendas repetidas

M×1 → M×2 → M×3 → M×4: 68.005% → 71.886% → 73.429% → 74.723%. Estrictamente
monótono creciente, con retornos decrecientes (+3.88pp, +1.54pp, +1.29pp).
M×4 muestra mayor varianza entre semillas (280.394cm vs 312.956cm, 32.6cm de
spread) — con solo 4 iteraciones sobre 20 piezas el presupuesto ALNS está
más ajustado que en las composiciones menores; la tendencia central sigue
siendo consistente con M×1-M×3, pero esta evidencia puntual es menos
convergida que las anteriores. Runtime por composición crece con el número
de piezas (Sección 10), no con el número de iteraciones (que de hecho se
redujo para las composiciones grandes).

## 13. Experimento multi-talla

M×2+L×1 (refrescado por el pipeline 2F.7H-1, 74.255197%) supera a M×3
(73.429349%, mismo número de piezas: 15) y queda ligeramente por debajo de
M×4 (74.722858%, más piezas pero también más varianza entre semillas — ver
Sección 12). S×3+XXL×5 (8 prendas, 40 piezas, dos tallas distintas) validó
en **660.0 cm / 72.524126%**, con ambas semillas convergiendo exactamente al
mismo resultado (660.0cm en ambas) — evidencia consistente, no un resultado
de una sola semilla afortunada. Este resultado responde directamente a la
pregunta que 2F.7H había dejado abierta ("S3xXXL5 NOT authorized yet"): la
composición **sí cabe** en la mesa de 700cm (660cm, 40cm de margen) y su
eficiencia (72.52%) supera cómodamente el piso duro del incumbent legado
(70.534348%), aunque no alcanza el umbral preferido de 75%. XS×6+XL×7 fue
rechazado por prefiltro (`VERY_TIGHT`, L90=771.38cm > 700cm de mesa) sin
ejecutar geometría, tal como pide la Sección 19E del encargo: exacto
aritméticamente no implica intentar la búsqueda cara a ciegas.

## 14. Análisis de producción exacta

`run_phase2f7h2_exact_production.py` ensambla el catálogo de 6 markers
validados en esta fase (M×1, M×2, M×3, M×4, M2+L1, S3+XXL5) y llama a
`ProductionPlanner.solve_profiles(demand_216, {talla: 0 para toda talla},
catalog)` — el mismo camino de código exacto que
`test_production_planner.py::test_allow_overproduction_false_can_be_infeasible`
ya prueba, sin CP-SAT nuevo. Resultado: **INFEASIBLE**. La razón es
estructural y esperable, no un defecto del solver ni una sorpresa: el
catálogo de esta sesión nunca produjo una talla XS ni XL (la única
composición que las cubría, XS×6+XL×7, fue rechazada por prefiltro antes de
llegar a geometría), así que ninguna combinación de capas/repeats de este
catálogo puede cubrir la demanda de XS=30 y XL=35. Esto es exactamente el
comportamiento correcto que pide la Sección 16 del encargo: "si no existe
solución exacta con el catálogo de markers evaluado hasta ahora, reportar
ese hecho en vez de permitir sobreproducción silenciosa" — no se publicó
ningún plan, por lo tanto no hay sobreproducción ni faltante en un plan real
(la cifra de "shortage=216" en `exact-production-216.json` refleja que no se
eligió ningún plan, no un plan publicado con faltante).

## 15. Mejor marker validado

`M2+L1` nuevo: 218.0 cm, 74.255197%, `marker_hash =
123438d26ed31a18f8c235356a542645e21c2cfb070c8041d3cb68cec765b946`,
`artifacts/phase2f7h2/M2+L1/best-marker.json`.

## 16. Mejor composición

Por eficiencia pura: **M×4** (74.722858%), aunque con mayor varianza entre
semillas que las demás. Por relevancia productiva (talla mixta, divisibilidad
exacta con el benchmark de 216, protección de incumbent activa): **M×2+L×1**
(74.255197%, 218.0cm) — mejora real de 9.483cm / 3.10pp sobre el mejor
resultado previo de 2F.7H (227.483cm / 71.159748%). S×3+XXL×5 (72.524126%,
660.0cm) es la evidencia más nueva y relevante para la Sección 19D del
encargo original: confirma que esa composición es geométricamente viable,
algo que 2F.7H no había podido establecer.

## 17. Implicaciones globales de producción

`CandidateCompositionGenerator`/`ProductionPlanner`/
`IndependentProductionPlanValidator` ya existían, probados y sin modificar;
`CompositionExplorer` produce `ValidatedMarkerCandidate`s compatibles sin
cambios en esos archivos (`to_validated_marker_candidate`), confirmando que
una futura integración (2F.8) puede alimentarlos directamente sin nueva
geometría de puente.

## 18. Limitaciones

- 2 semillas por composición (no 5): reducción de alcance explícita y
  documentada de antemano (ver plan de sesión), no oculta.
- SMALL-tier (4-6 iteraciones): suficiente para mostrar tendencia real, no
  para afirmar convergencia — M×4 en particular muestra varianza notable
  entre semillas.
- `SMALL_PIECE_REFILL` y `GAP_CLOSING` como operador separado: diferidos.
- Checkpoint/resume: probado para el loop ALNS con presupuestos pequeños; no
  ejercido contra una interrupción real de proceso (kill -9) en esta fase.
- Cross-platform (Docker): no verificado en esta sesión — ver Sección 20.

## 19. Experimentos fallidos

Ninguno catastrófico: XS×6+XL×7 fue rechazado por diseño (prefiltro), no por
fallo de búsqueda. Sin `SEARCH_FAILED` ni `INVALID_FINAL_MARKER` registrados
en este alcance.

## 20. Próxima fase recomendada

La búsqueda global (2F.7H-1) demostró mejora real y reproducible sobre el
mismo fixture ya conocido (M2+L1: 227.483→218.0cm), y CompositionExplorer
(2F.7H-2) demostró tanto que repetir prendas ayuda (hipótesis SUPPORTED)
como que al menos una composición multi-talla previamente no autorizada
(S3+XXL5) es geométricamente viable. Esto es evidencia suficiente para
recomendar avanzar a `FASE 2F.8 — JOINT PRODUCTION + GEOMETRY OPTIMIZATION`
integrando composición + geometría + capas + repeats + tendidos +
sobreproducción + consumo + eficiencia + diseños distintos + cambios de
marker en un único flujo coordinado — pero **no automáticamente en esta
sesión**, y con un catálogo de composiciones deliberadamente más amplio que
el de esta fase: el hallazgo de la Sección 14 (INFEASIBLE para 216 prendas
exactas) es un artefacto directo de que este catálogo nunca evaluó una
composición que cubra XS/XL, no una limitación del mecanismo de
planificación en sí. 2F.8 (o una extensión de 2F.7H-2) debe ampliar
`CompositionExplorer`'s conjunto evaluado para incluir al menos una
composición viable por cada talla con demanda positiva antes de intentar de
nuevo la factibilidad exacta de 216. `STOP_GATE` permanece `ACTIVE`.

## 21. Veredictos finales obligatorios (Sección 34 del encargo)

```
FASE_2F_7H_1_LOCAL_COMPACTION = PASS
FASE_2F_7H_2_COMPOSITION_SEARCH = PASS
REPEATED_GARMENTS_HYPOTHESIS = SUPPORTED
BEST_PREVIOUS_M2_L1 = 227.483 cm / 71.159748%
BEST_NEW_M2_L1 = 218.0 cm / 74.255197%
BEST_REPEATED_SINGLE_SIZE_COMPOSITION = M×4 (74.722858%, 280.394cm, mayor varianza entre semillas)
BEST_MIXED_SIZE_COMPOSITION = M×2+L×1 (74.255197%, 218.0cm)
BEST_GEOMETRIC_EFFICIENCY = 74.722858% (M×4)
EXACT_216_ORDER_SOLUTION_FOUND = NO (catálogo de esta sesión no cubre XS/XL -- ver Sección 14)
EXACT_216_ORDER_PHYSICAL_SPREADS = N/A (sin plan publicado)
EXACT_216_ORDER_OVERPRODUCTION = N/A (sin plan publicado)
EXACT_216_ORDER_SHORTAGE = N/A -- no se publicó ningún plan; la regla "0 shortage obligatorio"
                            aplica solo a un plan publicado, y aquí no se publicó ninguno
TARGET_75_REACHED = NO (máximo 74.722858%, M×4)
TARGET_80_REACHED = NO
TARGET_85_REACHED = NO
TARGET_90_REACHED = NO
DETERMINISM_STATUS = PASS (det1 == det2, byte a byte en best_layout_hash)
INDEPENDENT_VALIDATION_STATUS = PASS
INCUMBENT_PROTECTION_STATUS = PASS (218.0cm < 227.483cm < 229.5cm)
CROSS_PLATFORM_STATUS = NOT_RUN
PATTERN_VALIDATION_STATUS = ENGINEERING
PRODUCTION_READY = NO
STOP_GATE = ACTIVE
```

Ver también `artifacts/phase2f7h12/summary.json` (mismos campos,
máquina-legible, generado por `finalize_phase2f7h12.py`).
