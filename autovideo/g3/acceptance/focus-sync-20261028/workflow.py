"""Fresh normal API/worker generation from an unchanged existing script chapter."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    from fastapi.testclient import TestClient
    from g3.app import create_app
    from g3.config import Settings

    index_path = ROOT.parent/'AI_research/Base_CodingCLi/state/validation/ai-matrix-productization-20261027/validation-index.json'
    media = json.loads(index_path.read_text())['media']['models-1']
    source = Path(media['engine_data'])/'jobs'/media['engine_job_id']/'input/input.json'
    original = json.loads(source.read_text())
    # Keep an existing author's complete paragraph sequence. Only production
    # mode changes; no hand-authored storyboard, timing, or revised body text.
    payload = {key: copy.deepcopy(original[key]) for key in ['input_mode', 'duration_mode', 'voice_id',
        'video_speed', 'visual_style', 'creative_intent', 'packaging', 'production_context'] if key in original}
    payload.update(title='镜头同步验证：同一模型四档的等待与成本',
        chapters=[copy.deepcopy(original['chapters'][1])],
        production_mode='fast', visual_style='editorial_light', video_form='information_cards',
        visual_storytelling=True, use_draw_things=False)
    data = ROOT/'g3/data/acceptance-focus-sync-20261028'/uuid4().hex
    data.mkdir(parents=True)
    shutil.copyfile(ROOT/'g3/data/llm-settings.json', data/'llm-settings.json')
    (data/'llm-settings.json').chmod(0o600)
    save(out/'input.json', payload)
    with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=3600), start_worker=True)) as engine:
        response = engine.post('/api/jobs', json=payload)
        assert response.status_code == 202, response.text
        job_id = response.json()['job_id']
        save(out/'location.json', {'job_id': job_id, 'engine_data': str(data), 'source_input': str(source)})
        print(json.dumps({'job_id': job_id, 'engine_data': str(data)}), flush=True)
        deadline, previous = time.monotonic()+3600, None
        while time.monotonic() < deadline:
            job = engine.get(f'/api/jobs/{job_id}').json()
            current = (job['status'], job.get('active_stage'))
            if current != previous:
                print(json.dumps({'status': current[0], 'stage': current[1]}), flush=True)
                save(out/'progress.json', job)
                previous = current
            if job['status'] in {'COMPLETED', 'FAILED', 'CANCELLED', 'NEEDS_INPUT'}:
                break
            time.sleep(2)
        save(out/'job.json', job)
        assert job['status'] == 'COMPLETED', {k: job.get(k) for k in ['status', 'error_code', 'error_message']}
        for name in ['content.mp4', 'video.mp4', 'video-ir.json', 'qa.json', 'package-qa.json']:
            response = engine.get(f'/api/jobs/{job_id}/artifacts/{name}')
            assert response.status_code == 200, (name, response.text[:300])
            (out/name).write_bytes(response.content)
    attempt = max((data/'jobs'/job_id/'attempts').iterdir(), key=lambda p: int(p.name))/'g2-run'
    for name in ['compiled-timeline.json', 'caption-alignment.json', 'visual-cue-alignment.json',
                 'explanation-plan.json', 'storyboard-plan.json', 'hybrid-card-plan.json', 'production-execution.json',
                 'shot-director.json', 'scene-assembly.json', 'model-cost.json']:
        if (attempt/'artifacts'/name).is_file():
            shutil.copyfile(attempt/'artifacts'/name, out/name)
    save(out/'receipt.json', {'job_id': job_id, 'attempt': str(attempt), 'source_input': str(source),
        'unchanged_author_paragraphs': payload['chapters'][0]['paragraphs'] == original['chapters'][1]['paragraphs'],
        'manual_body_edits': 0, 'manual_storyboard_or_timing_edits': 0, 'normal_api_and_worker': True,
        'video_speed': payload['video_speed'], 'production_mode': payload['production_mode']})
    print(json.dumps({'completed': job_id, 'output': str(out)}), flush=True)


if __name__ == '__main__':
    main()
