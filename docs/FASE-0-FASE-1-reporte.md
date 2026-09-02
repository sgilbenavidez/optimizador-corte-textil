# COSTURA ÓPTIMA — Reporte de FASE 0 + FASE 1

**Estado:** propuesta para aprobación; STOP GATE activo  
**Fecha de investigación:** 2026-09-01  
**Alcance:** camiseta básica regular-fit, tallas XS–XXXL, una referencia por orden  
**No incluye:** implementación, geometrías finales aprobadas ni resultados de consumo/eficiencia

## 0. Resumen ejecutivo

El problema no es una calculadora de consumo. Tiene dos niveles acoplados:

1. **Nesting geométrico irregular 2D:** construir un marcador válido para una composición de tallas, respetando contornos reales aproximados, ancho, largo, separaciones, grainline, orientaciones y no solape.
2. **Planificación de corte:** escoger marcadores, número de capas y número de tendidos para cubrir la demanda, con sobreproducción controlada y el menor consumo razonable.

Se propone un **monolito modular desplegable como API + worker**, PostgreSQL como fuente de verdad, frontend React/TypeScript, backend Python/FastAPI, OR-Tools CP-SAT para planificación y un motor geométrico desacoplado. El algoritmo MVP recomendado es **NFP/IFP + Bottom-Left-Fill determinista + búsqueda local**, seguido siempre por un verificador geométrico independiente. Si el spike de robustez del NFP no supera los fixtures degenerados, el fallback de primera entrega será BLF poligonal con colisión exacta, sin sustituir piezas por bounding boxes.

La serie ISO 8559 **no contiene una tabla universal XS/S/M/L/XL**. ISO 8559-2:2017 fue retirada y sustituida por ISO 8559-2:2025. ASTM publica tablas para poblaciones concretas, no una escala unisex universal. Por ello, la tabla propuesta `TSHIRT-REGULAR-STRAIGHT-ADULT-v0` es un sistema interno provisional inspirado en la metodología ISO, no una tabla normativa, y requiere validación física antes de publicar `TSHIRT-REGULAR-v1`.

No se calculó ningún nesting. No se presentan metros, eficiencias ni desperdicios ficticios.

## 1. Investigación de tallaje

### 1.1 Qué cubre realmente cada norma

| Fuente | Aporte verificable | Población / sistema | Unidades | Implicación para el MVP |
|---|---|---|---|---|
| [ISO 8559-1:2017](https://www.iso.org/standard/61686.html) | Define medidas antropométricas y cómo tomarlas; sirve para bases de datos y perfiles corporales. Confirmada vigente en 2026. | Hombres, mujeres y niños; debe combinarse con una población objetivo. | Métricas antropométricas; el sistema del proyecto será cm. | Define semántica de `BODY_MEASUREMENT`, no una tabla XS–XXXL. |
| [ISO 8559-2:2017](https://www.iso.org/standard/64075.html) | Indicadores primarios/secundarios de talla basados en medidas corporales. | Designación de prendas por dimensiones corporales. | Medidas del cuerpo. | Está retirada; conservar solo como antecedente solicitado. |
| [ISO 8559-2:2025](https://www.iso.org/standard/85590.html) | Edición vigente de indicadores primarios/secundarios. | Designación de talla basada en ISO 8559-1. | Medidas del cuerpo. | Base vigente para designación; no prescribe medidas terminadas ni letras universales. |
| [ISO 8559-3:2018](https://www.iso.org/standard/67334.html) | Metodología estadística para crear tablas e intervalos; sus valores son ejemplos. Excluye dimensiones de prenda. Confirmada en 2024. | Infantes, niñas, niños, mujeres y hombres, con subgrupos. | Según la tabla construida. | Obliga a declarar población y evidencia; no autoriza inventar una tabla ISO. |
| [ISO 8559-4:2023](https://www.iso.org/standard/80356.html) | Método para determinar la cobertura de una tabla frente a una base antropométrica. | La población representada por la base de datos usada. | Según el estudio. | No puede afirmarse cobertura colombiana/latinoamericana sin datos de esa población. |
| [ISO 18890:2018](https://www.iso.org/standard/63693.html) | Define puntos y método de medición de prendas terminadas. Confirmada en 2024. | Prendas, no cuerpos. | Según especificación; aquí cm. | Separa formalmente `GARMENT_MEASUREMENT` de antropometría. |
| [ASTM D13.55](https://www.astm.org/get-involved/technical-committees/committee-d13/subcommittee-d13/jurisdiction-d1355) | Catálogo vigente de estándares de body measurements por grupos concretos. | Female misses, adult male, plus women, children, etc. | SI o inch-pound según cada estándar. | Debe elegirse una población concreta; no mezclar tablas. |
| [ASTM D6240/D6240M-24a](https://store.astm.org/d6240_d6240m-24a.html) | Tabla de cuerpo para hombre adulto, pecho 34–52, short/regular/tall. | Adult male. | SI e imperial, tratados separadamente. | Candidato si el negocio elige población masculina; el contenido completo es de pago. |
| [ASTM D5585-21](https://store.astm.org/d5585-21.html) | Tabla de cuerpo para adult female misses 00–20; exige considerar material, ease, estilo y fit. | Adult female misses. | SI o imperial, sin combinarlos. | Candidato si el negocio elige esa población; no es unisex. |
| [ASTM D5219-25](https://store.astm.org/d5219-25.html) | Terminología de dimensiones corporales para tallaje. | Apparel sizing. | Según definición. | Útil para el diccionario semántico; texto completo de pago. |

**Límite de la investigación:** los resúmenes oficiales fueron consultables; varias tablas numéricas ASTM/ISO completas requieren licencia. Ningún valor no visible se atribuye a esas normas.

### 1.2 Cinco capas de datos que no deben mezclarse

```text
BODY MEASUREMENTS
  cuerpo de la población objetivo
          ↓ + wearing/design ease
GARMENT MEASUREMENTS
  dimensiones de la camiseta terminada
          ↓ reglas de construcción y distribución
PATTERN MEASUREMENTS
  líneas de costura de las piezas planas
          ↓ + seam/hem allowances
CUT GEOMETRY
  contorno que realmente se corta
          ↓ + marker clearance
NESTING GEOMETRY
  geometría usada para impedir contacto/solape
```

- **Medida corporal:** propiedad del cuerpo; es la base de designación y selección de talla.
- **Holgura/ease:** diferencia de diseño y movimiento entre cuerpo y prenda. No es margen de costura.
- **Medida terminada:** dimensión de la prenda confeccionada, medible según ISO 18890.
- **Medida de patrón:** distancia sobre líneas de costura; debe considerar emparejamiento de costuras y forma 2D.
- **Margen de costura:** material añadido entre línea de costura y borde de corte.
- **Separación de nesting:** distancia de proceso entre dos contornos de corte; es otra restricción independiente.

La literatura técnica resume la relación como `body + wearing ease + design ease = garment silhouette`; una revisión técnica de patronaje describe bloques, ease y operaciones geométricas de patrón [en este capítulo técnico](https://www.sciencedirect.com/topics/engineering/clothing-pattern).

### 1.3 Sistema base propuesto: TSHIRT-REGULAR-STRAIGHT-ADULT-v0

**Población objetivo provisional:** adulto, bloque recto de presentación unisex construido sobre proporciones cercanas a figura masculina/straight; estatura regular nominal 176 cm y rango de diseño provisional 168–184 cm. No está validado específicamente para población colombiana/latinoamericana ni pretende cubrir con el mismo fit figuras curvy, petite, tall o plus.  
**Material/fit:** punto jersey de elasticidad moderada, manga corta, regular-fit.  
**Propósito:** generar geometría de ingeniería para validar la aplicación, no certificar ajuste antropométrico ni etiquetado comercial.  
**Unidad interna:** centímetros en catálogo; geometría cuantizada a rejilla entera al entrar al engine.

Todas las cifras de las dos tablas siguientes son **supuestos de diseño configurables del proyecto**, no valores ISO/ASTM.

#### Tabla corporal provisional

| Talla | Intervalo pecho, cm | Pecho nominal | Cintura | Cadera | Cuello base | Bíceps | Hombros | Estatura nominal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| XS | 80–<88 | 84 | 72 | 88 | 35.0 | 27 | 39.0 | 176 |
| S | 88–<96 | 92 | 80 | 96 | 36.5 | 29 | 40.5 | 176 |
| M | 96–<104 | 100 | 88 | 104 | 38.0 | 31 | 42.0 | 176 |
| L | 104–<112 | 108 | 96 | 112 | 39.5 | 33 | 43.5 | 176 |
| XL | 112–<120 | 116 | 104 | 120 | 41.0 | 35 | 45.0 | 176 |
| XXL | 120–<128 | 124 | 112 | 128 | 42.5 | 37 | 46.5 | 176 |
| XXXL | 128–136 | 132 | 120 | 136 | 44.0 | 39 | 48.0 | 176 |

```text
size_index = 0 para XS ... 6 para XXXL
chest = 84 + 8 × size_index
waist = chest - 12
hip = chest + 4
neck_base = 35 + 1.5 × size_index
biceps = 27 + 2 × size_index
shoulders = 39 + 1.5 × size_index
```

#### Prenda terminada provisional

| Talla | Pecho terminado | Bajo terminado | Largo HPS | Hombros terminados | Manga corta | Abertura manga |
|---|---:|---:|---:|---:|---:|---:|
| XS | 92 | 92 | 64 | 40.0 | 19 | 32 |
| S | 100 | 100 | 66 | 41.5 | 20 | 34 |
| M | 108 | 108 | 68 | 43.0 | 21 | 36 |
| L | 116 | 116 | 70 | 44.5 | 22 | 38 |
| XL | 124 | 124 | 72 | 46.0 | 23 | 40 |
| XXL | 132 | 132 | 74 | 47.5 | 24 | 42 |
| XXXL | 140 | 140 | 76 | 49.0 | 25 | 44 |

```text
finished_chest = body_chest + 8
finished_hem = max(finished_chest, body_hip + 4)
sleeve_opening = body_biceps + 5
HPS_length = 64 + 2 × size_index
short_sleeve_length = 19 + size_index
finished_shoulders = body_shoulders + 1
```

Los `+8 cm` de ease total de pecho son una decisión regular-fit del producto, no una prescripción normativa. Como contraste de mercado, la [Stanley/Stella Creator 2.0 STTU169](https://live.ink/images/pictures/shop/products/sttu169-creator-2-0.pdf) publica dimensiones terminadas de una camiseta unisex; no se usa como tabla corporal ni como autoridad normativa.

**Interpretación correcta:** estas cifras son una especificación provisional de producto para pruebas. Antes de publicar `TSHIRT-REGULAR-v1` se necesitan (a) población/mercado aprobados, (b) tabla corporal licenciada o estudio antropométrico, (c) prototipos al menos M/XL/XXXL y fitting, (d) tolerancias de confección, elasticidad y encogimiento.

### 1.4 Datos versionados, no constantes de algoritmo

Cada valor debe conservar:

- `measurement_kind`: `BODY | GARMENT | PATTERN`;
- talla, valor, unidad y tolerancia;
- fuente y URL;
- fecha de consulta;
- población y fit;
- transformación/fórmula;
- estado: `OBSERVED | PUBLISHED_REFERENCE | DERIVED | ASSUMPTION`;
- versión, autor y `content_hash`.

Una versión `PUBLISHED` es inmutable. Toda corrección produce una versión nueva y las órdenes históricas conservan snapshot y hash.

## 2. Supuestos de patronaje y piezas propuestas

### 2.1 Cadena paramétrica

El generador de patrones no debe escalar un dibujo global uniformemente. Debe producir una geometría por talla desde parámetros versionados y reglas explícitas:

1. seleccionar medidas corporales y población;
2. aplicar ease de pecho/cintura/cadera, balance delantero-espalda y largos;
3. construir líneas de costura paramétricas;
4. comprobar longitudes de costuras emparejadas y continuidad de curvas;
5. añadir márgenes de costura y dobladillo;
6. discretizar curvas con tolerancia registrada;
7. validar topología, área, bounding box, grainline y transforms;
8. congelar geometría y hash en `PatternSetVersion`.

### 2.2 Piezas de `TSHIRT-REGULAR-v1`

| Pieza | Cantidad/prenda | Geometría mínima | Grainline | Rotación propuesta | Mirror |
|---|---:|---|---|---|---|
| Delantero completo | 1 | hombro, escote delantero curvo, sisa, costado y bajo; no rectángulo | paralelo al centro delantero | 0°; 180° solo tela no direccional | no |
| Espalda completa | 1 | hombro, escote posterior, sisa, costado y bajo | paralelo al centro espalda | 0°; 180° solo tela no direccional | no |
| Manga corta | 2 | copa asimétrica frente/espalda, costados y boca | eje longitudinal de manga | 0°; 180° solo si compatible | par izquierda/derecha explícito |
| Banda de cuello | 1 | tira o anillo abierto con geometría de corte versionada | dirección de mayor elasticidad transversal definida | por regla propia | no |

Para un marcador industrial de ancho abierto se usarán piezas completas, no mitades “al doblez”. El modo del tendido (`FACE_ONE_WAY`, `FACE_TO_FACE`, tubular, etc.) afecta mirror y direccionalidad y debe modelarse. El MVP propone `OPEN_WIDTH_FACE_ONE_WAY`; esta decisión necesita aprobación.

### 2.3 Supuestos que deben validarse físicamente

- tejido de punto jersey con elasticidad y encogimiento definidos;
- regular-fit con 8 cm de ease total de pecho como hipótesis inicial;
- margen de costura por tipo de borde, no uno global implícito;
- dobladillos separados del margen de costura;
- reparto delantero/espalda, hombro, sisa, copa y escote realizado por reglas de patrón, no por bounding boxes;
- banda de cuello calculada según longitud de escote, elasticidad/recuperación y método de montaje;
- tolerancia de discretización y separación de corte aprobadas por producción.

Valores iniciales para prototipo —no normas—: unión general 1.0 cm, banda-cuello 0.7 cm, dobladillos 2.5 cm y longitud de banda `0.85 × seamline del escote`. Deben quedar en perfiles configurables y cambiarse tras prueba de máquina/tejido. [ISO 4916](https://www.iso.org/standard/10934.html) clasifica costuras y [ASTM D6193-16(2025)](https://store.astm.org/d6193-16r25.html) cubre puntadas y costuras, pero ninguna justifica un margen universal. La compensación por encogimiento permanecerá desactivada hasta medir la tela; [ISO 5077:2007](https://www.iso.org/standard/41877.html) define la determinación del cambio dimensional tras lavado y secado.

## 3. Investigación y estrategia de irregular 2D nesting

### 3.1 Comparación

| Técnica | Fortalezas | Debilidades | Decisión MVP |
|---|---|---|---|
| No-Fit Polygon / Inner-Fit Polygon | Representa regiones de solape y contención; cacheable por par de geometrías/orientaciones. | Casos degenerados, concavidades, huecos y tolerancias son difíciles. | Sí, tras spike de robustez y con verificación independiente. |
| Bottom-Left / BLF | Rápido, explicable y determinista; BLF aprovecha huecos mejor que BL. | Muy sensible al orden; puede quedar en mínimos locales. | Sí, como decodificador de placement. |
| Polygon clipping/offset/Minkowski | Primitivas para colisión, clearance, NFP e IFP. | No optimiza por sí solo; errores de precisión pueden invalidar todo. | Sí, kernel con enteros escalados. |
| Búsqueda local/tabu/ILS | Mejora orden/orientación; movimientos y trazas auditables. | Sigue siendo heurística. | Sí, con presupuesto fijo de evaluaciones. |
| Genetic algorithm | Explora globalmente y combina bien con BLF. | Muchas evaluaciones, parámetros, semilla y variabilidad. | No inicialmente; extensión posterior. |
| Simulated annealing | Simple y capaz de escapar óptimos locales. | Sensible al enfriamiento y costoso si reconstruye cada marker. | No inicialmente; benchmark posterior. |
| Raster/píxel | Colisión robusta a resolución fija y prototipado rápido. | Error de discretización y coste de memoria. | Solo oráculo/regresión visual, no kernel final. |
| MILP geométrico exacto | Puede ofrecer garantías/gaps en instancias acotadas. | Formulaciones y escalabilidad complejas. | Investigación posterior, no MVP. |

Fuentes técnicas clave:

- Burke et al., [A New Bottom-Left-Fill Heuristic Algorithm](https://doi.org/10.1287/opre.1060.0293).
- Bennell y Song, [A comprehensive and robust procedure for obtaining the no-fit polygon](https://doi.org/10.1016/j.ejor.2006.03.011).
- Burke et al., [Irregular Packing Using the Line and Arc No-Fit Polygon](https://doi.org/10.1287/opre.1090.0770).
- Toledo et al., [A hybrid methodology for irregular strip packing](https://doi.org/10.3182/20130522-3-BR-4036.00041).
- [Revisión 2022 de irregular 2D packing](https://www.frontiersin.org/journals/mechanical-engineering/articles/10.3389/fmech.2022.966691/full).
- [Clipper2](https://github.com/AngusJohnson/Clipper2), referencia de booleanas y offsets con enteros; licencia y binding deben revisarse en el spike.
- [CGAL Minkowski Sum 2](https://doc.cgal.org/latest/Minkowski_sum_2/group__PkgMinkowskiSum2Ref.html), alternativa exacta con implicaciones de licencia/complejidad.

### 3.2 Pipeline MVP recomendado

1. normalizar contornos, orientación y vértices; rechazar geometría no reparable;
2. discretizar curvas con tolerancia versionada;
3. convertir a coordenada entera escalada y comprobar overflow;
4. construir geometría de clearance separada de la geometría de corte;
5. generar orientaciones permitidas por grainline, material y lay mode;
6. cachear NFP/IFP por hashes, orientación, clearance y versión del kernel;
7. ejecutar BLF con orden total de candidatos y empates deterministas;
8. evaluar secuencias iniciales por área, largo, concavidad, tipo/talla e ID;
9. mejorar mediante `swap`, `insert`, `reverse` y compactación con presupuesto por evaluaciones;
10. validar desde cero contención, no solape, clearance, transforms, cantidades, ancho y largo;
11. emitir certificado JSON con inputs, hashes, placements, score, versión y trazas.

**Bounding boxes** se admiten únicamente como índice/poda; nunca como representación de la pieza ni prueba final.

### 3.3 Reproducibilidad

- rejilla entera y tolerancias centralizadas;
- orden canónico de contornos, piezas y candidatos;
- comparador total para empates;
- semilla explícita incluso si no hay azar;
- presupuesto por número de evaluaciones para `reproducible-search`;
- input hash, engine version, build/commit, config y razón de parada;
- placements persistidos para `reproducible-result`;
- modo audit single-thread o reducción paralela estable.

## 4. Estrategia de optimización de tendidos y capas

### 4.1 Separación en dos niveles

El generador crea composiciones candidatas `M(s,k)`; el nesting devuelve, solo si es válido, la longitud real `L(k)`, áreas y certificado. Después el master planner selecciona usos del marcador y capas.

Para cada alternativa de marcador `k` y capas `c ∈ [1,max_layers]`:

```text
production(s,k,c) = M(s,k) × c
linear_consumption(k,c) = L(k) × c
```

Variables enteras seleccionan tendidos; las restricciones imponen:

```text
Σ production(s) >= demand(s)
layers <= max_layers
L(k) <= usable_table_length
overproduction dentro de la política
```

Los candidatos se generan incrementalmente: tallas simples, combinaciones pequeñas, poda por cota de área/ancho/largo, dominancia y demanda residual. Si el catálogo crece, se evoluciona a generación de columnas.

### 4.2 Objetivos lexicográficos

La demanda completa es restricción dura, no un peso. El motor resuelve secuencialmente cada criterio y fija el óptimo anterior antes de continuar.

- **Opción A — menor tela:** sobreproducción total → consumo lineal → desperdicio absoluto → tendidos.
- **Opción B — menos tendidos:** sobreproducción total → tendidos → consumo lineal → desperdicio.
- **Opción C — balanceada:** máxima sobreproducción de una talla → sobreproducción total → consumo → tendidos → desperdicio.

La prioridad del encargo coloca sobreproducción antes que consumo. Si “razonablemente posible” debe permitir una unidad extra para ahorrar una cantidad considerable de tela, ese umbral debe aprobarse como política explícita; no se ocultará en pesos arbitrarios.

Se conserva la frontera no dominada y se deduplica por producción, spreads/composición y tolerancia material aprobada. Puede devolverse una o dos opciones si no existen tres diferencias útiles.

### 4.3 Métricas auditables

```text
marker_area = usable_width × marker_length
piece_area = Σ area(cut_geometry_instance)
marker_efficiency = piece_area / marker_area × 100
spread_linear_consumption = marker_length × layers
global_efficiency =
  Σ(piece_area × layers) / Σ(marker_area × layers) × 100
```

La eficiencia global es ponderada por área consumida; no es el promedio simple de porcentajes. Cada valor lleva estado `ESTIMATED`, `CALCULATED` u `OPTIMIZED_VALIDATED`, fórmula, unidad y procedencia.

## 5. Arquitectura propuesta

### 5.1 Estilo

```text
React Web
   │ HTTPS/JSON + polling/SSE opcional
   ▼
FastAPI — monolito modular
   ├─ Catalog & Standards
   ├─ Pattern & Versioning
   ├─ Cutting Resources
   ├─ Production Orders
   ├─ Optimization Coordination
   └─ Result Presentation
             │ job asíncrono
             ▼
Optimization Worker
   ├─ Candidate Generator
   ├─ Lay/Layer Planner (CP-SAT)
   └─ Geometry/Nesting Engine
             │
             ▼
PostgreSQL + artefactos
```

Es un monolito modular, no microservicios. API y worker comparten dominio y repositorio, pero Geometry/Nesting es una interfaz pura sin acceso a órdenes o base de datos. Podrá extraerse como proceso/binario sin romper el API.

### 5.2 Stack

| Capa | Propuesta | Justificación |
|---|---|---|
| API/domain | Python 3.12+, FastAPI, Pydantic | Contratos tipados, OpenAPI y velocidad de iteración. |
| Persistence | PostgreSQL, SQLAlchemy 2, Alembic | Transacciones, JSONB versionado y migraciones. PostGIS no es necesario en MVP. |
| Jobs | Redis + Dramatiq/RQ; decisión final en spike | Corridas largas, progreso, cancelación y retry. |
| Planning | OR-Tools CP-SAT | Variables/constraints enteras; la documentación oficial exige enteros y distingue `OPTIMAL`, `FEASIBLE`, `INFEASIBLE` y `UNKNOWN`: [OR-Tools](https://developers.google.com/optimization/cp/cp_solver). |
| Geometry | Adapter propio; Shapely/GEOS para validación + kernel entero Clipper2/equivalente | Operaciones poligonales sin acoplar dominio a una librería. |
| Frontend | React, TypeScript, Vite, TanStack Query, React Hook Form/Zod | Formulario y resultados desacoplados del solver. |
| Marker UI | SVG con zoom/pan; Canvas/WebGL solo por evidencia | Accesible, inspeccionable y suficiente para el volumen MVP. |
| Testing | pytest, Hypothesis, testcontainers, Playwright | Unit, propiedades, integración y E2E. |
| Operación | Docker Compose, CI, OpenTelemetry, logs JSON | Reproducibilidad y diagnóstico. |

No se fija versión exacta de dependencias en este reporte; el lockfile de implementación deberá congelarlas.

## 6. Modelo de dominio y datos

### 6.1 Agregados y decisiones

- `GarmentType`: categoría, por ejemplo T-shirt.
- `GarmentModel`: identidad estable `TSHIRT-REGULAR`, `origin_type=STANDARD|CUSTOM`.
- `GarmentModelVersion`: publicación inmutable, linaje, población, fit, hashes y referencias.
- `SizingStandardVersion`, `MeasurementDefinition`, `MeasurementValue`, `MeasurementDerivation`, `SourceReference`.
- `PatternSetVersion`, `PatternPieceDefinition`, `PatternPieceGeometry`, `AllowedTransform`.
- `FabricConfiguration`, `CuttingTableConfiguration`.
- `ProductionOrder`, `ProductionOrderItem`, `SizeDemand`.
- `OptimizationRun`, `OptimizationSolution`, `Spread`, `Marker`, `MarkerSizeComposition`, `MarkerPlacement`, `SizeResult`, `ValidationReport`, `Artifact`.

No se crean subclases físicas para `CustomGarmentModel`; un modelo clonado usa `origin_type=CUSTOM` y `derived_from_version_id`, preservando linaje sin duplicar el dominio.

### 6.2 Relación principal

```text
GarmentModel 1 ── * GarmentModelVersion
                         ├── 1 SizingStandardVersion
                         ├── 1 MeasurementSetVersion
                         └── 1 PatternSetVersion ── * PatternPiece

ProductionOrder ── * OrderItem ── * SizeDemand
       └── * OptimizationRun
                └── 1..3 OptimizationSolution
                          ├── * SizeResult
                          └── * Spread ── 1 Marker
                                            ├── * SizeComposition
                                            └── * Placement
```

Aunque el esquema admite varias líneas, el dominio MVP exige exactamente una referencia por orden.

### 6.3 Versionamiento y auditoría

Una versión publicada no se edita. Una corrida almacena:

- snapshot canónico de modelo, patrón, tela, mesa y política;
- SHA-256 de inputs y geometrías;
- schema/engine/solver/build versions;
- rejilla, tolerancias, semilla y presupuesto;
- orden de piezas y criterio de parada;
- placements, métricas, explicación y certificado final.

Estados de corrida:

```text
QUEUED → RUNNING → SUCCEEDED
                 ↘ FAILED | CANCELLED | TIMED_OUT
```

Un timeout puede conservar la mejor solución validada como `FEASIBLE_NOT_PROVEN_BEST`, si esta política se aprueba; nunca se etiqueta `OPTIMAL` sin prueba.

## 7. Contratos API principales

Prefijo `/api/v1`, errores `application/problem+json`, IDs UUID/ULID e `Idempotency-Key` para creación.

### 7.1 Catálogo y configuración

```http
GET /api/v1/garment-models?status=published
GET /api/v1/garment-model-versions/{version_id}
GET /api/v1/garment-model-versions/{version_id}/sizes
GET /api/v1/garment-model-versions/{version_id}/pattern-summary
GET /api/v1/fabric-configurations
GET /api/v1/cutting-table-configurations
```

El frontend obtiene las tallas del API; XS–XXXL no son lógica hardcodeada.

### 7.2 Órdenes

```http
POST /api/v1/production-orders
GET  /api/v1/production-orders/{order_id}
```

```json
{
  "garment_model_version_id": "uuid",
  "fabric_configuration_id": "uuid",
  "cutting_table_configuration_id": "uuid",
  "demand": [
    {"size_code": "S", "quantity": 3},
    {"size_code": "M", "quantity": 20}
  ]
}
```

RN-001/RN-002 se validan en cliente y servidor. Los ceros pueden omitirse del request normalizado, pero la respuesta conserva el catálogo completo.

### 7.3 Corridas y resultados

```http
POST /api/v1/production-orders/{order_id}/optimization-runs
GET  /api/v1/optimization-runs/{run_id}
POST /api/v1/optimization-runs/{run_id}/cancel
GET  /api/v1/optimization-runs/{run_id}/events
GET  /api/v1/optimization-runs/{run_id}/solutions
GET  /api/v1/optimization-solutions/{solution_id}
GET  /api/v1/markers/{marker_id}
GET  /api/v1/markers/{marker_id}/geometry
GET  /api/v1/optimization-runs/{run_id}/audit
```

La creación responde `202 Accepted`. Polling siempre está disponible; SSE es opcional para no acoplar futuros clientes.

```json
{
  "overproduction_policy": {
    "allow": true,
    "max_units_per_size": null,
    "max_percentage_per_size": null
  },
  "solution_profiles": ["MIN_FABRIC", "MIN_SPREADS", "BALANCED"],
  "compute_budget": {
    "max_nesting_evaluations": 50000,
    "time_limit_seconds": 120,
    "deterministic": true
  }
}
```

La geometría de resultados devuelve contornos y transforms explícitos. El navegador no recalcula placements ni eficiencia.

## 8. Diseño conceptual de UI

1. **Nueva orden:** selector de modelo/version, tabla XS–XXXL desde API, inputs enteros, resumen de tela/mesa y acción “Optimizar corte”.
2. **Procesando:** fases generación → nesting → planificación → validación, progreso, cancelación y ningún resultado provisional fingido.
3. **Alternativas:** tarjetas A/B/C y tabla comparativa; total, sobreproducción, tela, tendidos, eficiencia ponderada, estado y explicación.
4. **Detalle:** tarjetas por tendido con capas, largo, composición, producción, consumo y marcador SVG.
5. **Auditoría:** versión de modelo, fuentes, supuestos, snapshots, política, motores, hashes y advertencias.

El SVG permite zoom/pan, color por talla, estilo por tipo de pieza, tooltip con pieza/talla/rotación, etiquetas opcionales, leyenda, márgenes y límites visibles. Debe ser usable con teclado y no depender solo del color.

## 9. Estrategia de pruebas

### 9.1 Dominio

- enteros `>=0`, al menos una talla positiva y talla válida;
- producción, sobreproducción, consumo y eficiencia ponderada;
- capas, ancho/largo y snapshots;
- inmutabilidad de versiones publicadas.

### 9.2 Geometría

- invariancia de área bajo transforms;
- contención, no solape y clearance;
- grainline/rotaciones/mirror;
- NFP contra intersección directa;
- contacto punto/arista, colineales, concavidades, huecos, vértices duplicados y aristas casi cero;
- coordenadas negativas/grandes y overflow;
- golden layouts byte-estables y pruebas metamórficas;
- verifier independiente obligatorio.

### 9.3 Casos A–E

| Caso | Demanda | Qué debe probarse ahora |
|---|---|---|
| A | S=3 | menos de max layers, exactitud/sobreproducción y capas válidas |
| B | S=3, M=20, L=10, XL=12, XXL=30 | demanda desbalanceada, composiciones y múltiples tendidos |
| C | 1 de cada talla | muchas tallas, baja demanda y deduplicación de opciones |
| D | M=100 | demanda mayor que max layers y repetición de tendidos |
| E | XXXL=31 | frontera 30/31 y sobreproducción |

Agregar 29/30/31 unidades, demanda prima, `allow_overproduction=false`, combinación que no cabe en 7 m, tela direccional y separaciones extremas.

Hasta congelar las geometrías, las aserciones son invariantes y factibilidad. Los golden de metros/eficiencia se añaden solo después de aprobar `TSHIRT-REGULAR-v1` y ejecutar nesting real.

### 9.4 Integración, E2E y rendimiento

- API → DB → job → worker → solución;
- idempotencia, retry, cancelación, timeout y fallo sin publicar parcial;
- OpenAPI y cliente TS generado;
- formulario, progreso, comparación y mapa por teclado;
- tiempo hasta primera solución válida, evaluaciones, memoria y payload;
- benchmarks ESICUP más casos textiles reales anonimizados.

## 10. Riesgos técnicos

| Riesgo | Mitigación / gate |
|---|---|
| Tabla de tallaje no validada para el mercado | Elegir población; adquirir/licenciar fuente; fitting físico antes de `v1`. |
| Patrón paramétrico irreal | Revisión por patronista, toile, medidas de control y matching de costuras. |
| Geometría sucia/tolerancias | Normalización, rejilla, fixtures degenerados y verifier independiente. |
| NFP incorrecto | Spike previo, caché versionada y fallback BLF poligonal exacto. |
| Overflow al escalar/Minkowski | Escala documentada y validación previa de rango. |
| Explosión de candidatos/NFP | Poda, dominancia, caché y generación incremental. |
| No determinismo | Orden total, presupuesto por evaluaciones, seed y modo audit. |
| Optimizar marker aislado empeora pedido | Separar geometry cost de master planner global. |
| Reglas textiles omitidas | Modelar lay mode, nap, direction, mirror, shrinkage y lotes. |
| Licencias de kernel | Revisión legal/técnica antes de seleccionar binding definitivo. |
| Falsa precisión | Estados de procedencia y no publicar métricas sin certificado. |
| “Razonablemente posible” ambiguo | Política/umbrales aprobados, no pesos ocultos. |

## 11. Decisiones que necesitan aprobación

1. Población/mercado del MVP: ¿unisex comercial, masculina ASTM u otra población local?
2. Uso de `TSHIRT-REGULAR-STRAIGHT-ADULT-v0` como dato de ingeniería, no talla normativa.
3. Adopción de la gradación provisional y ease total de pecho de 8 cm.
4. Valores corporales faltantes y reglas completas de patronaje, tras revisión de patronista.
5. Geometrías y márgenes de `TSHIRT-REGULAR-v1`.
6. Tipo de tejido, elasticidad, encogimiento, ancho físico/útil y tolerancias.
7. Propuesta de desarrollo: mesa 8 m/7 m/30 capas y ancho útil de tela aún **sin valor**, hasta aprobación.
8. Lay mode inicial, direccionalidad y política 0°/180°/mirror.
9. Separación mínima y márgenes laterales/inicial/final.
10. Política de sobreproducción y significado cuantitativo de “razonablemente posible”.
11. Orden lexicográfico exacto de A/B/C y deduplicación material.
12. Presupuesto de cálculo, escala esperada y política de timeout.
13. Kernel geométrico final después del spike NFP/Clipper2/licencias.
14. Autenticación/multiempresa fuera del MVP, salvo necesidad inmediata.

## 12. Plan de implementación por fases

### Fase 2A — Catálogo versionado y esqueleto

Monorepo, CI, migraciones, catálogo, snapshots, fuentes, tela/mesa configurables, OpenAPI y cliente TS.  
**Gate:** publicación inmutable y trazabilidad completa.

### Fase 2B — Validación textil y patrón

Fuente corporal aprobada, reglas paramétricas, revisión de patronista, toile/fitting, geometrías y hashes por talla.  
**Gate:** `TSHIRT-REGULAR-v1` firmado; no se optimiza antes.

### Fase 2C — Órdenes y frontend de entrada

RN-001–RN-005, creación/consulta de órdenes y casos A–E sin solver.  
**Gate:** demanda y snapshots correctos.

### Fase 2D — Geometry Engine

Spike NFP/kernel, normalización, NFP/IFP, BLF, local search, verifier y visualizador.  
**Gate:** fixtures geométricos, degenerados y reproducibilidad.

### Fase 2E — Optimization Engine

Candidatos, nesting certificado, CP-SAT capas/tendidos, objetivos lexicográficos, alternativas y explicaciones.  
**Gate:** A–E válidos o diagnóstico reproducible; ninguna cifra ficticia.

### Fase 2F — Jobs y resultados

Worker, progreso, cancelación/timeout, vistas de resultado/auditoría y observabilidad.  
**Gate:** E2E reconstruible desde snapshot.

### Fase 2G — Endurecimiento

Properties, golden, carga, accesibilidad, benchmark, calibración textil y documentación operativa.  
**Gate:** criterios funcionales y presupuestos de rendimiento aprobados.

## 13. Stop gate y veredicto

La investigación estableció correctamente el alcance de ISO/ASTM y una propuesta provisional trazable, pero **no puede considerarse validado el tallaje productivo** sin elegir población, acceder a la tabla corporal correspondiente y hacer validación física del patrón. Por ello FASE 0 queda `PARTIAL`, no `PASS`.

La arquitectura, contratos, separación de motores, estrategia algorítmica, datos y pruebas quedan suficientemente definidos para aprobar FASE 1 sin comenzar implementación.

```text
FASE_0_RESEARCH = PARTIAL
FASE_1_ARCHITECTURE = PASS
STOP_GATE = ACTIVE
IMPLEMENTATION_AUTHORIZED = NO
```

Se requiere aprobación expresa de las decisiones de la sección 11 antes de iniciar FASE 2.
