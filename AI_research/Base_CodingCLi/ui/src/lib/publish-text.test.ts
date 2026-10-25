import { expect,it } from "vitest";
import { combinedText,publishCheck,validFirstComment } from "./publish-text";
it("counts the actual composed caption including separators and topics at the boundary",()=>{
  const value={title:"标题😀",description:"内".repeat(490),tags:["科普"]};
  expect(publishCheck("kuaishou",value).counts.combined).toBe(500);
  expect(publishCheck("kuaishou",value).valid).toBe(true);
  expect(publishCheck("kuaishou",{...value,description:value.description+"多"}).valid).toBe(false);
  expect(combinedText("douyin","独立标题","简介",["科技"])).toBe("简介 #科技");
  expect(combinedText("kuaishou","标题","标题",[])).toBe("标题");
});
it("uses the comment adapter's UTF-16 counter for Kuaishou without mixing it into the caption",()=>{
  expect(validFirstComment("kuaishou",{enabled:true,text:"😀".repeat(125),pin:false})).toBe(true);
  expect(validFirstComment("kuaishou",{enabled:true,text:"😀".repeat(126),pin:false})).toBe(false);
  expect(validFirstComment("bilibili",{enabled:true,text:"😀".repeat(1000),pin:false})).toBe(true);
});
