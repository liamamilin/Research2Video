"""Additional full-resolution inspection, selected by text density rather than outcome."""
import copy,json,subprocess,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).parent
BASE=ROOT/'g3/data/validation/generality-20261111'
out=BASE/'still-checks';out.mkdir(exist_ok=False)
final=BASE/'holdout2-video-final';template=json.loads((final/'props.json').read_text())
comparison=next(s for s in template['ir']['scenes'] if s['component']=='comparison')
old=BASE/'baseline-renderer';shutil.copytree(BASE/'baseline/renderer-source',old/'src');(old/'node_modules').symlink_to(ROOT/'g0/vendor/remotion-audiogram/node_modules',target_is_directory=True)
(old/'src/index.ts').write_text("import {registerRoot} from 'remotion';import {Root} from './Root';registerRoot(Root);")
dev=json.loads((HERE/'development.json').read_text());fresh=json.loads((HERE/'holdout2.json').read_text())
boundary={'left':{'label':'完整保留长标题以及多个条件下的结果说明'*3,'items':['完整保留每项文字以及输入输出之间的对应关系'*4]*4},'right':{'label':'简短对照','items':['一个结果']}}
boundary['left']['label']=boundary['left']['label'][:40];boundary['left']['items']=[t[:58] for t in boundary['left']['items']]
worst=max((c for c in fresh if c['family']=='comparison'),key=lambda c:sum(len(c['props'][s]['label'])+len(''.join(c['props'][s]['items'])) for s in ['left','right']))
checks=[('before-development-04',next(c['props'] for c in dev if c['id']=='development-comparison-04'),old,False),('after-development-04',next(c['props'] for c in dev if c['id']=='development-comparison-04'),final/'renderer',False),('holdout2-densest',worst['props'],final/'renderer',False),('contract-boundary-large',boundary,final/'renderer',True)]
for name,props,renderer,large in checks:
    data=copy.deepcopy(template);scene=copy.deepcopy(comparison);scene.update(id=name,start_frame=0,end_frame=180,props=props,purpose='长文本完整显示 · 结构压力测试')
    scene['beats']=[{'id':side,'action':'reveal','target_id':f'side:{side}','frame':0,'order':i} for i,side in enumerate(['left','right'])]
    data['ir'].update(scenes=[scene],total_frames=180,narration={'audio_asset_id':'audio','segments':[]},presentation={'preset':'explanation',**({'density':'large'} if large else {})})
    data['badge']={'text':name,'color':'#17784B'};path=out/f'{name}.json';path.write_text(json.dumps(data,ensure_ascii=False))
    cmd=['node',str(ROOT/'g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js'),'still','src/index.ts','G1CommunicationNoise',str(out/f'{name}.png'),f'--props={path}',f'--public-dir={final/"public"}','--frame=90','--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--bundle-cache=false']
    with (out/f'{name}.log').open('w') as log:subprocess.run(cmd,cwd=renderer,stdout=log,stderr=log,check=True)
    print(name,flush=True)
