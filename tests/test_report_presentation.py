from web.services.report_presentation import fallback_title


def test_title_ignores_model_auxiliary_text_and_code_examples(tmp_path):
    path = tmp_path / "report.md"
    raw = "<think>\n# 辅助内容\n</think>\n```md\n# 示例\n```\n# 正式研究报告\n正文"
    path.write_text(raw)
    assert fallback_title(str(path)) == "正式研究报告"
    assert path.read_text() == raw


def test_title_cache_follows_file_changes_and_tolerates_missing_files(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("# 第一版")
    assert fallback_title(str(path)) == "第一版"
    path.write_text("# 更新后的标题")
    assert fallback_title(str(path)) == "更新后的标题"
    path.unlink()
    assert fallback_title(str(path)) == ""
