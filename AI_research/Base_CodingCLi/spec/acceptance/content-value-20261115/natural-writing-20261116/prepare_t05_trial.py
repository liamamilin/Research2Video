"""Freeze the historical T05 writer inputs; compare only its effective column notes."""
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
BASE = ROOT / 'state/acceptance-content-value-20261115/natural-writing-20261116'
WORK = Path('/tmp/natural-writing-20261116-r7/candidate')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    if WORK.exists():
        raise FileExistsError('Preserve previous trial workspace')
    shutil.copytree('/tmp/natural-writing-20261116-r1/control', WORK,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'state', 'logs'))
    (WORK/'state').mkdir(); (WORK/'logs').mkdir()
    rel = Path('config/content_columns/chatgpt_t05_comparison.yaml')
    shutil.copyfile(HERE/'candidates/structural-requirements'/rel, WORK/rel)
    sys.path.insert(0, '/tmp/natural-writing-20261116-r1/control')
    from core.video import excerpt, story_pool
    script = json.loads((BASE/'baseline/scripts/194a1831aabc4f9e9558f75544c2293b.json').read_text())
    topic = script['topic']
    raw = (BASE/'baseline/material_files'/script['report_path']).read_text()
    report = excerpt.item_excerpt(raw, topic['from_item_title'], summary=topic['fields']['一句话'], include_nested=True)
    with sqlite3.connect(f'file:{ROOT / "state/video.db"}?mode=ro', uri=True) as db:
        row = db.execute('select payload from video_story_pools where id=?', (topic['story']['pool_id'],)).fetchone()
    pool = json.loads(row[0]); material = story_pool.material_for(pool, topic['story'])
    digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
    assert digest(report) == script['meta']['research_dossier']['report_sha256']
    assert digest(material) == script['meta']['research_dossier']['material_sha256']
    save(BASE/'round1/inputs/t05-voice-pool.json', pool)
    save(BASE/'round1/inputs/t05-voice.json', {'name':'t05-voice', 'script':script, 'report':report,
         'original_material':material, 'allocation_direction':'', 'pool_id':pool['id'],
         'report_sha256':digest(report), 'material_sha256':digest(material),
         'context_source':'current_production',
         'evidence_level':'saved automatic input replay; both material hashes exactly match historical writer receipt; fresh configuration context; not new selection'})
    manifest=[]
    for name in ('core/video/persona.py', 'core/video/writer.py', 'core/video/manuscript_workflow.py',
                 'core/video/angles.py', str(rel)):
        raw=(WORK/name).read_bytes(); dst=BASE/'round7/source/candidate'/name
        dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(raw)
        manifest.append({'file':name,'sha256':hashlib.sha256(raw).hexdigest()})
    save(HERE/'round7-source.json',manifest)
    print('T05 frozen report/material exactly match original runtime hashes; only column notes changed.')


if __name__ == '__main__':
    main()
