import { expect, it } from 'vitest';
import { DiffOptions, generateSmartDiff, summarizeDiff } from './diffEngine';
import { ChangeType, DiffSegment } from '../types';

// Property-based companion to diffEngine.test.ts. The examples there pin down what
// a good redline looks like; this file asserts the properties that must hold for
// *every* input, over randomly generated Czech-legal-ish text. Both bugs it caught
// while being written — a blank line anchoring the diff onto the wrong paragraph,
// and a region boundary showing an insertion before the deletion it replaces —
// were invisible to the hand-written examples.

const rebuildSource = (segments: DiffSegment[]) =>
  segments
    .filter(s => s.type !== ChangeType.ADDED)
    .map(s => s.originalText ?? s.text)
    .join('');

const rebuildTarget = (segments: DiffSegment[]) =>
  segments
    .filter(s => s.type !== ChangeType.REMOVED)
    .map(s => s.text)
    .join('');

const check = (a: string, b: string, options?: DiffOptions): DiffSegment[] => {
  const segments = generateSmartDiff(a, b, options);
  const problems: string[] = [];

  // Redlining must not alter either document.
  if (rebuildSource(segments) !== a) {
    problems.push(`source rebuilds as ${JSON.stringify(rebuildSource(segments))}`);
  }
  if (rebuildTarget(segments) !== b) {
    problems.push(`target rebuilds as ${JSON.stringify(rebuildTarget(segments))}`);
  }

  segments.forEach((seg, i) => {
    if (!seg.text && !seg.originalText) problems.push(`empty segment at ${i}`);
    if (i > 0 && segments[i - 1].type === seg.type) {
      problems.push(`two ${seg.type} segments in a row at ${i}`);
    }
    if (seg.type === ChangeType.ADDED && segments[i + 1]?.type === ChangeType.REMOVED) {
      problems.push(`insertion before its deletion at ${i}`);
    }
    if (seg.type === ChangeType.ADDED && seg.originalText !== undefined) {
      problems.push(`insertion carries originalText at ${i}`);
    }
    if (seg.type === ChangeType.CASE_CHANGED) {
      // A case change must be the very same characters, bar their case.
      if (seg.originalText === undefined) problems.push(`case change without originalText at ${i}`);
      else if (seg.originalText.toLowerCase() !== seg.text.toLowerCase()) {
        problems.push(`case change is a rewrite at ${i}: ${JSON.stringify([seg.originalText, seg.text])}`);
      }
    }
  });

  if (problems.length) {
    throw new Error(
      `${JSON.stringify(a)} -> ${JSON.stringify(b)} with ${JSON.stringify(options)}\n  ` +
        problems.join('\n  ') +
        `\n  segments: ${JSON.stringify(segments)}`
    );
  }
  return segments;
};

// Deterministic PRNG: a failure has to be reproducible.
let seed = 987654321;
const rnd = () => {
  seed = (seed * 1103515245 + 12345) & 0x7fffffff;
  return seed / 0x7fffffff;
};
const pick = <T,>(items: readonly T[]) => items[Math.floor(rnd() * items.length)];

const WORDS = [
  'smlouva', 'strany', 'dílo', 'cena', 'ÚČET', 'Kč', '1.500', '30. 6. 2025', 'odst', 'a', 'v',
  'Zhotovitel', 'objednatel', '§', '89/2012', 'DPH', 'Sb', 'ne', 'X',
];
const GAPS = [' ', '  ', '   ', '\n', '\n\n', ', ', '; ', '. ', '\t', ' – ', ') ', '\r\n'];
const OPTION_SETS: (DiffOptions | undefined)[] = [
  undefined,
  { ignoreCase: false },
  { ignoreWhitespace: false },
  { ignoreCase: false, ignoreWhitespace: false },
  { timeoutMs: 1 },
  { timeoutMs: 0 },
];

/** Alternating words and separators, so every join is a plausible document. */
const buildParts = (wordCount: number): string[] => {
  const parts: string[] = [];
  for (let i = 0; i < wordCount; i++) {
    parts.push(pick(WORDS));
    if (i < wordCount - 1) parts.push(pick(GAPS));
  }
  return parts;
};

it('holds its invariants under heavy randomised editing', () => {
  for (let round = 0; round < 20000; round++) {
    const source = buildParts(1 + Math.floor(rnd() * 40));
    const target = source.slice();
    const edits = Math.floor(rnd() * 8);
    for (let e = 0; e < edits && target.length; e++) {
      const at = Math.floor(rnd() * target.length);
      switch (Math.floor(rnd() * 7)) {
        case 0: // delete a run
          target.splice(at, 1 + Math.floor(rnd() * 3));
          break;
        case 1: // insert a run
          target.splice(at, 0, ...buildParts(1 + Math.floor(rnd() * 3)));
          break;
        case 2: // replace a word
          target[at] = pick(WORDS);
          break;
        case 3: // recase a word
          target[at] = rnd() < 0.5 ? target[at].toUpperCase() : target[at].toLowerCase();
          break;
        case 4: // respace
          target[at] = pick(GAPS);
          break;
        case 5: {
          // move a word elsewhere
          const [moved] = target.splice(at, 1);
          target.splice(Math.floor(rnd() * (target.length + 1)), 0, moved);
          break;
        }
        default: // extend a word
          target[at] = target[at] + pick(WORDS);
          break;
      }
    }
    check(source.join(''), target.join(''), OPTION_SETS[round % OPTION_SETS.length]);
  }
});

it('never reports content changes when only capitals and spacing move', () => {
  for (let round = 0; round < 3000; round++) {
    const parts = buildParts(1 + Math.floor(rnd() * 30));
    const a = parts.join('');
    const b = parts
      .map(part => {
        if (/^\s+$/.test(part)) return rnd() < 0.5 ? pick([' ', '  ', '\n', '\n\n', '\t']) : part;
        if (!/[\p{L}\p{N}]/u.test(part)) return part;
        return rnd() < 0.4 ? part.toUpperCase() : part;
      })
      .join('');

    const stats = summarizeDiff(check(a, b), a, b);
    const where = `${JSON.stringify(a)} -> ${JSON.stringify(b)}`;
    expect(stats.added, where).toBe(0);
    expect(stats.removed, where).toBe(0);
  }
});
