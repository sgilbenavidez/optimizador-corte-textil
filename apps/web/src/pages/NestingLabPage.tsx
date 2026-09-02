import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api } from "../api";
import type { MarkerPreview } from "../types";

const SIZES = ["XS", "S", "M", "L", "XL", "XXL", "XXXL"];
const COLORS: Record<string, string> = { FRONT: "#dcebe5", BACK: "#f4dfb9", SLEEVE: "#d9e1ef", NECKBAND: "#ead8e5" };
type Layers = { ids: boolean; bbox: boolean; grain: boolean; clearance: boolean; debug: boolean };

function Metric({ label, value, unit = "" }: { label: string; value: string | number; unit?: string }) {
  return <div><span>{label}</span><strong>{value}{unit}</strong></div>;
}

export function MarkerCanvas({ marker, layers }: { marker: MarkerPreview; layers: Layers }) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const units = 1000;
  const length = (marker.marker_length_cm ?? marker.max_length_cm) * units;
  const width = marker.physical_width_cm * units;
  const points = (ring: Array<[number, number]>) => ring.map(([x, y]) => `${x},${width - y}`).join(" ");
  return <div className="marker-stage">
    <div className="zoom-tools" aria-label="Controles de zoom del marcador">
      <button onClick={() => setZoom((v) => Math.min(4, v + .2))}>+</button><span>{Math.round(zoom * 100)}%</span>
      <button onClick={() => setZoom((v) => Math.max(.45, v - .2))}>−</button>
      <button onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>Centrar</button>
    </div>
    <svg role="img" aria-label="Marcador real validado geométricamente" viewBox={`0 0 ${length} ${width}`}
      onPointerDown={(event) => { drag.current = { x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y }; event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={(event) => { if (drag.current) setPan({ x: drag.current.panX + (event.clientX - drag.current.x) * 130, y: drag.current.panY + (event.clientY - drag.current.y) * 130 }); }}
      onPointerUp={() => { drag.current = null; }}>
      <defs><marker id="marker-arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#176a70" /></marker></defs>
      <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
        <rect className="fabric-physical" x="0" y="0" width={length} height={width} />
        <rect className="fabric-usable" x={marker.margins_cm.start * units} y={marker.margins_cm.right * units} width={length - (marker.margins_cm.start + marker.margins_cm.end) * units} height={marker.usable_width_cm * units} />
        {marker.placements.map((piece) => {
          const [x0, y0, x1, y1] = piece.bbox.units;
          const grainY1 = width - piece.grainline.start[1]; const grainY2 = width - piece.grainline.end[1];
          return <g key={piece.piece_instance_id}>
            {layers.clearance && <polygon className="clearance-envelope" points={points(piece.transformed_polygon.coordinates[0])} />}
            <polygon className="marker-piece" fill={COLORS[piece.piece_code] ?? "#dcebe5"} points={points(piece.transformed_polygon.coordinates[0])}>
              <title>{piece.piece_instance_id} · {piece.piece_code} · talla {piece.size_code} · {piece.transform.rotation}°</title>
            </polygon>
            {layers.bbox && <rect className="marker-bbox" x={x0} y={width - y1} width={x1 - x0} height={y1 - y0} />}
            {layers.grain && <line className="marker-grain" x1={piece.grainline.start[0]} y1={grainY1} x2={piece.grainline.end[0]} y2={grainY2} markerStart="url(#marker-arrow)" markerEnd="url(#marker-arrow)" />}
            {layers.ids && <text className="marker-label" x={x0 + 1800} y={width - y1 + 4000}>{piece.piece_code} · {piece.size_code}<tspan x={x0 + 1800} dy="3400">#{piece.sequence} · {piece.transform.rotation}°</tspan></text>}
          </g>;
        })}
        {layers.debug && marker.debug_geometry?.candidate_positions?.slice(0, 300).map(([x, y], index) => <circle key={index} className="candidate-dot" cx={x} cy={width - y} r="700" />)}
      </g>
    </svg>
  </div>;
}

export function NestingLabPage() {
  const [quantities, setQuantities] = useState<Record<string, number>>({ XS: 0, S: 0, M: 1, L: 0, XL: 0, XXL: 0, XXXL: 0 });
  const [layers, setLayers] = useState<Layers>({ ids: true, bbox: false, grain: true, clearance: false, debug: false });
  const patterns = useQuery({ queryKey: ["pattern-sets"], queryFn: api.listPatternSets });
  const fabrics = useQuery({ queryKey: ["fabrics"], queryFn: api.listFabrics });
  const tables = useQuery({ queryKey: ["tables"], queryFn: api.listTables });
  const pattern = patterns.data?.[0]; const fabric = fabrics.data?.[0]; const table = tables.data?.[0];
  const preview = useMutation({ mutationFn: (debug: boolean) => api.previewMarker({
    pattern_set_version_id: pattern!.id, fabric_configuration_id: fabric!.id,
    cutting_table_configuration_id: table!.id,
    composition: SIZES.filter((size) => quantities[size] > 0).map((size) => ({ size_code: size, quantity: quantities[size] })),
    deterministic: true, seed: 1, evaluation_budget: 100000, debug,
  }) });
  const ready = Boolean(pattern && fabric && table && Object.values(quantities).some(Boolean));
  const update = (size: string, delta: number) => setQuantities((value) => ({ ...value, [size]: Math.max(0, Math.min(10, value[size] + delta)) }));
  return <main className="workspace nesting-workspace">
    <header className="page-header"><div><p className="eyebrow">Geometry engine · Fase 2D</p><h1>Laboratorio de nesting irregular</h1><p className="intro">Compón un marcador técnico y verifica cada pieza sobre la geometría real de corte.</p></div><span className="phase-badge">NFP {preview.data?.nfp_status ?? "PARTIAL"}</span></header>
    <div className="warning-banner pattern-warning"><span>!</span>Laboratorio geométrico — no es todavía un plan de producción.</div>
    <section className="nesting-controls">
      <div className="nesting-source"><label>Pattern<select aria-label="Pattern" disabled><option>{pattern?.version_code ?? "Cargando patrón…"}</option></select></label><label>Tela<select aria-label="Tela" disabled><option>{fabric?.display_name ?? "Cargando tela…"}</option></select></label></div>
      <div className="composition-grid">{SIZES.map((size) => <div key={size} className="quantity-stepper"><strong>{size}</strong><button aria-label={`Restar ${size}`} onClick={() => update(size, -1)}>−</button><output aria-label={`Cantidad ${size}`}>{quantities[size]}</output><button aria-label={`Sumar ${size}`} onClick={() => update(size, 1)}>+</button></div>)}</div>
      <button className="primary-button generate-marker" disabled={!ready || preview.isPending} onClick={() => preview.mutate(layers.debug)}>{preview.isPending ? "CALCULANDO GEOMETRÍA…" : "GENERAR MARCADOR"}</button>
      {preview.error && <p className="form-error">{preview.error.message}</p>}
    </section>
    {preview.data && <>
      <section className="marker-summary" aria-label="Métricas del marcador">
        <Metric label="Estado" value={preview.data.status} /><Metric label="Validación" value={preview.data.validation.status} />
        <Metric label="Longitud" value={(preview.data.marker_length_cm ?? 0).toFixed(2)} unit=" cm" /><Metric label="Ancho útil" value={preview.data.usable_width_cm} unit=" cm" />
        <Metric label="Piezas" value={preview.data.piece_count} /><Metric label="Eficiencia" value={(preview.data.efficiency_percentage ?? 0).toFixed(2)} unit="%" />
        <Metric label="Desperdicio" value={(preview.data.waste_percentage ?? 0).toFixed(2)} unit="%" /><Metric label="Lower bound" value={preview.data.lower_bound_length_cm.toFixed(2)} unit=" cm" />
        <Metric label="Evaluaciones" value={preview.data.evaluation_count.toLocaleString("es-CO")} /><Metric label="Tiempo" value={preview.data.elapsed_time_ms.toFixed(0)} unit=" ms" />
      </section>
      <section className="marker-toolbar"><strong>Marcador SVG real</strong><fieldset><legend>Capas</legend>{Object.entries({ ids: "IDs", bbox: "Bboxes", grain: "Grainline", clearance: "Clearance", debug: "Debug" }).map(([key, label]) => <label key={key}><input type="checkbox" checked={layers[key as keyof Layers]} onChange={() => setLayers((v) => ({ ...v, [key]: !v[key as keyof Layers] }))} />{label}</label>)}</fieldset></section>
      {preview.data.status === "VALIDATED_FEASIBLE" ? <MarkerCanvas marker={preview.data} layers={layers} /> : <div className="load-error">{preview.data.status}: {preview.data.diagnostics.join(" · ")}</div>}
      <section className="marker-certificate"><div><span>Algoritmo</span><strong>{preview.data.algorithm}</strong></div><div><span>Estrategia</span><strong>{preview.data.piece_order_strategy}</strong></div><div><span>Resultado</span><code>{preview.data.result_hash}</code></div></section>
    </>}
  </main>;
}
