"""Prepare explicit editorial changes in an isolated copy; never alter production."""
from pathlib import Path
import ast
import hashlib
import json
import re
import yaml

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
CANDIDATE = Path('/tmp/natural-writing-20261116-soft/candidate')
CONTROL = CANDIDATE.parent / 'control'
inventory = json.loads((HERE / 'inventory.json').read_text())['jobs']
changes = []


def write(rel, text, reason):
    path = CANDIDATE / rel
    if path.read_text() == text:
        return
    path.write_text(text)
    changes.append({'file': rel, 'reason': reason})


def replace(rel, pairs, reason):
    text = (CANDIDATE / rel).read_text()
    for before, after in pairs:
        assert before in text, (rel, before)
        text = text.replace(before, after)
    write(rel, text, reason)


# A research dossier keeps its useful substance without forcing a table of contents.
knowledge = (
    '  选定问题后，用 Markdown 写出能供作者深入讲述的知识底稿，一级标题写选定的问题。\n'
    '  建议从真实现象或具体例子解释原因，把关键过程、前因后果和适用条件讲充分。\n'
    '  例子用于帮助理解；有帮助时可以补充类比、对照或常见误解，内容和篇幅随问题需要安排。\n'
    '  小节标题描述本题的具体内容，组织方式由材料决定。\n'
)
for entry in inventory:
    if not entry['job'].endswith('_illustrated'):
        continue
    rel = entry['job_path']
    text = (CANDIDATE / rel).read_text()
    start = text.index('  选定后按下面的结构写')
    end = text.index('\n  任务：', start)
    text = text[:start] + knowledge + text[end:]
    text = text.replace('保留原有标题、机制、类比和生活例子结构', '围绕问题保留有解释力的机制与生活细节，类比和段落按需要安排')
    write(rel, text, '去掉固定栏目、机制/例子/误解数量和强制人物类比，保留解释深度')

replace('config/video_recipes/illustrated_explainer.yaml', [
    ('  sections: [3, 6]\n  paragraph_units: [60, 160]\n', ''),
    ('    全片只分 4 到 6 节，每节 150 到 300 字。材料里的工序或要点多于 6 个时，挑最能讲明白原理的几个，其余合并或略过。',
     '    建议按理解过程组织章节，给重要的因果、操作和例子足够空间；篇幅随内容需要安排。'),
    ('    每一节对应一张插画，观众会盯着这张图听几十秒，所以每节只讲一件事，并让这件事能画出来：\n    最好是一两个、最多三个普通人在一个具体地方做一件具体的事，手边有一个关键物件（价牌、钱包、机器、一杯水）；\n    实在没有人物的概念，就围绕一个能画出来的物件或现象来讲。同一组人物或物件尽量贯穿全片，让例子推动解释。',
     '    具体物件、可观察现象和实际动作有助于配图与理解。可以直接解释对象本身；人物场景或类比有帮助时再采用。'),
    ('    开头用一个观众身边的问题进入，结尾回到一句观众能带走的话。',
     '    切入点和收束方式可结合本题最值得理解的内容选择。'),
    ('    上游材料里的数字和结论照用；每节最多说一两个关键数字，其余数字不说，不逐条罗列参数、标准号和牌号。',
     '    沿用上游材料的数字和结论，优先解释与本题理解有关的数值及条件，让观众明白它们的意义。'),
    ('    口语化，像对着一个好奇的朋友讲，少用术语；必须用的术语，先用这一节情境里的例子解释它。',
     '    像对着一个好奇的朋友讲；陌生术语可以结合实际现象或操作解释。'),
], '去掉节数、字数、人物和数字配额，视觉具体性改为建议')

replace('core/video/writer.py', [
    ('PROMPT_VERSION = 16', 'PROMPT_VERSION = 17'),
    ('只放一两个最关键的数字，不堆参数清单', '数字有助于说明看点时可以使用，避免堆成参数清单'),
    ('一段口播只念最关键的两三个数字，钱说成观众熟悉的口径（每月多少钱，材料给了人民币就说人民币），其余参数交给画面，但具体的场景、动作和原话要讲出来。',
     '数字优先选对理解有用的，结合实际含义和比较口径解释；钱可以说成观众熟悉的口径（每月多少钱，材料给了人民币就说人民币）。具体的动作、现象和原话往往比概括更有帮助。'),
    ('每条内容跟着一个具体的人、一个具体时刻走（谁、在哪、手里拿着什么、屏幕上跳出什么字），',
     '可以用材料中的具体人物、动作、现象或实际输出帮助理解，'),
    ('每个结论都落到这个场景里发生的事；', '让解释与支持它的细节连起来；'),
    ('整期按节目讲述：开场用一两句话抛出本期最抓人的那一条，再用一句话报出后面还有哪几条，细节留到正文讲。',
     '整期可以从最值得听的具体内容进入，需要帮助观众建立方向时再简短预告。'),
    ('随后逐条展开。两条之间确有关系、对比或递进就用一句话接上；没有就直接切换（比如“再说 Gemini。”），不硬造过渡，不重新打招呼。每条用一句短判断收住，各条措辞不同，不用“最值得记住的是”“这一条的要点是”之类的固定句式。',
     '各项内容可按关联、对比或递进自然衔接；独立内容也可以直接切换。条目讲清后进入下一项，是否补充判断由内容决定。'),
    ('收尾只用一两句话落下本期最重要的一个判断，不逐条复述。',
     '收尾可以留下最有价值的理解或尚值得追问的问题，尽量避免复述已经讲清的内容。'),
    ('整期总标题用本期最值得看的一两条内容来写', '整期总标题围绕本期最值得看的内容来写'),
], '共用作者取消数量配额、人物必备、目录预告及逐条判断收尾')

# These prompts belong only to the in-use specialised episode routes.
episode_topics = {
    'EXPLAINER_EPISODE_PROMPT': ('这期帮助观众理解研究底稿中的科学问题。可以从具体现象建立疑问，用实验、观察、模型或不同尺度的例子解释因果。建议保留研究者做了什么、看到了什么，以及这些材料怎样支持解释。相关背景与例子按理解需要展开。',
                                  '解释章节用 section 组织，标题体现具体内容；讲述顺序与收束方式可随本题的推理安排。'),
    'BUSINESS_EPISODE_PROMPT': ('这期帮助观众理解一门生意怎样运转。具体交易可用来说明客户需求、付款、交付和协作；成本、收益、风险及参与者关系值得展开。可以按实际材料选择业务过程、对比或其它清楚的讲法。',
                              '业务章节用 section 组织，标题体现具体内容；收束可落在本题有依据的经营认识上。'),
    'ARTWORK_EPISODE_PROMPT': ('这期帮助观众读懂作品的表达。构图、材质、声音、创作动作和观看感受可以成为理解的入口；历史背景或相邻作品有助于解释时也可展开。建议让评论与可以辨认的细节相连。',
                             '讲述章节用 section 组织，标题体现具体作品或值得理解的内容；切入和收束可由材料决定。'),
    'CULTURE_CHANGE_EPISODE_PROMPT': ('这期围绕选定的文化问题解释它的来路。代表作品、创作者行动、展示或传播处境可以帮助观众理解现象怎样形成或改变；相关作品之间的联系与差别按本题需要展开。建议把文化认识落在具体细节与背景上。',
                                    '讲述章节用 section 组织，标题体现具体内容；可以按本题的因果、时间或作品关系展开。'),
    'MANUFACTURING_EPISODE_PROMPT': ('这期帮助观众理解产品或部件怎样制造和交付。可以沿实际工序解释工具的作用、材料或结构的变化以及前后依赖；规格、伙伴、对照工艺与交付条件有助于理解时展开。建议保留能说明制造难点的具体过程与结果。',
                                   '制造章节用 section 组织，标题体现具体内容；可以按理解需要选择切入点和收束。'),
}
rel = 'core/video/writer.py'
text = (CANDIDATE / rel).read_text()
for name, (body, structure) in episode_topics.items():
    node = next(n for n in ast.parse(text).body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))
    old = ast.get_source_segment(text, node)
    # Preserve the existing JSON and episode-role contract verbatim.
    first = old.index('"""') + 3
    split = old.index('""" + EPISODE_LEAD', first)
    new = old[:first] + '\n' + body + '\n' + old[split:]
    begin = new.index('OPENING_RULE + """') + len('OPENING_RULE + """')
    end = new.index('""" + ENDING_RULE', begin)
    new = new[:begin] + '\n' + structure + '\n' + new[end:]
    text = text.replace(old, new)
write(rel, text, '专题模板改为解释方向建议，不要求固定主角、步骤顺序和首尾回环')

# Short, domain-specific suggestions retain the subject, useful details and relationships.
directions = {
 'ai_media/daily_opensource_radar': '建议用实际输入、操作和输出解释项目的工作位置与特点，相关机制、参数及衔接方式按理解需要展开。具体任务可以帮助说明用途，切入点与结尾由本题决定。',
 'ai_media/weekly_business_watch': '建议从具体产品、采用或交付案例理解商业变化，关注提供方、使用方、收费、授权及竞争关系怎样影响实际价值。例子、背景和行业解释可以按材料组织。',
 'ai_media/weekly_deep_research': '围绕本期值得深入理解的 AI 问题，建议把机制、实验设置、过程和结果的关系讲充分。具体任务、公式、不同实验与相关案例可以帮助解释，顺序与篇幅按理解需要安排。',
 'ai_media/weekly_model_arena': '建议围绕观众实际关心的模型差异，结合可比的任务和条件解释能力、速度、价格与上下文读数。比较维度、对象与展开顺序由本题材料决定。',
 'company/company_daily': '建议结合具体业务案例解释公司改变了什么，以及客户、伙伴或对手为什么作出不同反应。实际动作、交接、成本和结果有助于理解，背景与其它案例可按需要展开。',
 'company/company_weekly': '围绕本期生意的运转方式，建议把客户需求、收费、交付、协作及经营取舍讲清。具体交易可以帮助理解价值与成本，也可结合材料中的其它案例、条件与关系展开。',
 'consumer/consumer_daily': '建议通过实际操作和结果解释产品或服务的体验变化。准备、耗时、使用条件、维护及后续影响在相关时展开，参数尽量与具体体验相连。',
 'consumer/consumer_weekly': '建议让有意义的方案面对可比的使用需求与条件，结合操作、结果、成本、时间、兼容性及维护解释差别。比较范围与顺序随材料安排，结论来自实际取舍。',
 'culture/culture_daily': '建议用作品可看见或听见的细节解释表达，结合创作动作、材质、构图、声音与感受。背景或相邻作品有助于理解时可以展开，评论尽量有具体依据。',
 'culture/culture_weekly': '围绕本期文化问题，建议结合代表作品、创作与传播处境解释它的来路。相关作品和参与者的联系、差别与行动可以帮助理解，篇幅用于具体细节及有依据的认识。',
 'economy/economy_daily': '建议结合家庭、企业、账单或实际交易说明经济变化的传导：影响落在哪里，各方怎样应对，成本或结果怎样改变。数据、调查与政策背景可以按理解需要展开。',
 'economy/economy_weekly': '围绕本期经济机制，建议讲清规则、成本、激励与参与者行动的关系。钱、商品、报价或交易可以作为具体例子；跨国对照、分组数据与历史背景有帮助时充分展开。',
 'geography/geography_daily': '建议结合具体地点、路线、尺度和可观察过程解释空间问题，讲清地形、水流、道路或设施怎样影响当地活动。地图与生活实例可以帮助观众建立空间关系。',
 'geography/geography_weekly': '围绕本期空间问题，建议结合地点之间的关系、地形、距离、气候或设施解释生活差异。路线、尺度、空间过程和可比地点可以按材料组织，背景有帮助时展开。',
 'history/history_daily': '建议结合人物身份、当时处境、原话、物件与实际行动理解历史。年代、制度、地理和其它人物的回应可以帮助解释，故事顺序与视角按材料选择。',
 'history/history_weekly': '围绕本期历史转折，建议讲清参与者的处境、目标、行动、相互影响与后续变化。文物、原话、事件顺序及制度背景可帮助推理，不同故事的时间与条件尽量交代清楚。',
 'industry/industry_daily': '建议用具体产品、部件、工艺或交付过程解释产业变化，关注工具怎样作用、状态怎样改变以及前后环节的依赖。公司、规模、数字和相关案例可按理解需要展开。',
 'industry/industry_weekly': '围绕本期制造问题，建议结合实际工序解释输入、工具作用、材料或结构变化和交付条件。设备、伙伴、规格与对照可以帮助理解制造难点和重复交付，组织方式由材料决定。',
 'science/science_daily': '建议结合研究对象、实际实验或观测解释本次新增理解，保留操作、观察、比较、尺度与适用情形。具体过程可作为入口，术语和相关背景按理解需要展开。',
 'science/science_weekly': '围绕本期科学问题，建议把现象、条件变化、过程和解释之间的因果讲充分。可以结合具体对象、不同实验、尺度、公式或类比建立理解，例子与顺序随材料安排。',
 'chatgpt_acquisition/t01_product': '面向刚接触 ChatGPT 的观众，建议用材料中的输入、操作和可见输出解释能力与用途。案例可以帮助理解哪些工作得到支持、什么仍需要人处理；是否给使用建议及如何收束，由本题材料决定。',
 'tech/tech_weekly': '围绕本周值得追问的技术问题，建议用实际设计、做法、结果及适用条件解释变化。具体例子、相关机制和对照可以按理解需要展开，作者判断尽量与材料中的细节相连。',
}

for entry in inventory:
    job = entry['job']
    if job not in directions:
        continue
    for column in entry.get('content_columns', []):
        rel = f'config/content_columns/{column}.yaml'
        path = CANDIDATE / rel
        if not path.exists():
            continue
        raw = path.read_text()
        old = yaml.safe_load(raw).get('notes')
        if not old:
            continue
        # Replace only the YAML scalar; preserve all presentation/production settings.
        node = yaml.compose(raw)
        value = next(v for k, v in node.value if k.value == 'notes')
        raw = raw[:value.start_mark.index] + json.dumps(directions[job], ensure_ascii=False) + raw[value.end_mark.index:]
        write(rel, raw, '栏目写作方向改为本领域的内容建议，取消固定路径与首尾要求')

# Research keeps its data-format contract and field definitions. Only the
# narrative directions in prose are softened; no global runtime text filter.
soft_phrases = {
    '每个重点候选从当事人的购买、使用、生产或交付场景进入': '重点候选可以结合当事人的购买、使用、生产或交付场景展开',
    '让同一个例子贯穿价值、资金和交付关系': '具体例子可帮助理解价值、资金和交付关系',
    '让观众跟随同一客户从发起需求到得到结果': '可以结合客户从发起需求到得到结果的过程',
    '相关案例帮助解释当前关系后接回主例子': '相关案例可以帮助解释当前关系',
    '每个解释单元沿一个家庭、一家企业、一张账单、订单或资金展开': '解释单元可以结合家庭、企业、账单、订单或资金展开',
    '每层机制都让主交易前进一步': '具体交易可帮助说明不同机制的作用',
    '再接回这笔钱的去向、承担者与最终选择': '可结合钱的去向、承担者与最终选择理解',
    '结尾连起主路径，简洁说明一个相近用途': '有帮助时可以联系相近用途',
    '结尾回到主问题，写清用整套机制如何重新解释开场现象，以及材料中的相近例子': '可以用相关现象检验和说明这套机制，也可结合材料中的相近例子展开',
    '相邻案例在解释主过程某一步时进入，讲清该步动作与结果后回到主例子': '相邻案例可用于解释相关过程、差别与结果',
    '相邻作品的对应细节帮助理解当前一步，再接回主作品': '相邻作品的对应细节可以帮助理解表达上的联系与差别',
    '相关研究、历史发现和实验承担其中一层解释，再接回主例子': '相关研究、历史发现和实验可以帮助解释不同层次的原因',
    '每层机制、公式或相关实验说明了例子中的哪一步': '机制、公式或相关实验怎样帮助解释问题',
    '先用简短导读说明几个值得讲的候选，再为重点候选保留完整资料段落': '可以简短概览值得讲的候选，并为重点候选保留完整资料段落',
    '开头保留简短导读，重点项目的正文沿一个具体任务充分展开': '可以概览候选项目，重点项目结合实际任务充分展开',
    '开头用简短导读交代本期疑问，正文让一个具体例子有足够展开空间': '可以概述本期疑问，给有解释力的具体例子足够展开空间',
    '开头用简短导读说明本期值得关注的变化': '可以概览本期值得关注的变化',
    '让每次解释把现场向前带一步': '具体现场可以帮助理解相关解释',
    '每次解释让主场景向前一步': '具体场景可以帮助理解相关解释',
    '结尾追到人物或物件的新处境': '材料中的后续处境也可帮助理解变化',
    '结尾继续向前推进': '讲清后续影响',
    '让结尾能向前推进': '帮助理解后续体验',
    '使作者能在同一产品上逐步解释': '为作者解释制造过程提供细节',
    '每项配套硬件解释它改变哪个动作': '配套硬件可以结合它影响的动作解释',
    '再接回主例子': '并说明与本题的联系',
    '再接回原场景': '并结合实际场景理解',
}
for entry in inventory:
    if not entry.get('production_ids') or entry['job'].endswith('_illustrated'):
        continue
    rel = entry['job_path']
    raw = (CANDIDATE / rel).read_text()
    node = yaml.compose(raw)
    val = next((v for k, v in node.value if k.value == 'prompt'), None)
    if val is None:
        continue
    old = val.value
    new = old
    for before, after in soft_phrases.items():
        new = new.replace(before, after)
    if new != old:
        # Existing prompts are literal blocks. Preserve their surrounding YAML.
        replacement = '|\n' + ''.join('  ' + line + '\n' if line else '\n' for line in new.rstrip().splitlines())
        raw = raw[:val.start_mark.index] + replacement + raw[val.end_mark.index:]
        write(rel, raw, '上游取材建议取消固定叙事回环，保留内容信息与解析字段')

rel = 'core/video/manuscript_workflow.py'
text = (CANDIDATE / rel).read_text()
active = {r['job'] for r in json.loads((HERE / 'final-route-captures.json').read_text()) if r['profile']}
node = next(n for n in ast.parse(text).body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'JOB_PROMPTS' for t in n.targets))
replacements = []
for key, call in zip(node.value.keys, node.value.values):
    job = ast.literal_eval(key)
    if job not in active or job not in directions:
        continue
    fields = {kw.arg: kw.value for kw in call.keywords}
    if not ast.literal_eval(fields['draft']):
        continue
    old = ast.get_source_segment(text, call)
    new = old.replace(ast.get_source_segment(text, fields['draft']), repr(directions[job]), 1)
    if ast.literal_eval(fields['episode_lead']):
        new = new.replace(ast.get_source_segment(text, fields['episode_lead']), repr('开场可以从本题最有理解价值的细节进入，主持人介绍按行文自然安排。'), 1)
    new = new.replace(ast.get_source_segment(text, fields['version']), repr(ast.literal_eval(fields['version']) + '-soft-v1'), 1)
    # Readers should report understanding problems, not demand their preferred storyline.
    read = ast.literal_eval(fields['read'])
    revise = ast.literal_eval(fields['revise'])
    for before, after in {
        '用同一件作品': '可结合相关作品细节',
        '把听众带回稿里的同一件作品': '结合相关作品细节帮助听众理解',
        '回到开场那个对象': '结合相关对象或现象',
        '让同一件产品继续往下走': '结合已有的产品或工艺过程',
        '先用底稿里已经发生的一个动作或已有结果，把读者接不上的交易讲顺': '可用底稿中的实际动作或结果解释读者没听懂的关系',
        '先说产品哪里变了，再讲它让下一道工序或交付发生了什么': '产品变化、前后工序与交付的关系可以帮助理解，顺序按上下文安排',
        '先说眼睛或耳朵能辨认的变化，再自然接回原判断': '可用眼睛或耳朵能辨认的变化解释原判断',
    }.items():
        revise = revise.replace(before, after)
    if job == 'chatgpt_acquisition/t01_product':
        read = read.replace('只在一个段落里找听完还拿不定主意的任务选择', '只在一个段落里找尚未讲清的能力或用途').replace('我会怎样选', '我理解的能力与用途')
        revise = revise.replace('直接讲清观众在哪种任务里值得试、拿到产物后还要做哪个决定', '结合本段需要解释的能力、用途或边界，把输入与实际结果的关系讲明白')
    new = new.replace(ast.get_source_segment(text, fields['read']), repr(read), 1)
    new = new.replace(ast.get_source_segment(text, fields['revise']), repr(revise), 1)
    replacements.append((old, new))
for old, new in replacements:
    text = text.replace(old, new, 1)
write(rel, text, '实际启用 profile 改为领域建议，去掉固定开场/回环与必须作选择')

# No rendering, scheduling, publication, schema or archived local files are changed.
manifest = []
for rel in sorted({c['file'] for c in changes}):
    manifest.append({'file': rel,
                     'before_sha256': hashlib.sha256((CONTROL / rel).read_bytes()).hexdigest(),
                     'after_sha256': hashlib.sha256((CANDIDATE / rel).read_bytes()).hexdigest(),
                     'reasons': [c['reason'] for c in changes if c['file'] == rel]})
(HERE / 'soft-candidate.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'files': len(manifest), 'jobs': sum(x['file'].startswith('jobs/') for x in manifest),
                  'columns': sum(x['file'].startswith('config/content_columns/') for x in manifest)}))
