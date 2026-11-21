from pathlib import Path
import shutil,json,hashlib
HERE=Path(__file__).resolve().parent
SOURCE=HERE.parents[4]/'autovideo'
TARGET=HERE/'runtime/renderer'
if (HERE/'renderer-baseline.json').exists():raise SystemExit('保留已有渲染副本')
TARGET.mkdir(exist_ok=True)
manifest={}
for folder in ('g1','g2','g3','schemas'):
    for f in (SOURCE/folder).rglob('*'):
        rel=f.relative_to(SOURCE)
        if any(x in rel.parts for x in ('node_modules','__pycache__','data','runs','acceptance','tests')) or f.suffix=='.pyc' or f.is_symlink() or not f.is_file():continue
        out=TARGET/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,out)
        manifest[str(rel)]=hashlib.sha256(f.read_bytes()).hexdigest()
(TARGET/'g0').symlink_to(SOURCE/'g0',target_is_directory=True)
for rel in ('g2/renderer/node_modules','g2/layout/node_modules','g2/visual_renderer/node_modules'):
    f=SOURCE/rel
    if f.exists():(TARGET/rel).parent.mkdir(parents=True,exist_ok=True);(TARGET/rel).symlink_to(f,target_is_directory=True)
(TARGET/'g3/data').mkdir(exist_ok=True)
settings=SOURCE/'g3/data/llm-settings.json'
if settings.exists():shutil.copy2(settings,TARGET/'g3/data/llm-settings.json')
(HERE/'renderer-baseline.json').write_text(json.dumps({'source':str(SOURCE),'files':manifest},indent=2))
print('渲染副本文件',len(manifest))

for name in ("voice-runtime","voice-models"):
    src=SOURCE/"g3/data"/name
    if src.exists():(TARGET/"g3/data"/name).symlink_to(src,target_is_directory=True)
