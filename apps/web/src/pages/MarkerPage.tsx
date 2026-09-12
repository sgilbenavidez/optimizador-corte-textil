import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { MarkerCanvas, SIZE_COLORS } from "./NestingLabPage";

export function MarkerPage() {
  const { markerHash = "" } = useParams();
  const navigate = useNavigate();
  const [visualComplete, setVisualComplete] = useState<boolean | null>(null);
  const marker = useQuery({ queryKey: ["marker", markerHash], queryFn: () => api.getMarkerArtifact(markerHash), enabled: Boolean(markerHash) });
  if (marker.isLoading) return <main className="workspace"><div className="loading-card">Cargando mapa certificado…</div></main>;
  if (marker.error || !marker.data || marker.data.validation_status !== "VALIDATED" || marker.data.marker.status !== "VALIDATED_FEASIBLE") {
    return <main className="workspace"><div className="load-error" role="alert">Este marker no puede mostrarse como mapa válido.</div></main>;
  }
  const data = marker.data;
  return <main className="workspace marker-full-page"><header className="page-header"><div><p className="eyebrow">Mapa de corte certificado</p><h1>Marker {markerHash.slice(0, 12)}</h1><p className="intro">Distribución calculada de los moldes sobre el ancho útil de la tela.</p></div><button className="secondary-button" onClick={() => navigate(-1)}>Volver</button></header>
    <div className="warning-banner"><span aria-hidden="true">!</span>Patrón experimental de ingeniería — no validado para producción.</div>
    <section className="marker-summary" aria-label="Datos del marker"><div><span>Longitud</span><strong>{data.marker.marker_length_cm?.toFixed(2)} cm</strong></div><div><span>Ancho útil</span><strong>{data.marker.usable_width_cm.toFixed(2)} cm</strong></div><div><span>Piezas</span><strong>{data.marker.piece_count}</strong></div><div><span>Eficiencia</span><strong>{data.marker.efficiency_percentage?.toFixed(2)}%</strong></div><div><span>Estado visual</span><strong>{visualComplete === true ? data.validation_status : visualComplete === false ? "MARKER VISUAL INCOMPLETO" : "COMPROBANDO RENDER"}</strong></div></section>
    <MarkerCanvas marker={data.marker} layers={{ ids: true, bbox: false, grain: true, clearance: false, debug: false }} onIntegrityChange={(complete) => setVisualComplete(complete)} />
    <div className="legend" aria-label="Leyenda de tallas">{Object.keys(data.composition).map((size) => <span key={size}><i style={{ background: SIZE_COLORS[size] }} />{size}</span>)}</div>
    <a className="primary-button button-link" href={api.markerSvgUrl?.(markerHash) ?? `/api/v1/markers/${markerHash}/svg`}>Descargar mapa SVG</a>
  </main>;
}
