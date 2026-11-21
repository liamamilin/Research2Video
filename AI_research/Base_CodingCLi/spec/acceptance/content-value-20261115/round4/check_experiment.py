"""Check experimental controls and summarize measured calls; no production writes."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BASE = Path('/tmp/content-question-material-round4-20261115')


def read(path):
    return json.loads(path.read_text())


def main():
    result = {'channels':{}, 'production_files_changed':[]}
    manifest = read(BASE/'source-manifest.json')
    for relative,digest in manifest.items():
        path=ROOT/relative
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            result['production_files_changed'].append(relative)
    for channel in ('t05','ai_tutorial','company_daily'):
        out=BASE/'results'/channel
        selection,decision=read(out/'selection.json'),read(out/'decision.json')
        old=selection['candidates'][0]
        chosen=selection['candidates'][decision['chosen_index']-1]
        inputs={p.name[0]:read(p) for p in out.glob('?-author-input.json')}
        assert inputs['A']['topic']['title']==old['title']
        same='A' if decision['chosen_index']==1 else 'C'
        assert inputs[same]['topic']['title']==chosen['title']
        for field in ('column_style','content_context','model','profile','column_key','target_minutes'):
            assert all(v[field]==inputs['A'][field] for v in inputs.values()),(channel,field)
        assert all(v['topic']['preserve_audience_scope'] for v in inputs.values())
        if 'B' in inputs:
            assert inputs['B']['topic']==inputs[same]['topic']
        else:
            assert read(out/'B-alias.json')['same_as']==same
        supplement=read(out/'supplement.json')
        assert len(decision['gaps'])<=2 and len(supplement['added'])<=4
        assert all(len(a['reads'])<=4 for a in supplement['attempts'])
        records=[read(p) for p in sorted((out/'calls').glob('*response.json'))]
        requests=[read(p) for p in sorted((out/'calls').glob('*request.json'))]
        last={}
        for record in records:
            last[record.get('client_id') or 'legacy-selection-client']=record['usage']
        totals={k:sum(v.get(k,0) for v in last.values()) for k in
                ('requests','prompt_tokens','completion_tokens','total_tokens','cost_usd','cost_reported_requests')}
        result['channels'][channel]={'controls':'passed','written_arms':sorted(inputs),
            'selection_changed':decision['chosen_index']!=1,'material_fit':{k:v['material_fit'] for k,v in inputs.items()},
            'retrieved_sources':len(supplement['added']),
            'retrieval_modes':{mode:sum(s.get('retrieval_mode')==mode for s in supplement['added'])
                               for mode in ('page_sections','search_returned_body_excerpt')},
            'used_sources':read(out/'enrichment.json')['used_added_ids'],
            'llm_usage':totals,'llm_response_seconds':sum(r['seconds'] for r in records),
            'models':sorted({r['model'] for r in records}),'model_switches':[s for r in records for s in r.get('model_switches',[])],
            'call_errors':len(list((out/'calls').glob('*error.json'))),
            'schema_repair_requests':sum(any('上面的回复不符合要求' in str(m.get('content',''))
                                             for m in r['messages']) for r in requests),
            'writing_seconds':{p.name[0]:read(p)['seconds'] for p in out.glob('?-receipt.json')},
            'limit':'Service-reported model cost only; excludes search/read API charges. One sample per channel.'}
    (BASE/'invariants.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
