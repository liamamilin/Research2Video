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


def test_embedded_product_screenshot_uses_actual_ocr_labels_as_separate_targets():
    asset = {'width': 800, 'height': 300, 'quality': {'visual_kind': 'image', 'image': {
        'vision': {'visible_text': 'Schedule Trigger\nSave Weather Report'},
        'ocr_targets': [
            {'x1': 100, 'y1': 500, 'x2': 280, 'y2': 560, 'text': 'Schedule Trigger'},
            {'x1': 700, 'y1': 500, 'x2': 920, 'y2': 560, 'text': 'Save Weather Report'},
            {'x1': 50, 'y1': 20, 'x2': 200, 'y2': 60, 'text': 'Nodes'}]}}}
    focus = visual_focus.for_asset(asset, {'visual_subject': 'n8n', 'text': '先看触发步骤，再看保存结果。'})
    assert [r['label'] for r in focus['regions']] == ['Schedule Trigger', 'Save Weather Report']
    assert focus['regions'][0]['y1'] < 500 and focus['regions'][0]['x2'] < 700


def test_code_region_labels_fit_the_existing_engine_contract():
    text = 'result = md.convert("test.xlsx"); print(result.markdown)'
    asset = {'width': 800, 'height': 300, 'quality': {'visual_kind': 'code', 'image': {
        'vision': {'visible_text': text}, 'ocr_targets': [
            {'x1': 100, 'y1': 200, 'x2': 900, 'y2': 300, 'text': text}]}}}
    focus = visual_focus.for_asset(asset, {'visual_subject': 'markitdown', 'text': '用 convert 转换文件。'})
    assert focus['regions'][0]['label'] == text[:32]
