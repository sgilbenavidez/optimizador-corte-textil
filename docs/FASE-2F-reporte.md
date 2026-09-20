# COSTURA ÓPTIMA — cierre FASE 2F y 2F.1

Fecha de verificación: 2026-09-04 (America/Bogota). El patrón permanece `ENGINEERING / UNVALIDATED_FOR_PRODUCTION` y la implementación no modifica GeometryEngine, la formulación CP-SAT ni la semántica aprobada de ProductionPlanner.

## 1. Arquitectura operacional final

El flujo es `HTTP + X-Request-ID → PostgreSQL/OptimizationRun → Redis/RQ → coordinador → GeometryEngine/cache → ProductionPlanner → validador → publicación atómica → API resumen/artefactos bajo demanda → React por run_id`. PostgreSQL es la fuente de verdad; React sólo conserva preferencias visuales efímeras.

## 2. Cambios backend

Se normalizaron los estados `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `TIMED_OUT` e `INFEASIBLE`, y las fases `GENERATING_CANDIDATES`, `NESTING`, `PLANNING`, `VALIDATING` y `FINALIZING`. Cada run expone timestamps, elapsed, contadores, ronda y disponibilidad de solución. Se añadieron endpoints paginados, retry, export, SVG certificado, health y métricas.

## 3. Cambios frontend

Las rutas directas reconstruyen desde backend orden, run, progreso, resultado, error y auditoría. Hay estados loading/empty/error/retry, terminales diferenciados, comparación desktop/mobile, navegación por historial y mapa de corte principal. No se almacena el run crítico únicamente en memoria.

## 4. Historial

`/orders` pagina órdenes con fecha, modelo, cantidad, último run, estado y disponibilidad de resultado. `/orders/{order_id}` muestra snapshot histórico, demanda, recursos, patrón ENGINEERING y todos los runs numerados sin reemplazar resultados anteriores.

## 5. Retries

`FAILED` y `TIMED_OUT` admiten retry. La acción crea un nuevo `OptimizationRun` con configuración copiada e identidad propia; el run anterior queda inmutable y el cache de markers puede reutilizarse.

## 6. CANCELLED, TIMED_OUT, FAILED e INFEASIBLE

La cancelación pide confirmación, marca intención persistida y tiene checkpoints antes de publicar; un worker tardío no puede convertirla en éxito. Timeout conserva una solución validada si existe, pero la UI la rotula explícitamente como no óptima. Failed muestra mensaje sanitizado, `error_code` y `run_id`; exception class/fase quedan sólo en auditoría/log. Infeasible usa mensaje propio y no inventa causalidad.

## 7. Export

`GET /api/v1/optimization-runs/{run_id}/export` entrega JSON versionado con order, run, solución recomendada, spreads, producción, métricas, referencias de markers, hashes, validaciones y auditoría. DXF y PDF se mantienen fuera de alcance.

## 8. Observabilidad

Se añadieron health diferenciados, señal de consumidores RQ, métricas OpenMetrics y audit trail por run. La UI permite entender el estado sin entrar a Redis, PostgreSQL o logs.

## 9. Logs

Los logs operativos son JSON y contienen, cuando aplica, `request_id`, `order_id`, `run_id`, `job_id`, `solution_id`, `marker_hash` y `phase`. Los fallos registran clase de excepción sin credenciales, URLs privadas, rutas ni stack para el usuario.

## 10. Correlation ID

El middleware acepta un `X-Request-ID` seguro o genera UUID, lo devuelve en respuesta, lo persiste en el run y el worker lo recupera para sus logs. El contrato problem+json incorpora el mismo identificador.

## 11. Métricas

`/api/v1/metrics` publica `optimization_runs_total`, resultados terminales por estado, `optimization_duration_seconds`, `geometry_duration_seconds`, `planning_duration_seconds`, `candidate_count`, hits/misses de cache y `queue_depth`. Las métricas de runs/candidatos se derivan de DB para atravesar la frontera API/worker.

## 12. Healthchecks

`/health/live` sólo prueba proceso. `/health/ready` prueba `SELECT 1` y Redis PING sin ejecutar solver. PostgreSQL, Redis, API y web tienen healthchecks reales en Compose; el falso negativo web por resolución IPv6 de `localhost` fue corregido usando `127.0.0.1`.

## 13. Worker health

`/health/worker` requiere al menos un RQ worker registrado en estado idle/busy y expone nombres y profundidad. El contenedor worker ejecuta la misma comprobación; así se distingue Redis operativo sin consumidor. La detección está acotada por el TTL/heartbeat de RQ.

## 14. Configuración

`Settings` centraliza ambiente, DB, Redis, CORS, log level, cola, timeout, tamaño máximo y lease stale. `.env.example` documenta valores; no hay `os.getenv()` operacional disperso.

## 15. Security baseline

CORS es explícito, los UUID/DTO se validan, Content-Length tiene límite, se agregan headers básicos y los errores no filtran internals. `.env` está ignorado y la inspección de archivos versionados no encontró secretos productivos. `AUTHENTICATION = OUT_OF_SCOPE_MVP`.

## 16. Migraciones

Se añadió `20260904_0004_operational_integration`. Se verificó en SQLite de prueba la cadena vacía `0001 → 0002 → 0003 → 0004` y el upgrade `0003 → 0004`, sin recrear datos como mecanismo de upgrade.

## 17. Bootstrap desde cero

Se ejecutó `docker compose down -v`, `docker compose build` y `docker compose up -d`. El entrypoint del API aplica migraciones, seed y generación de patrón; el seed repetido conservó exactamente 1 tipo, 1 modelo, 1 versión, 7 tallas, 91 medidas, 1 tela, 1 mesa y 1 perfil. La generación reutilizó el hash `977412…e470`.

## 18. Concurrencia

Las corridas conservan job, hashes, artifacts y estados independientes. La carga warm A–E bounded/exact ejecutó diez jobs y pasó con una y dos réplicas worker.

## 19. Cache concurrency

`marker_artifacts.content_key` es único. La inserción se hace en savepoint; ante `IntegrityError` el perdedor revierte sólo el savepoint y reutiliza el artefacto ganador. La ejecución real multi-worker terminó sin hashes divergentes ni artifacts corruptos.

## 20. Recovery tests

La recuperación por lease convierte runs huérfanos en terminal coherente y conserva `failure_phase`. La suite cubre muerte durante `NESTING`, interrupción durante `PLANNING`, soft timeout y hard timeout. Se reinició Redis en el stack y API/workers recuperaron readiness; se reinició PostgreSQL y la API recuperó readiness conservando 31 órdenes.

## 21. Transacciones

Solución, spreads, resultados por talla, hashes y certificado se agregan en la misma transacción y sólo después el run se marca exitoso. Una excepción ejecuta rollback antes de publicar FAILED; una cancelación se revisa otra vez antes del commit final. No existe endpoint que exponga solución parcial como válida.

## 22. Paginación

Órdenes y runs asociados aceptan `page`/`page_size` y devuelven `items`, `total` y `pages`. La UI presenta navegación simple y estados vacíos.

## 23. Estrategia de payload

Listas y comparación usan summaries sin polígonos. Los spreads sólo llevan referencia `marker_hash`; placements/polígonos se cargan bajo demanda desde `/markers/{hash}` y el SVG desde `/markers/{hash}/svg`.

## 24. Responsive y accesibilidad

Hay layouts específicos para 390 px, 601–1100 px y escritorio 1440 px. Las métricas móviles no se ocultan. Controles tienen labels, fieldsets, estados anunciables, foco, botones reales y semántica de tabla; el SVG es focusable y no es la única fuente de información.

## 25. Benchmark cold cache

El suite comenzó después de volumen vacío. Los tiempos son milisegundos:

| Caso | Bounded total / geometry / planning | Exact total / geometry / planning | Tela bounded=exact |
|---|---:|---:|---:|
| A | 7248 / 7113 / 56 | 147 / 0 / 68 | 2.095 m |
| B | 82927 / 68543 / 14155 | 14317 / 0 / 14094 | 74.16954 m |
| C | 38459 / 37637 / 631 | 603 / 0 / 441 | 5.935 m |
| D | 3467 / 3238 / 141 | 225 / 0 / 148 | 74.58966 m |
| E | 6764 / 6423 / 248 | 187 / 0 / 107 | 31.78852 m |

En B bounded hubo 22 candidatos evaluados: 2 hits heredados dentro del propio suite y 20 misses; exact tuvo 22 hits y 0 misses. Esto evita presentar la segunda pasada como cold.

## 26. Benchmark warm cache

La repetición completa tuvo `geometry_ms = 0` en las diez corridas. Con un worker el wall time fue 39.083 s. B bounded midió 14.339 s (planning 14.177 s) y B exact 14.301 s (planning 14.128 s).

## 27. Benchmark multi-worker

Con dos workers, la misma carga warm de diez corridas tardó 22.683 s frente a 39.083 s con uno: throughput 1.72×, sin afirmar escalamiento lineal. B individual permaneció alrededor de 14.34 s; la mejora provino del paralelismo entre jobs. CPU/memoria no se convierten aún en SLO.

## 28. Regresión A–E

Bounded y exact terminaron SUCCEEDED en A–E. Tela/hash recomendado coincidieron por pareja: A `2.095 m`, B `74.16954 m`, C `5.935 m`, D `74.58966 m`, E `31.78852 m`. Todas conservaron shortage cero y certificado de plan válido.

## 29. Tests

La suite backend completa pasó: 81/81; incluye estados, retry, timeout, recovery NESTING/PLANNING, problem+json, correlation, readiness, export, SVG certificado, auditoría, transacciones/validación, paginación y cache. Frontend: 6 archivos y 11/11 tests pasaron; cubren historial, refresh/reapertura, estados y mapa real. TypeScript/Vite production build pasó.

## 30. Docker

Build y arranque desde volumen vacío pasaron. Tras reconstruir, `docker compose ps` muestra DB, Redis, API, web y dos workers healthy. Los endpoints readiness y worker health respondieron OK.

## 31. Incidentes

Se encontró un falso `unhealthy` del web: wget a `localhost` elegía una dirección no atendida pese a HTTP 200 en IPv4. Se cambió el probe a `127.0.0.1` y se verificó desde el contenedor. No hubo corrupción durante reinicios Redis/PostgreSQL.

## 32. Riesgos

No hay autenticación; health de worker depende del TTL de RQ; métricas son agregados básicos y no sustituyen alertas; no existe HA de Redis/PostgreSQL; la geometría sigue sin afirmar optimalidad global y el patrón continúa experimental.

## 33. Deuda técnica

Agregar Prometheus externo/alertas, pruebas chaos prolongadas con kill -9 en producción-like, SLO de cola, medición sostenida CPU/RAM, retención de auditoría, autorización por rol y export PDF/DXF posterior.

## 34. Recomendación para FASE 2G

No autorizar 2G ni uso productivo mientras `PATTERN_VALIDATION_STATUS = ENGINEERING`. El siguiente gate debe priorizar validación industrial del patrón, autenticación/autorización y ensayo operacional prolongado; no otra modificación heurística sin bug reproducible.

## FASE 2F.1 — mapa de corte

Cada solución presenta `MAPA DE CORTE` después del resumen y producción por talla. Una tabla y selector siguen `execution_order`; capas/repeats se explican aparte y no duplican polígonos. La suma derivada por tendido se contrasta con `production_by_size`.

El cliente consume `marker_hash → MarkerArtifact` bajo demanda y dibuja exclusivamente `transformed_polygon`, transform, rotación, talla, pieza y grainline certificados. Usa ancho/largo y `geometry_units_per_cm` reales; no recalcula placements. Un artefacto no validado se rechaza como mapa válido.

La interacción incluye zoom, pan, ajustar, reset, fullscreen, labels, hilo, bbox, clearance rotulado como aproximado, colores+texto por talla, tooltips y leyenda. `/markers/{marker_hash}` ofrece vista ampliada y el download entrega el SVG del marker certificado.

## Evidencia reproducible

- `artifacts/phase2f/cases-a-e-cold.json`
- `artifacts/phase2f/cases-a-e-warm-1-worker.json`
- `artifacts/phase2f/cases-a-e-warm-2-workers.json`
- `apps/api/tests/test_phase2f_operational.py`
- `apps/web/src/pages/OptimizationRunPage.test.tsx`
- `apps/web/src/pages/OrdersPage.test.tsx`

FASE_2F_OPERATIONAL_INTEGRATION = PASS

RUN_RECOVERY_STATUS = PASS

OBSERVABILITY_STATUS = PASS

TRANSACTIONAL_PUBLICATION_STATUS = PASS

CONCURRENCY_STATUS = PASS

FRONTEND_RESULT_UX_STATUS = PASS

COLD_CACHE_BENCHMARK = PASS

DOCKER_VERIFICATION = PASS

ORDER_CASES_A_E_REGRESSION = PASS

PATTERN_VALIDATION_STATUS = ENGINEERING

PRODUCTION_READY = NO

STOP_GATE = ACTIVE

IMPLEMENTATION_AUTHORIZED_FOR_PHASE_2G = NO

FASE_2F_1_CUT_MAP_UX = PASS

REAL_MARKER_RENDERING = PASS

SPREAD_TO_MARKER_TRACEABILITY = PASS

CUT_MAP_INTERACTION = PASS

PATTERN_VALIDATION_STATUS = ENGINEERING

STOP_GATE = ACTIVE
