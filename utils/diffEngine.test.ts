import { describe, expect, it } from 'vitest';
import { generateSmartDiff, summarizeDiff, tokenize } from './diffEngine';
import { ChangeType, DiffSegment } from '../types';

// --- helpers ----------------------------------------------------------------

/** Compact rendering of a redline, for readable assertions. */
const render = (segments: DiffSegment[]): string =>
  segments
    .map(s => {
      if (s.type === ChangeType.ADDED) return `{+${s.text}+}`;
      if (s.type === ChangeType.REMOVED) return `{-${s.text}-}`;
      if (s.type === ChangeType.CASE_CHANGED) return `{~${s.text}~}`;
      return s.text;
    })
    .join('');

/** The source document as the segments describe it. */
const rebuildSource = (segments: DiffSegment[]): string =>
  segments
    .filter(s => s.type !== ChangeType.ADDED)
    .map(s => s.originalText ?? s.text)
    .join('');

/** The target document as the segments describe it. */
const rebuildTarget = (segments: DiffSegment[]): string =>
  segments
    .filter(s => s.type !== ChangeType.REMOVED)
    .map(s => s.text)
    .join('');

const diff = (a: string, b: string) => generateSmartDiff(a, b);

const typesOf = (segments: DiffSegment[]) => segments.map(s => s.type);

/**
 * The properties that must hold for every diff the engine can produce. These are
 * what a "funky" result violates, so every example below is checked against them.
 */
const expectWellFormed = (a: string, b: string, segments: DiffSegment[]) => {
  // 1. Neither document may be altered by being redlined.
  expect(rebuildSource(segments)).toBe(a);
  expect(rebuildTarget(segments)).toBe(b);

  segments.forEach((seg, i) => {
    // 2. No empty segments, and no two neighbours of the same kind (they would
    //    render as two boxes and count as two changes).
    expect(seg.text.length + (seg.originalText?.length ?? 0)).toBeGreaterThan(0);
    if (i > 0) expect(seg.type).not.toBe(segments[i - 1].type);

    // 3. A deletion always precedes the matching insertion, never the reverse.
    if (seg.type === ChangeType.ADDED && i + 1 < segments.length) {
      expect(segments[i + 1].type).not.toBe(ChangeType.REMOVED);
    }

    // 4. originalText is set exactly when the two sides read differently.
    if (seg.type === ChangeType.CASE_CHANGED) {
      expect(seg.originalText).toBeDefined();
      expect(seg.originalText).not.toBe(seg.text);
      expect(seg.originalText!.toLowerCase()).toBe(seg.text.toLowerCase());
    }
    if (seg.type === ChangeType.ADDED) expect(seg.originalText).toBeUndefined();
    if (seg.originalText !== undefined) expect(seg.originalText).not.toBe(seg.text);

    // 5. Ids are what the change navigator scrolls to, so they must be unique.
    expect(segments.filter(s => s.id === seg.id)).toHaveLength(1);
  });

  // 6. Identifying a case change must not also report it as add/remove.
  const caseText = segments
    .filter(s => s.type === ChangeType.CASE_CHANGED)
    .map(s => s.text)
    .join('');
  if (caseText) {
    expect(
      segments.filter(s => s.type === ChangeType.ADDED).some(s => s.text === caseText)
    ).toBe(false);
  }
};

/** Runs the diff and checks the invariants before handing the result back. */
const checked = (a: string, b: string): DiffSegment[] => {
  const segments = diff(a, b);
  expectWellFormed(a, b, segments);
  return segments;
};

// --- tokenizer --------------------------------------------------------------

describe('tokenize', () => {
  it('reproduces the input exactly', () => {
    const samples = [
      'Příliš žluťoučký kůň úpěl ďábelské ódy.',
      'Smlouva\r\n\to dílo   č. j. 15/2024-ABC (dále jen „Smlouva“).',
      '§ 2586 odst. 1 zákona č. 89/2012 Sb.',
      'částka 1.500,50 Kč — 20.1.2024 — 100 %',
      'emoji 🙂 a tabulátor\tkonec',
      '',
      '\n\n\n',
    ];
    for (const sample of samples) {
      expect(tokenize(sample).join('')).toBe(sample);
    }
  });

  it('keeps decimal and date groups in one token but splits punctuation', () => {
    expect(tokenize('1.500,50')).toEqual(['1.500,50']);
    expect(tokenize('20.1.2024')).toEqual(['20.1.2024']);
    expect(tokenize('30. 6. 2025')).toEqual(['30. 6. 2025']);
    expect(tokenize('věta.')).toEqual(['věta', '.']);
    expect(tokenize('(a);')).toEqual(['(', 'a', ')', ';']);
  });

  it('does not glue a number across a sentence boundary', () => {
    // "…v roce 2024." followed by "15 dní…" must stay two sentences, and a
    // numbered list must not read as a date.
    expect(tokenize('roce 2024. 15 dní')).toEqual(['roce', ' ', '2024', '.', ' ', '15', ' ', 'dní']);
    expect(tokenize('1. 2. 3.')).toEqual(['1', '.', ' ', '2', '.', ' ', '3', '.']);
  });
});

// --- letter case ------------------------------------------------------------

describe('letter case', () => {
  it('flags an ALL CAPS word as the same word, not a replacement', () => {
    const segments = checked('Smluvní strany se dohodly.', 'SMLUVNÍ strany se dohodly.');
    expect(render(segments)).toBe('{~SMLUVNÍ~} strany se dohodly.');
    expect(typesOf(segments)).toEqual([ChangeType.CASE_CHANGED, ChangeType.UNCHANGED]);
    expect(segments[0].originalText).toBe('Smluvní');
    expect(summarizeDiff(segments, 'a', 'b')).toMatchObject({
      added: 0,
      removed: 0,
      caseChanged: 1,
      total: 1,
    });
  });

  it('flags every re-cased word in a line that is otherwise untouched', () => {
    const segments = checked('smlouva o dílo', 'Smlouva o DÍLO');
    expect(render(segments)).toBe('{~Smlouva~} o {~DÍLO~}');
  });

  it('flags case changes inside a paragraph that is otherwise unchanged', () => {
    const a = 'První odstavec.\n\nDruhý odstavec zmiňuje pojem smlouva.\n\nTřetí odstavec.';
    const b = 'První odstavec.\n\nDruhý odstavec zmiňuje pojem SMLOUVA.\n\nTřetí odstavec.';
    const segments = checked(a, b);
    expect(render(segments)).toBe(
      'První odstavec.\n\nDruhý odstavec zmiňuje pojem {~SMLOUVA~}.\n\nTřetí odstavec.'
    );
  });

  it('underlines a re-cased phrase in one piece', () => {
    const segments = checked('Smluvní strany potvrzují.', 'SMLUVNÍ STRANY potvrzují.');
    expect(render(segments)).toBe('{~SMLUVNÍ STRANY~} potvrzují.');
    expect(segments[0].originalText).toBe('Smluvní strany');
  });

  it('handles a case change next to a real edit', () => {
    const segments = checked(
      'Prodávající dodá zboží kupujícímu.',
      'PRODÁVAJÍCÍ odešle zboží kupujícímu.'
    );
    expect(render(segments)).toBe('{~PRODÁVAJÍCÍ~} {-dodá-}{+odešle+} zboží kupujícímu.');
  });

  it('does not confuse a case change with a different word', () => {
    const segments = checked('dodavatel', 'DODAVATELE');
    expect(render(segments)).toBe('{-dodavatel-}{+DODAVATELE+}');
  });

  it('can be turned off, and then reports case as a replacement', () => {
    const a = 'Smluvní strany';
    const b = 'SMLUVNÍ strany';
    const segments = generateSmartDiff(a, b, { ignoreCase: false });
    expectWellFormed(a, b, segments);
    expect(render(segments)).toBe('{-Smluvní-}{+SMLUVNÍ+} strany');
  });
});

// --- whitespace -------------------------------------------------------------

describe('whitespace', () => {
  it('ignores re-spacing and re-wrapping', () => {
    for (const [a, b] of [
      ['a  b', 'a b'],
      ['První věta. Druhá věta.', 'První věta.\nDruhá věta.'],
      ['řádek\r\ndalší', 'řádek\ndalší'],
      ['  odsazený text', 'odsazený text'],
      ['text', 'text\n'],
    ]) {
      const segments = checked(a, b);
      expect(render(segments)).toBe(b);
      expect(summarizeDiff(segments, a, b)).toMatchObject({ total: 0, formattingOnly: true });
    }
  });

  it('reports a genuinely empty diff as unchanged, not as formatting', () => {
    const segments = checked('stejný text', 'stejný text');
    expect(summarizeDiff(segments, 'stejný text', 'stejný text')).toMatchObject({
      total: 0,
      formattingOnly: false,
    });
  });
});

// --- redline quality --------------------------------------------------------

describe('redline quality', () => {
  it('puts the deletion before the insertion', () => {
    expect(render(checked('Dodavatel dodá zboží.', 'Prodávající dodá zboží.'))).toBe(
      '{-Dodavatel-}{+Prodávající+} dodá zboží.'
    );
  });

  it('reports an inserted paragraph as one insertion', () => {
    const segments = checked('Odst 1.\n\nOdst 3.', 'Odst 1.\n\nOdst 2.\n\nOdst 3.');
    expect(render(segments)).toBe('Odst 1.\n\n{+Odst 2.\n\n+}Odst 3.');
    expect(summarizeDiff(segments, '', '').added).toBe(1);
  });

  it('reports a deleted paragraph as one deletion', () => {
    const segments = checked('Odst 1.\n\nOdst 2.\n\nOdst 3.', 'Odst 1.\n\nOdst 3.');
    expect(render(segments)).toBe('Odst 1.\n\n{-Odst 2.\n\n-}Odst 3.');
  });

  it('anchors on the untouched sentence of a rewritten paragraph', () => {
    const a = 'Cena je 100 Kč. Splatnost je 30 dní. Smluvní pokuta se neuplatní.';
    const b = 'Cena je 200 Kč. Splatnost je 30 dní. Smluvní pokuta se neuplatní.';
    expect(render(checked(a, b))).toBe(
      'Cena je {-100-}{+200+} Kč. Splatnost je 30 dní. Smluvní pokuta se neuplatní.'
    );
  });

  it('does not shred a rewritten list into per-item fragments', () => {
    const segments = checked('body a, b, c a d', 'body x, y, z a w');
    expect(render(segments)).toBe('body {-a, b, c-}{+x, y, z+} a {-d-}{+w+}');
  });

  it('keeps a meaningful unchanged word between two edits', () => {
    const segments = checked(
      'Smluvní strany se dohodly na následujícím.',
      'Strany se zavazují k tomuto.'
    );
    expect(render(segments)).toContain(' se ');
  });

  it('redlines only the punctuation that actually changed', () => {
    expect(render(checked('text (a) další', 'text (a); další'))).toBe('text (a){+;+} další');
  });

  it('replaces a changed amount as a whole number', () => {
    expect(render(checked('částka 1.500,50 Kč', 'částka 1.600,50 Kč'))).toBe(
      'částka {-1.500,50-}{+1.600,50+} Kč'
    );
  });

  it('replaces a moved deadline as a whole date', () => {
    expect(render(checked('dílo do 30. 6. 2025 včetně', 'dílo do 31. 12. 2025 včetně'))).toBe(
      'dílo do {-30. 6. 2025-}{+31. 12. 2025+} včetně'
    );
  });

  it('handles an insertion at the very end', () => {
    expect(render(checked('Příliš žluťoučký kůň úpěl ďábelské ódy.', 'Příliš žluťoučký kůň úpěl ďábelské ódy na měsíci.'))).toBe(
      'Příliš žluťoučký kůň úpěl ďábelské ódy{+ na měsíci+}.'
    );
  });

  it('handles empty documents in both directions', () => {
    expect(diff('', '')).toEqual([]);
    expect(render(checked('', 'nový text'))).toBe('{+nový text+}');
    expect(render(checked('starý text', ''))).toBe('{-starý text-}');
  });

  it('replaces wholly unrelated text in one block', () => {
    const segments = checked('alfa beta gama', 'jedna dva tri');
    expect(typesOf(segments)).toEqual([ChangeType.REMOVED, ChangeType.ADDED]);
  });
});

// --- robustness -------------------------------------------------------------

describe('robustness', () => {
  const CORPUS = [
    ['', ''],
    ['a', 'a'],
    ['a', ''],
    ['', 'a'],
    ['a b c', 'c b a'],
    ['Alfa. Beta. Gama.', 'Gama. Beta. Alfa.'],
    ['smlouva o smlouvě budoucí o dílo', 'smlouva o dílo'],
    ['§ 2586 odst. 1', '§ 2586 odst. 2'],
    ['ÚČASTNÍK řízení, jenž podal návrh', 'účastník řízení, který podal NÁVRH'],
    ['\n\n\n', '\n'],
    ['   ', ''],
    ['a\tb', 'a b'],
    ['🙂 emoji test', '🙃 emoji test'],
    ['Ⅻ MMXXIV', 'ⅻ mmxxiv'],
    ['řádek 1\nřádek 2\nřádek 3', 'řádek 1\nřádek 3\nřádek 2'],
    ['x'.repeat(500), 'y'.repeat(500)],
  ] as const;

  it('holds its invariants across a hand-picked corpus', () => {
    for (const [a, b] of CORPUS) {
      expectWellFormed(a, b, diff(a, b));
      expectWellFormed(b, a, diff(b, a));
      expectWellFormed(a, a, diff(a, a));
    }
  });

  // Randomised inputs are covered far more thoroughly in diffEngine.fuzz.test.ts.

  it('stays responsive on long, wholly different documents', () => {
    const words: string[] = [];
    for (let i = 0; i < 8000; i++) words.push('slovo' + (i % 500));
    const a = words.join(' ');
    const b = words.slice().reverse().join(' ');

    const started = Date.now();
    const segments = generateSmartDiff(a, b, { timeoutMs: 250 });
    const elapsed = Date.now() - started;

    expectWellFormed(a, b, segments);
    // The old engine spent ~40 s here and froze the tab.
    expect(elapsed).toBeLessThan(5000);
  });

  it('spends its time budget once for the whole comparison, not per region', () => {
    // Eight anchored paragraphs, each followed by a long paragraph that has
    // nothing in common with its counterpart: eight separate expensive word
    // diffs. With a per-diff budget this would cost 8 × timeoutMs.
    const sourceParas: string[] = [];
    const targetParas: string[] = [];
    const unique = (salt: string) =>
      Array.from({ length: 2000 }, (_, i) => `${salt}${i}`).join(' ');
    for (let i = 0; i < 8; i++) {
      sourceParas.push(`Nezměněný odstavec číslo ${i}`, unique('alfa'));
      targetParas.push(`Nezměněný odstavec číslo ${i}`, unique('beta'));
    }
    const a = sourceParas.join('\n\n');
    const b = targetParas.join('\n\n');

    const started = Date.now();
    const segments = generateSmartDiff(a, b, { timeoutMs: 200 });
    const elapsed = Date.now() - started;

    expectWellFormed(a, b, segments);
    expect(elapsed).toBeLessThan(1200);
  });

  it('stays fast on a long document with a few edits', () => {
    const words: string[] = [];
    for (let i = 0; i < 8000; i++) words.push('slovo' + (i % 500));
    const a = words.join(' ');
    const b = words.map((w, i) => (i % 97 === 0 ? 'zmena' + i : w)).join(' ');

    const started = Date.now();
    const segments = generateSmartDiff(a, b);
    const elapsed = Date.now() - started;

    expectWellFormed(a, b, segments);
    expect(elapsed).toBeLessThan(2000);
    expect(summarizeDiff(segments, a, b).added).toBeGreaterThan(50);
  });
});
