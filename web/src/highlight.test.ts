import { describe, expect, it } from "vitest";

import { highlight } from "./highlight";

/**
 * The highlight is evidence, not decoration. These pin the two properties that matter:
 * it survives the whitespace differences a re-wrapped quote introduces, and it refuses
 * to match text the rule did not actually quote.
 */
describe("highlight", () => {
  const clause = "The name of the food shall be its legal name.\n  In the absence of such a name…";

  it("finds a span quoted verbatim", () => {
    const result = highlight(clause, "shall be its legal name");
    expect(result.found).toBe(true);
    expect(result.match).toBe("shall be its legal name");
    expect(result.before + result.match + result.after).toBe(clause);
  });

  it("finds a span whose whitespace was re-wrapped", () => {
    // The text handed to the model is re-wrapped, so a quote comes back with different
    // line breaks. The backend's verification pass normalises whitespace too.
    const result = highlight(clause, "legal name.\n\n   In the absence");
    expect(result.found).toBe(true);
    expect(result.match).toBe("legal name.\n  In the absence");
  });

  it("returns the source text, not a normalised copy", () => {
    const result = highlight(clause, "IN THE ABSENCE OF SUCH A NAME");
    expect(result.match).toBe("In the absence of such a name");
  });

  it("does not match a paraphrase", () => {
    // Highlighting something the rule did not quote would be worse than highlighting
    // nothing: the panel exists to show what the verdict actually rested on.
    const result = highlight(clause, "the food must carry its lawful designation");
    expect(result.found).toBe(false);
    expect(result.before).toBe(clause);
  });

  it("handles an empty span without claiming a match", () => {
    expect(highlight(clause, "   ").found).toBe(false);
  });

  it("handles an empty clause", () => {
    expect(highlight("", "anything").found).toBe(false);
  });
});
