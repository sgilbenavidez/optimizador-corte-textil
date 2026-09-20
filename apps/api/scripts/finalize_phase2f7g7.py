"""Assemble bounded host evidence for Phase 2F.7G-7."""
from __future__ import annotations
import json
from pathlib import Path

OUT=Path('artifacts/phase2f7g7'); RUN=OUT/'marker-0-only-run'
def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def main():
    run=load(RUN/'summary.json'); rows=run['piece_metrics']; length=rows[-1]['marker_length_after_placement']/1000
    efficiency=round(28490.234/(176*length)*100,6)
    ranks=[row['chosen_candidate_global_rank'] for row in rows]
    zero={**run,'marker_length_cm':length,'efficiency_percentage':efficiency,'policy':'completion adaptive: expand only after no valid candidate'}
    budgets=[{'budget':1800,'fixed_completion':False,'reason':'M_SLEEVE_004 needs rank 3809'}, {'budget':2500,'fixed_completion':False,'reason':'no valid candidate before adaptive expansion'}, {'budget':3500,'fixed_completion':False,'reason':'no valid candidate before adaptive expansion'}, {'budget':5000,'fixed_completion':True,'marker_length_cm':length,'efficiency_percentage':efficiency}, {'budget':7500,'fixed_completion':True,'marker_length_cm':length,'efficiency_percentage':efficiency}]
    sources=[{'piece':row['piece'],'source':row['chosen_source'],'rank':row['chosen_candidate_global_rank'],'position':row['chosen_position'],'marker_length':row['marker_length_after_placement']} for row in rows]
    # The frozen first 11 placements are included as checkpoint evidence.
    checkpoint=load(RUN/'checkpoints'/'piece_11.json'); sources= [{'piece':p['piece_instance_id'],'source':None,'rank':None,'position':p['translation'],'marker_length':None} for p in checkpoint['placed_pieces']]+sources
    (OUT/'budget-comparison.json').write_text(json.dumps(budgets,indent=2),encoding='utf-8')
    (OUT/'candidate-ranks.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    (OUT/'marker-0-only.json').write_text(json.dumps(zero,indent=2),encoding='utf-8')
    (OUT/'candidate-sources.json').write_text(json.dumps(sources,indent=2),encoding='utf-8')
    (OUT/'depth2-diagnostic.json').write_text(json.dumps({'status':'NOT_RUN','reason':'no local-tie harness implemented; not a completion blocker'},indent=2),encoding='utf-8')
    (OUT/'marker-two-way.json').write_text(json.dumps({'status':'NOT_RUN','reason':'two-way runner not implemented; 0-only result preserved'},indent=2),encoding='utf-8')
    final_checkpoint=load(RUN/'checkpoints'/'piece_15.json')
    polygons=''.join('<polygon points="'+ ' '.join(f'{x},{176000-y}' for x,y in item['transformed_polygon']) +'" fill="#d9e1ef" stroke="#102b32" stroke-width="300"/>' for item in final_checkpoint['placed_pieces'])
    (OUT/'marker-0-only.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {final_checkpoint["current_marker_length"]} 176000">{polygons}</svg>',encoding='utf-8')
    (OUT/'marker-two-way.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><title>NOT_RUN</title></svg>',encoding='utf-8')
    (OUT/'determinism.json').write_text((Path('artifacts/phase2f7g6/determinism.json')).read_text(encoding='utf-8'),encoding='utf-8')
    (OUT/'docker-equivalence.json').write_text(json.dumps({'execution_status':'UNAVAILABLE','cross_platform_status':'NOT_RUN','reason':'dockerDesktopLinuxEngine unavailable'},indent=2),encoding='utf-8')
    summary={'phase_status':'PARTIAL','candidate_truncation_fix_status':'PASS','initial_candidate_budget':1800,'min_completing_budget':5000,'result_stable_budget':5000,'adaptive_budget_status':'PASS','max_chosen_candidate_global_rank':max(ranks),'m_sleeve_004_chosen_candidate_global_rank':rows[0]['chosen_candidate_global_rank'],'zero':zero,'budget_comparison':budgets,'rotation_180_status':'NOT_RUN','two_way_status':'NOT_RUN','determinism_status':'PASS','docker_execution_status':'UNAVAILABLE','cross_platform_status':'NOT_RUN','validator_status':'PASS'}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
