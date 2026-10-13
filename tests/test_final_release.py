"""Static checks for the portable final design package (NOT real video acceptance)."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_unambiguous():
    m = json.loads((ROOT / 'PACKAGE_MANIFEST.json').read_text())
    assert m['document_release'] == 'V0.3-DESIGN-FINAL'
    assert m['mvp_product_target'] == 'V0.1'
    assert m['video_ir_contract'] == 'v0.2'
    assert m['active_stage'] == 'G0'


def test_manifest_hashes_and_file_coverage():
    m = json.loads((ROOT / 'PACKAGE_MANIFEST.json').read_text())
    records = {r['path']: r for r in m['files']}
    disk_files = {
        str(f.relative_to(ROOT)) for f in ROOT.rglob('*')
        if f.is_file() and f.name != 'PACKAGE_MANIFEST.json'
        and '__pycache__' not in f.parts and '.pytest_cache' not in f.parts
    }
    assert set(records) == disk_files
    for rel, r in records.items():
        p = ROOT / rel
        assert p.stat().st_size == r['bytes'], rel
        assert hashlib.sha256(p.read_bytes()).hexdigest() == r['sha256'], rel


def test_first_task_is_g0_only():
    first = (ROOT / 'AGENT_FIRST_TASK.md').read_text()
    assert '10–20' in first and 'G0_REPORT.md' in first
    assert '禁止未经用户授权继续 G1' in first
    assert 'Mock' in first


def test_reference_contract_is_present():
    required = ['START_HERE.md','RELEASE_STATUS.md','README.md','AGENTS.md',
                'BASELINE_SHA256.json','AGENT_FIRST_TASK.md',
                'schemas/video-ir-v0.2.schema.json',
                'schemas/component-registry-v0.2.json']
    assert all((ROOT / rel).is_file() for rel in required)
