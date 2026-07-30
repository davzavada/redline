import * as Diff from 'diff';
import { ChangeType, DiffSegment } from '../types';

// =============================================================================
// Redline engine
// =============================================================================
// The comparison runs in three passes, coarse to fine:
//
//   1. paragraphs — a paragraph that occurs once on each side and reads the same
//                   anchors the diff, so an inserted paragraph is reported as a
//                   whole inserted paragraph instead of drifting across the
//                   boundary of the next one.
//   2. sentences  — same idea inside a rewritten paragraph, and it keeps the
//                   expensive word pass working on small windows.
//   3. words      — the actual redline, on Unicode-aware tokens.
//
// Every pass compares *normalized* tokens: letter case is ignored (a word that
// only changed case is the same word) and any run of whitespace matches any
// other (reflowing text is not an edit). Wherever the two sides really differ
// the source wording is preserved in `originalText`, so both documents can be
// rebuilt from the segments exactly — see the invariants asserted in
// diffEngine.test.ts.

export interface DiffOptions {
  /**
   * Treat words that differ only in letter case ("smlouva" / "SMLOUVA") as the
   * same word, reported as ChangeType.CASE_CHANGED rather than as a
   * deletion + insertion. Default: true.
   */
  ignoreCase?: boolean;
  /**
   * Treat any run of whitespace as equivalent to any other, so re-wrapping or
   * re-indenting text produces no redline. Default: true.
   */
  ignoreWhitespace?: boolean;
  /**
   * Wall-clock budget in ms for one whole comparison. Two long, wholly unrelated
   * texts can otherwise keep the exact (Myers) diff busy for tens of seconds and
   * lock up the UI; whatever is still unresolved when the budget runs out is
   * reported as one replacement instead. Default: 1000.
   */
  timeoutMs?: number;
}

type Options = Required<DiffOptions>;

const DEFAULT_OPTIONS: Options = {
  ignoreCase: true,
  ignoreWhitespace: true,
  timeoutMs: 1000,
};

/**
 * Resolved options plus the deadline shared by every pass of one comparison — the
 * budget is for the whole run, not for each of the (recursively many) diffs it
 * fans out into.
 */
interface Ctx extends Options {
  deadline: number;
}

const remainingBudget = (ctx: Ctx): number => Math.max(1, ctx.deadline - Date.now());

// -----------------------------------------------------------------------------
// Tokenization
// -----------------------------------------------------------------------------
// A token is one of:
//   * a spaced Czech date — "30. 6. 2025" — as a single unit, so moving a
//     deadline redlines the date and not "30. 6" with a stray ". 2025." left
//     dangling behind it;
//   * a word: Unicode letters/digits/underscore, keeping grouped numbers such
//     as "1.500,50" or "20.1.2024" whole for the same reason;
//   * a run of whitespace;
//   * a single punctuation character. Punctuation is deliberately *not* grouped
//     into runs: a comma added after ")" should redline the comma, not "), ".
//
// The alternatives cover every code point, so tokenize(t).join('') === t.
const TOKEN_RE =
  /\p{N}{1,2}\.[ \u00A0]?\p{N}{1,2}\.[ \u00A0]?\p{N}{2,4}(?![\p{L}\p{N}])|[\p{L}\p{N}_]+(?:[.,]\p{N}+)*|\s+|[^\p{L}\p{N}_\s]/gu;

export const tokenize = (text: string): string[] => text.match(TOKEN_RE) ?? [];

const WHITESPACE_RE = /^\s+$/;
const isWhitespace = (token: string) => WHITESPACE_RE.test(token);
const isBlank = (text: string) => text === '' || WHITESPACE_RE.test(text);

/** Marker standing in for "some whitespace" when whitespace is being ignored. */
const WHITESPACE_KEY = '\u0000ws';

/**
 * Comparison key for a token. Diffing keys with `===` rather than passing a
 * comparator to jsdiff is both faster (no callback per probe, and Myers probes a
 * lot) and normalizes in exactly one place.
 */
const tokenKey = (token: string, opts: Options): string => {
  if (isWhitespace(token)) return opts.ignoreWhitespace ? WHITESPACE_KEY : token;
  return opts.ignoreCase ? token.toLowerCase() : token;
};

const WORD_CHAR_RE = /[\p{L}\p{N}_]/u;

/**
 * Whether an unchanged run is worth anchoring the diff on: it has to carry at
 * least one letter or digit. A blank line, ", " or " – " matching in the middle
 * of two rewritten passages is no evidence that they have anything in common —
 * anchoring there is what shreds a redline into "swiss cheese". Even a
 * one-letter word ("a", "v", "o") does count.
 */
const carriesText = (text: string) => WORD_CHAR_RE.test(text);

// -----------------------------------------------------------------------------
// Coarse units: paragraphs, then sentences
// -----------------------------------------------------------------------------
// Deliberately *not* lines. A line boundary is made of the whitespace this
// engine ignores, so re-wrapping text reshuffles the units: two documents whose
// words are identical can end up with one line matching the wrong counterpart,
// and everything around it is then reported as deleted and re-inserted. A blank
// line and a sentence end survive re-wrapping, so they can be trusted.

/**
 * Splits on blank lines, each paragraph keeping the whitespace that closes it.
 * Joins back exactly.
 */
const splitParagraphs = (text: string): string[] => {
  const boundary = /\n[ \t\r]*(?:\n[ \t\r]*)+/g;
  const units: string[] = [];
  let start = 0;
  let match: RegExpExecArray | null;
  while ((match = boundary.exec(text)) !== null) {
    const end = match.index + match[0].length;
    units.push(text.slice(start, end));
    start = end;
  }
  if (start < text.length) units.push(text.slice(start));
  return units;
};

const DIGIT_RE = /\p{N}/u;

/**
 * Splits after sentence-ending punctuation followed by whitespace, keeping that
 * whitespace with the sentence it closes. Abbreviations ("odst.", "č. j.") are
 * split too; that only costs an extra anchor boundary, never correctness, since
 * the pieces are re-joined verbatim.
 *
 * A dot between two numbers is not a sentence end, or "30. 6. 2025" would be
 * cut into three units and could never be redlined as one date.
 */
const splitSentences = (text: string): string[] => {
  const boundary = /[.!?;:]+["'”’»)\]]*\s+/g;
  const units: string[] = [];
  let start = 0;
  let match: RegExpExecArray | null;
  while ((match = boundary.exec(text)) !== null) {
    const end = match.index + match[0].length;
    if (
      match.index > 0 &&
      DIGIT_RE.test(text[match.index - 1]) &&
      DIGIT_RE.test(text[end] ?? '')
    ) continue;
    units.push(text.slice(start, end));
    start = end;
  }
  if (start < text.length) units.push(text.slice(start));
  return units;
};

const UNIT_SPLITTERS = [splitParagraphs, splitSentences];

/**
 * Comparison key for a paragraph/sentence. A missing trailing newline at the very
 * end of a document, and (when whitespace is ignored) indentation and internal
 * spacing, must not stop two identical units from anchoring.
 */
const unitKey = (unit: string, opts: Options): string => {
  let key = unit.replace(/\r/g, '').replace(/\n+$/, '');
  if (opts.ignoreWhitespace) key = key.replace(/\s+/g, ' ').trim();
  if (opts.ignoreCase) key = key.toLowerCase();
  return key;
};

/**
 * Keys to feed the coarse diff, following patience diff's rule: a unit may anchor
 * only if it occurs exactly once on each side. A unit repeated in the document —
 * boilerplate, a bare "odst.", an empty paragraph — carries no information about
 * *which* occurrence matches which, and picking the wrong pair splits a block in
 * two so that neither half can be diffed against its real counterpart.
 *
 * Non-anchorable units get a per-position sentinel, so the coarse pass can never
 * match them and they fall through to the finer pass instead. Returns null when
 * nothing at all can anchor, so the caller can skip this level outright.
 */
const anchorableKeys = (
  sourceUnits: string[],
  targetUnits: string[],
  opts: Ctx
): { source: string[]; target: string[] } | null => {
  const sourceKeys = sourceUnits.map(u => unitKey(u, opts));
  const targetKeys = targetUnits.map(u => unitKey(u, opts));

  const tally = (keys: string[]) => {
    const counts = new Map<string, number>();
    for (const key of keys) counts.set(key, (counts.get(key) ?? 0) + 1);
    return counts;
  };
  const inSource = tally(sourceKeys);
  const inTarget = tally(targetKeys);
  const anchorable = (key: string) => inSource.get(key) === 1 && inTarget.get(key) === 1;

  if (!sourceKeys.some(anchorable)) return null;
  return {
    source: sourceKeys.map((key, i) => (anchorable(key) ? key : `\u0000s${i}`)),
    target: targetKeys.map((key, i) => (anchorable(key) ? key : `\u0000t${i}`)),
  };
};

// -----------------------------------------------------------------------------
// Intermediate representation
// -----------------------------------------------------------------------------
// An Op is a run of tokens: unchanged (both sides, always the same length),
// deleted (source only) or inserted (target only).

type Op =
  | { kind: 'equal'; source: string[]; target: string[] }
  | { kind: 'del'; source: string[] }
  | { kind: 'ins'; target: string[] };

/** A segment before ids are handed out. `source` is '' for insertions. */
interface RawSegment {
  type: ChangeType;
  text: string;
  source: string;
}

const appendAll = <T,>(into: T[], from: readonly T[]): void => {
  // A spread would blow the argument limit on book-length documents.
  for (const item of from) into.push(item);
};

// -----------------------------------------------------------------------------
// Word-level diff
// -----------------------------------------------------------------------------

const diffTokenOps = (source: string[], target: string[], opts: Ctx): Op[] => {
  if (!source.length && !target.length) return [];
  if (!source.length) return [{ kind: 'ins', target }];
  if (!target.length) return [{ kind: 'del', source }];

  const sourceKeys = source.map(t => tokenKey(t, opts));
  const targetKeys = target.map(t => tokenKey(t, opts));

  // Trim the identical head and tail before running Myers: its cost grows with
  // the size of the window, and most real edits touch the middle of a block.
  const shortest = Math.min(source.length, target.length);
  let head = 0;
  while (head < shortest && sourceKeys[head] === targetKeys[head]) head++;
  let tail = 0;
  while (
    tail < shortest - head &&
    sourceKeys[source.length - 1 - tail] === targetKeys[target.length - 1 - tail]
  ) tail++;

  const ops: Op[] = [];
  if (head) {
    ops.push({ kind: 'equal', source: source.slice(0, head), target: target.slice(0, head) });
  }

  const midSource = source.slice(head, source.length - tail);
  const midTarget = target.slice(head, target.length - tail);

  if (midSource.length && midTarget.length) {
    const changes = Diff.diffArrays(
      sourceKeys.slice(head, source.length - tail),
      targetKeys.slice(head, target.length - tail),
      { timeout: remainingBudget(opts) }
    );

    const middle: Op[] = [];
    let si = 0;
    let ti = 0;
    if (changes) {
      for (const change of changes) {
        if (change.added) {
          middle.push({ kind: 'ins', target: midTarget.slice(ti, ti + change.count) });
          ti += change.count;
        } else if (change.removed) {
          middle.push({ kind: 'del', source: midSource.slice(si, si + change.count) });
          si += change.count;
        } else {
          middle.push({
            kind: 'equal',
            source: midSource.slice(si, si + change.count),
            target: midTarget.slice(ti, ti + change.count),
          });
          si += change.count;
          ti += change.count;
        }
      }
    }
    // No result (the time budget ran out) or a walk that did not consume every
    // token: fall back to reporting the whole block as one replacement.
    if (changes && si === midSource.length && ti === midTarget.length) {
      appendAll(ops, middle);
    } else {
      ops.push({ kind: 'del', source: midSource });
      ops.push({ kind: 'ins', target: midTarget });
    }
  } else if (midSource.length) {
    ops.push({ kind: 'del', source: midSource });
  } else if (midTarget.length) {
    ops.push({ kind: 'ins', target: midTarget });
  }

  if (tail) {
    ops.push({
      kind: 'equal',
      source: source.slice(source.length - tail),
      target: target.slice(target.length - tail),
    });
  }
  return ops;
};

/**
 * Merges neighbouring ops of the same kind and puts every change region into one
 * canonical shape: all deletions first, then all insertions. jsdiff emits the
 * two in either order depending on which diagonal it reached the end on, which
 * is why an edit would sometimes show the new wording before the old.
 */
const canonicalize = (ops: Op[]): Op[] => {
  const out: Op[] = [];
  let i = 0;
  while (i < ops.length) {
    const op = ops[i];
    if (op.kind === 'equal') {
      const last = out[out.length - 1];
      if (last && last.kind === 'equal') {
        appendAll(last.source, op.source);
        appendAll(last.target, op.target);
      } else {
        out.push({ kind: 'equal', source: op.source.slice(), target: op.target.slice() });
      }
      i++;
      continue;
    }
    const deleted: string[] = [];
    const inserted: string[] = [];
    while (i < ops.length && ops[i].kind !== 'equal') {
      const change = ops[i];
      if (change.kind === 'del') appendAll(deleted, change.source);
      else if (change.kind === 'ins') appendAll(inserted, change.target);
      i++;
    }
    if (deleted.length) out.push({ kind: 'del', source: deleted });
    if (inserted.length) out.push({ kind: 'ins', target: inserted });
  }
  return out;
};

/**
 * Absorbs unchanged runs that carry no real text (", ", " – ", a stray letter)
 * and are pinned between changes on both sides, turning
 * `{-a-}{+x+}, {-b-}{+y+}` into `{-a, b-}{+x, y+}`.
 *
 * Both sides keep the run, which is what the previous implementation got wrong:
 * it moved such text into the preceding block only, so an untouched comma was
 * shown as struck out and vanished from the reconstructed target text.
 */
const absorbWeakAnchors = (ops: Op[]): Op[] => {
  if (ops.length < 3) return ops;

  const absorbed: boolean[] = ops.map((op, i) => {
    if (op.kind !== 'equal') return false;
    // Never touch the first or last op: those anchor the block to its context.
    if (i === 0 || i === ops.length - 1) return false;
    if (ops[i - 1].kind === 'equal' || ops[i + 1].kind === 'equal') return false;
    return !op.target.some(carriesText);
  });

  if (!absorbed.some(Boolean)) return ops;

  const expanded: Op[] = [];
  ops.forEach((op, i) => {
    if (absorbed[i] && op.kind === 'equal') {
      expanded.push({ kind: 'del', source: op.source });
      expanded.push({ kind: 'ins', target: op.target });
    } else {
      expanded.push(op);
    }
  });
  return canonicalize(expanded);
};

/**
 * Turns ops into segments. Unchanged runs are walked token by token so that a
 * word which only changed case becomes its own CASE_CHANGED segment, and
 * reflowed whitespace stays UNCHANGED while remembering how the source read.
 */
const opsToSegments = (ops: Op[], opts: Ctx): RawSegment[] => {
  const out: RawSegment[] = [];

  const push = (type: ChangeType, text: string, source: string) => {
    if (!text && !source) return;
    const last = out[out.length - 1];
    if (last && last.type === type) {
      last.text += text;
      last.source += source;
      return;
    }
    out.push({ type, text, source });
  };

  for (const op of ops) {
    if (op.kind === 'del') {
      const text = op.source.join('');
      if (opts.ignoreWhitespace && op.source.every(isWhitespace)) {
        // Whitespace with nothing to pair it against — dropped indentation, a
        // collapsed blank line. Not an edit; it just leaves no mark on the
        // target side.
        push(ChangeType.UNCHANGED, '', text);
      } else {
        push(ChangeType.REMOVED, text, text);
      }
      continue;
    }
    if (op.kind === 'ins') {
      const text = op.target.join('');
      if (opts.ignoreWhitespace && op.target.every(isWhitespace)) {
        push(ChangeType.UNCHANGED, text, '');
      } else {
        push(ChangeType.ADDED, text, '');
      }
      continue;
    }
    if (op.source.length !== op.target.length) {
      // Cannot happen: unchanged runs are built from equal token counts. Kept as
      // a safety net so a bug can never silently drop text.
      push(ChangeType.REMOVED, op.source.join(''), op.source.join(''));
      push(ChangeType.ADDED, op.target.join(''), '');
      continue;
    }
    for (let i = 0; i < op.target.length; i++) {
      const sourceToken = op.source[i];
      const targetToken = op.target[i];
      if (sourceToken === targetToken || (isWhitespace(sourceToken) && isWhitespace(targetToken))) {
        // Identical, or whitespace that was merely reflowed: not a change. The
        // redline shows the target document's spacing.
        push(ChangeType.UNCHANGED, targetToken, sourceToken);
      } else {
        push(ChangeType.CASE_CHANGED, targetToken, sourceToken);
      }
    }
  }
  return out;
};

const diffTokens = (sourceText: string, targetText: string, opts: Ctx): RawSegment[] =>
  opsToSegments(
    absorbWeakAnchors(canonicalize(diffTokenOps(tokenize(sourceText), tokenize(targetText), opts))),
    opts
  );

/**
 * Pairs up two passages that hold the very same tokens, differing only in letter
 * case and spacing — no diff needed, every token matches its counterpart. Returns
 * null when they do not line up that way.
 */
const pairTokens = (sourceText: string, targetText: string, opts: Ctx): RawSegment[] | null => {
  if (sourceText === targetText) {
    return targetText ? [{ type: ChangeType.UNCHANGED, text: targetText, source: sourceText }] : [];
  }
  const source = tokenize(sourceText);
  const target = tokenize(targetText);
  if (source.length !== target.length) return null;
  if (!source.every((token, i) => tokenKey(token, opts) === tokenKey(target[i], opts))) return null;
  return opsToSegments([{ kind: 'equal', source, target }], opts);
};

/**
 * Both sides of a region that a coarse pass called equal. Usually byte-identical;
 * otherwise the two differ only in letter case and whitespace. If the tokens do
 * not line up after all — case folding can change their number — fall back to a
 * real word diff of the region.
 */
const diffEqualRegion = (sourceText: string, targetText: string, opts: Ctx): RawSegment[] =>
  pairTokens(sourceText, targetText, opts) ?? diffTokens(sourceText, targetText, opts);

// -----------------------------------------------------------------------------
// Coarse pass driver
// -----------------------------------------------------------------------------

interface Region {
  equal: boolean;
  sourceText: string;
  targetText: string;
}

/**
 * The op-level rule one level up: a matching unit that carries no letter or digit
 * — a blank line, a lone ")" — is not a real anchor. Left standing between two
 * changed blocks it splits them apart, and a word that merely changed case ends
 * up on opposite sides of the split, reported as a deletion plus an insertion
 * instead of a case change.
 */
const absorbWeakRegions = (regions: Region[]): Region[] => {
  if (regions.length < 3) return regions;

  const out: Region[] = [];
  regions.forEach((region, i) => {
    const weak =
      region.equal &&
      i > 0 &&
      i < regions.length - 1 &&
      !regions[i - 1].equal &&
      !regions[i + 1].equal &&
      !carriesText(region.sourceText) &&
      !carriesText(region.targetText);
    const equal = weak ? false : region.equal;
    const last = out[out.length - 1];
    if (last && last.equal === equal) {
      last.sourceText += region.sourceText;
      last.targetText += region.targetText;
      return;
    }
    out.push({ equal, sourceText: region.sourceText, targetText: region.targetText });
  });
  return out;
};

/**
 * Anchors on identical units at `level` (paragraphs, then sentences) and recurses
 * into whatever lies between them, ending at the word-level diff.
 */
const diffAtLevel = (
  sourceText: string,
  targetText: string,
  level: number,
  opts: Ctx
): RawSegment[] => {
  const split = UNIT_SPLITTERS[level];
  if (!split) return diffTokens(sourceText, targetText, opts);

  const sourceUnits = split(sourceText);
  const targetUnits = split(targetText);

  // Nothing to anchor on — go finer.
  if (sourceUnits.length <= 1 && targetUnits.length <= 1) {
    return diffAtLevel(sourceText, targetText, level + 1, opts);
  }
  const keys = anchorableKeys(sourceUnits, targetUnits, opts);
  if (!keys) return diffAtLevel(sourceText, targetText, level + 1, opts);

  const changes = Diff.diffArrays(keys.source, keys.target, {
    timeout: remainingBudget(opts),
  });
  if (!changes) return diffAtLevel(sourceText, targetText, level + 1, opts);

  const regions: Region[] = [];
  const addRegion = (equal: boolean, source: string, target: string) => {
    const last = regions[regions.length - 1];
    if (last && last.equal === equal) {
      last.sourceText += source;
      last.targetText += target;
      return;
    }
    regions.push({ equal, sourceText: source, targetText: target });
  };

  let si = 0;
  let ti = 0;
  for (const change of changes) {
    if (change.added) {
      addRegion(false, '', targetUnits.slice(ti, ti + change.count).join(''));
      ti += change.count;
    } else if (change.removed) {
      addRegion(false, sourceUnits.slice(si, si + change.count).join(''), '');
      si += change.count;
    } else {
      addRegion(
        true,
        sourceUnits.slice(si, si + change.count).join(''),
        targetUnits.slice(ti, ti + change.count).join('')
      );
      si += change.count;
      ti += change.count;
    }
  }
  if (si !== sourceUnits.length || ti !== targetUnits.length) {
    return diffAtLevel(sourceText, targetText, level + 1, opts);
  }

  return absorbWeakRegions(regions).flatMap(region =>
    region.equal
      ? diffEqualRegion(region.sourceText, region.targetText, opts)
      : diffAtLevel(region.sourceText, region.targetText, level + 1, opts)
  );
};

// -----------------------------------------------------------------------------
// Public API
// -----------------------------------------------------------------------------

export const generateSmartDiff = (
  original: string,
  modified: string,
  options?: DiffOptions
): DiffSegment[] => {
  const resolved: Options = { ...DEFAULT_OPTIONS, ...options };
  const opts: Ctx = { ...resolved, deadline: Date.now() + resolved.timeoutMs };

  // Two documents holding the same words in the same order, written with
  // different capitals or spacing, need no diff at all — and must never be
  // reported as edited just because a coarse pass anchored on the wrong
  // paragraph. Checking it up front is linear.
  const raw: RawSegment[] =
    pairTokens(original, modified, opts) ?? diffAtLevel(original, modified, 0, opts);

  const nonEmpty = raw.filter(seg => seg.text || seg.source);

  // Each region was diffed on its own, so one can end with an insertion while
  // the next starts with a deletion. Restate every run of changes the canonical
  // way round — what was deleted, then what replaced it.
  const kept: RawSegment[] = [];
  for (let i = 0; i < nonEmpty.length; ) {
    const isChange = (seg: RawSegment) =>
      seg.type === ChangeType.ADDED || seg.type === ChangeType.REMOVED;
    if (!isChange(nonEmpty[i])) {
      kept.push(nonEmpty[i]);
      i++;
      continue;
    }
    let removed = '';
    let added = '';
    while (i < nonEmpty.length && isChange(nonEmpty[i])) {
      if (nonEmpty[i].type === ChangeType.REMOVED) removed += nonEmpty[i].text;
      else added += nonEmpty[i].text;
      i++;
    }
    if (removed) kept.push({ type: ChangeType.REMOVED, text: removed, source: removed });
    if (added) kept.push({ type: ChangeType.ADDED, text: added, source: '' });
  }

  // Stitch runs that the recursion produced separately.
  const merged: RawSegment[] = [];
  kept.forEach((seg, i) => {
    const last = merged[merged.length - 1];
    if (last && last.type === seg.type) {
      last.text += seg.text;
      last.source += seg.source;
      return;
    }
    // A re-cased phrase reads as one underlined phrase rather than one underline
    // per word, so the space between two re-cased words joins them — but only an
    // untouched space, so that a CASE_CHANGED segment always holds the very same
    // characters as the source, bar their case.
    if (
      last?.type === ChangeType.CASE_CHANGED &&
      seg.type === ChangeType.UNCHANGED &&
      isBlank(seg.text) &&
      seg.text === seg.source &&
      kept[i + 1]?.type === ChangeType.CASE_CHANGED
    ) {
      last.text += seg.text;
      last.source += seg.source;
      return;
    }
    merged.push({ ...seg });
  });

  // Ids double as scroll anchors when stepping through the changes.
  return merged.map((seg, i) => ({
    id: `seg-${i}`,
    type: seg.type,
    text: seg.text,
    ...(seg.type !== ChangeType.ADDED && seg.source !== seg.text
      ? { originalText: seg.source }
      : {}),
  }));
};

/** Counts per change kind, for the header/legend. */
export interface DiffStats {
  added: number;
  removed: number;
  caseChanged: number;
  /** Every flagged segment — what the change navigator steps through. */
  total: number;
  /** The documents differ, but only in whitespace: nothing to redline. */
  formattingOnly: boolean;
}

export const summarizeDiff = (
  segments: DiffSegment[],
  original: string,
  modified: string
): DiffStats => {
  let added = 0;
  let removed = 0;
  let caseChanged = 0;
  for (const seg of segments) {
    if (seg.type === ChangeType.ADDED) added++;
    else if (seg.type === ChangeType.REMOVED) removed++;
    else if (seg.type === ChangeType.CASE_CHANGED) caseChanged++;
  }
  const total = added + removed + caseChanged;
  return { added, removed, caseChanged, total, formattingOnly: total === 0 && original !== modified };
};
