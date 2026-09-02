# COSTURA ÓPTIMA — Reporte final FASE 2E

Fecha de cierre: 2026-09-02. Alcance: Production Planning Engine sobre el catálogo de markers validados de FASE 2D.

## 1. Arquitectura implementada

`ProductionOrder → CandidateCompositionGenerator → GeometryEngine → MarkerArtifact catalog → ProductionPlanner → IndependentProductionPlanValidator → OptimizationSolution`. El planner no importa ni ejecuta geometría y solo consume longitudes enteras de artifacts `VALIDATED`.

## 2. Worker/queue seleccionados

Redis + RQ. La API confirma con HTTP 202 y el trabajo ocurre en `worker`; RQ aporta jobs identificables, timeout/retry y registros de ejecución. Se añadió heartbeat/lease en DB y recovery al iniciar el worker. Referencias: [RQ jobs](https://python-rq.org/docs/jobs/), [RQ workers](https://python-rq.org/docs/workers/).

## 3. Modelo de CandidateComposition

Composición canónica `(size_code, complete_garment_count)`, hash SHA-256, ronda, origen y lower bound. Nunca contiene piezas parciales.

## 4. Estrategia de generación

Fallback single-size, repeticiones 2/3, pares derivados de residuales y triples acotados. Defaults medidos con el patrón de ingeniería: máximo 3 prendas, 3 tallas distintas, 24 candidatos, 2 rondas; máximo 14 enviados a geometría.

## 5. Podas implementadas

Pieza individual contra ancho útil, cota inferior de área contra largo de mesa, demanda/sobreproducción admisible, límites combinatorios, deduplicación por hash y dominancia posterior por longitud/desperdicio para capacidades idénticas.

## 6. Número de candidatos A–E

| Caso | Generados | Evaluados | Factibles | Infeasibles | No evaluados |
|---|---:|---:|---:|---:|---:|
| A | 3 | 3 | 3 | 0 | 0 |
| B | 24 | 14 | 14 | 0 | 10 |
| C | 24 | 14 | 14 | 0 | 10 |
| D | 3 | 3 | 3 | 0 | 0 |
| E | 3 | 3 | 3 | 0 | 0 |

Los 10 candidatos B/C omitidos quedan explícitamente `NOT_EVALUATED`; no se presentan como infeasibles.

## 7. Caché de markers

`MarkerArtifact` se identifica por content hash de patrón, tela, mesa, composición, configuración/seed/versión de engine. La corrida final fue warm-cache: A/D/E 3 hits y B/C 14 hits; no se duplicaron blobs geométricos.

## 8. Formulación CP-SAT

Para cada marker `k` y capas `c ∈ [1,30]` se precomputa `SpreadOption(k,c)`. La variable entera no negativa `x(k,c)` cuenta tendidos repetidos. Producción, tela, waste y tendidos son sumas lineales de coeficientes constantes. CP-SAT exige modelos enteros: [documentación oficial de OR-Tools](https://developers.google.com/optimization/cp/cp_solver).

## 9. Variables y constraints

A/D/E: 90 variables; A/E 6 constraints, D 7. B: 420 variables; 16 constraints bounded y 15 exact. C: 420 variables y 24 constraints. Los conteos corresponden al último modelo de etapa persistido en auditoría.

## 10. Implementación lexicográfica

Cada objetivo se resuelve en una etapa separada; un óptimo se fija como igualdad antes de resolver el siguiente. Si una etapa no prueba óptimo, las posteriores no se declaran óptimas. Los tiempos por etapa permanecen en auditoría, pero se excluyen del hash de contenido.

## 11. Perfiles A/B/C

`MIN_FABRIC`: extra, tela, waste, tendidos. `MIN_SPREADS`: extra, tendidos, tela, waste. `BALANCED`: máximo extra por talla, extra total, tela, tendidos, waste. Fingerprints iguales se fusionan mostrando todos los perfiles realmente coincidentes.

## 12. Política de sobreproducción

Demanda completa es hard constraint. Con overproduction: límite por talla `max(2, ceil(3% × demanda))`. Con modo exacto: producción igual a demanda; nunca se relaja. Tallas con demanda cero tienen cota superior cero.

## 13. Validator global

`IndependentProductionPlanValidator` vuelve a calcular producción, excedente, tela, waste, tendidos y eficiencia ponderada desde spreads + catálogo, y comprueba certificados, hashes y capas. Solo `VALIDATED_PLAN` se persiste/publica.

## 14. Persistencia

Migración `20260902_0003`: optimization runs/candidates/solutions/profiles, marker artifacts, spreads y size results. Los spreads referencian artifacts inmutables por marker hash.

## 15. Estados de run

`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `TIMED_OUT`, `INFEASIBLE`; fases `GENERATING_CANDIDATES`, `NESTING`, `PLANNING`, `VALIDATING`, `FINALIZING`. Cancelación cooperativa se comprueba entre markers, perfiles y etapas del planner.

## 16. API

Se implementaron POST run (202 + `Idempotency-Key`), GET/poll, cancel, soluciones, solución, spreads, spread, marker y audit. Doble clic con misma clave/config devuelve el mismo run; reutilizar la clave con otra config devuelve 422.

## 17. UI

El botón real crea orden + run y navega a procesamiento. La vista consulta progreso, permite cancelar, compara alternativas, muestra tabla por talla, explicación cuantitativa, tendidos expandibles, SVG real y panel técnico. QA visual comprobado a 390 px y 1440 px sin overflow documental.

## 18. Resultado completo A

Demanda S=3. `SUCCEEDED/OPTIMAL`; una solución común a los tres perfiles: 2.09500 m, 1 tendido, 0 extra, 68.4423%. Layout `S×3 @ 1 capa × 1`.

## 19. Resultado completo B

Demanda S3/M20/L10/XL12/XXL30. `SUCCEEDED/FEASIBLE` por límite temporal del catálogo: MIN_FABRIC+MIN_SPREADS 68.58113 m, 10 tendidos, 0 extra, 65.8765%; BALANCED 69.90181 m, 11 tendidos, 0 extra, 64.6319%.

## 20. Resultado completo C

Una unidad de las siete tallas. `SUCCEEDED/OPTIMAL`; 7.14922 m, 7 tendidos, 0 extra, 56.2263%. Catálogo bounded eligió siete singles.

## 21. Resultado completo D

M=100. MIN_FABRIC: 74.58966 m, 3 tendidos, 70.2237%, `M×1@1`, `M×3@3`, `M×3@30`. MIN_SPREADS: 75.25000 m, 2 tendidos, 69.6074%, `M×2@5`, `M×3@30`. Todo `OPTIMAL`, sin extra.

## 22. Resultado completo E

XXXL=31. `SUCCEEDED/OPTIMAL`; 31.78852 m, 2 tendidos, 0 extra, 70.4633%. Layout `XXXL×2 @ 14` + `XXXL×3 @ 1`; no se asumió 30+1.

## 23. Resultados allow_overproduction=false

A–E exactos terminaron `SUCCEEDED`, producción exactamente igual a demanda y cero extra. A/E igualaron el layout bounded. B exacto: 69.96671 m/12 tendidos. C exacto: 6.34655 m/4 tendidos mediante pares. D exacto conserva las tres alternativas bounded. No hubo relajación silenciosa.

## 24. Tela total por solución

A 2.09500 m; B 68.58113/69.90181 m; C 7.14922 m; D 74.58966/74.58966/75.25000 m; E 31.78852 m. El JSON contiene las variantes exactas y precisión completa.

## 25. Tendidos por solución

A 1; B 10/11; C 7; D 3/3/2; E 2. `repeats` cuenta ejecuciones matemáticas y la UI puede expandirlas reproduciblemente.

## 26. Producción/exceso por talla

Todas las soluciones publicadas cubren exactamente los pedidos A–E en este baseline; extra total cero y producción cero en tallas no solicitadas.

## 27. Eficiencia global

Se usa `Σ(piece_area×layers×repeats) / Σ(marker_area×layers×repeats)`, no promedio de porcentajes. Valores principales: A 68.4423%, B 65.8765%, C 56.2263%, D 70.2237%, E 70.4633%.

## 28. Markers utilizados

Cada solución y spread incluye `marker_hash`; el inventario completo está en `artifacts/phase2e/cases-a-e.json`. El endpoint `/api/v1/markers/{hash}` devuelve placements y certificado para el SVG.

## 29. Capas por marker

El artefacto registra composición, capas y repeats por spread. Ejemplos límite verificados: D usa 30 capas y múltiples spreads; E usa 14 y 1 capas; B emplea capas variables 1/2/3/5.

## 30. Tiempos

Warm-cache total: A 120.082 ms; B 7,174.613 ms; C 302.407 ms; D 161.095 ms; E 191.288 ms. B planner 7,032.647 ms; generación 0.371 ms; geometría/cache 63.222 ms. Serialization/DB queda incluida en `total - fases`.

## 31. Cache hits

A 3/3; B 14/14; C 14/14; D 3/3; E 3/3. El primer llenado puede tardar más; el baseline final mide deliberadamente reutilización entre órdenes.

## 32. Solver status

A/C/D/E prueban `OPTIMAL` sobre su catálogo. B devuelve `FEASIBLE` por timeout de etapas; la UI no lo presenta como óptimo. Planner status y marker search status se mantienen separados.

## 33. Marker search statuses

Los markers publicados son `VALIDATED_FEASIBLE`; búsqueda geométrica individual `FEASIBLE_NOT_PROVEN_BEST`. Ninguna solución implica optimalidad geométrica global.

## 34. Hashes

El run hash incorpora demanda normalizada, snapshot de patrón/tela/mesa, políticas, configs, perfiles y seed. Candidate, marker, solution y spread tienen hashes canónicos. Dos ejecuciones completas conservaron los 10 input hashes, catálogos y solution hashes.

## 35. Tests

Docker: backend 69/69, frontend 8/8; TypeScript + Vite production build OK. Incluyen optimum manual, prioridades, infeasible exacto, dominancia, tamper del validator, podas, hash de demanda, idempotencia y reproducibilidad de solution hash.

## 36. Incidentes

El build inicial de Vite local falló por `EPERM` al crear `.vite-temp` dentro del sandbox; fuera del sandbox y dentro de Docker pasó. Durante E2E se corrigieron: refresh transaccional que borraba métricas, `candidates_pending` incorrecto, demand ausente del snapshot hash y timing volátil dentro de solution hash.

## 37. Riesgos

NFP sigue parcial; markers son heurísticos. Un solo worker procesa la cola serialmente. PostgreSQL/Redis usan defaults de desarrollo. RQ no sustituye observabilidad/alertas operativas. Los patrones no tienen fit validation.

## 38. Deuda técnica

Añadir índices espaciales/NFP robusto, pruebas de crash durante una etapa CP-SAT, métricas separadas de serialization/DB, healthchecks HTTP del API/web, paginación de órdenes, optimizar cache layers del Dockerfile y migrar la advertencia de TestClient/httpx2.

## 39. Screenshots o artefactos

Artefactos auditables: `artifacts/phase2e/cases-a-e.json`, repetición `cases-a-e-repro.json`, script `apps/api/scripts/run_phase2e_cases.py`. QA en navegador verificó comparación, tabla, expansión del tendido y SVG real.

## 40. Recomendaciones para FASE 2F/2G

No avanzar el gate todavía. Antes: validación física/fit de patrón, dataset textil real, robustness/recovery chaos tests, seguridad/secretos, observabilidad y benchmark cold-cache con múltiples workers. Después evaluar DXF y multi-roll como fases separadas.

FASE_2E_PRODUCTION_PLANNING = PASS

CANDIDATE_GENERATOR_STATUS = PASS

PLANNER_STATUS = PASS

PLAN_VALIDATOR_STATUS = PASS

ASYNC_WORKER_STATUS = PASS

ORDER_CASES_A_E = PASS

PATTERN_VALIDATION_STATUS = ENGINEERING

GLOBAL_GEOMETRIC_OPTIMALITY_CLAIMED = NO

STOP_GATE = ACTIVE

IMPLEMENTATION_AUTHORIZED_FOR_PHASE_2F = NO
