from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys,json,importlib.util
HERE=Path(__file__).resolve().parent;ROOT=HERE/'runtime';sys.path.insert(0,str(ROOT))
from web.services import chain_reports
settings=SimpleNamespace(paths=SimpleNamespace(output_dir=str(ROOT/'output')))
with patch.object(chain_reports,'get_settings',return_value=settings):
 expected='chains/acceptance_science/2026-11-17.md'
 for value in (expected,'output/'+expected,str(ROOT/'output'/expected),str((ROOT/'output'/expected).resolve())):assert chain_reports.relative_path(value)==expected
 try:chain_reports.relative_path('/etc/hosts')
 except ValueError:pass
 else:raise AssertionError('Outside path accepted')
sys.path.insert(0,str(ROOT/'renderer'))
from g2 import explanation
context={'groups':[{'id':'original'}]}
with patch.object(explanation.Draft202012Validator,'validate',return_value=None):
 try:explanation.validate({'pages':[{'id':'changed'}]},context)
 except ValueError as exc:
  assert 'original' in str(exc) and 'changed' in str(exc)
 else:raise AssertionError('Mismatched group accepted')
(HERE/'runtime-edges-tests.json').write_text(json.dumps({'report_paths':4,'outside_path_rejected':True,'storyboard_retry_has_exact_expected_ids':True},indent=2))
print('Report paths and actionable storyboard retry feedback passed')
