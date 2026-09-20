# COSTURA ÓPTIMA — cierre FASE 2E.1

Fecha de verificación: 2026-09-04 (America/Bogota).

## 1. Causas raíz

1. El corte de `max_marker_candidates` seguía el orden de generación: fallbacks y repeats agotaban el cupo antes de los pares. Había un segundo truncamiento equivalente en `max_candidate_compositions`.
2. `residual` era aceptado por el generador, pero el coordinador sólo ejecutaba un ciclo generate/geometry/plan.
3. `rounds`, `marker_length_within_table` y `audit_complete` eran datos decorativos sin cálculo efectivo.
4. La dominancia usaba una elección lexicográfica por composición, no el frente de Pareto longitud/desperdicio.
5. El fingerprint incluía la asignación exacta de capas, por lo que separaba resultados comerciales iguales.
6. UI afirmaba optimalidad incondicionalmente; objective stages no salían por `/audit`; recovery convertía todo lease vencido en `FAILED`; el retry frontend creaba otra clave.
7. En C bounded, repeats ocupaban el presupuesto interno y excluían pares que sí quedaban en exact. No era una propiedad matemática de la sobreproducción.

## 2. Archivos modificados

- Dominio/coordinación: `candidate_generator.py`, `production_models.py`, `production_planner.py`, `production_plan_validator.py`, `planning_coordinator.py`.
- API/worker: `schemas.py`, `services.py`, `queue.py`, `tasks.py`, `recovery.py`.
- Frontend: `NewOrderPage.tsx`, `OptimizationRunPage.tsx`, `types.ts`, `styles.css`.
- Pruebas: `test_production_planner.py`, `test_phase2e1_corrections.py`, `OptimizationRunPage.test.tsx`, `NewOrderPage.test.ts`.
- Evidencia: `run_phase2e1_verification.py` y `artifacts/phase2e1/*`.

## 3. Solución aplicada

La selección es determinista y estratificada: conserva fallbacks obligatorios, prioriza PAIR/TRIPLE/RESIDUAL_DRIVEN, rellena por ranking y reserva hasta cuatro evaluaciones globales para residual. Los candidatos exact-compatible se seleccionan primero también en bounded, preservando el núcleo factible exact. El default pasa de 14 a 24 por evidencia del benchmark, no por incremento arbitrario.

El coordinador ejecuta hasta `max_rounds`: genera, deduplica por composición, evalúa sólo nuevos, añade artifacts, replanifica, valida y registra causa de parada. El residual se calcula descontando producción aportada por marcadores multi-talla; la segunda ronda añade pares ponderados cuando la política lo permite.

## 4. Pruebas agregadas

Se cubren: B multi-size evaluado; ronda residual; audit rounds; marker mayor que mesa; audit incompleta; dominancia y no-dominancia; fingerprint material/D; texto FEASIBLE; objective stages; transición TIMED_OUT; hard-timeout e interrupción; FALLBACK_SINGLE_SIZE; clave idempotente frontend; PEDIDO 75; comparación completa. Total final backend: 76/76. Frontend: 10/10. TypeScript: limpio.

## 5. Antes/después

- B: 0 candidatos multi-size evaluados → 13 con default 24 (22 candidatos evaluados totales en Docker).
- C anterior: bounded 7.14922 m/7 frente a exact 6.34655 m/4. Ahora, en local y Docker: bounded = exact = 5.935 m/3, OPTIMAL.
- D: las dos tarjetas idénticas MIN_FABRIC/BALANCED se convierten en una alternativa `Menor tela · Balanceada`; la alternativa MIN_SPREADS permanece porque materialmente sí difiere.

## 6. Benchmark de presupuesto

Medición local reproducible, mismo budget geométrico y de solver:

| Límite | B: tela/tendidos/multi/ms | C: tela/tendidos/multi/ms |
|---:|---|---|
| 10 | 65.365 m / 11 / 5 / 3037 | 6.815 m / 5 / 3 / 412 |
| 14 | 68.915 m / 9 / 9 / 3049 | 5.935 m / 3 / 7 / 522 |
| 18 | 71.920 m / 9 / 12 / 3096 | 5.935 m / 3 / 11 / 726 |
| 24 | 64.305 m / 8 / 13 / 3074 | 5.935 m / 3 / 17 / 1122 |

B permanece FEASIBLE, por eso los incumbents no son monótonos entre límites. Se eligió 24: máxima cobertura multi-size, mejor incumbent observado de B, mejora C frente a 10 y coste de B esencialmente igual a 10/14/18.

## 7. Rondas residuales B

- R1: 24 generados, 20 evaluados, 20 factibles, 4 NOT_EVALUATED; incumbent 74.655 m/10.
- Residual: M=20, XL=12, XXL=16 (S/L resueltos mediante multi-size en esa ejecución local).
- R2: 13 generados, 11 deduplicados, 2 nuevos evaluados/factibles; incumbent 64.305 m/8; mejora material verdadera; parada `MAX_ROUNDS`.
- Docker con geometría de producción: 74.655 m/10 → 74.16954 m/12; sigue siendo mejora de tela y status FEASIBLE.

## 8. Rondas residuales C

- R1: 24 generados, 20 evaluados/factibles; 6.425 m/4.
- Residual dirigido a XXXL=1.
- R2: 24 generados, 16 deduplicados, 4 nuevos evaluados; 5.935 m/3; OPTIMAL; parada `MAX_ROUNDS`.

## 9–10. A–E bounded y exact (Docker, configuración productiva)

| Caso | Bounded | Exact |
|---|---|---|
| A | 2.095 m / 1 / OPTIMAL | 2.095 m / 1 / OPTIMAL |
| B | 74.16954 m / 12 / FEASIBLE | 74.16954 m / 12 / FEASIBLE |
| C | 5.935 m / 3 / OPTIMAL | 5.935 m / 3 / OPTIMAL |
| D | 74.58966 m / 3 / OPTIMAL | 74.58966 m / 3 / OPTIMAL |
| E | 31.78852 m / 2 / OPTIMAL | 31.78852 m / 2 / OPTIMAL |

Todas las soluciones tienen shortage cero y pasan el validador independiente.

## 11. Quality gate tamper

El validador vuelve a calcular `marker_length_units <= usable_table_length_units`. Un marker 120 sobre mesa 119 devuelve `INVALID_PLAN`. La ausencia de cualquiera de los campos auditables, o hashes de marker inconsistentes, devuelve `INVALID_PLAN`; ya no hay booleanos hardcodeados.

## 12. Dominancia

Se aplica al catálogo validado, donde sí pueden converger artifacts/cache equivalentes. A domina B sólo con el mismo vector por capa, longitud y waste no mayores y al menos una desigualdad estricta. La prueba pública elimina 200/200 ante 180/180 y conserva 170/300 como trade-off no dominado.

## 13. Deduplicación D

`MaterialSolutionFingerprint` usa tela total, tendidos, producción, sobreproducción y eficiencia global; excluye la secuencia exacta de capas. Docker entrega `MIN_FABRIC + BALANCED` como una sola alternativa (74.58966 m, 3 tendidos) y mantiene `MIN_SPREADS` (75.25 m, 2 tendidos), que sí cambia la ejecución comercial.

## 14–15. UI y objective stages

OPTIMAL usa el texto de optimalidad sobre el catálogo; FEASIBLE usa “Mejor planificación validada encontrada dentro del presupuesto de cálculo”; UNKNOWN/timeout niega explícitamente que se haya probado optimalidad. `/audit` expone por solución/perfil `stage`, `objective`, `status`, `value`, `bound` cuando CP-SAT lo aporta, `elapsed_ms` y `fixed_from_previous_stage`. La UI lo muestra dentro de Auditoría técnica colapsable.

## 16. Timeout/recovery

El deadline cooperativo produce `RUNNING → TIMED_OUT`. Un resultado ya validado y persistido puede conservarse con `best_solution_available=true`; TIMED_OUT no se convierte en SUCCEEDED. El callback RQ clasifica `JobTimeoutException` como `worker_hard_timeout`; lease/interrupción sin señal de timeout queda FAILED/`worker_interrupted` o `worker_lease_expired`.

## 17. Solution origin

Se emiten tres estados auditables: `FALLBACK_SINGLE_SIZE` si todos los markers usados son fallbacks unitarios; `RESIDUAL_IMPROVED` si se usa un marker de ronda residual; `MIXED_CANDIDATES` en los demás catálogos validados.

## 18. Idempotencia E2E

El frontend mantiene una `OptimizationIntent` con una sola UUID y el mismo `order_id` durante retries o dobles submits concurrentes; una prueba verifica una sola creación de orden y una sola generación de UUID. La prueba API existente verifica mismo `order_id` + Idempotency-Key → mismo run y rechazo si cambia la configuración.

## 19. Performance

Se miden directamente `candidate_generation_ms`, `geometry_ms`, `planning_ms`, `validation_ms`, `serialization_ms` y `total_ms`. `database_ms` se entrega como `null` con nota explícita: la latencia de commit no se aisló limpiamente de flush/ORM; no se presenta una resta como medición.

## 20. Reproducibilidad

La segunda batería A–E coincide 5/5 en `input_hash`, lista de `solution_hash` y `marker_hashes`. Timings no participan en hashes de contenido.

## 21. Docker

`docker compose build` y `docker compose up -d` ejecutados realmente. PostgreSQL y Redis healthy; API `/health=ok`; worker RQ escuchando `optimization`; web HTTP 200. Dentro de imágenes: backend 76/76, frontend 10/10, TypeScript limpio. La batería HTTP asíncrona bounded/exact terminó SUCCEEDED 10/10.

## 22. Riesgos y deuda restante

- B sigue FEASIBLE: no se afirma optimalidad del catálogo ni global.
- La medición aislada de base de datos queda pendiente y se declara `null`.
- La recuperación hard-timeout depende de que RQ entregue el tipo/mensaje de excepción al callback; existe test de clasificación y recuperación persistida.
- Pattern Engineering continúa en estado ENGINEERING y no se modificó Geometry Engine.

FASE_2E_1_CORRECTION = PASS

FASE_2E_PRODUCTION_PLANNING = PASS

CANDIDATE_GENERATOR_STATUS = PASS

RESIDUAL_ROUNDS_STATUS = PASS

PLANNER_STATUS = PASS

PLAN_VALIDATOR_STATUS = PASS

ASYNC_WORKER_STATUS = PASS

IDEMPOTENCY_E2E_STATUS = PASS

ORDER_CASES_A_E = PASS

DOCKER_VERIFICATION = PASS

PATTERN_VALIDATION_STATUS = ENGINEERING

GLOBAL_GEOMETRIC_OPTIMALITY_CLAIMED = NO

STOP_GATE = ACTIVE

IMPLEMENTATION_AUTHORIZED_FOR_PHASE_2F = NO
