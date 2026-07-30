export enum ChangeType {
  UNCHANGED = 'UNCHANGED',
  ADDED = 'ADDED',
  REMOVED = 'REMOVED',
  /**
   * The same word on both sides, written with different letter case
   * (e.g. "smlouva" → "SMLOUVA"). Not a replacement — it is flagged, not
   * struck out and re-inserted.
   */
  CASE_CHANGED = 'CASE_CHANGED'
}

export interface Document {
  id: string;
  name: string;
  text: string;
}

export interface DiffSegment {
  id: string;
  /**
   * The text as it reads in the target document. For REMOVED segments (which
   * exist only in the source) this is the source text.
   */
  text: string;
  type: ChangeType;
  /**
   * The source-document wording, set only when it differs from `text` — i.e. on
   * CASE_CHANGED segments and on UNCHANGED segments whose whitespace was
   * reflowed. Lets a consumer rebuild the source text exactly:
   * `segments.filter(s => s.type !== ADDED).map(s => s.originalText ?? s.text)`.
   */
  originalText?: string;
}
