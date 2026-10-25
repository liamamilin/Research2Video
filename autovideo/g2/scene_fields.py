"""Only explicit screen-text fields are editable; never arbitrary object paths."""
from copy import deepcopy


def editable_fields(scene: dict) -> list[dict]:
    p=scene['props'];c=scene['component'];fields=[]
    def add(path,label,value):fields.append({'path':path,'label':label,'value':value})
    if c in {'title_card','concept_reveal','flow_diagram','big_number'}:
        add('title','场景标题',p.get('title',''))
    if c=='title_card' and 'subtitle' in p:add('subtitle','副标题',p['subtitle'])
    if c=='concept_reveal':
        for i,text in enumerate(p['lines']):add(f'lines.{i}',f'卡片 {i+1}（标题｜说明）',text)
    if c=='big_number':
        add('value','大数字',p['value']);add('label','数字说明',p['label'])
        if 'note' in p:add('note','前提或口径',p['note'])
    if c=='price_change':
        add('title','场景标题',p['title'])
        if 'unit' in p:add('unit','计量口径',p['unit'])
        for i,row in enumerate(p['rows']):
            add(f'rows.{i}.label',f'第 {i+1} 行名称',row['label']);add(f'rows.{i}.before',f'第 {i+1} 行旧值',row['before']);add(f'rows.{i}.after',f'第 {i+1} 行新值',row['after'])
            if 'change' in row:add(f'rows.{i}.change',f'第 {i+1} 行幅度',row['change'])
    if c=='data_chart':
        add('title','场景标题',p['title']);add('unit','单位',p['unit'])
        if 'takeaway' in p:add('takeaway','结尾强调',p['takeaway'])
        for i,text in enumerate(p['categories']):add(f'categories.{i}',f'横轴 {i+1}',text)
        for i,series in enumerate(p['series']):add(f'series.{i}.name',f'系列 {i+1}',series['name'])
    if c=='scatter_chart':
        add('title','场景标题',p['title'])
        for key,label in [('x_label','横轴名称'),('x_unit','横轴单位'),('y_label','纵轴名称'),('y_unit','纵轴单位'),('x_hint','横轴提示'),('y_hint','纵轴提示'),('takeaway','结尾强调')]:
            if key in p:add(key,label,p[key])
        for i,point in enumerate(p['points']):
            add(f'points.{i}.name',f'对象 {i+1}',point['name'])
            if 'note' in point:add(f'points.{i}.note',f'对象 {i+1} 说明',point['note'])
        for i,step in enumerate(p.get('focus_steps',[])):
            if 'caption' in step:add(f'focus_steps.{i}.caption',f'强调 {i+1}',step['caption'])
    if c=='bar_chart':
        for i,text in enumerate(p['categories']):add(f'categories.{i}',f'第 {i+1} 条名称',text)
        add('unit','单位',p['unit'])
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
