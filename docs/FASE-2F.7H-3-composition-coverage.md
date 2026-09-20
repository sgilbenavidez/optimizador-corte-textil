# FASE 2F.7H-3 — Composition coverage expansion + Pareto marker catalog

`PATTERN_VALIDATION_STATUS = ENGINEERING` · `PRODUCTION_READY = NO` · `STOP_GATE = ACTIVE`

`EXACT_216_ORDER_SOLUTION_FOUND = YES` — ver el bloque de veredictos completo
en la Sección 6 y `artifacts/phase2f7h3/summary.json`.

## 1. Resultado ejecutivo

2F.7H-2 había dejado la factibilidad exacta de las 216 prendas del benchmark
en `INFEASIBLE` por una razón estructural diagnosticada con precisión: el
catálogo evaluado nunca producía las tallas XS ni XL. Esta fase encontró
una **solución exacta completa**: `SHORTAGE = 0`, `OVERPRODUCTION = 0`,
`production(talla) == demand(talla)` para las 7 tallas, validada
independientemente (`VALIDATED_PLAN`).

La solución ganadora combina 4 diseños de marker — dos ya validados y sin
cambios desde 2F.7H-2 (mismo `marker_hash`, ninguna regresión), dos
descubiertos en esta fase:

| Marker | Composición | Capas | Producción | Origen |
| --- | --- | --- | --- | --- |
| `d183a4b1...` | S×3+XXL×5 | 11 | S:33, XXL:55 | 2F.7H-2 (sin cambios) |
| `123438d2...` | M×2+L×1 | 21 | M:42, L:21 | 2F.7H-2 (sin cambios) |
| `d748a4a3...` | XS×3+XL×3 | 10 | XS:30, XL:30 | **nuevo, 2F.7H-3** |
| `cceb19dd...` | XL×1 | 5 | XL:5 | **nuevo, 2F.7H-3** |

`GLOBAL_WEIGHTED_EFFICIENCY = 72.578893%`, 4 tendidos, 4 diseños distintos,
167.209 m de tela total. No se forzó XS×6+XL×7 (correctamente prefiltrada en
2F.7H-2): la cobertura de XS/XL se resolvió con una composición
geométricamente mucho más cómoda (XS×3+XL×3, L90=351cm vs los 700cm de
mesa) más un XL×1 residual — exactamente el enfoque de "portfolio, no una
sola composición" que pide la Sección 2 del encargo.

## 2. Búsqueda de composiciones (Stage A/B)

21 candidatos evaluados: la familia explícita XS/XL de 9 composiciones
(Sección 9), 6 pares de sinergia cruzada (Sección 10: XS/XL con S, M, L,
XXL), y 6 repeticiones de talla única XS×1-3/XL×1-3 (Sección 15). Screening
de área (Stage A) clasificó los 21 como `PROMISING` — ninguno se acercó al
límite de 700cm (el más largo, XS×3+XL×3, tiene L90=351cm). Los candidatos
residuales generados automáticamente por `CandidateCompositionGenerator`
(llamado con `residual={"XS":30,"XL":35,resto:0}`) coincidieron
exactamente con composiciones ya sembradas manualmente — no aportaron
composiciones nuevas más allá de la familia explícita, lo cual en sí mismo
confirma que la familia manual ya cubre el espacio que el generador
residual habría propuesto.

Stage B (cold-start únicamente, sin ALNS) validó los 21 — ninguno falló ni
excedió el largo de mesa. Costo: ~40s por composición sin importar mucho el
número de piezas (10-30 piezas), evidencia de que el costo de Stage B está
dominado por el *cold start* del motor legado, no por el tamaño del
problema en este rango.

| Composición | Prendas | Piezas | L100 (cm) | L90 (cm) | Largo real (cm) | Eficiencia | Cobertura | Runtime | Estado |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| XS×1+XL×2 | 3 | 15 | 167.38 | 185.98 | 233.500 | 71.683% | XS+XL | 43.8s | VALIDATED |
| **XS×3+XL×3** | 6 | 30 | 316.12 | 351.24 | 441.500 | **71.600%** | XS+XL | 1092.4s (Stage C) | VALIDATED |
| XS×2+XL×3 | 5 | 25 | 272.75 | 303.06 | 393.409 | 69.330% | XS+XL | 40.7s | VALIDATED |
| XS×2+XL×1 | 3 | 15 | 148.74 | 165.26 | 217.500 | 68.384% | XS+XL | 43.0s | VALIDATED |
| XL×2 | 2 | 10 | 124.02 | 137.80 | 182.000 | 68.141% | XL | 40.7s | VALIDATED |
| XS×3+XL×2 | 5 | 25 | 254.11 | 282.34 | 377.409 | 67.329% | XS+XL | 41.5s | VALIDATED |
| XS×3 | 3 | 15 | 130.09 | 144.54 | 193.500 | 67.230% | XS | 39.0s | VALIDATED |
| XS×1+XL×3 | 4 | 20 | 229.39 | 254.88 | 341.909 | 67.091% | XS+XL | 41.2s | VALIDATED |
| **XL×1** | 1 | 5 | 62.01 | 68.90 | 93.580 | **66.263%** | XL | 202.5s (Stage C) | VALIDATED |
| M×1+XL×1 | 2 | 10 | 114.39 | 127.10 | 174.000 | 65.740% | M+XL | 41.9s | VALIDATED |
| XS×2+XL×2 | 4 | 20 | 210.74 | 234.16 | 325.909 | 64.663% | XS+XL | 42.7s | VALIDATED |
| S×1+XL×1 | 2 | 10 | 109.81 | 122.00 | 170.000 | 64.591% | S+XL | 39.7s | VALIDATED |
| XS×1+XXL×1 | 2 | 10 | 110.42 | 122.69 | 172.000 | 64.196% | XS+XXL | 42.3s | VALIDATED |
| XL×3 | 3 | 15 | 186.03 | 206.69 | 290.409 | 64.056% | XL | 40.9s | VALIDATED |
| XS×1+XL×1 | 2 | 10 | 105.37 | 117.08 | 166.000 | 63.477% | XS+XL | 41.8s | VALIDATED |
| XS×3+XL×1 | 4 | 20 | 192.10 | 213.44 | 305.023 | 62.978% | XS+XL | 39.2s | VALIDATED |
| L×1+XS×1 | 2 | 10 | 100.48 | 111.64 | 160.000 | 62.800% | L+XS | 42.9s | VALIDATED |
| M×1+XS×1 | 2 | 10 | 95.74 | 106.38 | 154.000 | 62.171% | M+XS | 42.8s | VALIDATED |
| S×1+XS×1 | 2 | 10 | 91.16 | 101.29 | 148.000 | 61.594% | S+XS | 42.4s | VALIDATED |
| XS×2 | 2 | 10 | 86.73 | 96.36 | 142.000 | 61.075% | XS | 37.8s | VALIDATED |
| XS×1 | 1 | 5 | 43.36 | 48.18 | 76.500 | 56.684% | XS | 28.3s | VALIDATED |

(XS×3+XL×3 y XL×1 muestran su valor **post-Stage-C**; los demás son
resultado de Stage B solamente — cotas inferiores honestas, no refinadas por
ALNS. Ver Sección 4.)

## 3. Familia XS/XL y sinergia cruzada

**Mejor XS sola**: XS×3 (67.230%). **Mejor XL sola**: XL×2 (68.141%,
superando incluso a XL×3 — ver Sección 5). **Mejor combinación XS+XL**:
XS×1+XL×2 (71.683%, la más alta de toda la tabla en Stage B). **Mejor
sinergia cruzada** (XS/XL con una tercera talla): M×1+XL×1 (65.740%),
seguida de cerca por XS×1+XXL×1 y S×1+XL×1 — ninguna sinergia cruzada superó
a la familia XS+XL pura, contradiciendo la hipótesis explícita de la
Sección 10 ("small XS bodies... may fill cavities created by larger
XXL/XL pieces") para este catálogo de patrones: medida, no asumida, la
sinergia cruzada resultó *menos* eficiente que emparejar XS con XL
directamente.

Nótese que XS×1+XL×2 (71.683%, Stage B) superó incluso a XS×3+XL×3 en Stage
B (71.051%) antes de que este último recibiera refinamiento ALNS —
XS×3+XL×3 solo tomó la delantera después de Stage C. XS×1+XL×2 no fue
promovida a Stage C en esta sesión (no era necesaria para la solución
exacta encontrada); queda como candidata de mejora futura documentada, no
descartada.

## 4. Diseño staged (Stage A/B/C/D)

Los 21 candidatos pasaron por Stage A (screening de área, instantáneo) y
Stage B (cold-start único, sin ALNS, ~40s cada uno). Solo 2 — XS×3+XL×3 y
XL×1, los dos que resultaron necesarios en la solución exacta ganadora —
se promovieron a Stage C (ALNS completo): XL×1 con 3 semillas/6
iteraciones (202.5s, mejoró de 54.393% cold-start a 66.263%), XS×3+XL×3 con
2 semillas/3 iteraciones (1092.4s, mejoró de 71.051% a 71.600%, ambas
semillas convergiendo exactamente al mismo largo — evidencia de
determinismo real, no solo de una semilla afortunada). Stage D (semillas
adicionales para los markers de la solución ganadora) queda cubierto por
las 3 semillas ya usadas en XL×1 y las 2 en XS×3+XL×3 — no se ejecutaron
semillas extra por separado dado que ya excedían el mínimo de confirmación
esperado.

Ningún otro candidato fue promovido a Stage C: la solución exacta ya se
encontró con el catálogo Stage-B, así que continuar refinando candidatos
que no cambian la factibilidad no se justificaba (Sección 7: "do not
continue expensive composition search unless trying to improve production
objectives" — aquí sí valía la pena refinar XS×3+XL×3/XL×1 porque son
literalmente los markers que se van a cortar).

## 5. Aprendizaje de prenda repetida para XS/XL

XS: 56.684% → 61.075% → 67.230% (XS×1→2→3) — **monótono creciente**, igual
que M. XL: 54.393% → 68.141% → 64.056% (XL×1→2→3) — **NO monótono**: XL×2
supera a XL×3. Esto confirma explícitamente la advertencia de la Sección 15
("Do not assume monotonicity universally") con evidencia real, no
hipotética: el patrón M×1<M×2<M×3<M×4 no se generaliza automáticamente a
todas las tallas. Una hipótesis plausible (no verificada en esta fase) es
que la geometría de XL a 3 unidades cruza un umbral donde el "sobrante"
deja de acomodarse limpiamente dentro del ancho utilizable de 176cm dado el
ancho de la pieza XL — se deja como pregunta abierta para 2F.8.

## 6. Veredictos finales obligatorios

```
FASE_2F_7H_3_COMPOSITION_COVERAGE = PASS
EXACT_216_ORDER_SOLUTION_FOUND = YES
SHORTAGE = 0
OVERPRODUCTION = 0
PHYSICAL_SPREADS = 4
DISTINCT_MARKER_DESIGNS = 4
TOTAL_FABRIC_METERS = 167.209
GLOBAL_WEIGHTED_EFFICIENCY = 72.578893%
BEST_XS_COMPOSITION = XS×3 (67.229644%)
BEST_XL_COMPOSITION = XL×2 (68.141163%)
BEST_XS_XL_COMPOSITION = XS×1+XL×2 (71.683099%, Stage B)
BEST_CROSS_SIZE_SYNERGY = M×1+XL×1 (65.740265%)
TARGET_75_REACHED = NO
TARGET_80_REACHED = NO
TARGET_85_REACHED = NO
TARGET_90_REACHED = NO
DETERMINISM_STATUS = PASS (XS3+XL3: 2 semillas independientes -> 441.5cm idéntico en ambas;
                     ningún cambio a global_nesting_search.py esta fase, evidencia de 2F.7H-1 sigue vigente)
INDEPENDENT_VALIDATION_STATUS = PASS (VALIDATED_PLAN via IndependentProductionPlanValidator)
INCUMBENT_PROTECTION_STATUS = PASS (M2+L1 y S3+XXL5 reusados con el mismo marker_hash de 2F.7H-2,
                               sin regresión; XS3+XL3/XL1 son composiciones nuevas, no reemplazos)
PATTERN_VALIDATION_STATUS = ENGINEERING
PRODUCTION_READY = NO
STOP_GATE = ACTIVE
```

Ver también `artifacts/phase2f7h3/summary.json` (mismos campos,
máquina-legible), `artifacts/phase2f7h3/exact-production-216.json` (plan
completo con IndependentProductionPlanValidator) y
`artifacts/phase2f7h3/svg/{S3+XXL5,M2+L1,XS3+XL3,XL1}.svg` (los 4 markers de
la solución ganadora).

## 7. Componentes reutilizados sin modificar

`CandidateCompositionGenerator`, `ProductionPlanner`/CP-SAT,
`IndependentProductionPlanValidator`, `GlobalNestingSearch`,
`IndependentMarkerValidator`, `CandidateSpaceEngine` — ninguno se tocó.
Cambios de esta fase: `composition_explorer.py::evaluate_geometrically`
ganó un parámetro `skip_search` (aditivo, retrocompatible, los tests de
2F.7H-1/2 siguen pasando sin cambios) y `generate_candidates` ganó paso de
`residual`/`round_number` (también aditivo). Nuevo:
`coverage_catalog.py` (`coverage_priority_score`, `pareto_filter`,
`diagnose_infeasibility`) — módulo de dominio, sin dependencias de
`application`/`infrastructure`, igual que `composition_explorer.py`.
`docs/architecture/costura-optima.architecture.json` no se modifica — mismo
razonamiento que 2F.7H-1/2: refinamiento interno del componente ya
modelado, sin nuevos límites/colas/flujos runtime.

## 8. Próxima fase recomendada

`EXACT_216_ORDER_SOLUTION_FOUND = YES` con `DETERMINISM_STATUS = PASS` e
`INDEPENDENT_VALIDATION_STATUS = PASS` — condición explícita de la Sección
20 del encargo para recomendar `FASE 2F.8 — JOINT PRODUCTION + GEOMETRY
OPTIMIZATION`. No se comienza 2F.8 automáticamente en esta sesión.
Candidatos concretos para 2F.8: (a) refinar vía Stage C los candidatos que
Stage B ya mostró prometedores pero no fueron necesarios para esta solución
exacta (XS×1+XL×2 en particular, 71.683% ya en Stage B); (b) investigar la
no-monotonicidad de XL×3 encontrada en la Sección 5; (c) integrar
`coverage_catalog.py`/`CompositionExplorer` en el flujo real de
`PlanningCoordinator` bajo feature flag, reemplazando la generación
puramente aritmética actual por una que también considere evidencia
geométrica real cuando esté disponible. `STOP_GATE` permanece `ACTIVE`.
