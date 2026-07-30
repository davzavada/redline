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
  /** [a, b, c, d, x, y] — only x (index 4) is used. */
  transform?: number[];
  width?: number;
  height?: number;
}

/**
 * A PDF has no idea what a word is: it paints runs of glyphs at coordinates. pdf.js
 * turns most positional gaps back into spaces, but where it does not, a gap wider
 * than a fraction of the line height is a space that would otherwise be lost,
 * gluing two words together.
 */
const SPACE_GAP_RATIO = 0.2;

const endsOpen = (text: string) => !text || /[\s\u00AD-]$/.test(text);

/** Turns one page's items into text, restoring the spaces and line breaks. */
export const assemblePageText = (items: PdfTextItem[]): string => {
  let out = '';
  let previous: PdfTextItem | null = null;

  for (const item of items) {
    if (!item.str) {
      // pdf.js reports a bare line break as an empty item.
      if (item.hasEOL && out && !out.endsWith('\n')) out += '\n';
      previous = null;
      continue;
    }

    if (previous && !out.endsWith('\n') && !endsOpen(out) && !/^\s/.test(item.str)) {
      const previousEnd = (previous.transform?.[4] ?? 0) + (previous.width ?? 0);
      const gap = (item.transform?.[4] ?? 0) - previousEnd;
      const lineHeight = item.height || previous.height || 0;
      if (gap > Math.max(1, lineHeight * SPACE_GAP_RATIO)) out += ' ';
    }

    out += item.str;
    if (item.hasEOL) out += '\n';
    previous = item;
  }
  return out;
};

/**
 * Undoes the line breaks that only exist because the text was typeset: a word
 * hyphenated across a line break is one word. Requires two letters before the
 * hyphen and a lowercase letter after it, so "e-mail" and "Praha-Smíchov" survive.
 */
export const mendHyphenation = (text: string): string =>
  text
    .replace(/(\p{L}{2,})[-\u00AD]\n(\p{Ll})/gu, '$1$2')
    .replace(/\u00AD/gu, '');

/** Pages are separated by a blank line, so each one anchors the diff on its own. */
export const joinPages = (pages: string[]): string =>
  pages
    .map(page => page.replace(/\s+$/, ''))
    .filter(page => page)
    .join('\n\n');

/** Raised when the PDF cannot be read; the message is user-facing. */
export class PdfError extends Error {}

/** Loaded once: the import is what registers pdf.js's in-process parser. */
let parserReady: Promise<unknown> | null = null;
const loadParser = () => {
  parserReady ??= import('pdfjs-dist/legacy/build/pdf.worker.min.mjs');
  return parserReady;
};

export interface PdfExtractOptions {
  /** Rejoin words hyphenated across a line break. Default: true. */
  mendLineBreaks?: boolean;
}

export const extractPdfText = async (
  data: ArrayBuffer,
  options: PdfExtractOptions = {}
): Promise<string> => {
  const { mendLineBreaks = true } = options;
  const [pdfjs] = await Promise.all([import('pdfjs-dist/legacy/build/pdf.mjs'), loadParser()]);

  let task: { promise: Promise<unknown>; destroy: () => Promise<void> } | null = null;
  try {
    task = pdfjs.getDocument({
      data: new Uint8Array(data),
      // No forms, and nothing fetched from anywhere: this is a text extraction,
      // and the document is not to be trusted with more than that. (pdf.js 6
      // dropped both document scripting and the eval it once needed, so there is
      // nothing else left to switch off.)
      enableXfa: false,
      useWorkerFetch: false,
      disableFontFace: true,
      // Missing standard-font data is irrelevant to text and only clutters the
      // console with warnings.
      verbosity: 0,
    });

    const doc = (await task.promise) as {
      numPages: number;
      getPage: (n: number) => Promise<{ getTextContent: () => Promise<{ items: unknown[] }> }>;
    };

    const pages: string[] = [];
    for (let number = 1; number <= doc.numPages; number++) {
      const page = await doc.getPage(number);
      const content = await page.getTextContent();
      pages.push(assemblePageText(content.items as PdfTextItem[]));
    }

    const text = joinPages(pages);
    if (!text.trim()) {
      throw new PdfError(
        'PDF neobsahuje žádný text — jde pravděpodobně o sken. Takový soubor je nutné nejprve rozpoznat (OCR).'
      );
    }
    return mendLineBreaks ? mendHyphenation(text) : text;
  } catch (error) {
    if (error instanceof PdfError) throw error;
    const detail = error instanceof Error ? error.message : String(error);
    throw new PdfError(`PDF se nepodařilo přečíst: ${detail}`);
  } finally {
    await task?.destroy().catch(() => undefined);
  }
};
