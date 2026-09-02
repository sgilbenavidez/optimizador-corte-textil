import { useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { PatternPieceGeometry } from "../types";

const SIZE_CODES = ["XS", "S", "M", "L", "XL", "XXL", "XXXL"];
const ORDER = ["FRONT", "BACK", "SLEEVE", "NECKBAND"] as const;

type Layers = { seam: boolean; cut: boolean; grain: boolean; bbox: boolean; measures: boolean };
type Point = [number, number];

function PatternCanvas({ pieces, layers }: { pieces: PatternPieceGeometry[]; layers: Layers }) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const ordered = ORDER.map((code) => pieces.find((piece) => piece.piece_code === code)).filter(Boolean) as PatternPieceGeometry[];
  const front = ordered.find((piece) => piece.piece_code === "FRONT");
  const back = ordered.find((piece) => piece.piece_code === "BACK");
  const bodyHeight = Math.max(front?.geometry_metrics.cutline.bbox_cm.max_y ?? 70, back?.geometry_metrics.cutline.bbox_cm.max_y ?? 70) + 4;
  const frontWidth = front ? front.geometry_metrics.cutline.bbox_cm.max_x - front.geometry_metrics.cutline.bbox_cm.min_x : 60;
  const placements: Record<string, Point> = {
    FRONT: [5, 8], BACK: [frontWidth + 18, 8], SLEEVE: [5, bodyHeight + 17], NECKBAND: [56, bodyHeight + 17],
  };
  const canvasWidth = 150;
  const canvasHeight = bodyHeight + 64;

  const mapPoint = (piece: PatternPieceGeometry, [x, y]: Point): Point => {
    const box = piece.geometry_metrics.cutline.bbox_cm;
    const [ox, oy] = placements[piece.piece_code];
    return [ox + x - box.min_x, oy + box.max_y - y];
  };
  const points = (piece: PatternPieceGeometry, ring: Array<Point>) => ring.map((point) => mapPoint(piece, point).join(",")).join(" ");
  const path = (piece: PatternPieceGeometry) => piece.source_geometry.segments.map((segment, index) => {
    const start = mapPoint(piece, segment.start);
    const end = mapPoint(piece, segment.end);
    const prefix = index === 0 ? `M ${start.join(" ")} ` : "";
    if (segment.kind === "CUBIC_BEZIER" && segment.control1 && segment.control2) {
      const c1 = mapPoint(piece, segment.control1); const c2 = mapPoint(piece, segment.control2);
      return `${prefix}C ${c1.join(" ")} ${c2.join(" ")} ${end.join(" ")}`;
    }
    return `${prefix}L ${end.join(" ")}`;
  }).join(" ") + " Z";

  return (
    <div className="pattern-stage">
      <div className="zoom-tools" aria-label="Controles de zoom">
        <button onClick={() => setZoom((value) => Math.min(3, value + 0.2))}>+</button>
        <span>{Math.round(zoom * 100)}%</span>
        <button onClick={() => setZoom((value) => Math.max(0.55, value - 0.2))}>−</button>
        <button onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>Centrar</button>
      </div>
      <svg
        role="img" aria-label="Vista técnica separada de las piezas, no es un nesting"
        viewBox={`0 0 ${canvasWidth} ${canvasHeight}`}
        onPointerDown={(event) => { drag.current = { x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y }; event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={(event) => { if (drag.current) setPan({ x: drag.current.panX + (event.clientX - drag.current.x) / 6, y: drag.current.panY + (event.clientY - drag.current.y) / 6 }); }}
        onPointerUp={() => { drag.current = null; }}
      >
        <defs><marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#176a70" /></marker></defs>
        <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
          {ordered.map((piece) => {
            const box = piece.geometry_metrics.cutline.bbox_cm;
            const [ox, oy] = placements[piece.piece_code];
            const width = box.max_x - box.min_x; const height = box.max_y - box.min_y;
            const g1 = mapPoint(piece, piece.grainline.start); const g2 = mapPoint(piece, piece.grainline.end);
            return <g key={piece.id}>
              {layers.cut && <polygon className="cutline" points={points(piece, piece.cutline_geometry.coordinates[0])} />}
              {layers.seam && <path className="seamline" d={path(piece)} />}
              {layers.grain && <line className="grainline" x1={g1[0]} y1={g1[1]} x2={g2[0]} y2={g2[1]} markerStart="url(#arrow)" markerEnd="url(#arrow)" />}
              {layers.bbox && <rect className="bbox" x={ox} y={oy} width={width} height={height} />}
              <text className="piece-label" x={ox + 2} y={oy + 4}>{piece.piece_code} × {piece.quantity}</text>
              {layers.measures && <text className="measure-label" x={ox + 2} y={oy + 7}>{piece.geometry_metrics.cutline.area_cm2.toFixed(1)} cm² · P {piece.geometry_metrics.cutline.perimeter_cm.toFixed(1)} cm</text>}
            </g>;
          })}
        </g>
      </svg>
    </div>
  );
}

export function PatternInspectionPage() {
  const [size, setSize] = useState("M");
  const [layers, setLayers] = useState<Layers>({ seam: true, cut: true, grain: true, bbox: false, measures: true });
  const sets = useQuery({ queryKey: ["pattern-sets"], queryFn: api.listPatternSets });
  const currentSet = sets.data?.[0];
  const summaries = useQuery({
    queryKey: ["pattern-set", currentSet?.id, size],
    queryFn: () => api.getPatternSet(currentSet!.id, size), enabled: Boolean(currentSet),
  });
  const pieceIds = useMemo(() => summaries.data?.pieces.map((piece) => piece.id) ?? [], [summaries.data]);
  const geometries = useQuery({
    queryKey: ["pattern-geometries", pieceIds],
    queryFn: () => Promise.all(pieceIds.map(api.getPatternPieceGeometry)), enabled: pieceIds.length === 4,
  });

  if (sets.isLoading) return <main className="workspace"><div className="loading-card">Cargando patrones…</div></main>;
  if (!currentSet) return <main className="workspace"><div className="load-error">No hay patrones de ingeniería generados.</div></main>;
  return <main className="workspace pattern-workspace">
    <header className="page-header">
      <div><p className="eyebrow">Pattern engineering · Fase 2B</p><h1>Inspección técnica del patrón</h1><p className="intro">Piezas separadas para revisión geométrica. Esta vista no representa un trazo ni optimiza consumo de tela.</p></div>
      <span className="phase-badge">{currentSet.version_code}</span>
    </header>
    <div className="warning-banner pattern-warning"><span>!</span>{currentSet.warning}</div>
    <section className="pattern-toolbar">
      <label>Talla<select value={size} onChange={(event) => setSize(event.target.value)}>{SIZE_CODES.map((code) => <option key={code}>{code}</option>)}</select></label>
      <fieldset><legend>Capas</legend>{Object.entries({ seam: "Costura", cut: "Corte", grain: "Hilo", bbox: "Caja", measures: "Medidas" }).map(([key, label]) => <label key={key}><input type="checkbox" checked={layers[key as keyof Layers]} onChange={() => setLayers((value) => ({ ...value, [key]: !value[key as keyof Layers] }))} />{label}</label>)}</fieldset>
    </section>
    {geometries.isLoading ? <div className="loading-card">Preparando geometría SVG…</div> : geometries.data ? <PatternCanvas pieces={geometries.data} layers={layers} /> : <div className="load-error">No fue posible cargar la geometría.</div>}
    <section className="pattern-meta"><div><span>Estado</span><strong>{currentSet.lifecycle_status}</strong></div><div><span>Validación</span><strong>{currentSet.validation_status}</strong></div><div><span>Escala entera</span><strong>{currentSet.geometry_units_per_cm} u/cm</strong></div><div><span>Transformaciones</span><strong>0° / 180° · sin espejo</strong></div></section>
  </main>;
}
