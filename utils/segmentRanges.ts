import { ChangeType, DiffSegment } from '../types';

// Where each segment of a redline sits in each of the two documents.
//
// This falls straight out of the property the engine guarantees: concatenating
// every segment that is not an insertion (taking `originalText` where it is set)
// rebuilds the source exactly, and everything that is not a deletion rebuilds the
// target. Walking the segments once therefore gives character ranges that can be
// pointed at — which is what the document viewer needs to draw a change onto the
// page it happened on.

export interface SegmentRange {
  segment: DiffSegment;
  /** Character range in the source document, or null if it is not in it. */
  source: readonly [number, number] | null;
  /** Character range in the target document, or null if it is not in it. */
  target: readonly [number, number] | null;
}

/** The text a segment contributes to the source document. */
export const sourceTextOf = (segment: DiffSegment): string =>
  segment.type === ChangeType.ADDED ? '' : segment.originalText ?? segment.text;

/** The text a segment contributes to the target document. */
export const targetTextOf = (segment: DiffSegment): string =>
  segment.type === ChangeType.REMOVED ? '' : segment.text;

export const segmentRanges = (segments: DiffSegment[]): SegmentRange[] => {
  let source = 0;
  let target = 0;
  return segments.map(segment => {
    const inSource = sourceTextOf(segment);
    const inTarget = targetTextOf(segment);
    const range: SegmentRange = {
      segment,
      source: inSource ? ([source, source + inSource.length] as const) : null,
      target: inTarget ? ([target, target + inTarget.length] as const) : null,
    };
    source += inSource.length;
    target += inTarget.length;
    return range;
  });
};

/** Which side of the comparison is on screen. */
export type DocumentSide = 'source' | 'target';

/**
 * The changes worth marking on one side: what was struck out only exists in the
 * source, what was inserted only in the target, and a re-cased word is on both.
 */
export const changesOnSide = (ranges: SegmentRange[], side: DocumentSide): SegmentRange[] =>
  ranges.filter(({ segment }) => {
    if (segment.type === ChangeType.CASE_CHANGED) return true;
    return segment.type === (side === 'source' ? ChangeType.REMOVED : ChangeType.ADDED);
  });

export const rangeOn = (range: SegmentRange, side: DocumentSide) =>
  side === 'source' ? range.source : range.target;

/** A stretch of one document that a change covers. */
export interface Mark {
  start: number;
  end: number;
  type: ChangeType;
  id: string;
}

export const marksOnSide = (ranges: SegmentRange[], side: DocumentSide): Mark[] =>
  changesOnSide(ranges, side)
    .map(entry => {
      const range = rangeOn(entry, side);
      return range
        ? { start: range[0], end: range[1], type: entry.segment.type, id: entry.segment.id }
        : null;
    })
    .filter((mark): mark is Mark => mark !== null);

export interface RunPart {
  text: string;
  /** The change covering this part, or null where nothing changed. */
  mark: Mark | null;
}

/**
 * Cuts one run of text — a line off a PDF page, say — into the stretches a change
 * covers and the stretches it does not, so each can be drawn on its own. Marks must
 * be in document order and must not overlap, which is how the engine emits them.
 */
export const splitRun = (runText: string, runStart: number, marks: Mark[]): RunPart[] => {
  const parts: RunPart[] = [];
  const runEnd = runStart + runText.length;
  let cursor = 0;

  for (const mark of marks) {
    if (mark.end <= runStart) continue;
    if (mark.start >= runEnd) break;
    const from = Math.max(mark.start - runStart, cursor);
    const to = Math.min(mark.end - runStart, runText.length);
    if (to <= from) continue;
    if (from > cursor) parts.push({ text: runText.slice(cursor, from), mark: null });
    parts.push({ text: runText.slice(from, to), mark });
    cursor = to;
  }

  if (cursor < runText.length) parts.push({ text: runText.slice(cursor), mark: null });
  return parts;
};
