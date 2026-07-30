import { unzipSync } from 'fflate';
import { decodeXmlText, walkXml } from './xml';

// A .docx is a zip; the body text lives in word/document.xml as WordprocessingML.
// Everything happens in the browser — the file is never uploaded anywhere.

/** Element whose text belongs to the machinery, not to the document. */
const SKIPPED_ELEMENTS = new Set([
  'w:instrText', // field codes, e.g. the PAGE in a footer
  'w:delText', // text struck out by a tracked change: no longer part of the document
  'w:delInstrText',
]);

const utf8 = new TextDecoder('utf-8');

/**
 * Pulls the readable text out of WordprocessingML, one line per paragraph — the
 * same shape you would get by copying the document into a text box, so a pasted
 * version and an imported one compare cleanly.
 */
export const documentXmlToText = (xml: string): string => {
  const parts: string[] = [];
  let skipDepth = 0;
  let cellPending = false;

  const append = (text: string) => {
    if (!text) return;
    if (cellPending) {
      parts.push('\t');
      cellPending = false;
    }
    parts.push(text);
  };

  walkXml(
    xml,
    tag => {
      if (SKIPPED_ELEMENTS.has(tag.name)) {
        if (tag.closing) skipDepth = Math.max(0, skipDepth - 1);
        else if (!tag.selfClosing) skipDepth++;
        return;
      }
      if (skipDepth > 0) return;

      switch (tag.name) {
        case 'w:tab':
          if (!tag.closing) append('\t');
          break;
        case 'w:br':
        case 'w:cr':
          if (!tag.closing) append('\n');
          break;
        case 'w:p':
          // Paragraph end. A paragraph holding no text still ends a line, which
          // is how an empty paragraph becomes the blank line it looks like.
          if (tag.closing || tag.selfClosing) {
            cellPending = false;
            parts.push('\n');
          }
          break;
        case 'w:tc':
          // Cells are separated by a tab, but only once the next one has content,
          // so a row never ends in a dangling tab.
          if (tag.closing) {
            while (parts.length && parts[parts.length - 1] === '\n') parts.pop();
            cellPending = true;
          }
          break;
        case 'w:tr':
          if (tag.closing) {
            cellPending = false;
            parts.push('\n');
          }
          break;
      }
    },
    text => {
      if (skipDepth > 0) return;
      append(decodeXmlText(text));
    }
  );

  return parts.join('').replace(/\n$/, '');
};

/** Raised when the file is not a .docx we can read; the message is user-facing. */
export class DocxError extends Error {}

export const extractDocxText = (data: ArrayBuffer): string => {
  let files: Record<string, Uint8Array>;
  try {
    files = unzipSync(new Uint8Array(data), {
      filter: file => file.name === 'word/document.xml',
    });
  } catch {
    throw new DocxError('Soubor .docx se nepodařilo otevřít — může být poškozený.');
  }

  const document = files['word/document.xml'];
  if (!document) {
    throw new DocxError(
      'V souboru chybí word/document.xml. Jde skutečně o dokument .docx? Starší .doc je nutné uložit jako .docx.'
    );
  }
  return documentXmlToText(utf8.decode(document));
};
