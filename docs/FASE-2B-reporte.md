# Costura Óptima — reporte técnico FASE 2B

Fecha de ejecución: 2026-09-01  
Modelo fuente: `TSHIRT-REGULAR-STRAIGHT-ADULT-v0`  
Versión final: `TSHIRT-REGULAR-STRAIGHT-ADULT-v0-PATTERN-v3`  
Hash reproducible Windows/Docker: `977412195988cb7248c06f40a00282cc9eaebaf4fdf09909c5985488e531e470`

## Resultado

Se implementó Pattern Engineering versionado para XS, S, M, L, XL, XXL y XXXL. Cada talla contiene `FRONT × 1`, `BACK × 1`, `SLEEVE × 2` y `NECKBAND × 1`: 28 registros `PatternPiece` por versión.

El estado es `ENGINEERING / UNVALIDATED_FOR_PRODUCTION` y toda exposición presenta: “Patrón experimental de ingeniería — no validado para producción.”

No se implementaron NFP, BLF, búsqueda local, CP-SAT, planeación, consumo, eficiencia, desperdicio ni resultados A/B/C.

## Arquitectura y persistencia

Cadena explícita: `BODY → GARMENT → PARAMETERS → SEAMLINE → CUT → NESTING`. `NESTING` queda únicamente como consumidor futuro. `GeometryEngine` recibe `PatternPieceGeometry`, un tipo puro con polígonos enteros, grano y transformaciones; no importa SQLAlchemy ni FastAPI.

La migración `20260901_0002` agrega `pattern_parameter_profiles`, `pattern_set_versions`, `pattern_pieces` y `production_orders.pattern_set_version_id`. La FK es nullable para conservar órdenes históricas. Las órdenes nuevas congelan ID y hashes del patrón vigente; las antiguas siguen legibles y no se recalculan.

## Modelo geométrico

La fuente usa segmentos `LINE` y `CUBIC_BEZIER` con semántica de borde. Se escogieron Bézier cúbicas porque son estructuradas, portables y permiten controlar tangencias de escote, sisa, costado y copa. SVG 2 define cada curva cúbica mediante extremos y dos controles ([W3C SVG Paths](https://www.w3.org/TR/SVG/paths.html)).

Las curvas se discretizan por subdivisión adaptativa de De Casteljau hasta `0.02 cm` (0.2 mm). Después se normalizan orientación, punto inicial, cierre y redondeo; se validan intersecciones, área, perímetro, bounding box y `seamline ⊂ cutline` con Shapely.

La geometría operativa usa `1 cm = 1000 unidades enteras`, resolución de 0.01 mm. A 800 cm de mesa el valor nominal máximo es 800 000, muy por debajo del rango robusto documentado para operaciones enteras de Clipper2 ([overview](https://angusj.com/clipper2/Docs/Overview.htm), [robustness](https://www.angusj.com/clipper2/Docs/Robustness.htm)). El layout futuro no depende de Clipper2, pero queda preparado para esa familia de operaciones.

## Parámetros y supuestos centralizados

El perfil `TSHIRT-REGULAR-STRAIGHT-ADULT-PARAMETERS-v1` versiona:

- cuello = 40% del hombro, limitado a 16–21 cm;
- caída de hombro base 2.2 cm, grado 0.1 cm/talla;
- profundidad de sisa `finished_chest / 6 + 7 cm`;
- escotes y controles de sisa diferenciados para delantero/espalda;
- holgura de ancho de copa sobre bíceps: 7 cm;
- holgura longitudinal de copa: 0 cm;
- banda = escote real × 0.85 y ancho terminado 1.8 cm;
- márgenes general 1.0 cm, banda 0.7 cm y dobladillo 2.5 cm;
- rotaciones `[0, 180]`, espejo prohibido;
- tolerancia de compatibilidad 0.25 cm.

Son supuestos de ingeniería y requieren prototipo físico antes de producción.

## Relaciones de costura

- Delantero y espalda reutilizan exactamente hombros y costados.
- Escotes y sisas son deliberadamente distintos.
- La copa se resuelve por búsqueda binaria contra `sisa_delantera + sisa_espalda + holgura`.
- La banda se deriva de la longitud real de ambos escotes.
- Los márgenes se aplican por borde semántico; no existe un offset global ciego.
- `NECKBAND` declara `GREATEST_STRETCH`; las otras piezas, `STRAIGHT_GRAIN`.

## Versionado e idempotencia

El seed crea medidas y el perfil, nunca miles de coordenadas. El comando explícito es:

```bash
python -m costura_optima.patterns.generate
```

El hash incorpora versión de algoritmo, snapshot de medidas, perfil y geometría operativa entera. El mismo contenido reutiliza la versión; un cambio crea otra inmutable. V3 produjo el mismo hash en Windows/SQLite y Docker/Linux/PostgreSQL.

## API, frontend y artefactos

Endpoints: `GET /api/v1/pattern-sets`, `GET /api/v1/pattern-sets/{id}?size_code=M` y `GET /api/v1/pattern-pieces/{id}/geometry`.

El visor `/patterns` incluye selector XS–XXXL, SVG con disposición fija separada —no nesting—, capas de costura/corte/hilo/bounding box/medidas, zoom, pan y metadatos.

Artefactos: `pattern-XS.svg`, `pattern-M.svg`, `pattern-XL.svg` y `pattern-XXXL.svg` en `artifacts/patterns`.

## Verificación

- Backend: 36 pruebas aprobadas.
- Frontend: 6 pruebas aprobadas.
- Migración: aprobada en SQLite y PostgreSQL.
- Docker: `db`, `api` y `web` activos; PostgreSQL healthy; API `status=ok`.
- Persistencia: v3 vigente, 28 piezas; versiones de desarrollo previas conservadas como historial.
- Idempotencia y hash cruzado: aprobados.
- Inspección visual: XS, M, XL y XXXL revisadas en el visor real; piezas separadas, sin recorte, curvas y granos visibles.

## Diagnóstico del fallo Docker

El fallo original del frontend provenía de ejecutar `pnpm install` antes de copiar `pnpm-workspace.yaml`. La política `onlyBuiltDependencies` no estaba disponible y el postinstall de `esbuild` quedaba bloqueado (`ERR_PNPM_IGNORED_BUILDS`). El `Dockerfile` ahora copia `package.json`, lockfile y workspace antes de instalar. La reconstrucción completa finalizó correctamente y no requiere `pnpm approve-builds` manual.

## Puertas de fase

FASE_2B_PATTERN_ENGINEERING = PASS  
PATTERN_VALIDATION_STATUS = ENGINEERING  
PRODUCTION_PATTERN_APPROVED = NO  
STOP_GATE = ACTIVE  
IMPLEMENTATION_AUTHORIZED_FOR_PHASE_2D = NO
