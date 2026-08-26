import { describe, expect, it } from "vitest";

import type { RuleResult } from "./api";
import { groupBySeverity, needsHumanReview, ruleTitle } from "./verdicts";

function result(rule_id: string, verdict: RuleResult["verdict"]): RuleResult {
  return {
    rule_id,
    verdict,
    citations: [],
    rationale: "",
    suggested_fix: null,
    confidence: 0.9,
    abstention_reason: verdict === "NEEDS_REVIEW" ? "judge_uncertain" : null,
    metrics: {},
    llm_usage: [],
    duration_ms: 0,
    kind: "rag",
    langsmith_run_id: null,
  };
}

describe("groupBySeverity", () => {
  it("orders failures, then abstentions, then passes", () => {
    // Not alphabetical and not the enum's order: it is the order a reviewer works
    // through. An abstention below a pass would bury the cases needing a person.
    const groups = groupBySeverity([
      result("ORIGIN_DECLARATION", "PASS"),
      result("LEGAL_NAME_AND_QUID", "NEEDS_REVIEW"),
      result("NUTRITION_PER_100", "FAIL"),
    ]);
    expect(groups.map(([verdict]) => verdict)).toEqual(["FAIL", "NEEDS_REVIEW", "PASS"]);
  });

  it("omits a group with nothing in it", () => {
    const groups = groupBySeverity([result("ORIGIN_DECLARATION", "PASS")]);
    expect(groups).toHaveLength(1);
  });

  it("leads a group with the failures that need a person", () => {
    const groups = groupBySeverity([
      result("ORIGIN_DECLARATION", "FAIL"),
      result("ALLERGEN_EMPHASIS", "FAIL"),
    ]);
    expect(groups[0]![1].map((r) => r.rule_id)).toEqual([
      "ALLERGEN_EMPHASIS",
      "ORIGIN_DECLARATION",
    ]);
  });
});

describe("needsHumanReview", () => {
  it("escalates an allergen failure", () => {
    expect(needsHumanReview(result("ALLERGEN_EMPHASIS", "FAIL"))).toBe(true);
    expect(needsHumanReview(result("MANDATORY_FIELDS", "FAIL"))).toBe(true);
  });

  it("does not escalate an allergen rule that passed", () => {
    expect(needsHumanReview(result("ALLERGEN_EMPHASIS", "PASS"))).toBe(false);
  });

  it("does not escalate an unrelated failure", () => {
    expect(needsHumanReview(result("ORIGIN_DECLARATION", "FAIL"))).toBe(false);
  });
});

describe("ruleTitle", () => {
  it("reads as a phrase rather than an enum value", () => {
    expect(ruleTitle("NUTRITION_CLAIM_CONDITIONS")).toBe("Nutrition claim conditions");
  });
});
