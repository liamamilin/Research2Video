"""Document-only integrity checks; not evidence of real video production."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
BASELINE = json.loads((ROOT / 'BASELINE_SHA256.json').read_text())['files']


def test_all_numbered_docs_are_present():
    names = sorted(p.name for p in ROOT.glob('[0-9][0-9]_*.md'))
    assert len(names) == 25, names
    assert [int(name[:2]) for name in names] == list(range(25))


def test_mvp_core_specs_remain_unchanged_against_portable_baseline():
    for rel, digest in BASELINE.items():
        assert (ROOT / rel).is_file(), rel
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, rel



def test_roadmap_authorization_guards():
    roadmap = (ROOT/'16_POST_MVP_ROADMAP.md').read_text()
    readme = (ROOT/'README.md').read_text()
    assert 'G4' in roadmap and 'M0' in roadmap
    assert 'H1' in roadmap and 'H2' in roadmap and 'H3' in roadmap and 'H4' in roadmap
    assert '不得自动执行 H1–H4' in readme


def test_all_future_docs_linked_from_readme():
    readme = (ROOT/'README.md').read_text()
    for number in range(15, 25):
        matches = list(ROOT.glob(f'{number:02d}_*.md'))
        assert len(matches) == 1
        assert matches[0].name in readme


def test_future_docs_disclaim_implementation():
    selected = ['15_POST_MVP_PRODUCT_STRATEGY.md', '16_POST_MVP_ROADMAP.md',
                '17_CREATIVE_ARCHITECTURE_EVOLUTION.md', '23_POST_MVP_AGENT_EXECUTION_PROTOCOL.md',
                '24_ROADMAP_DESIGN_AUDIT.md']
    for name in selected:
        txt = (ROOT/name).read_text()
        assert any(term in txt for term in ('未来', '规划', '未实现', 'DESIGN', 'FUTURE', '未授权')), name
