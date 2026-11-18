"""Apply the reviewed overlay to this workbench only; never to the formal product."""
from pathlib import Path
import json,hashlib,shutil
HERE=Path(__file__).resolve().parent
for row in json.loads((HERE/'implementation-manifest.json').read_text())['files']:
    source=HERE/'implementation'/row['component']/row['path']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==row['sha256'],source
    root=HERE/('runtime/renderer' if row['component']=='renderer' else 'runtime')
    target=root/row['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
print('Isolated implementation applied. Build runtime/ui before starting.')
