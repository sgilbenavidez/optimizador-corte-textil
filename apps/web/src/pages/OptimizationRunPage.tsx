import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import type { MarkerArtifact, OptimizationRun, OptimizationSolution, OptimizationSolutionSummary, Spread } from "../types";
import { MarkerCanvas, SIZE_COLORS } from "./NestingLabPage";

const PHASES: Record<string, string> = { GENERATING_CANDIDATES: "Buscando primer plan...", NESTING: "Buscando menos cortes...", PLANNING: "Planificando capas y tendidos", PLANNING_HEURISTIC: "Plan válido encontrado.", PLANNING_REFINEMENT: "Mejorando aprovechamiento de tela...", VALIDATING: "Validando el plan completo", FINALIZING: "Finalizando auditoría" };
const PROFILE_LABELS: Record<string, string> = { MAX_ORDER_PER_CUT: "MENOS CORTES", MIN_FABRIC: "Menor tela", MIN_SPREADS: "Menos tendidos", BALANCED: "Balanceada", CONSOLIDATED_PRODUCTION: "Producción consolidada", CONSOLIDATED_MARKERS: "Markers consolidados", MIN_MARKER_CHANGES: "Menos cambios de marker" };
const PROFILE_SUBTITLES: Record<string, string> = { MAX_ORDER_PER_CUT: "Produce la mayor cantidad posible del pedido en cada tendido." };
const formatMeters = (value: unknown) => Number(value ?? 0).toLocaleString("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const composition = (spread: Spread) => Object.entries(spread.composition).map(([size, quantity]) => `${size} × ${quantity}`).join(" · ");

export function planningMessage(status: string) {
  if (status === "OPTIMAL") return "Planificación óptima sobre el catálogo de marcadores evaluado.";
  if (status === "FEASIBLE") return "Mejor planificación validada encontrada dentro del presupuesto de cálculo.";
  return "Planificación validada disponible; no se ha probado optimalidad dentro del límite de cálculo.";
}

function Comparison({ solutions, selected, onSelect }: { solutions: OptimizationSolutionSummary[]; selected: string; onSelect: (id: string) => void }) {
  return <section className="solution-comparison"><h2>Comparar alternativas</h2><div className="comparison-table-wrap"><table className="comparison-table"><thead><tr><th>Perfil</th><th>Tela</th><th>Diseños</th><th>Cambios</th><th aria-label="Tendidos físicos">CORTES</th><th>Cobertura primer corte</th><th>Producción total</th><th>Sobreproducción</th><th>Eficiencia global</th><th>Planning status</th><th>Tiempo</th></tr></thead><tbody>{solutions.map((solution) => {
    const produced = Object.values((solution.metrics.produced_by_size ?? {}) as Record<string, number>).reduce((sum, value) => sum + value, 0);
    const primaryProfile = solution.profiles[0];
    return <tr key={solution.id} className={selected === solution.id ? "selected" : ""}><th><button onClick={() => onSelect(solution.id)}>{solution.profiles.map((profile) => PROFILE_LABELS[profile] ?? profile).join(" · ")}</button>{PROFILE_SUBTITLES[primaryProfile] && <small>{PROFILE_SUBTITLES[primaryProfile]}</small>}{solution.recommended && <small className="recommended-tag">Recomendada</small>}</th><td data-label="Tela">{formatMeters(solution.metrics.total_linear_consumption_m)} m</td><td data-label="Diseños">{String(solution.metrics.marker_design_count ?? "—")}</td><td data-label="Cambios">{String(solution.metrics.marker_change_count ?? "—")}</td><td data-label="Cortes">{String(solution.metrics.physical_spreads ?? solution.metrics.spread_count)}</td><td data-label="Cobertura primer corte">{Number(solution.metrics.primary_spread_coverage_percentage ?? 0).toFixed(1)}%</td><td data-label="Producción total">{produced}</td><td data-label="Extra">{String(solution.metrics.total_overproduction)}</td><td data-label="Eficiencia global">{Number(solution.metrics.global_efficiency_percentage ?? 0).toFixed(2)}%</td><td data-label="Planning status">{solution.planning_status}</td><td data-label="Tiempo">{Math.round(Number(solution.metrics.solver_time_ms ?? 0))} ms</td></tr>;
  })}</tbody></table></div></section>;
}

function FullPlan({ spreads, markers, layers }: { spreads: Spread[]; markers: Array<{ data?: MarkerArtifact; isLoading: boolean; error: Error | null }>; layers: { ids: boolean; bbox: boolean; grain: boolean; clearance: boolean; debug: boolean } }) {
  return <section className="full-plan" aria-labelledby="full-plan-title"><div className="section-heading"><div><p className="eyebrow">Vista global</p><h3 id="full-plan-title">Plan completo</h3></div></div>
    <div className="full-plan-strips">{spreads.map((spread, index) => {
      const artifact = markers[index]?.data as Awaited<ReturnType<typeof api.getMarkerArtifact>> | undefined;
      const expected = artifact?.marker.placements.length ?? Object.values(spread.composition).reduce((sum, value) => sum + value * 5, 0);
      return <article className={`plan-strip ${spread.is_primary ? "primary-spread" : ""}`} key={spread.id}><header><div><strong>CORTE {spread.sequence}{spread.is_primary ? " · PRIMARY_SPREAD" : ""}</strong><span>{composition(spread)}</span></div><dl><div><dt>Capas</dt><dd>{spread.layers}</dd></div><div><dt>Prendas útiles</dt><dd>{spread.useful_garments ?? 0}</dd></div><div><dt>Cobertura</dt><dd>{Number(spread.order_coverage_percentage ?? 0).toFixed(1)}%</dd></div><div><dt>Largo</dt><dd>{(spread.marker_length_cm / 100).toFixed(2)} m</dd></div><div><dt>Eficiencia</dt><dd>{spread.marker_efficiency_percentage.toFixed(2)}%</dd></div><div><dt>Piezas</dt><dd>{artifact ? `${artifact.marker.placements.length} / ${expected}` : `… / ${expected}`}</dd></div></dl></header>
        {markers[index]?.isLoading && <div className="loading-card">Cargando marker certificado…</div>}
        {markers[index]?.error && <div className="load-error" role="alert">No fue posible cargar este marker.</div>}
        {artifact && artifact.validation_status === "VALIDATED" && artifact.marker.status === "VALIDATED_FEASIBLE" ? <MarkerCanvas marker={artifact.marker} layers={layers} controls={false} /> : artifact && <div className="load-error">Marker no validado; franja visual bloqueada.</div>}<section className="cut-residual"><strong>PEDIDO RESTANTE</strong><span>{Object.entries(spread.remaining_demand_after ?? {}).map(([size, count]) => `${size} ${count}`).join(" · ")}</span></section>
      </article>;
    })}</div>
  </section>;
}

function CutMap({ solution }: { solution: OptimizationSolution }) {
  const spreads = useMemo(() => [...solution.spreads].sort((a, b) => a.sequence - b.sequence), [solution.spreads]);
  const PLAN = "__full_plan__";
  const [selectedId, setSelectedId] = useState(PLAN);
  const [layers, setLayers] = useState({ ids: true, bbox: false, grain: true, clearance: false, debug: false });
  const [visualComplete, setVisualComplete] = useState<boolean | null>(null);
  useEffect(() => setSelectedId(PLAN), [solution.id]);
  const spread = spreads.find((item) => item.id === selectedId) ?? spreads[0];
  const marker = useQuery({ queryKey: ["marker", spread?.marker_hash], queryFn: () => api.getMarkerArtifact(spread!.marker_hash), enabled: Boolean(spread) && selectedId !== PLAN });
  const allMarkers = useQueries({ queries: spreads.map((item) => ({ queryKey: ["marker", item.marker_hash], queryFn: () => api.getMarkerArtifact(item.marker_hash) })) });
  if (!spread) return <section className="cut-map"><h2>Mapa de corte</h2><div className="empty-state">Esta solución no contiene tendidos.</div></section>;
  const expected = Object.fromEntries(Object.entries(spread.composition).map(([size, count]) => [size, count * spread.layers * spread.repeats]));
  const consistent = JSON.stringify(expected) === JSON.stringify(spread.production_by_size);
  const markerDesigns = new Set(spreads.map((item) => item.marker_hash)).size;
  const physicalSpreads = spreads.reduce((sum, item) => sum + item.repeats, 0);
  return <section className="cut-map" aria-labelledby="cut-map-title"><div className="section-heading"><div><p className="eyebrow">Ejecución física</p><h2 id="cut-map-title">Mapa de corte</h2><p>Distribución calculada de los moldes sobre el ancho útil de la tela.</p></div><div className="spread-counts"><span>DISEÑOS DE MARCADOR <strong>{markerDesigns}</strong></span><span>TENDIDOS FÍSICOS <strong>{physicalSpreads}</strong></span><small>Σ repeticiones = {physicalSpreads}</small></div></div>
    <div className="spread-selector" aria-label="Seleccionar corte"><button className="plan-complete-button" aria-pressed={selectedId === PLAN} onClick={() => setSelectedId(PLAN)}><strong>PLAN COMPLETO</strong><span>{markerDesigns} diseños · {physicalSpreads} tendidos físicos</span></button>{spreads.map((item) => <button aria-pressed={selectedId === item.id} key={item.id} onClick={() => setSelectedId(item.id)}><strong>CORTE {item.sequence}{item.is_primary ? " · PRINCIPAL" : ""}</strong><span>{composition(item)}</span><small>{item.layers} capas · {item.useful_garments ?? 0} prendas útiles · {Number(item.order_coverage_percentage ?? 0).toFixed(1)}%</small></button>)}</div>
    <div className="operational-table-wrap"><table className="operational-table"><thead><tr><th>#</th><th>Composición</th><th>Capas</th><th>Rep.</th><th>Largo</th><th>Tela consumida</th><th>Producción por tendido</th></tr></thead><tbody>{spreads.map((item) => <tr key={item.id} className={item.id === spread.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><th>{item.sequence}</th><td>{composition(item)}</td><td aria-label={`${item.layers} capas`}>{item.layers}</td><td>{item.repeats}</td><td>{(item.marker_length_cm / 100).toFixed(2)} m</td><td>{item.fabric_consumption_m.toFixed(2)} m</td><td>{Object.entries(item.production_by_size).map(([size, count]) => `${size} ${count}`).join(" · ")}</td></tr>)}</tbody></table></div>
    {selectedId === PLAN ? <FullPlan spreads={spreads} markers={allMarkers} layers={layers} /> : <article className="selected-marker"><header><div><p className="eyebrow">CORTE {spread.sequence}{spread.is_primary ? " · PRIMARY_SPREAD" : ""}</p><h3>{composition(spread)}</h3><p>{spread.layers} capas · {spread.useful_garments ?? 0} prendas útiles · {Number(spread.order_coverage_percentage ?? 0).toFixed(1)}% del pedido restante · {(spread.marker_length_cm / 100).toFixed(2)} m · {spread.marker_efficiency_percentage.toFixed(2)}%</p></div><div className="marker-actions"><Link to={`/markers/${spread.marker_hash}`}>Ver mapa completo</Link><a href={api.markerSvgUrl?.(spread.marker_hash) ?? `/api/v1/markers/${spread.marker_hash}/svg`}>Descargar mapa SVG</a></div></header>
      <div className="marker-toolbar"><strong>Marker {spread.marker_hash.slice(0, 12)}</strong><fieldset><legend>Capas visuales</legend>{Object.entries({ ids: "Etiquetas", grain: "Hilo", bbox: "BBox", clearance: "Separación" }).map(([key, label]) => <label key={key}><input type="checkbox" checked={layers[key as keyof typeof layers]} onChange={() => setLayers((value) => ({ ...value, [key]: !value[key as keyof typeof layers] }))} />{label}</label>)}</fieldset></div>
      {layers.clearance && <p className="approximation-note">Visualización aproximada de separación.</p>}
      {marker.isLoading && <div className="loading-card">Cargando placements certificados…</div>}
      {marker.error && <div className="load-error" role="alert">No fue posible cargar el marker. <button onClick={() => marker.refetch()}>Reintentar</button></div>}
      {marker.data && marker.data.validation_status === "VALIDATED" && marker.data.marker.status === "VALIDATED_FEASIBLE" ? <MarkerCanvas marker={marker.data.marker} layers={layers} onIntegrityChange={(complete) => setVisualComplete(complete)} /> : marker.data && <div className="load-error">El marker no posee validación geométrica vigente y no se mostrará.</div>}
      <div className="legend" aria-label="Leyenda por talla">{Object.keys(spread.composition).map((size) => <span key={size}><i style={{ background: SIZE_COLORS[size] }} />{size}</span>)}</div>
      <div className="marker-data"><span>Estado geométrico <strong>{spread.marker_search_status}</strong></span><span>Validación visual <strong>{visualComplete === true ? spread.validation_status : visualComplete === false ? "MARKER VISUAL INCOMPLETO" : "COMPROBANDO RENDER"}</strong></span><span>Waste <strong>{(100 - spread.marker_efficiency_percentage).toFixed(2)}%</strong></span></div>
      <section className="spread-production"><h3>Este corte produce</h3>{Object.entries(spread.composition).map(([size, perLayer]) => <p key={size}><strong>{size}</strong> {perLayer} por capa × {spread.layers} capas = <b>{spread.production_by_size[size]}</b></p>)}{!consistent && <p className="form-error" role="alert">La producción derivada no coincide con el plan certificado.</p>}<h3>PEDIDO RESTANTE</h3>{Object.entries(spread.remaining_demand_after ?? {}).map(([size, count]) => <p key={size}><strong>{size}</strong> {count}</p>)}</section>
    </article>}
  </section>;
}

function SolutionDetail({ solution }: { solution: OptimizationSolution }) {
  return <div className="solution-detail"><section className="result-kpis"><div><span>Diseños de marker</span><strong>{String(solution.metrics.marker_design_count ?? new Set(solution.spreads.map((item) => item.marker_hash)).size)}</strong></div><div><span>Tendidos físicos</span><strong>{String(solution.metrics.spread_count)}</strong></div><div><span>Producción</span><strong>{Object.values(solution.metrics.produced_by_size as Record<string, number>).reduce((a, b) => a + b, 0)}</strong></div><div><span>Sobreproducción</span><strong>{String(solution.metrics.total_overproduction)}</strong></div><div><span>Eficiencia global</span><strong>{Number(solution.metrics.global_efficiency_percentage).toFixed(2)}%</strong></div></section><section className="why-card"><p className="eyebrow">¿Por qué se recomienda esta opción?</p><p>{solution.explanation}</p></section><section className="size-results"><h2>Producción por talla</h2><table><thead><tr><th>Talla</th><th>Pedido</th><th>Producción</th><th>Extra</th></tr></thead><tbody>{solution.size_results.map((row) => <tr key={row.size_code} className={row.overproduction ? "has-extra" : ""}><th>{row.size_code}</th><td>{row.requested}</td><td>{row.produced}</td><td>{row.overproduction ? `+${row.overproduction}` : "0"}</td></tr>)}</tbody></table></section><CutMap solution={solution} /></div>;
}

function Results({ run, summaries }: { run: OptimizationRun; summaries: OptimizationSolutionSummary[] }) {
  const recommended = summaries.find((item) => item.recommended) ?? summaries.find((item) => item.profiles.includes("MIN_FABRIC")) ?? summaries[0];
  const [selectedId, setSelectedId] = useState(recommended?.id ?? "");
  useEffect(() => { if (!selectedId && recommended) setSelectedId(recommended.id); }, [selectedId, recommended]);
  const selected = useQuery({ queryKey: ["solution", selectedId], queryFn: () => api.getOptimizationSolution(selectedId), enabled: Boolean(selectedId) });
  const audit = useQuery({ queryKey: ["run-audit", run.id], queryFn: () => api.getOptimizationAudit(run.id) });
  const doExport = async () => { const data = await api.exportOptimizationRun(run.id); const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })); const link = document.createElement("a"); link.href = url; link.download = `costura-optima_${run.id}.json`; link.click(); URL.revokeObjectURL(url); };
  const solution = selected.data;
  const requested = solution ? Object.values((solution.metrics.requested_by_size ?? {}) as Record<string, number>).reduce((a, b) => a + b, 0) : 0;
  return <><header className="results-hero"><div><p className="eyebrow">Solución recomendada</p><h1>Plan de corte validado</h1><p className="intro">{planningMessage(solution?.planning_status ?? recommended?.planning_status ?? "UNKNOWN")}</p><p className="order-total"><strong>PEDIDO</strong> {requested} prendas</p></div>{solution && <div className="recommendation-metric"><span>Tela lineal</span><strong>{formatMeters(solution.metrics.total_linear_consumption_m)} m</strong></div>}</header>
    {run.status === "TIMED_OUT" && <div className="status-banner timeout"><strong>Se alcanzó el límite de mejora.</strong><span>Te mostramos la mejor solución validada encontrada.</span></div>}
    {run.status === "SUCCEEDED_EARLY" && <div className="status-banner"><strong>Se detuvo la mejora.</strong><span>Se conservó el mejor plan validado disponible.</span></div>}
    <div className="warning-banner"><span aria-hidden="true">!</span>Patrón experimental de ingeniería — no validado para producción.</div>
    <div className="result-actions"><Link to={`/orders/${run.production_order_id}`}>Volver a la orden</Link><button onClick={doExport}>Exportar JSON</button></div>
    <Comparison solutions={summaries} selected={selectedId} onSelect={setSelectedId} />{selected.isLoading && <div className="loading-card">Cargando solución…</div>}{selected.error && <div className="load-error">No fue posible cargar la solución.</div>}{solution && <SolutionDetail solution={solution} />}
    <details className="audit-panel"><summary>Auditoría técnica</summary><h3>Objective stages</h3><p>Hashes, versiones, semilla, rondas, certificados y tiempos de esta corrida.</p>{audit.isLoading ? <p>Cargando auditoría…</p> : <pre>{JSON.stringify(audit.data ?? {}, null, 2)}</pre>}</details></>;
}

function TerminalState({ run }: { run: OptimizationRun }) {
  const navigate = useNavigate();
  const retry = useMutation({ mutationFn: () => api.retryOptimizationRun(run.id), onSuccess: (next) => navigate(`/optimization-runs/${next.id}`) });
  const copy: Record<string, { title: string; detail: string }> = {
    FAILED: { title: "No fue posible completar la optimización.", detail: "Puedes reintentar sin modificar esta corrida." },
    TIMED_OUT: { title: "El tiempo máximo de cálculo terminó.", detail: "No se alcanzó a publicar una solución validada." },
    CANCELLED: { title: "La optimización fue cancelada.", detail: "La corrida conserva su auditoría y no publicará resultados tardíos." },
    INFEASIBLE: { title: "No se encontró un plan que cumpla las restricciones actuales.", detail: "El solver no demostró una causa más específica." },
  };
  const state = copy[run.status] ?? copy.FAILED;
  return <main className="workspace"><section className="prepared-card terminal-card"><p className="eyebrow">{run.status}</p><h1>{state.title}</h1><p className="intro">{state.detail}</p><dl className="terminal-meta"><div><dt>error_code</dt><dd>{run.error_code ?? "—"}</dd></div><div><dt>run_id</dt><dd><code>{run.id}</code></dd></div></dl><div className="prepared-actions"><Link className="secondary-button" to={`/orders/${run.production_order_id}`}>Volver a la orden</Link>{["FAILED", "TIMED_OUT"].includes(run.status) && <button className="primary-button" onClick={() => retry.mutate()} disabled={retry.isPending}>{retry.isPending ? "Creando reintento…" : "Reintentar"}</button>}</div>{retry.error && <p className="form-error">{retry.error.message}</p>}</section></main>;
}

export function OptimizationRunPage() {
  const { runId = "" } = useParams();
  const run = useQuery({ queryKey: ["optimization-run", runId], queryFn: () => api.getOptimizationRun(runId), enabled: Boolean(runId), refetchInterval: (query) => ["QUEUED", "RUNNING"].includes(query.state.data?.status ?? "") ? 1500 : false });
  const hasResults = ["SUCCEEDED", "SUCCEEDED_EARLY"].includes(run.data?.status ?? "") || (run.data?.status === "TIMED_OUT" && run.data.best_solution_available);
  const solutions = useQuery({ queryKey: ["optimization-solutions", runId], queryFn: () => api.listOptimizationSolutions(runId), enabled: hasResults });
  const cancel = useMutation({ mutationFn: () => api.cancelOptimizationRun(runId), onSuccess: () => run.refetch() });
  const useCurrent = useMutation({ mutationFn: () => api.useCurrentOptimizationPlan(runId), onSuccess: () => run.refetch() });
  if (run.isLoading) return <main className="workspace"><div className="loading-card">Consultando corrida…</div></main>;
  if (run.error || !run.data) return <main className="workspace"><div className="load-error">No fue posible consultar la optimización. <button onClick={() => run.refetch()}>Reintentar</button></div></main>;
  if (hasResults) return <main className="workspace results-workspace"><Results run={run.data} summaries={solutions.data ?? []} /></main>;
  if (["FAILED", "CANCELLED", "TIMED_OUT", "INFEASIBLE"].includes(run.data.status)) return <TerminalState run={run.data} />;
  const progress = run.data.progress;
  const knownTotal = progress.candidates_generated > 0 ? progress.candidates_generated : null;
  return <main className="workspace processing-workspace"><section className="processing-card" aria-live="polite"><div className="processing-mark"><span /></div><p className="eyebrow">Optimización operacional</p><h1>Optimizando corte…</h1><p className="intro">{PHASES[run.data.phase] ?? run.data.phase}</p>{run.data.best_solution_available && <div className="status-banner"><strong>Ya encontramos un plan válido. Estamos buscando una alternativa mejor.</strong><span>Plan válido: {run.data.incumbent?.first_solution_spreads ?? "—"} cortes · {Number(run.data.incumbent?.first_solution_fabric_m ?? 0).toFixed(2)} m</span></div>}{knownTotal ? <progress max={knownTotal} value={progress.candidates_evaluated}>{progress.candidates_evaluated} de {knownTotal}</progress> : <div className="indeterminate-progress">Preparando candidatos…</div>}<dl className="processing-stats"><div><dt>Candidatos evaluados</dt><dd>{progress.candidates_evaluated}{knownTotal ? ` / ${knownTotal}` : ""}</dd></div><div><dt>Mejor solución válida</dt><dd>{run.data.best_solution_available || progress.best_feasible_found ? "Encontrada" : "Buscando"}</dd></div><div><dt>Markers factibles</dt><dd>{progress.candidates_feasible}</dd></div><div><dt>Ronda</dt><dd>{run.data.round_current || "—"}{run.data.round_total_if_known ? ` / ${run.data.round_total_if_known}` : ""}</dd></div><div><dt>Tiempo</dt><dd>{Math.round(run.data.elapsed_ms / 1000)} s</dd></div><div><dt>Actualizado</dt><dd>{new Date(run.data.updated_at).toLocaleTimeString("es-CO")}</dd></div></dl>{run.data.best_solution_available && <button className="primary-button" onClick={() => useCurrent.mutate()} disabled={useCurrent.isPending || run.data.use_current_plan_requested}>{run.data.use_current_plan_requested ? "Finalización solicitada" : "USAR MEJOR PLAN ACTUAL"}</button>}<button className="secondary-button cancel-button" onClick={() => window.confirm("¿Cancelar esta optimización?") && cancel.mutate()} disabled={cancel.isPending || run.data.cancel_requested}>{run.data.cancel_requested ? "Cancelación solicitada" : "Cancelar optimización"}</button><code className="run-id">Run {run.data.id}</code></section></main>;
}
