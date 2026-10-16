"""Only explicit screen-text fields are editable; never arbitrary object paths."""
from copy import deepcopy


def editable_fields(scene: dict) -> list[dict]:
    p=scene['props'];c=scene['component'];fields=[]
    def add(path,label,value):fields.append({'path':path,'label':label,'value':value})
    if c in {'title_card','concept_reveal','flow_diagram'}:
        add('title','场景标题',p.get('title',''))
    if c=='title_card' and 'subtitle' in p:add('subtitle','副标题',p['subtitle'])
    if c=='concept_reveal':
        for i,text in enumerate(p['lines']):add(f'lines.{i}',f'卡片 {i+1}（标题｜说明）',text)
    if c=='comparison':
        for side,name in [('left','左侧'),('right','右侧')]:
            add(f'{side}.label',name+'标题',p[side]['label'])
            for i,text in enumerate(p[side]['items']):add(f'{side}.items.{i}',f'{name}要点 {i+1}',text)
    if c=='flow_diagram':
        add('takeaway','结尾说明',p.get('takeaway',''))
        for n in p['nodes']:add(f"nodes.{n['id']}.label",'节点 '+n['label'],n['label'])
        for e in p['edges']:add(f"edges.{e['id']}.label",f"连接 {e['from']} → {e['to']}",e['label'])
    return fields


def apply_changes(scene: dict, changes: dict) -> dict:
    allowed={f['path'] for f in editable_fields(scene)}
    if not set(changes).issubset(allowed):raise ValueError('只能修改当前场景已提供的文字字段。')
    updated=deepcopy(scene);p=updated['props']
    for path,text in changes.items():
        if not isinstance(text,str) or '\x00' in text:raise ValueError('画面文字无效。')
        parts=path.split('.')
        if parts[0] in {'nodes','edges'}:
            next(x for x in p[parts[0]] if x['id']==parts[1])[parts[2]]=text
        else:
            node=p
            for key in parts[:-1]:node=node[int(key)] if isinstance(node,list) else node[key]
            if isinstance(node,list):node[int(parts[-1])]=text
            else:node[parts[-1]]=text
    return updated
