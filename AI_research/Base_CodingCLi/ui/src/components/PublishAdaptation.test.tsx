import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { PublishAdaptation } from "./PublishAdaptation";
import { publishCheck, type PublishAdaptationResult, type PublishPayload } from "@/lib/publish-text";

vi.mock("@/api/client",()=>({api:vi.fn()}));
const original: PublishPayload = {title:"原来的标题",description:"长".repeat(1844),tags:["科技"],first_comment:{enabled:false,text:"",pin:false}};
const adapted: PublishPayload = {...original,description:"保留测试条件的短摘要。\n\n主要来源：\nhttps://example.com/source"};
const result: PublishAdaptationResult = {payload:adapted,before:original,check:publishCheck("kuaishou",adapted),notes:["完整出处保留在原发布包中。"],references:[{title:"完整来源",url:"https://example.com/source"}],source_description:original.description,references_in_description:1};
function Harness() {
  const [value,setValue]=useState(original);
  return <><label>当前简介<textarea value={value.description} onChange={e=>setValue({...value,description:e.target.value})}/></label><PublishAdaptation platform="kuaishou" renderId="r1" accountUid="owner" value={value} initial={result} onApply={setValue} run={async fn=>{await fn();}} busy={false}/></>;
}
beforeEach(()=>{vi.clearAllMocks();vi.mocked(api).mockImplementation(async(path,options)=>{
  if(path.endsWith("/adapt"))return result;
  if(path.endsWith("/draft") && options?.method==="PUT")return {saved:true};
  if(path.includes("/history/"))return {versions:[{id:1,saved_at:"今天",payload:original}]};
  throw new Error(String(path));
});});
afterEach(cleanup);
it("previews before changing anything, then saves both copies without a publishing call",async()=>{
  render(<Harness/>);
  expect(screen.queryByText("完整出处保留在原发布包中。")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"一键适配快手"}));
  await screen.findByLabelText("平台适配预览");
  expect(screen.getByLabelText("当前简介")).toHaveProperty("value",original.description);
  expect(vi.mocked(api).mock.calls.some(([path])=>path.includes("uploads"))).toBe(false);
  fireEvent.click(screen.getByRole("button",{name:"应用适配并保留原版本"}));
  await waitFor(()=>expect(screen.getByLabelText("当前简介")).toHaveProperty("value",adapted.description));
  const saved=vi.mocked(api).mock.calls.find(([path])=>path.endsWith("/draft"));
  expect(saved?.[1]?.body).toEqual({render_id:"r1",account_uid:"owner",payload:adapted,previous_payload:original});
});
it("refuses to apply a stale preview after further manual edits",async()=>{
  render(<Harness/>);fireEvent.click(screen.getByRole("button",{name:"一键适配快手"}));
  await screen.findByLabelText("平台适配预览");
  fireEvent.change(screen.getByLabelText("当前简介"),{target:{value:"新编辑的内容"}});
  expect(screen.getByRole("button",{name:"应用适配并保留原版本"})).toHaveProperty("disabled",true);
  expect(screen.getByText(/文案已变化/)).toBeTruthy();
});
it("can restore a saved long copy while preserving the current copy",async()=>{
  render(<Harness/>);
  fireEvent.click(screen.getByRole("button",{name:"查看手动版本"}));
  await screen.findByLabelText("发布包手动版本");
  fireEvent.click(screen.getByRole("button",{name:"恢复这个版本并保留当前内容"}));
  await waitFor(()=>expect(vi.mocked(api).mock.calls.some(([path])=>path.endsWith("/draft"))).toBe(true));
});
it("reports a failed read-only preview locally without changing the draft",async()=>{
  vi.mocked(api).mockRejectedValueOnce(new Error("预览暂不可用"));
  render(<Harness/>);fireEvent.click(screen.getByRole("button",{name:"一键适配快手"}));
  expect((await screen.findByRole("alert")).textContent).toContain("预览暂不可用");
  expect(screen.getByLabelText("当前简介")).toHaveProperty("value",original.description);
});
