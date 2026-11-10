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
