// PDF text extraction with pdf.js, running entirely in the browser — the file is
// never uploaded anywhere.
//
// Two deliberate choices:
//
//  * the `legacy` build, which is the one that also runs outside a browser, so the
//    tests exercise this very code path, and which carries the transpilation that
//    keeps older browsers working;
//  * no Web Worker. Importing the worker build registers pdf.js's documented
//    `globalThis.pdfjsWorker` hook, which makes it parse in-process. A worker
//    would keep the page responsive, but Vite can only inline one as a blob URL,
//    and Chromium refuses to start a blob worker from a file:// page — which is
//    exactly how the downloaded offline copy of this app is opened. Parsing takes
//    a moment and holds the page while it runs; being unable to open a PDF at all
//    would be worse.

/** The part of a pdf.js text item this module needs. */
export interface PdfTextItem {
  str: string;
  hasEOL: boolean;
  /** [a, b, c, d, x, y] — the last two are the run's origin. */
  transform?: number[];
  width?: number;
  height?: number;
  /** Key into the `styles` map that comes with the page's text content. */
  fontName?: string;
}

/** What pdf.js can tell us about a font, which is little but enough. */
export interface PdfTextStyle {
  fontFamily?: string;
  ascent?: number;
  descent?: number;
}

/**
 * A PDF has no idea what a word is: it paints runs of glyphs at coordinates. pdf.js
 * turns most positional gaps back into spaces, but where it does not, a gap wider
 * than a fraction of the line height is a space that would otherwise be lost,
 * gluing two words together.
 */
const SPACE_GAP_RATIO = 0.2;

const endsOpen = (text: string) => !text || /[\s\u00AD-]$/.test(text);

/** Where a run of the extracted text sits on the page it came from. */
export interface PdfTextBox {
  /** Character range of the document text that this run covers. */
  start: number;
  end: number;
  /** Zero-based page index. */
  page: number;
  /** PDF user space: left edge, baseline, advance width, font height. */
  x: number;
  y: number;
  width: number;
  height: number;
  /** Generic family and ascent, for laying the selectable text over the page. */
  fontFamily?: string;
  ascent?: number;
}

export interface PdfPageSize {
  width: number;
  height: number;
  rotation: number;
}

export interface PdfDocument {
  text: string;
  /** In text order, so a character range can be found by scanning once. */
  boxes: PdfTextBox[];
  pages: PdfPageSize[];
}

/**
 * A parsed PDF kept around so the viewer can render its pages. The bytes stay in
 * memory only — the workspace that survives a reload holds text, not files.
 */
export interface PdfSource extends PdfDocument {
  bytes: ArrayBuffer;
}

/**
 * A word hyphenated across a line break is one word: "dodava-\ntele" reads as
 * "dodavatele". Two letters are required before the hyphen and a lowercase letter
 * after it, so "e-mail" and "Praha-Smíchov" are left alone.
 */
const HYPHEN_BREAK_RE = /\p{L}{2}[-\u00AD]\n$/u;

/**
 * Builds the document text and, in the same pass, a note of where each run of that
 * text sits on the page. It has to be one pass: the viewer points at the very
 * characters the diff is talking about, and mending a hyphen afterwards would
 * shift every offset that follows it.
 */
export const assembleDocument = (
  pagesOfItems: PdfTextItem[][],
  stylesPerPage: Record<string, PdfTextStyle>[] = []
): Omit<PdfDocument, 'pages'> => {
  let text = '';
  const boxes: PdfTextBox[] = [];

  pagesOfItems.forEach((items, page) => {
    if (page > 0) {
      // One blank line between pages, so each page anchors the diff on its own.
      text = text.replace(/\s+$/, '');
      if (text) text += '\n\n';
    }

    let previous: PdfTextItem | null = null;
    for (const item of items) {
      // A soft hyphen is a typesetting hint, never part of the word.
      const run = item.str.replace(/\u00AD/gu, '');
      if (!run) {
        // pdf.js reports a bare line break as an empty item.
        if (item.hasEOL && text && !text.endsWith('\n')) text += '\n';
        previous = null;
        continue;
      }

      if (/^\p{Ll}/u.test(run) && HYPHEN_BREAK_RE.test(text)) {
        text = text.slice(0, -2); // drop the hyphen and the line break
        const last = boxes[boxes.length - 1];
        // The hyphen was part of that run's text; it no longer is.
        if (last && last.end === text.length + 1) last.end -= 1;
      } else if (previous && !text.endsWith('\n') && !endsOpen(text) && !/^\s/.test(run)) {
        const previousEnd = (previous.transform?.[4] ?? 0) + (previous.width ?? 0);
        const gap = (item.transform?.[4] ?? 0) - previousEnd;
        const lineHeight = item.height || previous.height || 0;
        if (gap > Math.max(1, lineHeight * SPACE_GAP_RATIO)) text += ' ';
      }

      const style = item.fontName ? stylesPerPage[page]?.[item.fontName] : undefined;
      const start = text.length;
      text += run;
      boxes.push({
        start,
        end: text.length,
        page,
        x: item.transform?.[4] ?? 0,
        y: item.transform?.[5] ?? 0,
        width: item.width ?? 0,
        height: item.height ?? 0,
        ...(style?.fontFamily ? { fontFamily: style.fontFamily } : {}),
        ...(typeof style?.ascent === 'number' ? { ascent: style.ascent } : {}),
      });

      if (item.hasEOL) text += '\n';
      previous = item;
    }
  });

  const trimmed = text.replace(/\s+$/, '');
  for (const box of boxes) {
    if (box.end > trimmed.length) box.end = Math.max(box.start, trimmed.length);
  }
  return { text: trimmed, boxes };
};

/** Turns one page's items into text. */
export const assemblePageText = (items: PdfTextItem[]): string =>
  assembleDocument([items]).text;

export interface PageRectangle {
  page: number;
  /** PDF user space, y measured from the bottom of the page as PDF does. */
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * The rectangles covering a range of the document text. A range usually falls
 * inside one run, but a changed phrase can span several runs and several lines, so
 * this returns one rectangle per run it touches, trimmed to the range by
 * interpolating along the run's width.
 */
export const rangeRectangles = (
  boxes: PdfTextBox[],
  start: number,
  end: number
): PageRectangle[] => {
  const rectangles: PageRectangle[] = [];
  for (const box of boxes) {
    if (box.end <= start) continue;
    if (box.start >= end) break;
    const span = box.end - box.start;
    if (span <= 0) continue;
    const from = (Math.max(start, box.start) - box.start) / span;
    const to = (Math.min(end, box.end) - box.start) / span;
    rectangles.push({
      page: box.page,
      x: box.x + box.width * from,
      y: box.y,
      width: Math.max(box.width * (to - from), 1),
      height: box.height || 10,
    });
  }
  return rectangles;
};

/** Raised when the PDF cannot be read; the message is user-facing. */
export class PdfError extends Error {}

/** Loaded once: the import is what registers pdf.js's in-process parser. */
let parserReady: Promise<unknown> | null = null;
const loadParser = () => {
  parserReady ??= import('pdfjs-dist/legacy/build/pdf.worker.min.mjs');
  return parserReady;
};

/** pdf.js, loaded on first use. Also used by the viewer to render pages. */
export const loadPdfjs = async () => {
  const [pdfjs] = await Promise.all([import('pdfjs-dist/legacy/build/pdf.mjs'), loadParser()]);
  return pdfjs;
};

/**
 * Options every `getDocument` call in this app shares: no forms and nothing
 * fetched from anywhere, because a document being compared is not to be trusted
 * with more than its own text. (pdf.js 6 dropped both document scripting and the
 * eval it once needed, so there is nothing else left to switch off.)
 */
export const documentOptions = (data: ArrayBuffer) => ({
  // A copy, always: pdf.js takes ownership of the bytes it is handed and leaves the
  // buffer detached. The original has to survive extraction so the viewer can still
  // render the pages afterwards.
  data: new Uint8Array(data.slice(0)),
  enableXfa: false,
  useWorkerFetch: false,
  // Missing standard-font data would otherwise fill the console with warnings.
  verbosity: 0,
});

export const extractPdf = async (data: ArrayBuffer): Promise<PdfDocument> => {
  const pdfjs = await loadPdfjs();

  let task: { promise: Promise<unknown>; destroy: () => Promise<void> } | null = null;
  try {
    task = pdfjs.getDocument(documentOptions(data));

    const doc = (await task.promise) as {
      numPages: number;
      getPage: (n: number) => Promise<{
        getTextContent: () => Promise<{ items: unknown[]; styles: Record<string, PdfTextStyle> }>;
        getViewport: (options: { scale: number }) => { width: number; height: number; rotation: number };
      }>;
    };

    const pagesOfItems: PdfTextItem[][] = [];
    const stylesPerPage: Record<string, PdfTextStyle>[] = [];
    const pages: PdfPageSize[] = [];
    for (let number = 1; number <= doc.numPages; number++) {
      const page = await doc.getPage(number);
      const viewport = page.getViewport({ scale: 1 });
      pages.push({ width: viewport.width, height: viewport.height, rotation: viewport.rotation });
      const content = await page.getTextContent();
      pagesOfItems.push(content.items as PdfTextItem[]);
      stylesPerPage.push(content.styles ?? {});
    }

    const { text, boxes } = assembleDocument(pagesOfItems, stylesPerPage);
    if (!text.trim()) {
      throw new PdfError(
        'PDF neobsahuje žádný text — jde pravděpodobně o sken. Takový soubor je nutné nejprve rozpoznat (OCR).'
      );
    }
    return { text, boxes, pages };
  } catch (error) {
    if (error instanceof PdfError) throw error;
    const detail = error instanceof Error ? error.message : String(error);
    throw new PdfError(`PDF se nepodařilo přečíst: ${detail}`);
  } finally {
    await task?.destroy().catch(() => undefined);
  }
};

/** Text only, for callers that do not care where it sat on the page. */
export const extractPdfText = async (data: ArrayBuffer): Promise<string> =>
  (await extractPdf(data)).text;
