/**
 * How verdicts are ordered and described in the UI.
 *
 * Severity order is not alphabetical and not the enum's order: it is the order a
 * reviewer needs to work through. Failures first because they block a listing,
 * abstentions next because they are the ones needing a person, passes last because they
 * need nothing. An abstention sitting below a pass would bury the cases this system
 * exists to surface.
 */

import type { RuleResult, Verdict } from "./api";

export const SEVERITY: Verdict[] = ["FAIL", "NEEDS_REVIEW", "PASS"];

export const VERDICT_LABEL: Record<Verdict, string> = {
  FAIL: "Fails",
  NEEDS_REVIEW: "Needs review",
  PASS: "Passes",
};

/** Why a rule declined to decide, in words rather than an enum value. */
export const ABSTENTION_REASON: Record<string, string> = {
  low_extraction_confidence: "the field this rule reads was not read confidently enough",
  field_missing: "the specification does not contain the field this rule reads",
  no_relevant_clause_retrieved: "no clause relevant enough to judge against was retrieved",
  citation_unverified: "the cited clause did not support the verdict on checking",
  judge_uncertain: "the judgement was not confident enough to report",
  rule_error: "the rule could not be evaluated",
};

/**
 * Rules whose failure always goes to a person, mirroring ALLERGEN_SENSITIVE in
 * specguard.guardrails.verdicts. Duplicated deliberately and narrowly: the UI needs to
 * mark these, and inventing an endpoint to ask the backend which rules are life-critical
 * would be a lot of machinery for two strings that have not changed since M4.
 */
const ESCALATED = new Set(["ALLERGEN_EMPHASIS", "MANDATORY_FIELDS"]);

export function needsHumanReview(result: RuleResult): boolean {
  return result.verdict === "FAIL" && ESCALATED.has(result.rule_id);
}

export function groupBySeverity(results: RuleResult[]): [Verdict, RuleResult[]][] {
  const groups: [Verdict, RuleResult[]][] = [];
  for (const verdict of SEVERITY) {
    const group = results
      .filter((result) => result.verdict === verdict)
      // Escalated failures lead their group; the rest keep a stable alphabetical order
      // so a report does not reshuffle between runs.
      .sort((a, b) => {
        const escalation = Number(needsHumanReview(b)) - Number(needsHumanReview(a));
        return escalation !== 0 ? escalation : a.rule_id.localeCompare(b.rule_id);
      });
    // An empty group is a heading with nothing under it, which reads as a defect.
    if (group.length > 0) groups.push([verdict, group]);
  }
  return groups;
}

/** "ALLERGEN_EMPHASIS" reads as "Allergen emphasis" in a list a person is scanning. */
export function ruleTitle(ruleId: string): string {
  const words = ruleId.toLowerCase().replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
