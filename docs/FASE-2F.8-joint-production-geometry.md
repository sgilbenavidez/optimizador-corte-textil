# FASE 2F.8 — Joint Production + Geometry Optimization

`PATTERN_VALIDATION_STATUS = ENGINEERING` · `PRODUCTION_READY = NO` · `STOP_GATE = ACTIVE`

`FASE_2F_8_JOINT_OPTIMIZATION = PASS` — ver el bloque de veredictos completo
en la Sección 19 y `artifacts/phase2f8/summary.json`.

## 1. Delta de arquitectura

Ninguno estructural. `docs/architecture/costura-optima.architecture.json` se
actualiza únicamente para anexar `sources` a los componentes ya modelados
`pipeline` (Candidates) y `solvers` (Production Solvers), apuntando a
`global_nesting_search.py`/`composition_explorer.py` (pipeline) y
`coverage_catalog.py` (solvers) — el mismo patrón que `c8d04ee` ya usó para
2F.7G-8/8P/8P2. No se agregan cajas, colas, datastores ni conexiones nuevas:
toda la nueva capacidad vive dentro del flujo ya modelado Worker →
Optimization Domain → Persistence, activada bajo el flag
`joint_optimization_enabled` (por defecto `False`).

## 2. Componentes modificados

- `apps/api/src/costura_optima/settings.py`: `joint_optimization_enabled: bool = False`.
- `apps/api/src/costura_optima/application/schemas.py`: campo opcional
  `joint_optimization_enabled` en `OptimizationRunCreate` (mismo patrón que
  `planner_refinement_engine`).
- `apps/api/src/costura_optima/application/services.py`: resuelve el flag
  hacia `run.configuration` (Settings → payload → configuration, mismo
  patrón de 3 saltos ya establecido); extrae `build_marker_request` de
  `MarkerPreviewService.generate` (extracción mecánica, sin cambio de
  comportamiento — sus propios tests pasan sin cambios).
- `apps/api/src/costura_optima/application/planning_coordinator.py`:
  extrae `_persist_marker_artifact` de `_evaluate_candidate` (misma razón);
  agrega `_refine_marker_geometry`/`_refine_plan_with_geometry` (nuevo,
  Sección 3); agrega el `run.phase = "REFINING_MARKERS"`.

Sin cambios en `production_planner.py`, `production_plan_validator.py`,
`candidate_generator.py`, `operational_heuristic_solver.py`,
`worker/recovery.py`, `worker/queue.py`, `worker/tasks.py` — toda la
infraestructura de colas, recuperación, CP-SAT y validación independiente
se reutiliza exactamente como estaba.

## 3. Pipeline runtime

**Hallazgo central de esta fase**: al leer `PlanningCoordinator.execute`
completo (704 líneas) se confirmó que el pipeline round-based,
residual-driven, con oráculo CP-SAT, incumbent anytime, cache de markers
por hash y auditoría por candidato — exactamente lo que las Secciones
4/6/10/21 del encargo describen como "nuevo" — **ya existe y ya está en
producción**, usando el motor legado. Reescribirlo, o enrutar cada
candidato por `CompositionExplorer`/`GlobalNestingSearch`, habría sido
exactamente el "rediseñar un algoritmo que funciona" que la Sección 2 del
encargo prohíbe explícitamente.

Lo único genuinamente nuevo — y lo que la Sección 12 del encargo llama
"central para 2F.8" — es el refinamiento geométrico consciente del plan:

```
best_valid (ya calculado por el round loop existente)
  → seleccionar markers USADOS por el mejor plan (por contribución a tela: layers × repeats × largo)
  → limitar a joint_optimization_max_markers_to_refine (3)
  → por cada marker: GlobalNestingSearch sembrado desde SUS PROPIOS placements ya validados
    (nunca vuelve a correr el motor legado)
  → si el resultado es más corto Y pasa IndependentMarkerValidator: nuevo MarkerArtifactORM
    (content_key distinto, engine="joint-optimization-alns-v1"; el original NUNCA se sobreescribe)
  → sustituir en el catálogo, remove_dominated, heuristic.solve + refinement.solve_profiles
    (las MISMAS llamadas que el round loop ya hace)
  → _merge_valid_solutions (nunca descarta una solución previamente validada)
```

Se ejecuta una sola vez, después de que el round loop termina, solo si
`joint_optimization_enabled` está activo y ya existe un `best_valid`.

## 4. Feature flag

`joint_optimization_enabled` sigue el patrón de 3 saltos ya establecido por
`planner_refinement_engine`: `Settings.joint_optimization_enabled` (default
`False`) → override opcional por request
(`OptimizationRunCreate.joint_optimization_enabled`) →
`run.configuration["joint_optimization_enabled"]` → leído dentro de
`execute()`. Con el flag apagado (el default), `_refine_plan_with_geometry`
retorna `(best_valid, markers)` sin modificar — confirmado por test
(`test_flag_off_never_calls_refinement`: `_refine_marker_geometry` nunca se
invoca) y por el benchmark (Sección 12: baseline idéntico en ambos
intentos, 84-85s, mismo resultado).

## 5. Comportamiento de crecimiento del catálogo

Sin cambios: el round loop sigue generando/filtrando composiciones con
`CandidateCompositionGenerator` (residual-driven desde la ronda 2, igual
que siempre). El refinamiento geométrico de 2F.8 actúa *después*, sobre el
catálogo que el round loop ya produjo — no participa en la generación de
composiciones nuevas. `coverage_catalog.py`/`CompositionExplorer` (2F.7H-2/3)
permanecen sin integrar al runtime — el hallazgo de la Sección 3 hace que
integrarlos ahí sea redundante con lo que el round loop ya hace.

## 6. Rondas de planificación

Sin cambios en la lógica de rondas (`max_rounds`, límites de candidatos,
presupuestos por ronda). El refinamiento ocurre una única vez, fuera del
loop de rondas, no una ronda adicional.

## 7. Comportamiento de refinamiento de markers

Presupuesto acotado deliberadamente para un contexto de worker realista:
`max_iterations=3`, `candidate_budget=1000`, `piece_time_budget_ms=3000`,
1 semilla (`config["seed"]`), hasta 3 markers por corrida, presupuesto total
de refinamiento `joint_optimization_refinement_budget_seconds=60` (acotado
además por el `deadline` global de la corrida). Esto es intencionalmente
mucho menos profundo que las corridas experimentales de 2F.7H-1/2/3
(multi-semilla, cientos de iteraciones-segundo por marker) — esa evidencia
ya existe; esta fase demuestra el *mecanismo* de integración bajo
restricciones de tiempo reales de worker, no vuelve a derivar la mejor
geometría posible.

**Hallazgo de costo real**: incluso cuando el refinamiento no mejora ningún
marker (`markers_refined=0`), el intento en sí consume tiempo real (~90-200s
adicionales en las corridas de benchmark). Esto empujó una corrida en modo
exacto a `TIMED_OUT` (con el incumbent preservado, no una falla) en vez de
`SUCCEEDED` bajo un presupuesto de 280s. Esto es un hallazgo honesto sobre
el costo de intentar refinamiento, no un defecto del mecanismo en sí — debe
presupuestarse explícitamente (Sección 24 del encargo) en cualquier
despliegue futuro.

## 8. Caché

Reutilizado sin cambios: cada marker refinado obtiene su propio
`content_key` (incluye `engine="joint-optimization-alns-v1"`, semilla,
composición, `max_iterations`), nunca colisiona con ni sobreescribe el
`MarkerArtifactORM` del motor legado para la misma composición — confirmado
por `test_refined_marker_gets_a_distinct_cache_entry_from_the_original`
(prueba real, sin mocks, geometría real sobre M×1). Un intento de
refinamiento repetido con la misma configuración reutiliza la fila cacheada
(`marker_cache_hits`) en vez de volver a correr ALNS.

## 9. Determinismo

`test_orchestration_is_deterministic_given_identical_inputs` confirma que,
dado el mismo `best_valid`/catálogo/resultado de refinamiento, la lógica de
orquestación (selección de markers, sustitución, replanificación,
comparación) produce el mismo `_best_key` y el mismo conjunto de
`marker_hash` en dos corridas independientes. El determinismo del propio
ALNS ya fue probado exhaustivamente en 2F.7H-1 (mismo código,
sin cambios) — esta fase no lo vuelve a probar, prueba que la nueva capa de
orquestación no introduce no-determinismo propio.

## 10. Timeout/recuperación

El contrato anytime existente se preserva sin cambios: `_merge_valid_solutions`
nunca descarta una solución previamente validada, así que el refinamiento
solo puede *añadir* opciones, nunca perder el incumbent. La corrida en modo
exacto que terminó en `TIMED_OUT` demuestra esto en vivo:
`best_solution_available: true`, `shortage: 0`, `overproduction: 0` — el
plan válido se preservó exactamente como pide la Sección 5 del encargo
("TIMED_OUT_WITH_VALID_SOLUTION" semánticamente, usando el status/campo
`error_detail` ya existentes, sin proliferación de nuevos estados).
`worker/recovery.py` no se modifica: un crash a mitad de refinamiento se
maneja por el mecanismo de detección-de-corridas-obsoletas ya existente,
igual que un crash a mitad de cualquier otra fase — no se agregó ninguna
capacidad de resume nueva, y por lo tanto no se inventó ninguna prueba de
resume para una capacidad que no se construyó.

## 11. Cancelación

`_refine_plan_with_geometry` llama `_check_cancel` antes de cada marker;
`test_cancellation_mid_refinement_propagates_and_stops_further_markers`
confirma que una cancelación a mitad del loop de refinamiento detiene el
procesamiento de markers restantes y propaga `RunCancelled` sin publicar
ningún resultado parcial.

## 12. Comparación de benchmark (216 prendas, vía API real)

Dos series de corridas, ambas con `PlanningCoordinator.execute()` invocado
directamente vía `client`/`TestClient`, exactamente como lo hace
`worker/tasks.py::execute_optimization_run`:

**Serie A (modo acotado, `allow_overproduction=True`, caché de DB
compartida entre baseline y joint)**:

| Modo | Estado | Tendidos | Diseños | Tela (m) | Eficiencia global |
| --- | --- | --- | --- | --- | --- |
| baseline | SUCCEEDED | 10 | 6 | 225.865 | 53.730% |
| joint | SUCCEEDED | 5 | 5 | 189.612 | 65.738% |

Mejora real de 36.25m / +12pp — pero **con una salvedad importante**: ambas
corridas comparten la misma base de datos en memoria dentro del script de
benchmark, así que `joint` se benefició de cache hits de markers que
`baseline` ya había persistido, no (solo) de refinamiento geométrico
(`markers_refined=0` también aquí). No se puede atribuir esta mejora al
mecanismo de 2F.8 con confianza sin una comparación aislada.

**Serie B (modo exacto, `allow_overproduction=False`, bases de datos
aisladas por modo — comparación justa)**:

| Modo | Estado | Tendidos | Diseños | Tela (m) | Eficiencia global | Runtime |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | SUCCEEDED | 10 | 6 | 225.865 | 53.730% | 84.9s |
| joint | TIMED_OUT (incumbent preservado) | 10 | 6 | 225.865 | 53.730% | 299.9s |

Con una comparación limpia (sin compartir caché), ambos modos convergen al
**mismo plan exacto** (`shortage=0, overproduction=0` en ambos). El
refinamiento consideró 3 markers, mejoró 0 — el resultado final es idéntico
al legado, y el único efecto medible de 2F.8 en esta corrida fue el costo
de tiempo adicional (215s más), que llevó la corrida a `TIMED_OUT` en vez
de `SUCCEEDED` bajo el presupuesto configurado.

## 13. Plan de producción ganador (Serie B, modo exacto)

10 tendidos físicos, 6 diseños de marker distintos, 225.865 m de tela total,
53.730473% de eficiencia global ponderada, `shortage=0`, `overproduction=0`,
validado independientemente (`VALIDATED_PLAN`). Idéntico entre baseline y
joint en esta corrida — ver Sección 12.

## 14. Lista de markers

Ver `artifacts/phase2f8/benchmark-comparison.json` para el detalle completo
de cada corrida (composiciones no exportadas individualmente en esta fase
para mantener el reporte enfocado; el catálogo completo por ronda ya está
disponible vía el endpoint `/audit` existente, sin cambios).

## 15. Tela total

225.865 m (Serie B, ambos modos) — no mejora sobre 167.209m (evidencia
experimental curada de 2F.7H-3). Ver Sección 17 para el análisis honesto de
esta brecha.

## 16. Eficiencia global ponderada

53.730473% (Serie B) — calculada por `ProductionPlanner`/
`IndependentProductionPlanValidator` con la fórmula exacta del encargo
(`SUM(piece_area×layers×repeats) / SUM(marker_area×layers×repeats)`, ya
implementada, sin cambios, reconfirmada en el código: `production_planner.py`).

## 17. Carencias

- El refinamiento geométrico no encontró ninguna mejora medible en ninguna
  de las dos corridas de benchmark limpias (`markers_refined=0`). El
  mecanismo está probado como correcto (9 tests, incluyendo uno de extremo
  a extremo con geometría real), pero su *efectividad* dentro de un
  presupuesto de worker realista no quedó demostrada en este benchmark
  específico.
- El generador de composiciones en vivo (`CandidateCompositionGenerator`
  vía el round loop) no alcanza el resultado de 167.209m que 2F.7H-1/2/3
  lograron mediante tres fases dedicadas de búsqueda manual/curada
  (M×1-4, M×2+L×1, S×3+XXL×5, XS×3+XL×3, XL×1). Esta es una brecha real
  entre "lo mejor que se puede lograr con exploración experimental dirigida"
  y "lo que el sistema automático encuentra dentro de presupuestos
  acotados por ronda" — no una regresión de nada publicado en el sistema
  vivo (167.209m nunca fue un incumbent del sistema en producción, solo un
  artefacto experimental).
- El refinamiento, incluso sin éxito, tiene un costo de tiempo real
  (~90-215s adicionales observados) que debe presupuestarse explícitamente;
  en un caso empujó una corrida de `SUCCEEDED` a `TIMED_OUT`.
- La Serie A (caché compartida) muestra que el sistema SÍ puede encontrar
  planes sustancialmente mejores (189.6m/65.7%) cuando el catálogo de
  markers ya está poblado — sugiere que la ganancia real de 2F.8 podría
  estar más en *acumular* un catálogo de markers entre corridas sucesivas
  del mismo pedido que en el refinamiento ALNS en sí, una hipótesis para
  2F.8.1/2F.9, no confirmada aquí.

## 18. Próxima fase recomendada

Con el mecanismo de integración probado correcto (tests, determinismo,
cancelación, protección de incumbent, no-regresión de la ruta legada) pero
sin ganancia demostrada en este benchmark específico, se recomienda:

1. **Antes de cualquier fase nueva**: investigar por qué el refinamiento no
   mejoró ningún marker en Serie B — ¿los markers que el round loop en vivo
   produce ya están cerca de su óptimo local para ALNS con presupuesto tan
   acotado, o el presupuesto (3 iteraciones, 1 semilla, 3s/pieza) es
   simplemente insuficiente incluso para una mejora marginal? Esto se
   puede investigar con un script experimental fuera del runtime, sin
   volver a tocar `planning_coordinator.py`.
2. Si se confirma que el presupuesto es el limitante: considerar exponer
   `joint_optimization_refinement_budget_seconds`/
   `joint_optimization_max_markers_to_refine` como overrides de
   configuración por request (hoy son fijos en `services.py`), permitiendo
   una corrida explícitamente más profunda para pedidos donde vale la pena
   el costo de tiempo adicional.
3. Investigar la hipótesis de "catálogo acumulado entre corridas" de la
   Sección 17 como una vía de mejora más prometedora que el refinamiento
   ALNS de un solo run.

No se comienza ninguna fase nueva automáticamente. `STOP_GATE` permanece
`ACTIVE`.

## 19. Veredictos finales obligatorios

```
FASE_2F_8_JOINT_OPTIMIZATION = PASS
FEATURE_FLAG_INTEGRATION = PASS
EXACT_216_ORDER_SOLUTION_FOUND = YES
SHORTAGE = 0
OVERPRODUCTION = 0
PHYSICAL_SPREADS = 10
DISTINCT_MARKER_DESIGNS = 6
TOTAL_FABRIC_METERS = 225.865
GLOBAL_WEIGHTED_EFFICIENCY = 53.730473%
PREVIOUS_TOTAL_FABRIC = 167.209
FABRIC_IMPROVEMENT_METERS = 0.0 (no improvement demonstrated this session in the clean/isolated comparison)
PREVIOUS_GLOBAL_EFFICIENCY = 72.578893%
GLOBAL_EFFICIENCY_GAIN_PP = -18.848 (live automated system vs. 2F.7H's curated experimental catalog -- not a regression of a published incumbent, see Section 17)
PRODUCTION_PLAN_VALIDATION = PASS
MARKER_VALIDATION = PASS
DETERMINISM_STATUS = PASS
RECOVERY_STATUS = PASS (worker/recovery.py unchanged; no new resume capability added or claimed)
CANCELLATION_STATUS = PASS
LEGACY_PATH_REGRESSION = PASS (flag-off behavior byte-identical to pre-2F.8: 84-85s, same plan, both trials)
PATTERN_VALIDATION_STATUS = ENGINEERING
PRODUCTION_READY = NO
STOP_GATE = ACTIVE
```

Ver también `artifacts/phase2f8/summary.json` (mismos campos,
máquina-legible, generado por `finalize_phase2f8.py`) y
`artifacts/phase2f8/benchmark-comparison.json` (ambas series de benchmark
completas).
