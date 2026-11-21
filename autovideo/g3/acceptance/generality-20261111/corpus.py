"""Freeze structural examples before implementation; holdout details are not printed."""
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from g2.process_diagram import layout_request

WORDS = ['收集材料','整理记录','生成初稿','修改内容','导出结果','归档文件','同步状态','发送通知']
LONG = ['把本周多个来源的记录整理为可继续编辑的完整文档',
        '保留输入表格中的栏目顺序并输出对应的任务状态',
        '将 weekly.html 转为 Markdown 文档',
        '把不同成员的反馈合并到同一个版本并保存修改记录']

def graph(topology, count):
    if topology=='chain': return [(i,i+1) for i in range(count-1)]
    if topology=='fork': return [(0,i) for i in range(1,count)]
    if topology=='merge': return [(i,count-1) for i in range(count-1)]
    if topology=='diamond': return [(0,i) for i in range(1,count-1)]+[(i,count-1) for i in range(1,count-1)]
    return [(i,(i+1)%count) for i in range(count)]

def build(split, seed):
    rng=random.Random(seed);cases=[]
    for i in range(20):
        count=rng.randint(3,8);kind=['chain','fork','merge','diamond','cycle'][i%5]
        long=i%3!=0
        labels=[rng.choice(LONG if long else WORDS) for _ in range(count)]
        nodes=[{'id':f'n{j}','label':text} for j,text in enumerate(labels)]
        edges=[{'id':f'e{j}','from':f'n{a}','to':f'n{b}','label':'传递' if j%2 else '', 'relation':'flow'}
               for j,(a,b) in enumerate(graph(kind,count))]
        cases.append({'id':f'{split}-flow-{i:02}', 'family':'flow', 'topology':kind,
            'props':{'title':'从输入到结果的关系','takeaway':'按连接关系理解处理过程','nodes':nodes,'edges':edges,'paths':[]},
            'duration_frames':rng.choice([360,540,720]), 'profile':rng.choice(['brief','balanced','explain'])})
    assert all(len(n['label']) <= 30 for c in cases for n in c['props']['nodes'])
    request=layout_request({'scenes':[{'id':c['id'],**c['props']} for c in cases]})
    path=Path(__file__).parent/f'.{split}-layout-input.json';path.write_text(json.dumps(request,ensure_ascii=False))
    raw=subprocess.check_output(['node',str(ROOT/'g2/layout/compile.cjs'),str(path)],cwd=ROOT)
    path.unlink()
    layouts={s['id']:s['layout'] for s in json.loads(raw)['scenes']}
    for c in cases:c['props']['layout']=layouts[c['id']]
    for i in range(8):
        width,height=[(1200,800),(600,1600),(2400,600),(900,900)][i%4]
        x=rng.choice([0,100,600]);y=rng.choice([0,100,650]);w=rng.randint(200,350);h=rng.randint(180,320)
        cases.append({'id':f'{split}-material-{i:02}','family':'material','width':width,'height':height,
            'region':{'x1':x,'y1':y,'x2':min(1000,x+w),'y2':min(1000,y+h),'label':'需要看清的结果','style':'box'},
            'pair':bool(i%2),'duration_frames':rng.choice([180,360,540])})
    for i in range(8):
        count=rng.randint(1,4);length=rng.choice([8,22,45,58])
        cases.append({'id':f'{split}-comparison-{i:02}','family':'comparison',
            'props':{side:{'label':rng.choice(['本地处理方案','适合多人协作与跨设备同步的处理方案']),
                'items':[(rng.choice(LONG)*3)[:length] for _ in range(count)]} for side in ['left','right']},
            'duration_frames':rng.choice([240,480,600])})
    for i in range(8):
        count=rng.randint(3,8);gap=rng.choice([9,18,45,90]);speed=rng.choice([.8,1.,1.25,1.5])
        cases.append({'id':f'{split}-motion-{i:02}','family':'motion','count':count,'gap':gap,'speed':speed,
            'duration_frames':round((90+count*gap+90)/speed), 'profile':rng.choice(['brief','balanced','explain'])})
    for i in range(8):
        name=rng.choice(['本地文档','任务表格','邮件草稿','会议记录']);phrase=rng.choice(['整理完成','输出结果','可以继续编辑'])
        cases.append({'id':f'{split}-timing-{i:02}','family':'timing','introduction':'接着看'+name,
            'emphasis':phrase,'segments':['上一个任务已经'+phrase+'。','接着看'+name+'，等待处理，'+phrase+'。'],
            'speed':rng.choice([.8,1,1.25,1.5])})
    from jsonschema import Draft202012Validator
    for c in cases:
        component={'flow':'flow_diagram','comparison':'comparison'}.get(c['family'])
        if component:
            Draft202012Validator(json.loads((ROOT/f'schemas/components/{component}.schema.json').read_text())).validate(c['props'])
    return cases

if __name__=='__main__':
    folder=Path(__file__).parent
    assert not (folder/'manifest.json').exists(), 'Frozen corpus already exists; do not overwrite it.'
    manifest={'version':1,'seeds':{'development':2026111101,'holdout':2026111102},'files':{},
              'representative_indices':{'flow':[0,1,2,3,4,7],'material':[0,3],'comparison':[0,3],'motion':[0],'timing':[0]},
              'holdout_policy':'Do not inspect or run holdout until implementation is frozen. If used to tune, add a new holdout cohort.'}
    for split,seed in manifest['seeds'].items():
        cases=build(split,seed);data=(json.dumps(cases,ensure_ascii=False,indent=2)+'\n').encode();path=folder/f'{split}.json';path.write_bytes(data)
        manifest['files'][path.name]={'sha256':hashlib.sha256(data).hexdigest(),'cases':len(cases)}
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(manifest,ensure_ascii=False))
