"""Automatic pictures are paragraph-scoped inputs, independent of title spelling."""
import pytest

from core.video import visual_cache, visual_materials, visual_matching


@pytest.mark.parametrize('pair', [('Orion', 'Nova'), ('Aurora', 'Vertex'), ('Cedar', 'Nimbus')])
def test_title_order_does_not_change_candidate_selection(pair):
    first, second = pair
    text = f'{first} 与 {second} 的界面和输出结果对比。'
    url = f'https://docs.example.com/{first.lower()}/examples'
    candidate = {'text': f'{first} and {second} interface and output results.', 'visual_kind': 'image'}
    decisions = []
    for title in (f'{first} 与 {second} 对比', f'{second} 与 {first} 对比', '产品对比'):
        need, = visual_materials.needs_for({'sections': [{'title': title, 'paragraphs': [
            {'text': text, 'citations': [url]}]}]})
        decisions.append(visual_matching.compatible(need, candidate, 'Examples')[0])
        assert not need.get('visual_subject')
    assert decisions == [True, True, True]


@pytest.mark.parametrize('text', [
    '个人账号和工作账号各自保留聊天与记忆。',
    '图像生成支持选择画幅和修改局部内容。',
    '语音输入现在可以编辑识别结果。',
    '工作空间支持共享文件与设置权限。',
])
def test_chinese_paragraph_does_not_match_an_unrelated_publisher_image(text):
    need, = visual_materials.needs_for({'sections': [{'title': '产品动态', 'paragraphs': [
        {'text': text, 'citations': ['https://help.openai.com/en/articles/updates']}]}]})
    candidate = {'text': 'OpenAI product → tunnel service → tunnel-client', 'visual_kind': 'image'}
    assert not visual_matching.compatible(need, candidate, 'Secure MCP Tunnel | OpenAI API')[0]


def test_picture_cache_does_not_share_a_decision_between_different_features():
    policy = visual_materials.policy({'enabled': True})
    first = {'id': 'c1p1', 'kind': 'feature', 'text': 'Acme 可以导出文件。', 'claims': 'Acme 可以导出文件。'}
    second = {**first, 'text': 'Acme 可以切换账号。', 'claims': 'Acme 可以切换账号。'}
    assert visual_cache.key(first, 'https://example.com/docs', policy) != visual_cache.key(second, 'https://example.com/docs', policy)
