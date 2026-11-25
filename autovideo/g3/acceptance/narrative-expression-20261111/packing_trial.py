"""Prepare a local analogy trial after the generic label/arrow trial proved weak."""
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT/'g3/data/validation/narrative-expression-20261111/science-study'
source = BASE/'stages'
out = BASE/'packing'
out.mkdir(exist_ok=False)
for name in ('props.json', 'source.json', 'video-ir.json'):
    shutil.copyfile(source/name, out/name)
shutil.copytree(source/'renderer/src', out/'renderer/src')
(out/'renderer/node_modules').symlink_to(ROOT/'g0/vendor/remotion-audiogram/node_modules', target_is_directory=True)
shutil.copyfile(Path(__file__).with_name('PackingStudy.tsx'), out/'renderer/src/PackingStudy.tsx')
root = out/'renderer/src/Root.tsx'
text = root.read_text()
text = "import {PackingStudy} from './PackingStudy';\n"+text
old = "{scene.component === 'flow_diagram' && <ObjectFlow scene={scene} frame={frame}/>}"
assert old in text
text = text.replace(old,"{scene.component === 'flow_diagram' && (scene.id === 'study_4' ? <PackingStudy scene={scene} frame={frame}/> : <ObjectFlow scene={scene} frame={frame}/>)}")
root.write_text(text)
command = json.loads((source/'command.json').read_text())
command[5] = str(out/'video.mp4')
command = [f'--props={out/"props.json"}' if arg.startswith('--props=') else arg for arg in command]
command.append('--frames=790-1243')
(out/'command.json').write_text(json.dumps(command))
(out/'study-note.json').write_text(json.dumps({'manual_expression_only':True,'registered_in_production':False,
    'purpose':'equal blocks occupy different space; labels and emphasis follow original analogy',
    'frames':[790,1243]},ensure_ascii=False,indent=2))
print(out)
