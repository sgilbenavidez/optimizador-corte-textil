# Costura Óptima — Reporte Fase 2D

Fecha de cierre: 2026-09-02. Alcance: motor geométrico de un marcador para una composición conocida. Este cierre no implementa planificación de órdenes, capas, tendidos ni CP-SAT.

## 1. Kernel geométrico seleccionado

Se implementó un kernel híbrido: Pyclipper 1.4 para offsets enteros/caché de geometría de nesting y Shapely 2.1/GEOS para predicados poligonales exactos, distancia y validación independiente. La geometría autoritativa permanece en enteros a 1 cm = 1000 unidades.

## 2. Spike, justificación y licencias

| Alternativa | Robustez/operaciones | NFP/Minkowski | Python/Docker | Licencia/mantenimiento | Decisión |
|---|---|---|---|---|---|
| Clipper2 nativo | Enteros, clipping, offset y triangulación; muy apropiado para topología robusta | Clipper2 documenta operaciones Minkowski | No hay binding Python oficial dominante; integrar C++ aumenta complejidad de build | Uso comercial y open source permitido; Boost Software License | Candidato futuro para NFP completo |
| Pyclipper | Enteros, booleanas, offset y Minkowski | Disponible, pero envuelve el Clipper clásico 6.4.2, no Clipper2 | Wheel CPython disponible y comprobada en Windows/Linux | Binding MIT; núcleo bajo Boost | Seleccionado para offset entero, no para certificar NFP completo |
| Shapely/GEOS | Predicados, intersección, distancia, STRtree y buffer maduros | No entrega un NFP irregular listo para producción | Wheels estables y empaquetado Docker simple | Shapely BSD-3; GEOS LGPL-2.1 | Seleccionado para colisión/distancia/validator |
| Híbrido propio | Conserva coordenadas enteras y permite canonicalización | Puede añadir NFP por etapas | Sin toolchain C++ adicional | Código del proyecto | Seleccionado como orquestación |

Fuentes técnicas: [Clipper2 Overview](https://angusj.com/clipper2/Docs/Overview.htm), [Clipper2 Minkowski](https://www.angusj.com/clipper2/Docs/Units/Clipper.Minkowski/_Body.htm), [ClipperOffset](https://angusj.com/clipper2/Docs/Units/Clipper.Offset/Classes/ClipperOffset/_Body.htm), [Pyclipper](https://github.com/fonttools/pyclipper), [licencia de Pyclipper](https://github.com/fonttools/pyclipper/blob/main/LICENSE), [Shapely](https://github.com/shapely/shapely) y [Shapely buffer](https://shapely.readthedocs.io/en/stable/reference/shapely.buffer.html).

## 3. Algoritmo implementado

`DETERMINISTIC_IRREGULAR_BOTTOM_LEFT_FILL`, versión `blf-exact-collision-local-search-v1`. Los bounding boxes sólo intervienen como broad phase, cota y generación de extremos; toda aceptación final usa polígonos de corte reales y el certificado independiente.

## 4. Estado NFP

`PARTIAL`. No se oculta un NFP completo inexistente. Se usa fallback explícito de colisión poligonal exacta + candidatos deterministas. Los offsets de nesting se generan y cachean, pero no sustituyen el contorno de corte.

## 5. Estrategia BLF

Para cada instancia se intersectan transformaciones de pieza y tela, se generan candidatos, se ordenan por menor X y luego menor Y, se filtran región/colisión/clearance y se acepta el primer candidato válido. X representa longitud y Y ancho. Una semilla conservadora por extremos poligonales evita confundir un callejón greedy con `INFEASIBLE`; las secuencias BLF intentan mejorarla.

## 6. Candidate positions

Se usan contactos con borde útil, extremos de polígonos colocados, contactos de cajas como broad phase y alineaciones de vértices significativos con desplazamientos de clearance. No existe barrido de rejilla continua. Orden estable: X, Y, rotación y coordenadas enteras.

## 7. Local search

La vecindad acotada incluye swap, insert, reverse subsequence, cambio de preferencia de orientación y repack del sufijo. Cada variante se reconstruye y valida; no se muta un layout certificado.

## 8. Modelo de clearance

Con clearance 0 se admite contacto de frontera y se prohíbe intersección de interiores. Con clearance > 0 se exige distancia >= clearance. CUT_GEOMETRY y NESTING_GEOMETRY son objetos distintos. La comparación GEOS usa una tolerancia central de `1e-7` unidades exclusivamente para neutralizar error de representación en igualdad exacta; 500 unidades pasa y 499 falla.

## 9. Validator independiente

`IndependentMarkerValidator` crea su propio kernel y reconstruye desde cero instancias, transformaciones, polígonos, grainlines y región. Verifica presencia, duplicados, cantidades, containment, overlap, clearance, transforms, grainline y longitud máxima. Una rotación desconocida se certifica `INVALID` sin excepción.

## 10. Convención de coordenadas

X = longitud de marcador/tela. Y = ancho. El marcador usa coordenadas físicas; la región útil empieza en `(start_margin, left_margin)` y termina antes de end/right margin. API, SVG, engine, golden y tests siguen la misma convención.

## 11. Precisión

`PrecisionConfiguration` centraliza 1000 unidades/cm, tolerancia de flattening 20 unidades, epsilon topológico entero 0, epsilon de comparación de distancia `1e-7` y versión de kernel. No hay conversiones float repetidas dentro del packing.

## 12. Caché

LRU versionada por hash de geometría, rotación, delta de clearance y versión de kernel. No usa IDs de base de datos. Los golden registran hits/misses. Cuando se incorpore NFP completo, la clave deberá ampliarse a hash A/B y orientación A/B.

## 13. Result states

Se usan `VALIDATED_FEASIBLE`, `INFEASIBLE`, `EVALUATION_LIMIT` y `ENGINE_ERROR`. Cuando el presupuesto termina con solución válida se conserva `VALIDATED_FEASIBLE` y `FEASIBLE_NOT_PROVEN_BEST`. Nunca se emite `OPTIMAL`.

## 14. API

`POST /api/v1/geometry/markers/preview` acepta patrón, composición, tela, mesa, semilla, determinismo, presupuesto y debug. Expande automáticamente cantidades por pieza y devuelve placements completos, polígonos transformados, grainlines, métricas, certificados y hashes. No toca `ProductionOrder`.

## 15. Nesting Lab

`/nesting-lab` permite composición XS–XXXL, genera M × 1 por defecto, muestra estados, longitud, ancho, piezas, eficiencia, desperdicio, lower bound, evaluaciones y tiempo. Conserva la identidad visual existente y muestra exactamente: “Laboratorio geométrico — no es todavía un plan de producción.”

## 16. SVG y artefactos

El frontend dibuja únicamente los polígonos entregados por el backend. Incluye ancho físico/útil, márgenes, pieza, talla, orientación, grainline, tooltips, zoom/pan y toggles de IDs, bbox, clearance y debug. Se congelaron SVG M1–M6 en `artifacts/nesting/`.

## 17. Resultados M1–M6

| Caso | Composición | Piezas | Estado |
|---|---|---:|---|
| M1 | M × 1 | 5 | VALIDATED_FEASIBLE |
| M2 | M × 2 | 10 | VALIDATED_FEASIBLE |
| M3 | S × 1 + M × 1 | 10 | VALIDATED_FEASIBLE |
| M4 | M × 1 + XL × 1 | 10 | VALIDATED_FEASIBLE |
| M5 | XS–XXXL × 1 | 35 | VALIDATED_FEASIBLE |
| M6 | XXXL × 2 | 10 | VALIDATED_FEASIBLE |

## 18. Longitudes

M1 83.466 cm; M2 164.000 cm; M3 160.000 cm; M4 176.000 cm; M5 583.130 cm; M6 204.000 cm. Todas incluyen start/end margin y son <= 700 cm.

## 19. Eficiencias

M1 62.755616%; M2 63.877564%; M3 62.609480%; M4 64.993216%; M5 68.933861%; M6 70.838868%. Son eficiencias del marcador geométrico validado, no de una orden o tendido.

## 20. Lower bounds

M1 63.000 cm; M2 108.760 cm; M3 104.176 cm; M4 118.389 cm; M5 405.975 cm; M6 148.512 cm. Se calcula `max(area/usable_width, menor extensión requerida)` más márgenes. No constituye prueba de optimalidad.

## 21. Evaluaciones y tiempos baseline Windows

M1 10.687 / 1.124 s; M2 57.861 / 5.345 s; M3 66.023 / 9.793 s; M4 86.520 / 13.177 s; M5 100.000 / 18.636 s; M6 100.000 / 12.901 s. M5/M6 terminan por presupuesto con layout válido. El tamaño profundo aproximado de respuesta fue 0.136–0.939 MiB.

## 22. Golden fixtures

M1, M3 y M4 guardan input hash, result hash, placements, longitud, área y certificado bajo `artifacts/nesting/golden/`. Son fixtures de reproducibilidad, no de optimalidad global.

## 23. Hashes Windows/Docker

| Caso | result_hash Windows = Linux |
|---|---|
| M1 | `1035ec65e5c82b3b75c0bd3e83ac0dbffc14b1eb20096c888e23605327591655` |
| M2 | `f492e29487e6ddb40038d30b51cd1b08eafaa72e92b1dc75128e2a2e256c9e55` |
| M3 | `332504a43634271135f64ce3dbb5e34e6e71d3aec8d6f6df104a0229979cfe63` |
| M4 | `4e33ebdf8be9e1f36ad64ba24142556e3246f52bff0e275ea97eff90a3ae2f00` |
| M5 | `f6f725955238de709250e9759f8a702009b41a53772f969c80f1c6ffbca7f92a` |
| M6 | `28ba924d1801c71e84030b8defb761f083da0ef1e8d46a443e88728ef5487a86` |

La baseline Linux está en `artifacts/nesting/docker-baseline.json`; la Windows en `artifacts/nesting/baseline.json`.

## 24. Tests

Backend: 58 tests pasaron localmente y dentro de la imagen Docker/Linux final. Frontend: 7 tests. TypeScript y build Vite: PASS. Docker build sin caché y rebuild final: PASS. La única advertencia es deprecación de `fastapi.testclient`/httpx, sin fallo funcional.

## 25. Casos degenerados

Se cubren contacto punto/punto y arista/arista, solape de una unidad, clearance exacto y clearance-1, aristas colineales, cóncavos, pasillo/arista de una unidad, vértices duplicados, CW/CCW, negativos, rotación 180° y piezas idénticas. No hubo crash, NaN ni aceptación silenciosa inválida.

## 26. Casos INFEASIBLE

Se probaron pieza más ancha que región, capacidad total excedida, márgenes sin longitud, clearance extremo, composición vacía y ausencia de transform permitido. Retornan `INFEASIBLE` con diagnóstico conocido, no excepción genérica.

## 27. Incidentes

1. El BLF greedy original podía encajonarse y reportar falso `INFEASIBLE`; se añadió semilla por extremos exactos.
2. GEOS devolvió una distancia microscópicamente menor para un clearance exactamente 500; se centralizó epsilon de comparación y se fijó golden 500/499.
3. Tracemalloc hacía M5 inservible para baseline; se sustituyó por tamaño profundo reproducible del resultado.
4. El contador podía llegar a budget+1; se corrigió a corte previo en 100.000.
5. El build Docker reportado fallaba en la instalación pnpm por orden de copia. El Dockerfile ahora copia manifest, lockfile y workspace antes de instalar; rebuild limpio PASS.

## 28. Riesgos

La calidad heurística aún depende del orden; M5/M6 agotan presupuesto. Shapely usa doble precisión en predicados de distancia aunque las entradas sean enteras. Pyclipper sigue basado en Clipper clásico. El NFP cóncavo completo permanece pendiente y es la principal limitación técnica.

## 29. Deuda técnica

Incorporar NFP/IFP entero robusto, índice espacial para reducir predicados, envelopes de clearance reales en el SVG en lugar del trazo visual aproximado, persistencia opcional de caché, benchmark de RSS de proceso y migración de TestClient para eliminar la advertencia. Ninguna deuda autoriza planificación de producción.

## 30. Recomendaciones para Fase 2E

Mantener el marker engine como servicio puro. Antes de ampliar composiciones, implementar NFP Clipper2 y comparar golden. En una fase expresamente autorizada, un componente superior podrá proponer composiciones y consumir sólo marcadores `VALIDATED_FEASIBLE`; no debe confiar en eficiencias no certificadas ni reinterpretar placements en frontend.

FASE_2D_GEOMETRY_ENGINE = PASS

NFP_STATUS = PARTIAL

VALIDATOR_STATUS = PASS

DETERMINISTIC_MODE = PASS

PRODUCTION_PLANNING_IMPLEMENTED = NO

STOP_GATE = ACTIVE

IMPLEMENTATION_AUTHORIZED_FOR_PHASE_2E = NO
