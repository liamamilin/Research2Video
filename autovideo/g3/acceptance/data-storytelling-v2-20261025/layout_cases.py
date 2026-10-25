"""Render trusted scatter fixtures for names, small values, overlaps and flat ranges."""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--case', choices=['tiny','equal','dense','negative'])
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
(out/'src').mkdir()
shutil.copy2(ROOT/'g1/renderer/src/ScatterChart.tsx',out/'src/ScatterChart.tsx')
(out/'node_modules').symlink_to(ROOT/'g0/vendor/remotion-audiogram/node_modules')
(out/'src/index.tsx').write_text('''import React from 'react';
import {AbsoluteFill,Composition,registerRoot,useCurrentFrame} from 'remotion';
import {ScatterChart} from './ScatterChart';
const View=({scene}:any)=><AbsoluteFill style={{background:'#F7F8F5',fontFamily:'Hiragino Sans GB'}}><div style={{position:'absolute',left:140,top:162,fontSize:64,color:'#202D28'}}>{scene.props.title}</div><ScatterChart scene={scene} frame={useCurrentFrame()}/></AbsoluteFill>;
registerRoot(()=> <Composition id="ScatterLayout" component={View} width={1920} height={1080} fps={30} durationInFrames={300}/>);
''')
base={'title':'散点布局示例','x_label':'单次成本','x_unit':'美元','y_label':'任务得分','y_unit':'分','focus_indices':[0,1], 'takeaway':'按讲述突出相应对象'}
cases={
    'tiny':{'points':[{'name':'轻量版','x':.002,'y':72},{'name':'标准版','x':.006,'y':86},{'name':'增强版','x':.01,'y':91}]},
    'equal':{'points':[{'name':'方案A','x':0,'y':0},{'name':'方案B','x':0,'y':0},{'name':'方案C','x':0,'y':0}]},
    'dense':{'points':[{'name':f'同一区域方案{i+1}','x':10+(i%3)*.01,'y':75+(i//3)*.01,'group':f'类别{i%3+1}'} for i in range(9)]+[{'name':'对照方案10','x':20,'y':100,'group':'类别2'}], 'focus_indices':[0,5,9]},
    'negative':{'points':[{'name':'低值','x':-4,'y':-8},{'name':'中值','x':0,'y':0},{'name':'高值','x':3,'y':5}]}}
env={**os.environ,'PATH':'/Users/milin/.nvm/versions/node/v22.21.1/bin:'+os.environ.get('PATH','')}
for name,extra in cases.items():
    if args.case and args.case!=name:continue
    props={**base,**extra};n=len(props['points'])
    scene={'props':props,'start_frame':0,'end_frame':300,'beats':[{'target_id':'title','frame':0}]+[{'target_id':f'line:{i}','frame':10+i*10} for i in range(n)]+[{'target_id':f'line:{n}','frame':220}]}
    path=out/(name+'.json');path.write_text(json.dumps({'scene':scene},ensure_ascii=False))
    command=[str(ROOT/'g0/vendor/remotion-audiogram/node_modules/.bin/remotion'),'still',str(out/'src/index.tsx'),'ScatterLayout',str(out/(name+'.png')),'--props='+str(path),'--frame=270','--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--log=error']
    result=subprocess.run(command,cwd=out,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (out/(name+'.log')).write_text(result.stdout)
    if result.returncode:raise RuntimeError(result.stdout[-2000:])
    print(name,flush=True)
