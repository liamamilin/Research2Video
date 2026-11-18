from pathlib import Path
import sys,json
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'runtime'))
from web.services import chains
mine={'key':'a','job':'research','outputs':['a_each']};other={'key':'b','name':'尚未添加输出的链路','job':'research','outputs':[]}
with patch.object(chains,'find',return_value=mine),patch.object(chains,'paths',return_value=SimpleNamespace(config_dir='',jobs_dir='')),patch.object(chains.production,'load_all',return_value={}),patch.object(chains,'active_for_job',return_value=[]),patch.object(chains,'digest',return_value='revision'):
 with patch.object(chains,'all_chains',return_value=[mine,other]):
  preview=chains.delete_preview('a');assert preview['keep_job'];assert preview['remaining_outputs']==[];assert preview['shared_chains']==[{'key':'b','name':other['name']}]
 with patch.object(chains,'all_chains',return_value=[mine]):assert not chains.delete_preview('a')['keep_job']
(ROOT/'shared-delete-tests.json').write_text(json.dumps({'shared_chain_without_outputs_keeps_research':True,'last_reference_removes_research':True},indent=2))
print('Shared research survives a sibling chain with zero video outputs')
