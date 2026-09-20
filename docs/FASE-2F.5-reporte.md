# COSTURA ÓPTIMA — FASE 2F.5

## Resultado

Se implementó un pipeline anytime completamente local. El coordinador genera y valida primero un incumbent con `OperationalHeuristicSolver`, lo persiste como `BEST_VALIDATED_SO_FAR` y sólo después permite refinamiento. El incumbent se conserva ante timeout y puede finalizarse mediante **USAR MEJOR PLAN ACTUAL** con estado `SUCCEEDED_EARLY`.

La abstracción `ProductionSolver` separa el planner operacional del adaptador `CpSatRefinementSolver`. El módulo heurístico no importa OR-Tools; `PLANNER_REFINEMENT_ENGINE=heuris# COSTURA ÓPTIMA
## FASE 2F.5 — Autonomous Anytime Optimization

PROBLEMA

La ampliación del espacio de búsqueda para producir markers multi-talla
y reducir cortes aumenta considerablemente el tiempo de optimización.

Actualmente una corrida puede terminar en:

TIMED_OUT
"No se alcanzó a publicar una solución validada."

Esto no es aceptable como comportamiento normal.

El sistema debe ser capaz de encontrar rápidamente una solución válida
y utilizar el tiempo restante únicamente para mejorarla.

Además, la ejecución debe ser completamente local/autocontenida.
No utilizar servicios externos de optimización ni APIs cloud.

STOP_GATE = ACTIVE.


======================================================================
1. OBJETIVO DE ARQUITECTURA
======================================================================

Implementar un optimizador ANYTIME.

Propiedad fundamental:

Mientras mayor tiempo tenga disponible, mejor puede ser la solución.

Pero debe producir una primera solución válida rápidamente.


Pipeline:

ProductionOrder
↓
Fast Candidate Generation
↓
OperationalCoveragePlanner
↓
FIRST VALIDATED PLAN
↓
Candidate Expansion
↓
Geometry refinement
↓
CP-SAT improvement opcional
↓
BEST VALIDATED PLAN


======================================================================
2. NO DEPENDER DE SERVICIOS EXTERNOS
======================================================================

Todo debe ejecutarse dentro del stack local.

Permitido:

Python packages empaquetados en la imagen
OR-Tools local
Shapely/GEOS local
Pyclipper local
PostgreSQL
Redis


No permitido:

API externa de optimización
Optitex
servicio SaaS
cloud solver obligatorio
Internet en runtime


El sistema debe poder ejecutarse offline después de construir las imágenes.


======================================================================
3. FAST FEASIBLE PLAN
======================================================================

Implementar un algoritmo propio:

OperationalCoveragePlanner


Debe intentar obtener una solución completa antes de ejecutar búsqueda extensa.


Objetivos:

1. shortage = 0
2. minimizar physical_spreads
3. maximizar cobertura útil por spread
4. minimizar marker designs
5. minimizar fabric


No exigir optimalidad.


======================================================================
4. FALLBACK GARANTIZADO
======================================================================

Mantener markers básicos de cada talla como fallback.


Si cada talla solicitada tiene un marker VALIDATED_FEASIBLE:

debe ser posible producir un plan base.


Ejemplo:

M = 42

con:

M × 1

puede resolverse:

30 capas
12 capas


No esperar al solver global para descubrir este fallback.


======================================================================
5. PUBLICAR PRIMER INCUMBENT
======================================================================

Tan pronto exista un plan válido:

persistir como:

BEST_VALIDATED_SO_FAR


No marcar todavía run SUCCEEDED.


Exponer:

best_solution_available = true


La UI debe poder mostrar:

"Ya encontramos un plan válido. Estamos buscando una alternativa mejor."


======================================================================
6. PRESUPUESTO POR FASE
======================================================================

No compartir indiscriminadamente un único timeout.


Definir configurable:

fast_plan_budget_seconds
candidate_generation_budget_seconds
geometry_budget_seconds
planning_budget_seconds
total_budget_seconds


Ejemplo conceptual, no hardcodeado:

fast plan: 3–5 s
geometry improvement: 20–40 s
planner improvement: 10–30 s


Benchmark para seleccionar defaults.


======================================================================
7. CANDIDATE FUNNEL
======================================================================

No enviar todas las compositions al GeometryEngine.


Pipeline:

RAW COMPOSITIONS
↓
mathematical pruning
↓
coverage ranking
↓
estimated length ranking
↓
operational ranking
↓
TOP K
↓
GeometryEngine


Registrar conteos en cada etapa.


======================================================================
8. RAW CANDIDATES BARATOS
======================================================================

Puede generar muchos candidatos sin nesting usando únicamente:

demand
ratio
max_layers
pattern areas
usable width
max length
overproduction policy


No generar geometría en esta etapa.


======================================================================
9. LOWER-BOUND PRUNING
======================================================================

Para composition c:

area_lower_bound =
pattern_area(c) / usable_width


Si:

area_lower_bound + margins > max_length

descartar.


También aplicar:

individual_piece_feasibility
overproduction feasibility
demand relevance


======================================================================
10. ESTIMATED MARKER LENGTH
======================================================================

Crear sólo para ranking:

estimated_length


Puede usar:

lower_bound / expected_efficiency


expected_efficiency debe derivarse de:

histórico de markers similares
o
config conservadora


Nunca usar estimated_length como longitud final.


======================================================================
11. COVERAGE SCORE
======================================================================

Para composition + layer candidate:

useful_coverage =
Σ min(
  remaining_demand_s,
  qty_s × layers
)


coverage_ratio =
useful_coverage / remaining_total


Rankear alto:

alta coverage
baja overproduction
pocos marker designs
longitud estimada razonable


======================================================================
12. TOP-K GEOMETRY
======================================================================

Enviar únicamente los mejores candidatos a GeometryEngine.


Configurable:

geometry_top_k_initial
geometry_top_k_per_round


Benchmark:

10
20
30
50


No elegir arbitrariamente.


======================================================================
13. GEOMETRY CACHE
======================================================================

Reutilizar cualquier MarkerArtifact existente.


Antes de ejecutar nesting:

buscar content hash/cache.


Esto es obligatorio.


======================================================================
14. PRIMARY LARGE MARKER
======================================================================

Buscar primero candidates multi-size de alta cobertura.


No comenzar llenando el presupuesto con:

XS×1
S×1
M×1...


Esos quedan como fallback.


======================================================================
15. RESIDUAL SOLVER
======================================================================

Después de seleccionar un spread:

remaining demand


Generar candidatos específicamente sobre residual.


Cada ronda debe intentar reducir:

remaining_total
physical_spreads


======================================================================
16. BEAM SEARCH
======================================================================

Implementar Beam Search operacional.


State:

remaining_demand
selected_spreads
fabric
marker_designs


Expandir con los mejores validated marker/layer choices.


Conservar sólo:

beam_width


mejores estados.


Benchmark:

5
10
20
30


Ranking lexicográfico:

remaining demand
physical spreads
marker designs
fabric


======================================================================
17. GREEDY SEED
======================================================================

Antes de Beam Search:

crear solución greedy.


En cada paso:

seleccionar marker/layers que cubra mayor demanda útil


desempate:

menos overproduction
menos estimated/validated fabric
marker ya utilizado
mayor layers cuando equivalente


Esto proporciona incumbent rápido.


======================================================================
18. CP-SAT COMO REFINADOR OPCIONAL
======================================================================

OR-Tools deja de ser requisito para encontrar la primera solución.


CP-SAT recibe únicamente:

ValidatedMarkerCatalog reducido


y el incumbent del planner propio.


Puede intentar mejorar:

spreads
marker designs
fabric


No debe impedir publicar incumbent.


======================================================================
19. ABSTRACCIÓN DE SOLVER
======================================================================

Crear:

ProductionSolver


Implementaciones:

OperationalHeuristicSolver
CpSatRefinementSolver


Coordinator:

heuristic first
optional exact refinement later


Así será posible retirar OR-Tools en el futuro
sin modificar dominio/API/UI.


======================================================================
20. FEATURE FLAG
======================================================================

Agregar:

PLANNER_REFINEMENT_ENGINE

valores:

heuristic
cp_sat
hybrid


HYBRID será inicialmente recomendado.


Permitir ejecutar pruebas completamente sin OR-Tools:

heuristic


======================================================================
21. TEST SIN OR-TOOLS
======================================================================

Ejecutar A–E y el pedido grande usando:

PLANNER_REFINEMENT_ENGINE=heuristic


Debe:

completar demanda
respetar overproduction
respetar max_layers
usar marker validado
no depender de CP-SAT


Esto demuestra independencia arquitectónica.


======================================================================
22. OFFLINE TEST
======================================================================

Después de build:

bloquear acceso de red externo.


Ejecutar:

crear orden
optimizar
ver resultado


Debe funcionar.


PostgreSQL/Redis internos pueden continuar.


======================================================================
23. TIMEOUT CON INCUMBENT
======================================================================

Si total deadline ocurre con solución válida:

run_status = TIMED_OUT

best_solution_available = true


La UI mostrará:

"Se alcanzó el límite de mejora.
Te mostramos la mejor solución validada encontrada."


Debe permitir abrirla normalmente.


======================================================================
24. TIMEOUT SIN INCUMBENT
======================================================================

Sólo mostrar:

"No se alcanzó a publicar una solución validada."


si:

best_solution_available = false


Debe ser excepcional.


Registrar la fase responsable.


======================================================================
25. EARLY SUCCESS
======================================================================

Permitir detener búsqueda antes del timeout si:

- no hay mejora material durante N rondas;
- physical_spreads alcanza lower bound;
- residual es cero;
- catálogo adicional no mejora;
- presupuesto mínimo de mejora ya se cumplió.


No consumir 120 s obligatoriamente.


======================================================================
26. SOLUTION QUALITY
======================================================================

Registrar:

first_solution_elapsed_ms
first_solution_spreads
first_solution_fabric

final_solution_elapsed_ms
final_solution_spreads
final_solution_fabric


Mostrar cuánto mejoró el refinamiento.


======================================================================
27. UI DE PROGRESO
======================================================================

Estados:

Buscando primer plan...
Plan válido encontrado.
Buscando menos cortes...
Mejorando aprovechamiento de tela...


Si ya existe incumbent mostrar:

Plan válido:
3 cortes
xx.xx m


sin presentar todavía como resultado final.


======================================================================
28. BOTÓN DETENER Y USAR RESULTADO
======================================================================

Cuando best_solution_available=true:

permitir:

[ USAR MEJOR PLAN ACTUAL ]


Esto solicita cancelación de mejora,
conserva incumbent validado
y finaliza como:

SUCCEEDED_EARLY

o estado equivalente bien definido.


No esperar obligatoriamente al timeout.


======================================================================
29. BENCHMARK PRINCIPAL
======================================================================

Usar la orden grande actual:

XS 30
S 33
M 42
L 21
XL 35
XXL 55
XXXL 0


Medir:

first feasible time
final time
physical spreads
distinct markers
fabric
coverage primary spread
geometry evaluations
cache hits
candidate counts


Comparar:

heuristic
CP-SAT actual
hybrid


======================================================================
30. CRITERIO PRINCIPAL DE ÉXITO
======================================================================

El pedido grande no debería terminar normalmente sin solución por timeout.


PASS exige:

first valid plan antes del total timeout.


No exigir que el plan final sea globalmente óptimo.


======================================================================
31. TESTS
======================================================================

Agregar:

- heuristic-only works;
- no OR-Tools import in heuristic implementation;
- fallback plan;
- greedy incumbent;
- beam improvement;
- candidate funnel;
- top-K geometry;
- timeout with incumbent;
- timeout without incumbent;
- early stop;
- use-current-plan;
- offline runtime;
- large-order benchmark;
- A–E regression.


======================================================================
32. DEFINITION OF DONE
======================================================================

PASS si:

1. existe solver heurístico propio;
2. encuentra primera solución sin CP-SAT;
3. CP-SAT es refinador opcional;
4. candidate funnel reduce GeometryEngine calls;
5. cache se usa antes de nesting;
6. greedy seed existe;
7. Beam Search existe;
8. residual search existe;
9. timeout conserva incumbent;
10. UI permite usar incumbent;
11. modo heuristic-only pasa;
12. runtime offline pasa;
13. pedido grande obtiene solución;
14. A–E siguen pasando;
15. Docker/tests pasan.


Finalizar:

FASE_2F_5_AUTONOMOUS_ANYTIME_OPTIMIZER = PASS / PARTIAL / FAIL

HEURISTIC_SOLVER_STATUS = PASS / FAIL

CP_SAT_REQUIRED_FOR_FEASIBILITY = NO

HYBRID_REFINEMENT_STATUS = PASS / PARTIAL / FAIL

OFFLINE_RUNTIME_STATUS = PASS / FAIL

FIRST_FEASIBLE_TIME_MS = <n>

LARGE_ORDER_SOLUTION_FOUND = YES / NO

TIMEOUT_WITHOUT_SOLUTION = YES / NO

PATTERN_VALIDATION_STATUS = ENGINEERING

PRODUCTION_READY = NO

STOP_GATE = ACTIVE

IMPLEMENTATION_AUTHORIZED_FOR_PHASE_2G = NOtic` ejecuta factibilidad, greedy, beam y residual search sin CP-SAT. `hybrid` permanece como valor recomendado.

## Búsqueda y presupuestos

- Candidatos raw se construyen con demanda, ratio, áreas, ancho, largo, capas y política de sobreproducción, sin geometría.
- El lower bound de área incluye los márgenes longitudinales al decidir factibilidad.
- El funnel registra raw, pruned, ranked, TOP-K y fallbacks obligatorios.
- Un candidato multi-talla de alta cobertura se evalúa antes del catálogo fallback.
- Los fallbacks `talla × 1` no pueden ser expulsados por un TOP-K configurado demasiado bajo.
- La caché por content hash se consulta antes de cada nesting.
- El planner greedy publica la primera solución y beam conserva estados por shortage, cortes, diseños y tela.
- Las rondas posteriores se dirigen al residual y no pueden reemplazar el incumbent por una solución peor.
- Los presupuestos de generación, geometría, planning y total se contabilizan por fase; CP-SAT comparte un único presupuesto de planning entre perfiles.

## Benchmark obligatorio — 216 prendas

Demanda: `XS 30, S 33, M 42, L 21, XL 35, XXL 55, XXXL 0`.

| Motor | Primera solución | Tiempo final | Cortes | Diseños | Tela | Cobertura primaria | Geometrías | Cache hits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| heuristic | 12097.464 ms | 48301.611 ms | 5 | 5 | 175.31529 m | 77.314815% | 19 | 0 |
| cp_sat | 3238.529 ms | 65025.128 ms | 4 | 4 | 181.84240 m | 51.851852% | 22 | 9 |
| hybrid | 3292.616 ms | 42299.765 ms | 4 | 4 | 181.84240 m | 51.851852% | 22 | 15 |

Las corridas CP-SAT e híbrida reutilizaron la caché local creada por la primera corrida. Las tres finalizaron `SUCCEEDED`, publicaron incumbent antes de 120 s y no usaron servicios externos. Evidencia reproducible: `artifacts/phase2f5/large-order-benchmark.json`.

## Runtime offline

Las imágenes se construyeron antes de aislar el runtime. API, worker, web, PostgreSQL y Redis usan una red Docker `internal`; se añadió resolución local determinista porque Docker Desktop no podía consultar su DNS embebido sin forwarder externo. Un gateway TCP de destinos fijos publica `localhost:8000` y `localhost:5173` sin conectar el solver ni sus dependencias a la red con salida. El smoke test dentro de la imagen creó una orden, la procesó mediante Redis/worker y abrió sus soluciones con `planner_refinement_engine=heuristic`.

Resultado final sobre la imagen reconstruida: `SUCCEEDED`, primera solución en `224.789 ms`, `best_solution_available=true`, dos soluciones persistidas. Script: `apps/api/scripts/run_offline_smoke.py`.

## Verificación

- Pruebas específicas: heuristic-only, ausencia de imports OR-Tools, fallback, greedy, beam, funnel/TOP-K, márgenes del lower bound, pedido grande, regresión A–E, timeout con/sin incumbent y `SUCCEEDED_EARLY`.
- UI: progreso por fase, incumbent visible, botón para usar el plan actual y apertura normal del resultado conservado tras timeout.
- Build local: API, worker y web construidos con dependencias empaquetadas.
- Estado de patrones: ingeniería; no se autoriza producción ni FASE 2G.

`FASE_2F_5_AUTONOMOUS_ANYTIME_OPTIMIZER = PASS`

`HEURISTIC_SOLVER_STATUS = PASS`

`CP_SAT_REQUIRED_FOR_FEASIBILITY = NO`

`HYBRID_REFINEMENT_STATUS = PASS`

`OFFLINE_RUNTIME_STATUS = PASS`

`FIRST_FEASIBLE_TIME_MS = 12097.464`

`LARGE_ORDER_SOLUTION_FOUND = YES`

`TIMEOUT_WITHOUT_SOLUTION = NO`

`PATTERN_VALIDATION_STATUS = ENGINEERING`

`PRODUCTION_READY = NO`

`STOP_GATE = ACTIVE`

`IMPLEMENTATION_AUTHORIZED_FOR_PHASE_2G = NO`
