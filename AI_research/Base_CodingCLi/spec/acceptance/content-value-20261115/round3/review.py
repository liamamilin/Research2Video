"""One fresh-context, label-blinded model reading per channel; advisory only."""
import json
import secrets
import sys

import pilot


def main(channel):
    pilot.PHASE = 'blind_reading'
    out, _, cfg, Client = pilot.setup(channel)
    from pydantic import BaseModel
    from core.video.adapt.common import call_json
    if (out/'blind-reading.json').exists():
        return
    arms = [p.stem.split('-')[0] for p in sorted(out.glob('?-script.txt'))]
    secrets.SystemRandom().shuffle(arms)
    labels = {f'稿{i+1}':arm for i,arm in enumerate(arms)}
    pilot.save(out/'blind-labels.json',labels)
    if len(arms)<2:
        pilot.save(out/'blind-reading.json', {'readings':[], 'preference':[list(labels)],
            'comparison':'本组未形成不同材料条件下的两稿，不调用模型强行比较同一稿。',
            'uncertainty':'只能记录本次检索未形成可用补材，无法判断补材对文稿的效果。', 'status':'not_comparable'})
        print(json.dumps({'channel':channel,'status':'not_comparable'},ensure_ascii=False),flush=True)
        return
    class Reading(BaseModel):
        label: str
        takeaway: str
        strongest_quote: str
        strongest_reason: str
        weakest_quote: str
        weakest_reason: str
    class Verdict(BaseModel):
        readings: list[Reading]
        preference: list[list[str]]
        comparison: str
        uncertainty: str
    client = Client(cfg)
    try:
        prompt = '''你第一次听这些完整视频口播，不知道它们来自什么流程。面向尽可能广、同时有实际消费/工作决策价值的中文观众，比较谁更值得听完、听完能多理解什么。读完整篇，不能只看标题或开头；参数更多不等于价值更高，受众更广也不等于表达更泛。各稿题材可能不同，分别讲清题材吸引力与整稿讲述效果；同题稿比较观众理解或决策的真实差异。判断作者推论是否跨过了材料和正文已给的解释，但不要外查事实。引用各稿原句说明保留项与主要问题，差不多可并列，不强求分胜负。只输出 JSON：readings（label,takeaway,strongest_quote,strongest_reason,weakest_quote,weakest_reason），preference（从最好到最差的并列编号数组），comparison（整体取舍，具体到段落内容），uncertainty（仅凭此批文本还不能确认什么）。'''
        body = json.dumps({'channel':channel,'drafts':{label:(out/f'{arm}-script.txt').read_text()
                                                     for label,arm in labels.items()}},ensure_ascii=False)
        verdict = call_json(client,prompt,body,Verdict,temperature=0.2)
        flattened = [label for group in verdict.preference for label in group]
        if len(flattened)!=len(labels) or set(flattened)!=set(labels) or {r.label for r in verdict.readings}!=set(labels):
            raise ValueError('Blind reading label mismatch')
        for reading in verdict.readings:
            text = (out/f'{labels[reading.label]}-script.txt').read_text()
            for quote in (reading.strongest_quote,reading.weakest_quote):
                if quote and quote not in text:
                    raise ValueError('Reader quote not verbatim; preserve response and do not silently accept')
        pilot.save(out/'blind-reading.json',verdict.model_dump())
        print(json.dumps({'channel':channel,'preference':[[labels[x] for x in group] for group in verdict.preference]},ensure_ascii=False),flush=True)
    finally:
        client.close()


if __name__=='__main__':
    main(sys.argv[1])
