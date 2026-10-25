"""Render all authored Lottie actions at a moving frame and at their held end state."""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
(out/'src').mkdir()
for name in ('VisualObjects.tsx','ObjectMotions.ts'):
    shutil.copy2(ROOT/'g1/renderer/src'/name,out/'src'/name)
(out/'node_modules').symlink_to(ROOT/'g0/vendor/remotion-audiogram/node_modules')
(out/'src/index.tsx').write_text('''import React from 'react';
import {AbsoluteFill,Composition,registerRoot,useCurrentFrame} from 'remotion';
import {VisualObject} from './VisualObjects';
const items=[{target:'line:0',icon:'file',action:'process'},{target:'line:1',icon:'result',action:'complete'},{target:'line:2',icon:'network',action:'disconnect'}];
const scene={id:'demo',props:{visuals:items},beats:items.map((v,i)=>({id:`demo_action_${i}`,target_id:v.target,action:'reveal',frame:0}))};
const View=()=> {const frame=useCurrentFrame();return <AbsoluteFill style={{background:'#F7F8F5',color:'#202D28',fontFamily:'Hiragino Sans GB',padding:'130px 140px'}}>
  <h1 style={{fontSize:64,margin:0}}>对象保持一致，动作表达状态</h1>
  <div style={{display:'flex',gap:30,marginTop:130}}>{items.map((v,i)=><div key={v.target} style={{flex:1,background:'white',border:'1px solid #E1E8E1',borderRadius:22,padding:48,height:380}}>
    <VisualObject scene={scene as any} target={v.target} frame={frame} size={112}/>
    <div style={{fontSize:44,fontWeight:600,marginTop:36}}>{['文件处理中','结果已生成','连接已中断'][i]}</div>
  </div>)}</div></AbsoluteFill>;};
registerRoot(()=> <Composition id="ObjectActions" component={View} width={1920} height={1080} fps={30} durationInFrames={150}/>);
''')
env={**os.environ,'PATH':'/Users/milin/.nvm/versions/node/v22.21.1/bin:'+os.environ.get('PATH','')}
for frame in (24,90,120):
    command=[str(ROOT/'g0/vendor/remotion-audiogram/node_modules/.bin/remotion'),'still',str(out/'src/index.tsx'),'ObjectActions',str(out/f'actions-{frame}.png'),f'--frame={frame}','--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--log=verbose']
    result=subprocess.run(command,cwd=out,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (out/f'actions-{frame}.log').write_text(result.stdout)
    if result.returncode:raise RuntimeError(result.stdout[-2500:])
    print(frame,flush=True)
