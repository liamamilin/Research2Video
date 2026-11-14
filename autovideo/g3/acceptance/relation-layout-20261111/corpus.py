"""Independent structural cohort; generate without printing held-out instances."""
import hashlib,json,os,random,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from g2 import process_diagram
from g1.visual_language import component_schema
from jsonschema import Draft202012Validator
HERE=Path(__file__).parent
SHORT=['采样','过滤','分配','装配','储存','复核','回流','输出']
LONG=['把不同批次的原料分别送入对应的清洗与预处理装置','将多路传感器读数汇总为同一时刻的设备运行状态','对照各阶段的实验观察记录整理共同出现的变化','将筛选得到的候选配置交给后续的验证与比较步骤','依据不同区域的用水需求分别安排供水与回收过程']
MIXED=['将 sensor_A.csv 送入后续分析步骤','使用 --dry-run 预览各分支的处理结果','在 batch_04 中保留全部候选参数','把 calibration_v2 同步给下游设备']
KINDS=['fork','merge','diamond','ladder','layered','cycle','disconnected','parallel']

def links(kind,n,rng):
 if kind=='fork':return [(0,i) for i in range(1,n)]
 if kind=='merge':return [(i,n-1) for i in range(n-1)]
 if kind=='diamond':return [(0,i) for i in range(1,n-1)]+[(i,n-1) for i in range(1,n-1)]
 if kind=='ladder':return list(dict.fromkeys([(i,i+2) for i in range(n-2)]+[(i,i+1) for i in range(0,n-1,2)]))
 if kind=='layered':return [(i,j) for i in range(n) for j in range(i+1,n) if (j-i<3 or rng.random()<.25)][:16]
 if kind=='cycle':return [(i,(i+1)%n) for i in range(n)]+([(0,n//2)] if n>3 else [])
 if kind=='disconnected':return [(i,i+1) for i in range(n-1) if i!=n//2-1]
 pairs=[(i,i+1) for i in range(n-1)];return [e for pair in pairs for e in [pair,pair]][:16]

def build(split,seed):
 rng=random.Random(seed);cases=[]
 for i in range(40):
  kind=KINDS[i%8];n=rng.randint(2,8)
  if kind in {'diamond','ladder','disconnected'}:n=max(4,n)
  vocabulary=SHORT if i<8 else MIXED if i>=32 else LONG
  nodes=[{'id':f'n{j}','label':rng.choice(vocabulary)} for j in range(n)]
  pairs=links(kind,n,rng);edges=[{'id':f'e{j}','from':f'n{a}','to':f'n{b}','label':('满足当前阶段的输入条件' if i>=24 else '通过' if j%2 else ''),'relation':'blocked' if j%7==3 else 'flow'} for j,(a,b) in enumerate(pairs)]
  if i%2:rng.shuffle(nodes)
  order=[n['id'] for n in nodes]
  if i%3==1:rng.shuffle(order)
  speed=rng.choice([.8,1,1.5]);length=round(rng.choice([120,360,720])/speed)
  frames=[round(25+(length-65)*j/max(1,len(order)-1)) for j in range(len(order))]
  props={'title':'理解各部分怎样连接','takeaway':'保留每个对象及其连接条件','nodes':nodes,'edges':edges,'paths':[],
   'visuals':[{'target':f'node:{n["id"]}','icon':'settings'} for n in nodes] if i%4==1 else []}
  case={'id':f'{split}-{kind}-{i:02}','family':kind,'props':props,'duration_frames':length,'profile':rng.choice(['brief','balanced','explain']),
   'beats':[{'id':'scene','action':'reveal','target_id':'scene','frame':0}]+[{'id':f'b{j}','action':'reveal','target_id':f'node:{key}','frame':frames[j]} for j,key in enumerate(order)],'speed':speed}
  if i%5==2:
   case['beats'] += [{'id':f'edge{j}','action':'reveal','target_id':f'edge:{e["id"]}','frame':round(length-35+j*20/max(1,len(edges)-1))} for j,e in enumerate(edges)]
  Draft202012Validator(component_schema('flow_diagram')).validate(props);cases.append(case)
 request=process_diagram.layout_request({'scenes':[{'id':c['id'],**c['props']} for c in cases]});path=HERE/f'.{split}-input.json';path.write_text(json.dumps(request,ensure_ascii=False))
 env={**os.environ,'NODE_PATH':str(ROOT/'g0/vendor/remotion-audiogram/node_modules')}
 raw=json.loads(subprocess.check_output(['node',str(process_diagram.LAYOUT_SCRIPT),str(path)],env=env));path.unlink()
 layouts={r['id']:r['layout'] for r in raw['scenes']}
 for c in cases:c['props']['layout']=layouts[c['id']]
 return cases
if __name__=='__main__':
 assert not (HERE/'manifest.json').exists()
 manifest={'seeds':{'development':2026111121,'holdout':2026111122},'files':{},'representative_indices':[0,1,2,3,4,5,6,7,16,17,32,33],'limits':{'node_px':30,'edge_px':24},'candidate_wrap_widths':[180,300,420,540]}
 for split,seed in manifest['seeds'].items():
  cases=build(split,seed);data=(json.dumps(cases,ensure_ascii=False,indent=2)+'\n').encode();(HERE/f'{split}.json').write_bytes(data);manifest['files'][f'{split}.json']={'sha256':hashlib.sha256(data).hexdigest(),'cases':len(cases)}
 (HERE/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(manifest))
