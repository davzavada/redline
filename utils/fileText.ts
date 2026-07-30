import { DocxError, extractDocxText } from './docxText';
import { PdfError, extractPdfText } from './pdfText';

// Turns a dropped or picked file into text to compare. Everything runs in the
// browser: nothing is uploaded, and the app works with the network switched off.

/** Extensions the file picker offers and the drop zone accepts. */
export const ACCEPTED_FILE_TYPES = '.pdf,.docx,.txt,.md,.markdown,.csv,.tsv,.json,.xml,.text';

/** What the user is told they can drop. */
export const ACCEPTED_HINT = 'PDF, Word (.docx) nebo text';

const PLAIN_TEXT_RE = /\.(txt|text|md|markdown|csv|tsv|json|xml|log|srt|tex)$/i;

/** Formats we can name but not read, with what to do instead. */
const UNREADABLE: { pattern: RegExp; advice: string }[] = [
  { pattern: /\.doc$/i, advice: 'Starší formát .doc neumíme přečíst — uložte prosím dokument jako .docx nebo PDF.' },
  { pattern: /\.rtf$/i, advice: 'Formát .rtf neumíme přečíst — uložte prosím dokument jako .docx nebo PDF.' },
  { pattern: /\.odt$/i, advice: 'Formát .odt neumíme přečíst — uložte prosím dokument jako .docx nebo PDF.' },
  { pattern: /\.pages$/i, advice: 'Formát .pages neumíme přečíst — exportujte prosím dokument do PDF nebo .docx.' },
  { pattern: /\.(xlsx?|pptx?|numbers|key)$/i, advice: 'Tabulky a prezentace neumíme přečíst — exportujte prosím text do PDF nebo .docx.' },
];

/** Bigger than any contract, and past this the browser starts to struggle. */
const MAX_BYTES = 40 * 1024 * 1024;

/** Raised with a message meant to be shown to the user as-is. */
export class FileImportError extends Error {}

export interface ImportedDocument {
  text: string;
  /** File name without its extension, for the document tab. */
  name: string;
}

const baseName = (fileName: string) => fileName.replace(/\.[^.]+$/, '').trim();

const isPdf = (file: File) => /\.pdf$/i.test(file.name) || file.type === 'application/pdf';

const isDocx = (file: File) =>
  /\.docx$/i.test(file.name) ||
  file.type === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

const isPlainText = (file: File) =>
  PLAIN_TEXT_RE.test(file.name) || file.type.startsWith('text/') || file.type === 'application/json';

export const readDocumentFile = async (file: File): Promise<ImportedDocument> => {
  if (file.size > MAX_BYTES) {
    throw new FileImportError(
      `Soubor je příliš velký (${Math.round(file.size / 1024 / 1024)} MB). Limit je ${MAX_BYTES / 1024 / 1024} MB.`
    );
  }

  // Checked before anything is read: an .rtf announces itself as text/rtf, and
  // dumping its markup into the panel would look like a successful import.
  const unreadable = UNREADABLE.find(entry => entry.pattern.test(file.name));
  if (unreadable) throw new FileImportError(unreadable.advice);

  const name = baseName(file.name) || file.name;

  try {
    if (isPdf(file)) {
      return { text: await extractPdfText(await file.arrayBuffer()), name };
    }
    if (isDocx(file)) {
      return { text: extractDocxText(await file.arrayBuffer()), name };
    }
    if (isPlainText(file)) {
      return { text: await file.text(), name };
    }
  } catch (error) {
    if (error instanceof PdfError || error instanceof DocxError) {
      throw new FileImportError(error.message);
    }
    throw new FileImportError(
      `Soubor „${file.name}“ se nepodařilo přečíst${error instanceof Error ? `: ${error.message}` : '.'}`
    );
  }

  throw new FileImportError(
    `Nepodporovaný soubor „${file.name}“. Podporujeme ${ACCEPTED_HINT}.`
  );
};
