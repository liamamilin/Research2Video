"""Export only isolated implementation sources, without runtime data or credentials."""
from pathlib import Path
import json,hashlib,shutil,difflib
HERE=Path(__file__).resolve().parent
manifest=[];patch=[]
for component,root,baseline_name,folders in [('research',HERE/'runtime','baseline.json',('core','utils','web','ui/src')),('renderer',HERE/'runtime/renderer','renderer-baseline.json',('g1','g2','g3','schemas'))]:
    baseline=json.loads((HERE/baseline_name).read_text())
    source=Path(baseline['source'])
    for folder in folders:
        for file in (root/folder).rglob('*'):
            if file.suffix not in ('.py','.ts','.tsx','.js','.css','.html'):continue
            if not file.is_file() or any(part in file.parts for part in ('__pycache__','node_modules','data')) or file.suffix=='.pyc':continue
            rel=str(file.relative_to(root));digest=hashlib.sha256(file.read_bytes()).hexdigest()
            if baseline['files'].get(rel)==digest:continue
            target=HERE/'implementation'/component/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
            original=source/rel
            before=original.read_text().splitlines(True) if original.exists() else []
            patch.extend(difflib.unified_diff(before,file.read_text().splitlines(True),fromfile='a/'+component+'/'+rel,tofile='b/'+component+'/'+rel))
            manifest.append({'component':component,'path':rel,'sha256':digest})
(HERE/'implementation.patch').write_text(''.join(patch))
(HERE/'implementation-manifest.json').write_text(json.dumps({'scope':'isolated workbench only','files':manifest},ensure_ascii=False,indent=2))
print('Exported',len(manifest),'source files')
