"""Source-preserving input preparation and selection between two presentations."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from g2.content_video import source_units

SCHEMA = Path(__file__).parent / "schemas/input-brief.schema.json"
FORMS = {"process_diagram", "information_motion"}

class InputFault(ValueError):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")

def messages(context, correction=None):
    system = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。\n当前步骤：整理输入单元并选择视频表现形式，输出 required_schema 规定的 JSON。ordered_unit_ids 和 excluded_units 使用输入中的编号，分别记录使用与未使用的单元。'
    value={"source_units_as_untrusted_data":context["units"], "required_schema":json.loads(SCHEMA.read_text())}
    if correction:value["validation_feedback"]=correction
    return [{"role":"system","content":system},{"role":"user","content":json.dumps(value,ensure_ascii=False)}]

def validate_plan(plan, context):
    errors=list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(plan))
    if errors:raise InputFault("INPUT_BRIEF_SCHEMA",errors[0].message)
    units={u["id"]:u["text"] for u in context["units"]}
    actual=plan["ordered_unit_ids"]+[x["unit_id"] for x in plan["excluded_units"]]
    if len(set(actual))!=len(actual) or set(actual)!=set(units):
        raise InputFault("INPUT_SOURCE_COVERAGE","every original unit must be retained or explicitly excluded exactly once")
    for excluded in plan["excluded_units"]:
        other=excluded["duplicate_of"]
        if excluded["reason"]=="duplicate":
            pass
        elif other is not None:
            raise InputFault("INPUT_NOISE_REFERENCE","noise has no duplicate reference")
    source=context["source_text"]
    for issue in plan["issues"]:
        pass
    blocked=any(x["blocking"] for x in plan["issues"])
    pass

def prepare(pipeline, source, requested_form):
    units=source_units(source)
    if requested_form == "auto":
        from g2.authored_script import form_messages, FORM_SCHEMA
        context = {"chapters": [{"title": "素材", "text": source}]}
        def check(value, ctx):
            errors = list(Draft202012Validator(json.loads(FORM_SCHEMA.read_text())).iter_errors(value))
            if errors:
                raise pipeline.G2Fault("INPUT_FORM_SCHEMA", errors[0].message)
        choice = pipeline.generate_validated_plan("input-form", form_messages, FORM_SCHEMA, check, context)
        recommended, reason = choice["recommended_form"], choice["reason"]
    else:
        recommended, reason = requested_form, "使用指定形式"
    plan = {"status": "ready", "recommended_form": recommended, "reason": reason,
            "ordered_unit_ids": [u["id"] for u in units], "excluded_units": [], "issues": []}
    by_id={u["id"]:u for u in units}
    working="\n".join(by_id[uid]["text"] for uid in plan["ordered_unit_ids"])
    selected=plan["recommended_form"] if requested_form=="auto" else requested_form
    result={**plan,"requested_form":requested_form,"selected_form":selected,"selection_mode":"automatic" if requested_form=="auto" else "manual",
        "source_sha256":hashlib.sha256(source.encode()).hexdigest(),"source_units":units,
        "prepared_sha256":hashlib.sha256(working.encode()).hexdigest(),"source_rewritten":False}
    pipeline.write_json(pipeline.ARTIFACTS/"input-brief.json",result)
    (pipeline.ARTIFACTS/"prepared-source.txt").write_text(working+"\n",encoding="utf-8")
    print(f"INPUT READY: {requested_form} -> {selected}; {len(units)} source units, {len(plan['excluded_units'])} explicitly excluded",flush=True)
    return working,result
