"""Domain rollout keeps original Jobs and provides isolated, scoped source pools."""
from pathlib import Path

import yaml

from core import matrices, source_supply
from core.config import load_job
from core.cron import parse_schedule
from core.source_discovery import search_queries

TARGETS = ['tech', 'software', 'science', 'company', 'economy', 'industry', 'consumer',
           'history', 'geography', 'culture', 'chatgpt_acquisition']


def test_rollout_profiles_match_existing_matrix_jobs_without_changing_production_roles():
    domain = matrices.load_all('config')
    seen = set()
    times = set()
    for key in TARGETS:
        profile = yaml.safe_load((Path('config/source_supply') / f'{key}.yaml').read_text())
        assert profile['matrix'] == key
        assert set(profile['jobs']) == set(domain[key].jobs)
        assert not seen.intersection(profile['jobs'])
        seen.update(profile['jobs'])
        assert profile['timezone'] == 'Asia/Shanghai'
        assert profile['recurrence'] not in times
        times.add(profile['recurrence'])
        assert parse_schedule(profile['recurrence'])
        job = load_job('jobs', f'source_discovery/{key}')
        assert job['source_discovery'] == key
        assert job['enabled'] == profile['enabled']
        assert not any(field in job for field in ['production', 'topics', 'auto', 'publish'])
        assert len(search_queries(profile)) == len(domain[key].jobs)
        for canonical, column in profile['jobs'].items():
            original = load_job('jobs', canonical)
            assert original and column['mode'] in {'changes', 'context'}
            selected = source_supply.for_job('config', original)
            assert (selected is not None) is profile['enabled']
            if selected:
                assert selected['key'] == key
    assert domain['tech'].content['role'] == domain['software'].content['role'] == 'source'
    assert not domain['education'].jobs
    assert not (Path('config/source_supply') / 'education.yaml').exists()


def test_explanatory_and_evergreen_columns_keep_current_background():
    for key in TARGETS:
        profile = yaml.safe_load((Path('config/source_supply') / f'{key}.yaml').read_text())
        for name, column in profile['jobs'].items():
            changes = name in {'tech/tech_daily', 'software/software_daily', 'chatgpt_acquisition/t09_updates'}
            assert column['mode'] == ('changes' if changes else 'context')


def test_two_matrix_profiles_route_sources_without_cross_assignment(tmp_path):
    folder = tmp_path / 'source_supply'
    folder.mkdir()
    for key in ['tech', 'software']:
        (folder / f'{key}.yaml').write_text(yaml.safe_dump({'enabled': True, 'jobs': {f'{key}/daily': {'mode': 'changes'}}}))
    assert source_supply.for_job(str(tmp_path), {'_file': 'tech/daily'})['key'] == 'tech'
    assert source_supply.for_job(str(tmp_path), {'_file': 'software/daily'})['key'] == 'software'
    assert source_supply.for_job(str(tmp_path), {'_file': 'education/daily'}) is None
