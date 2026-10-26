"""Caption handoff rejects stale audio timing and keeps the original speech track."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from g2.caption_alignment import build_cues, chunks, narration_hash, render_cues, srt_text, supports_short_captions, word_spans


def test_short_final_syllable_stays_with_a_readable_phrase_and_original_timing():
    text = '而一个多月前，双方才刚刚宣布过一笔同样规模的交易。'
    row = {'id': 'c2_n5', 'text': text, 'start_ms': 56858, 'end_ms': 60317}
    edges = [160, 320, 400, 480, 640, 800, 960, 1200, 1360, 1440, 1600, 1760,
             1920, 2080, 2160, 2320, 2480, 2640, 2800, 2880, 3040, 3120, 3280, 3440]
    words = word_spans(text)
    items = [{'text': word, 'start_ms': edges[i], 'end_ms': edges[i + 1]} for i, (word, _, _) in enumerate(words)]
    cues = build_cues(row, items)
    assert ''.join(c['text'] for c in cues) == text
    assert cues[-1]['text'].endswith('交易。') and cues[-1]['text'] != '易。'
    assert all(c['end_ms'] - c['start_ms'] >= 200 for c in cues)
    assert all(row['start_ms'] <= c['start_ms'] < c['end_ms'] <= row['end_ms'] for c in cues)


@pytest.mark.parametrize('length', [23, 24, 29, 45, 46, 51])
def test_caption_chunks_do_not_strand_a_tiny_final_fragment(length):
    text = '字' * length
    spans = chunks(text)
    assert ''.join(text[a:b] for a, b, _, _ in spans) == text
    assert 8 <= spans[-1][3] - spans[-1][2] <= 22
    assert all(last - first <= 22 for _, _, first, last in spans)


def test_an_entire_utterance_too_short_to_read_still_fails():
    with pytest.raises(ValueError, match='区间过短'):
        build_cues({'id': 'n1', 'text': '是。', 'start_ms': 0, 'end_ms': 100},
                   [{'text': '是', 'start_ms': 0, 'end_ms': 100}])


def test_old_punctuation_only_voice_segment_keeps_its_text_and_timing():
    from g2.caption_alignment import verify_plan
    row = {'id': 'c1_n4', 'text': '”', 'start_ms': 7041, 'end_ms': 7106}
    ir = {'narration': {'segments': [row]}}
    cues = build_cues(row, [])
    assert cues == [{'segment_id': 'c1_n4', 'text': '”', 'char_start': 0, 'char_end': 1,
                     'start_ms': 7041, 'end_ms': 7106}]
    verify_plan(ir, {'version': 2, 'narration_sha256': narration_hash(ir), 'cues': cues})


def test_missing_spoken_segment_still_reports_its_identity():
    from g2.caption_alignment import verify_plan
    ir = {'narration': {'segments': [{'id': 'c1_n4', 'text': '继续。', 'start_ms': 0, 'end_ms': 1000}]}}
    with pytest.raises(ValueError, match='缺少配音段：c1_n4'):
        verify_plan(ir, {'version': 2, 'narration_sha256': narration_hash(ir), 'cues': []})


def track(tmp_path):
    ir = {'narration': {'segments': [{'id': 'n1', 'text': '作者报告提升，但仅限该评测。', 'start_ms': 100, 'end_ms': 3200}]}}
    cues = [
        {'segment_id': 'n1', 'text': '作者报告提升，', 'char_start': 0, 'char_end': 7, 'start_ms': 100, 'end_ms': 1400},
        {'segment_id': 'n1', 'text': '但仅限该评测。', 'char_start': 7, 'char_end': 14, 'start_ms': 1450, 'end_ms': 3200},
    ]
    plan = {'version': 2, 'narration_sha256': narration_hash(ir), 'cues': cues}
    (tmp_path / 'caption-alignment.json').write_text(json.dumps(plan, ensure_ascii=False))
    (tmp_path / 'captions.srt').write_text(srt_text(cues))
    return SimpleNamespace(ARTIFACTS=tmp_path), ir, cues


@pytest.mark.parametrize('preset', [None, 'information_motion', 'information_cards'])
def test_existing_video_executors_use_the_same_short_track(preset, tmp_path):
    pipeline, ir, cues = track(tmp_path)
    if preset:
        ir['presentation'] = {'preset': preset}
    original = deepcopy(ir)
    assert supports_short_captions(ir)
    assert render_cues(pipeline, ir) == cues
    assert ir == original


@pytest.mark.parametrize('change', ['timing', 'text', 'srt'])
def test_render_handoff_rejects_stale_or_changed_captions(change, tmp_path):
    pipeline, ir, cues = track(tmp_path)
    if change == 'timing':
        ir['narration']['segments'][0]['end_ms'] += 100
    elif change == 'text':
        ir['narration']['segments'][0]['text'] = '已独立证明提升。'
    else:
        (tmp_path / 'captions.srt').write_text(srt_text(cues).replace('作者报告', '已经证明'))
    with pytest.raises(ValueError):
        render_cues(pipeline, ir)


def test_historical_outputs_keep_their_original_phrase_track(tmp_path):
    assert render_cues(SimpleNamespace(ARTIFACTS=tmp_path), {'narration': {'segments': []}}) is None


def test_changed_voice_cannot_reuse_captions_even_with_identical_words_and_duration(tmp_path):
    pipeline, ir, cues = track(tmp_path)
    ir['narration']['audio_asset_id'] = 'voice'
    ir['assets'] = [{'id': 'voice', 'sha256': 'current-voice'}]
    path = tmp_path / 'caption-alignment.json'
    plan = json.loads(path.read_text())
    plan['audio_sha256'] = 'previous-voice'
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='音轨'):
        render_cues(pipeline, ir)
    plan['audio_sha256'] = 'current-voice'
    path.write_text(json.dumps(plan))
    assert render_cues(pipeline, ir) == cues
