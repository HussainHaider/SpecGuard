/**
 * Locate a cited span inside the clause it was quoted from.
 *
 * The span cannot be found with a plain `indexOf`. The text a rule quoted came from a
 * re-wrapped copy of the clause, so line breaks and runs of spaces differ from the
 * stored text even when the words are identical — which is exactly why the backend's
 * verification pass matches on whitespace-normalised text too.
 *
 * So the search is done on a normalised copy while an index map is kept back to the
 * original, and the slice returned is real source text rather than a normalised
 * approximation of it. A paraphrase still fails to match, which is the point: the
 * highlight is evidence, and highlighting something the rule did not actually quote
 * would be worse than highlighting nothing.
 */

export interface Highlighted {
  before: string;
  match: string;
  after: string;
  found: boolean;
}

function normalise(text: string): { value: string; map: number[] } {
  const characters: string[] = [];
  const map: number[] = [];
  let pendingSpace = false;

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index]!;
    if (/\s/.test(character)) {
      pendingSpace = characters.length > 0;
      continue;
    }
    if (pendingSpace) {
      characters.push(" ");
      map.push(index);
      pendingSpace = false;
    }
    characters.push(character.toLowerCase());
    map.push(index);
  }
  return { value: characters.join(""), map };
}

export function highlight(text: string, span: string): Highlighted {
  const trimmed = span.trim();
  if (!text || !trimmed) return { before: text, match: "", after: "", found: false };

  const haystack = normalise(text);
  const needle = normalise(trimmed);
  if (!needle.value) return { before: text, match: "", after: "", found: false };

  const at = haystack.value.indexOf(needle.value);
  if (at === -1) return { before: text, match: "", after: "", found: false };

  const start = haystack.map[at]!;
  // The end index maps from the last matched character, so the slice includes it.
  const end = haystack.map[at + needle.value.length - 1]! + 1;

  return {
    before: text.slice(0, start),
    match: text.slice(start, end),
    after: text.slice(end),
    found: true,
  };
}
