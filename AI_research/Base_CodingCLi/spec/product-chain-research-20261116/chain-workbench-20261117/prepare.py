from pathlib import Path
import shutil,json,hashlib,sqlite3
HERE=Path(__file__).resolve().parent
SOURCE=HERE.parents[2]
BASE=HERE.parent/'isolated-real-chain-20261117/runtime'
ROOT=HERE/'runtime'
if (HERE/'baseline.json').exists():raise SystemExit('保留已有隔离开发副本')
ROOT.mkdir(exist_ok=True)
from storage_layout import initialize
initialize(ROOT)
for name in ('core','utils','web','scripts','config','jobs','output'):
    shutil.copytree(BASE/name,ROOT/name,symlinks=True,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
(ROOT/'state').mkdir(exist_ok=True)
for f in (BASE/'state').glob('*.db'):
    with sqlite3.connect(f.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as src,sqlite3.connect(ROOT/'state'/f.name) as dst:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
for name in ('logs','tmp','cache','evidence','implementation'):(ROOT/name).mkdir(exist_ok=True)
shutil.copytree(SOURCE/'ui',ROOT/'ui',ignore=shutil.ignore_patterns('node_modules','dist','.vite','*.tsbuildinfo'))
(ROOT/'ui/node_modules').symlink_to(SOURCE/'ui/node_modules',target_is_directory=True)
manifest={}
for parent in ('core','utils','web','scripts','config','jobs','ui/src'):
    for f in (ROOT/parent).rglob('*'):
        if f.is_file():manifest[str(f.relative_to(ROOT))]=hashlib.sha256(f.read_bytes()).hexdigest()
(HERE/'baseline.json').write_text(json.dumps({'base':str(BASE),'source':str(SOURCE),'files':manifest},ensure_ascii=False,indent=2))
print('独立开发副本已创建',len(manifest))
