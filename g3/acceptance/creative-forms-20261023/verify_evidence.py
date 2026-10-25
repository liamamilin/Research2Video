"""Recheck saved real production artifacts without invoking models or renderers."""
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))
from g3.store import JobStore
from g1.video_ir import validate_video_ir

HERE = Path(__file__).resolve().parent
store = JobStore(PROJECT / 'g3/data')
saved = json.loads((HERE / 'real-jobs.json').read_text())
entries = saved['jobs'] + saved['revisions'] + saved['additional_checks']
results = []
for entry in entries:
    jid = entry['response']['job_id']
    job = store.get_job(jid)
    assert job['status'] == 'COMPLETED'
    qa_path, _ = store.artifact_path(jid, 'qa.json')
    qa = json.loads(qa_path.read_text())
    assert qa['status'] == qa['video']['full_decode'] == qa['audio']['full_decode'] == 'PASS'
    assert qa['audio']['decoded_tts_correlation'] >= .98
    package_path = qa_path.parent / 'package/package-qa.json'
    package = json.loads(package_path.read_text())
    assert package['status'] == package['checks']['full_decode'] == 'PASS'
    assert package['checks']['content_audio']['correlation'] >= .98
    ir_path, _ = store.artifact_path(jid, 'video-ir.json')
    ir = json.loads(ir_path.read_text())
    validate_video_ir(ir, qa_path.parent.parent / 'fixtures')
    assert [sid for s in ir['scenes'] for sid in s['narration_segment_ids']] == [s['id'] for s in ir['narration']['segments']]
    texts = {s['id']: s['text'] for s in ir['narration']['segments']}
    assert all(s['props']['text'] == texts[s['narration_segment_ids'][0]] for s in ir['scenes'])
    video, _ = store.artifact_path(jid, 'video.mp4')
    manifest_path, _ = store.artifact_path(jid, 'manifest.json')
    manifest = json.loads(manifest_path.read_text())
    for record in manifest['files']:
        path = PROJECT / record['path']
        assert path.stat().st_size == record['bytes'], path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256'], path
    cost = json.loads((qa_path.parent / 'model-cost.json').read_text())
    recipe = json.loads((store.source_path(jid).parent / 'input.json').read_text())['production_recipe_resolved']
    results.append({'job_id': jid, 'form': qa['video_form'], 'operation': job['operation_type'], 'status': 'PASS',
        'recipe': recipe['recipe']['id'], 'recipe_version': recipe['recipe']['version'],
        'content_seconds': qa['video']['duration_seconds'], 'content_frames': qa['video']['frames'],
        'output_seconds': package['checks']['duration']['seconds'], 'output_frames': package['checks']['duration']['frames'],
        'tts_correlation': qa['audio']['decoded_tts_correlation'],
        'packaged_tts_correlation': package['checks']['content_audio']['correlation'],
        'scene_count': len(ir['scenes']), 'image_assets': len([a for a in ir['assets'] if a['type'] == 'image']),
        'actual_model_calls': len(cost['calls']), 'cost_upper_bound_usd': cost['peak_uncached_cost_upper_bound_usd'],
        'manifest_files_verified': len(manifest['files']), 'video': str(video.relative_to(PROJECT)),
        'video_sha256': hashlib.sha256(video.read_bytes()).hexdigest()})
media = next(r for r in results if r['operation'] == 'media_revision')
parent_id = next(e['parent'] for e in saved['additional_checks'] if e['response']['job_id'] == media['job_id'])
original_ir = json.loads(store.artifact_path(parent_id, 'video-ir.json')[0].read_text())
revision_ir = json.loads(store.artifact_path(media['job_id'], 'video-ir.json')[0].read_text())
assert [a for a in original_ir['assets'] if a['type'] == 'image'] == [a for a in revision_ir['assets'] if a['type'] == 'image']
assert [s['props'] for s in original_ir['scenes']] == [s['props'] for s in revision_ir['scenes']]
report = {'status': 'PASS', 'jobs': results, 'parent_artifacts_unchanged': True, 'media_revision_images_and_screen_props_unchanged': True,
          'actual_model_calls': sum(r['actual_model_calls'] for r in results),
          'cost_upper_bound_usd': round(sum(r['cost_upper_bound_usd'] for r in results), 8)}
(HERE / 'media-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
