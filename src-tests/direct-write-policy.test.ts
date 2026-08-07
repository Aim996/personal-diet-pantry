import { describe, expect, it } from "vitest";

import {
  classifyDirectWrite,
  directWriteInstruction,
} from "../src/direct-write-policy.js";


describe("v0.7.5 direct write policy", () => {
  it.each([
    "刚啃了根玉米",
    "吃了个玉米",
    "刚吃了一个苹果",
    "炫了两根火腿肠",
    "鸡胸肉吃了100克。",
  ])("routes a completed meal with executable quantity directly: %s", (text) => {
    expect(classifyDirectWrite(text)).toMatchObject({
      kind: "direct",
      domain: "meal",
      action: "record",
    });
  });

  it.each([
    "吃了点花生",
    "尝了几口菜",
    "吃了一些米饭",
    "抓了差不多一把瓜子吃了",
  ])("keeps an open portion in one clarification flow: %s", (text) => {
    expect(classifyDirectWrite(text)).toMatchObject({
      kind: "clarify",
      domain: "meal",
      action: "preview_record",
    });
  });

  it("defaults a bare value to kg only in explicit weighing context", () => {
    expect(classifyDirectWrite("刚称了下106.8")).toMatchObject({
      kind: "direct",
      domain: "weight",
      action: "record",
      defaults: { unit: "kg" },
    });
    expect(classifyDirectWrite("106.8")).toEqual({ kind: "unhandled" });
  });

  it.each([
    "刚买了俩苹果，放冰箱了",
    "买了袋大米",
    "入库两袋速冻水饺",
  ])("routes ordinary stock intake directly without date requirements: %s", (text) => {
    expect(classifyDirectWrite(text)).toMatchObject({
      kind: "direct",
      domain: "pantry",
      action: "add",
    });
  });

  it("keeps plain water on its existing direct route", () => {
    expect(classifyDirectWrite("刚喝了137毫升水")).toMatchObject({
      kind: "direct",
      domain: "water",
      action: "record",
    });
  });

  it.each([
    "刚喝了一盒库存里的燕麦奶",
    "刚吃了一个冰箱里的苹果",
    "喝了一瓶刚买的酸奶",
  ])("keeps explicit inventory consumption on the two-call inventory route: %s", (text) => {
    const decision = classifyDirectWrite(text);
    expect(decision).toMatchObject({
      kind: "direct",
      domain: "meal",
      action: "record",
      route: "inventory_consumption",
    });
    expect(directWriteInstruction(decision)).toMatch(
      /diet_pantry search exactly once.*diet_meal record exactly once.*do not.*deduct/is,
    );
  });

  it.each([
    "明早想吃个苹果",
    "差点啃了根玉米，最后没吃",
    "我老婆吃了个苹果",
    "刚才记上了吗？",
  ])("never upgrades non-user or non-occurred text into a write: %s", (text) => {
    expect(classifyDirectWrite(text)).toEqual({ kind: "unhandled" });
  });

  it("builds one-action instructions instead of confirmation workflows", () => {
    const meal = classifyDirectWrite("刚啃了根玉米");
    expect(directWriteInstruction(meal)).toMatch(
      /diet_meal record exactly once.*do not.*preview.*confirmation/is,
    );

    const weight = classifyDirectWrite("刚称了下106.8");
    expect(directWriteInstruction(weight)).toMatch(
      /default.*kg.*diet_weight record exactly once/is,
    );

    const pantry = classifyDirectWrite("刚买了俩苹果，放冰箱了");
    expect(directWriteInstruction(pantry)).toMatch(
      /diet_pantry add exactly once.*do not ask.*production.*expiry/is,
    );
  });
});
