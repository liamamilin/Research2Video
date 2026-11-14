"""Reflow spoken relationship groups with the same trusted offline graph layout."""
from __future__ import annotations
import json
from g2 import process_diagram


def attach(board: dict, pipeline) -> None:
    groups, requests = [], []
    for scene in board['scenes']:
        if scene['component'] != 'flow_diagram' or not scene['props'].get('shot_plan'):
            continue
        props = scene['props']
        beats = {b['id']: b for b in scene['beats']}
        def onset(cue):
            beat = beats[cue['beat_id']]
            if 'frame' in beat:
                return (0, beat['frame'])
            trigger = beat['trigger']
            return (scene['narration_segment_ids'].index(trigger['segment_id']), trigger.get('offset_ms', 0))
        cues = sorted(props['shot_plan']['cues'], key=onset)
        # Dense narration merges consecutive cues. Freeze layouts for those
        # possible groups as well, independent of the later playback speed.
        seen = set()
        combinations = [set().union(*(c['targets'] for c in cues[start:end]))
                        for start in range(len(cues)) for end in range(start+1, len(cues)+1)]
        for combined in combinations:
            targets = tuple(sorted(combined))
            if targets in seen:
                continue
            seen.add(targets)
            ids = {target.split(':', 1)[1] for target in targets}
            subgraph = {**props, 'id': f'view_{len(requests)}',
                        'nodes': [n for n in props['nodes'] if n['id'] in ids],
                        'edges': [e for e in props['edges'] if e['from'] in ids and e['to'] in ids],
                        'visuals': [v for v in props.get('visuals', []) if v['target'] in targets]}
            requests.append(subgraph)
            groups.append((scene, list(targets)))
        props['layout_views'] = []
    if not requests:
        return
    request = pipeline.ARTIFACTS / 'flow-context-layout-request.json'
    pipeline.write_json(request, process_diagram.layout_request({'scenes': requests}))
    raw = pipeline._run_fixed_command('flow-context-layout',
        [pipeline.NODE, process_diagram.LAYOUT_SCRIPT, request], pipeline.PROJECT, timeout=60)
    layouts = json.loads(raw)['scenes']
    for (scene, targets), layout in zip(groups, layouts):
        scene['props']['layout_views'].append({'targets': targets, 'layout': layout['layout']})
    pipeline.write_json(pipeline.ARTIFACTS / 'flow-context-layout.json', {'scenes': layouts})
