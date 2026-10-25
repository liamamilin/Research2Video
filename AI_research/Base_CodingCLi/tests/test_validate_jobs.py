"""The CLI accepts prompt-led research without inventing required search keywords."""
import pytest
import yaml

from scripts.validate import check_jobs


@pytest.mark.parametrize('extra', [{}, {'keywords': []}, {'sources': ['github:pyodide/pyodide']}])
def test_prompt_led_and_source_backed_jobs_are_valid(tmp_path, extra):
    (tmp_path / 'research.yaml').write_text(yaml.safe_dump({'name': 'Research', 'prompt': 'Study browser Python', **extra}))
    assert check_jobs(str(tmp_path))


@pytest.mark.parametrize('extra', [{'prompt': ''}, {'keywords': 'not-a-list'}, {'keywords': [7]}])
def test_missing_prompt_and_invalid_keyword_types_are_reported(tmp_path, extra):
    (tmp_path / 'research.yaml').write_text(yaml.safe_dump({'name': 'Research', 'prompt': 'Study browser Python', **extra}))
    assert not check_jobs(str(tmp_path))
