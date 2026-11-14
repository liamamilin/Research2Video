"""Cards without a spoken phrase reveal where their own label is said (11-10, picture following narration)."""

from g2 import information_motion as motion
from g2 import visual_language as vl


def rows(text, end=10000):
    return {'n1': {'id': 'n1', 'text': text, 'start_ms': 0, 'end_ms': end}}


def at(cues, target):
    return next(c['character_index'] for c in cues if c['target_id'] == target)


def test_a_comparison_side_appears_when_its_product_is_named_not_at_an_even_split():
    text = '结果很清楚。ChatGPT结构最清楚，但偶尔有模板腔；Claude语气最像人，而且它记得住偏好。第三方横评五个任务。'
    scene = {'id': 's', 'component': 'comparison', 'narration_segment_ids': ['n1'],
             'props': {'left': {'label': 'ChatGPT Plus · 结构', 'items': ['a']},
                       'right': {'label': 'Claude Pro · 语气', 'items': ['b']}}}
    cues = motion.automatic_cues(scene, rows(text))
    assert at(cues, 'side:left') == text.index('ChatGPT')
    assert at(cues, 'side:right') == text.index('Claude')


def test_bars_follow_their_categories_and_unspoken_labels_keep_the_even_split():
    text = '先看榜单。OpenAI 是 7.1，Gemini 4 Carbon 是 6.4，还有一家没点名。'
    scene = {'id': 's', 'component': 'bar_chart', 'narration_segment_ids': ['n1'],
             'props': {'categories': ['OpenAI', 'Gemini 4 Carbon', '某家'], 'values': [7.1, 6.4, 5], 'unit': '分'}}
    cues = motion.automatic_cues(scene, rows(text))
    assert at(cues, 'bar:0') == text.index('OpenAI') and at(cues, 'bar:1') == text.index('Gemini')
    assert at(cues, 'bar:2') == len(text) * 2 // 3  # not said: as before


def test_a_phrase_with_other_spacing_still_anchors_and_a_vague_short_label_does_not():
    text = '再说结果。官方写的是：在同一账号的 Billing 页面能看到今天的花费。'
    scene = {'id': 's', 'component': 'concept_reveal', 'narration_segment_ids': ['n1'],
             'props': {'title': 't', 'lines': ['结果：在同一账号的 Billing 看花费', '另一行'],
                       'visuals': [{'target': 'line:1', 'phrase': '今天 的花费'}]}}
    cues = motion.automatic_cues(scene, rows(text))
    assert at(cues, 'line:1') == text.index('今天的花费')  # matched despite the space
    assert at(cues, 'line:0') != text.index('结果')  # "结果" alone is too vague to anchor


def test_labels_of_each_card_kind():
    big = {'component': 'big_number', 'props': {'value': '116万', 'label': '管理事务（项）', 'note': '执行超360万次 · 用户'}}
    assert vl.target_label(big, 'line:0')[0] == '116万' and vl.target_label(big, 'line:2')[1] == '执行超360万次'
    assert vl.target_label({'component': 'flow_diagram', 'props': {}}, 'node:a') == []


def test_explicit_anchor_in_second_segment_keeps_following_label_in_that_segment():
    first = '上一种情况，Claude适合写作。'
    second = '这次先看ChatGPT结构，然后Claude语气。'
    speech = {'n1': {'id': 'n1', 'text': first, 'start_ms': 0, 'end_ms': 5000},
              'n2': {'id': 'n2', 'text': second, 'start_ms': 5000, 'end_ms': 12000}}
    scene = {'id': 's', 'component': 'comparison', 'narration_segment_ids': ['n1', 'n2'],
             'props': {'left': {'label': 'ChatGPT', 'items': ['结构']},
                       'right': {'label': 'Claude', 'items': ['语气']},
                       'visuals': [{'target': 'side:left', 'phrase': '这次先看ChatGPT'}]}}
    cues = {c['target_id']: c for c in motion.automatic_cues(scene, speech)}
    assert cues['side:left']['segment_id'] == 'n2'
    assert cues['side:right']['segment_id'] == 'n2'
    assert cues['side:right']['character_index'] == second.index('Claude')


def test_object_appears_at_introduction_and_focuses_when_the_result_is_explained():
    text = '再换一场聊《影之刃零》的，它把两种格挡机制做成对照卡片。'
    scene = {'id': 's', 'component': 'concept_reveal', 'narration_segment_ids': ['n1'],
             'props': {'title': '结果', 'lines': ['游戏机制对照'], 'visuals': [
                 {'target': 'line:0', 'icon': 'result', 'reveal_phrase': '再换一场聊《影之刃零》',
                  'phrase': '做成 对照卡片'}]}}
    speech = rows(text)
    cues = motion.automatic_cues(scene, speech)
    assert at(cues, 'line:0') == 0
    timing = []
    beats = vl.action_beats(scene, speech, timing)
    assert timing[0]["character_index"] == text.index("做成对照卡片")
    ir = {"narration": {"segments": list(speech.values())}, "scenes": [{**scene, "beats": beats}]}
    from g2.caption_alignment import word_spans
    words = [{"text": w, "start_ms": i * 100, "end_ms": (i + 1) * 100}
             for i, (w, _, _) in enumerate(word_spans(text))]
    changes = motion.align_visual_beats(ir, {"word_evidence": [{"segment_id": "n1", "items": words}]}, {"cues": timing})
    assert len(changes) == 1 and changes[0]["beat_id"] == beats[0]["id"]
    assert len(beats) == 1
    assert beats[0]["trigger"]["offset_ms"] == changes[0]["offset_ms"]


def test_repeated_conclusion_is_bound_after_this_objects_introduction():
    speech = {'a': {'id': 'a', 'text': '前一项已经生成结果。', 'start_ms': 0, 'end_ms': 3000},
              'b': {'id': 'b', 'text': '接着看任务表，等待处理，生成结果。', 'start_ms': 3000, 'end_ms': 8000}}
    scene = {'id': 'repeat', 'narration_segment_ids': ['a', 'b'], 'props': {'visuals': [
        {'target': 'line:0', 'reveal_phrase': '接着看任务表', 'phrase': '生成结果'}]}}
    timing = []
    beats = vl.action_beats(scene, speech, timing)
    assert len(beats) == 1 and beats[0]['trigger']['segment_id'] == 'b'
    assert timing[0]['character_index'] == speech['b']['text'].index('生成结果')


def test_emphasis_follows_normalized_introduction_without_raw_length_offset():
    text = '结果已生成。接着看文档结果已生成。'
    scene = {'id':'normalized','narration_segment_ids':['n1'],'props':{'visuals':[
        {'target':'line:0','reveal_phrase':'接 着 看 文 档','phrase':'结果已生成'}]}}
    timing=[]
    beats=vl.action_beats(scene,rows(text),timing)
    assert beats and timing[0]['character_index']==text.rindex('结果已生成')
