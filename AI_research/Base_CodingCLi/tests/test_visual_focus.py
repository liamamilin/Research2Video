from core.video import visual_focus


def test_focus_uses_measured_image_coordinates_and_preserves_model_proposal_as_audit():
    asset = {'width': 1440, 'height': 900, 'quality': {'region_text': 'Playwright element screenshot',
        'dom_targets': [{'x1': 200, 'y1': 300, 'x2': 800, 'y2': 400, 'text': 'Take an element screenshot', 'tag': 'p'},
                        {'x1': -10, 'y1': 100, 'x2': 900, 'y2': 200, 'text': 'element screenshot', 'tag': 'p'}],
        'image': {'vision': {'focus': {'x1': 0, 'y1': 0, 'x2': 1000, 'y2': 1000}}}}}
    result = visual_focus.for_asset(asset, {'claims': 'Playwright 支持元素截图。'})
    assert result == {'width': 1440, 'height': 900, 'regions': [{'x1': 200, 'y1': 300, 'x2': 800, 'y2': 400,
                     'label': '对应功能说明', 'style': 'box'}]}
    assert visual_focus.for_asset({'width': 800, 'height': 400}) is None
