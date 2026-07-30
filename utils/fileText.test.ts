import { describe, expect, it } from 'vitest';
import { zipSync, strToU8 } from 'fflate';
import { ACCEPTED_HINT, FileImportError, readDocumentFile } from './fileText';
import { documentXmlToText, extractDocxText } from './docxText';
import { PdfTextItem, assembleDocument, assemblePageText, extractPdf, extractPdfText, rangeRectangles } from './pdfText';
import { segmentRanges, changesOnSide, marksOnSide, splitRun } from './segmentRanges';
import { decodeXmlText, walkXml } from './xml';
import { generateSmartDiff, summarizeDiff } from './diffEngine';
import { ChangeType } from '../types';

// --- fixtures ---------------------------------------------------------------

/** Wraps WordprocessingML body markup into a real (minimal) .docx archive. */
const buildDocx = (bodyXml: string, extra: Record<string, string> = {}): Uint8Array =>
  zipSync({
    '[Content_Types].xml': strToU8(
      '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
        '<Default Extension="xml" ContentType="application/xml"/></Types>'
    ),
    'word/document.xml': strToU8(
      '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
        `<w:body>${bodyXml}</w:body></w:document>`
    ),
    ...Object.fromEntries(Object.entries(extra).map(([name, text]) => [name, strToU8(text)])),
  });

/** A paragraph of runs, the way Word actually nests them. */
const para = (...runs: string[]) => `<w:p>${runs.map(r => `<w:r>${r}</w:r>`).join('')}</w:p>`;
const text = (value: string, preserve = false) =>
  `<w:t${preserve ? ' xml:space="preserve"' : ''}>${value}</w:t>`;

/**
 * Builds a tiny, uncompressed, syntactically complete PDF around a content
 * stream — enough for pdf.js to read, and no binary fixture in the repository.
 */
const buildPdf = (...pageContents: string[]): Uint8Array => {
  const pageIds = pageContents.map((_, i) => 4 + i * 2);
  const objects: string[] = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    `<< /Type /Pages /Kids [${pageIds.map(id => `${id} 0 R`).join(' ')}] /Count ${pageContents.length} >>`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  pageContents.forEach(content => {
    objects.push(
      '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] ' +
        `/Resources << /Font << /F1 3 0 R >> >> /Contents ${objects.length + 2} 0 R >>`
    );
    objects.push(`<< /Length ${content.length} >>\nstream\n${content}\nendstream`);
  });

  let pdf = '%PDF-1.4\n';
  const offsets: number[] = [];
  objects.forEach((body, i) => {
    offsets.push(pdf.length);
    pdf += `${i + 1} 0 obj\n${body}\nendobj\n`;
  });
  const startXref = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets) pdf += `${String(offset).padStart(10, '0')} 00000 n \n`;
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${startXref}\n%%EOF\n`;
  return strToU8(pdf);
};

const line = (y: number, value: string) => `BT /F1 12 Tf 50 ${y} Td (${value}) Tj ET`;

const asFile = (bytes: Uint8Array | string, name: string, type = '') =>
  new File([bytes as BlobPart], name, { type });

const readPdf = (bytes: Uint8Array) => extractPdfText(bytes.buffer as ArrayBuffer);

/** A pdf.js text item, as the page assembler sees them. */
let runX = 50;
const run = (str: string, over: Partial<PdfTextItem> = {}): PdfTextItem => {
  const item: PdfTextItem = {
    str,
    hasEOL: false,
    transform: [12, 0, 0, 12, runX, 800],
    width: str.length * 6,
    height: 12,
    ...over,
  };
  runX = over.hasEOL || item.hasEOL ? 50 : runX + (item.width ?? 0);
  return item;
};

// --- xml --------------------------------------------------------------------

describe('xml walking', () => {
  it('resolves the entities a document part can carry', () => {
    expect(decodeXmlText('a &amp; b &lt;c&gt; &quot;d&quot; &apos;e&apos;')).toBe('a & b <c> "d" \'e\'');
    expect(decodeXmlText('&#268;esk&#xe1;')).toBe('Česká');
    expect(decodeXmlText('&nesmysl; zůstává')).toBe('&nesmysl; zůstává');
  });

  it('is not fooled by a > inside an attribute value', () => {
    const seen: string[] = [];
    walkXml('<a title="x > y"><b/>text</a>', tag => seen.push(`${tag.closing ? '/' : ''}${tag.name}`), t => seen.push(`#${t}`));
    expect(seen).toEqual(['a', 'b', '#text', '/a']);
  });

  it('skips comments, declarations and reads CDATA as text', () => {
    const seen: string[] = [];
    walkXml('<?xml version="1.0"?><!-- <a> --><b><![CDATA[<raw> & co]]></b>', () => {}, t => seen.push(t));
    expect(seen).toEqual(['<raw> & co']);
  });
});

// --- docx -------------------------------------------------------------------

describe('Word (.docx) import', () => {
  it('reads one line per paragraph', () => {
    const docx = buildDocx(para(text('První odstavec.')) + para(text('Druhý odstavec.')));
    expect(extractDocxText(docx.buffer as ArrayBuffer)).toBe('První odstavec.\nDruhý odstavec.');
  });

  it('joins the runs Word splits a sentence into', () => {
    expect(
      documentXmlToText(para(text('Smluvní '), text('strany se '), text('dohodly.')))
    ).toBe('Smluvní strany se dohodly.');
  });

  it('keeps an empty paragraph as a blank line', () => {
    expect(documentXmlToText(para(text('A')) + '<w:p/>' + para(text('B')))).toBe('A\n\nB');
  });

  it('turns tabs and manual line breaks into tabs and newlines', () => {
    expect(documentXmlToText(para(text('a'), '<w:tab/>', text('b'), '<w:br/>', text('c')))).toBe('a\tb\nc');
  });

  it('lays a table out as tab-separated rows', () => {
    const row = (...cells: string[]) =>
      `<w:tr>${cells.map(c => `<w:tc>${para(text(c))}</w:tc>`).join('')}</w:tr>`;
    expect(documentXmlToText(`<w:tbl>${row('Cena', '100')}${row('Splatnost', '30')}</w:tbl>`)).toBe(
      'Cena\t100\nSplatnost\t30'
    );
  });

  it('leaves out text that a tracked change deleted', () => {
    const body = `<w:p><w:r><w:t xml:space="preserve">Cena je </w:t></w:r>` +
      `<w:del><w:r><w:delText>100</w:delText></w:r></w:del>` +
      `<w:ins><w:r><w:t>200</w:t></w:r></w:ins>` +
      `<w:r><w:t xml:space="preserve"> Kč.</w:t></w:r></w:p>`;
    expect(documentXmlToText(body)).toBe('Cena je 200 Kč.');
  });

  it('leaves out field codes but keeps their result', () => {
    const body = para(
      text('Strana '),
      '<w:fldChar w:fldCharType="begin"/>',
      '<w:instrText>PAGE \\* MERGEFORMAT</w:instrText>',
      '<w:fldChar w:fldCharType="separate"/>',
      text('3'),
      '<w:fldChar w:fldCharType="end"/>'
    );
    expect(documentXmlToText(body)).toBe('Strana 3');
  });

  it('decodes escaped characters', () => {
    expect(documentXmlToText(para(text('&#268;l. 5 &amp; 6 &lt; 7')))).toBe('Čl. 5 & 6 < 7');
  });

  it('explains itself when the file is not a .docx', () => {
    expect(() => extractDocxText(new Uint8Array([1, 2, 3, 4]).buffer as ArrayBuffer)).toThrow(/poškozený|docx/i);
    const noDocument = zipSync({ 'hello.txt': strToU8('hi') });
    expect(() => extractDocxText(noDocument.buffer as ArrayBuffer)).toThrow(/document\.xml/);
  });
});

// --- pdf --------------------------------------------------------------------

describe('PDF import', () => {
  it('reads a page as lines of text', async () => {
    const pdf = buildPdf([line(800, 'Prvni radek'), line(780, 'Druhy radek')].join('\n'));
    expect(await readPdf(pdf)).toBe('Prvni radek\nDruhy radek');
  });

  it('separates pages by a blank line, so each page anchors on its own', async () => {
    const pdf = buildPdf(line(800, 'Strana jedna'), line(800, 'Strana dve'));
    expect(await readPdf(pdf)).toBe('Strana jedna\n\nStrana dve');
  });

  it('rejoins a word hyphenated across a line break', async () => {
    const pdf = buildPdf([line(800, 'dodava-'), line(780, 'tele plni')].join('\n'));
    expect(await readPdf(pdf)).toBe('dodavatele plni');
  });

  it('keeps a real hyphen that happens to end a line', () => {
    // One letter before the hyphen, or a capital after it, means the hyphen is
    // part of the word rather than a typesetter's.
    expect(assembleDocument([[run('e-', { hasEOL: true }), run('mail')]]).text).toBe('e-\nmail');
    expect(assembleDocument([[run('Praha-', { hasEOL: true }), run('Smíchov')]]).text).toBe(
      'Praha-\nSmíchov'
    );
    expect(assembleDocument([[run('dodava-', { hasEOL: true }), run('tele')]]).text).toBe(
      'dodavatele'
    );
  });

  it('says so when a PDF holds no text at all', async () => {
    await expect(readPdf(buildPdf('BT ET'))).rejects.toThrow(/sken|OCR/i);
  });

  it('reports an unreadable PDF instead of throwing raw internals', async () => {
    await expect(readPdf(strToU8('%PDF-1.4 rubbish'))).rejects.toThrow(/nepodařilo|sken/i);
  });

  describe('page assembly', () => {
    it('inserts the space a positional gap stands for', () => {
      expect(
        assemblePageText([
          { str: 'Alfa', hasEOL: false, transform: [12, 0, 0, 12, 50, 800], width: 20, height: 12 },
          { str: 'Beta', hasEOL: false, transform: [12, 0, 0, 12, 110, 800], width: 24, height: 12 },
        ])
      ).toBe('Alfa Beta');
    });

    it('does not insert a space where the runs simply touch', () => {
      expect(
        assemblePageText([
          { str: 'Smlou', hasEOL: false, transform: [12, 0, 0, 12, 50, 800], width: 30, height: 12 },
          { str: 'va', hasEOL: false, transform: [12, 0, 0, 12, 80, 800], width: 12, height: 12 },
        ])
      ).toBe('Smlouva');
    });

    it('does not double a space pdf.js already reported', () => {
      expect(
        assemblePageText([
          { str: 'Alfa', hasEOL: false, transform: [12, 0, 0, 12, 50, 800], width: 20, height: 12 },
          { str: ' ', hasEOL: false, transform: [12, 0, 0, 12, 70, 800], width: 39, height: 0 },
          { str: 'Beta', hasEOL: false, transform: [12, 0, 0, 12, 110, 800], width: 24, height: 12 },
        ])
      ).toBe('Alfa Beta');
    });

    it('treats an empty item carrying hasEOL as the line break it is', () => {
      expect(
        assemblePageText([
          { str: 'A', hasEOL: false, transform: [12, 0, 0, 12, 50, 800], width: 8, height: 12 },
          { str: '', hasEOL: true },
          { str: 'B', hasEOL: false, transform: [12, 0, 0, 12, 50, 780], width: 8, height: 12 },
        ])
      ).toBe('A\nB');
    });

    it('leaves one blank line between pages, and none for a page with nothing on it', () => {
      expect(
        assembleDocument([[run('jedna')], [], [run('dve')]]).text
      ).toBe('jedna\n\ndve');
    });
  });
});

// --- pointing at the page a change happened on ------------------------------

describe('locating text on the page', () => {
  it('records a range for every run, matching the text it produced', async () => {
    const pdf = buildPdf(
      [line(800, 'Cena dila cini 1.500 Kc'), line(780, 'bez DPH.')].join('\n'),
      line(800, 'Priloha 1.')
    );
    const { text, boxes, pages } = await extractPdf(pdf.buffer as ArrayBuffer);

    expect(text).toBe('Cena dila cini 1.500 Kc\nbez DPH.\n\nPriloha 1.');
    expect(pages).toHaveLength(2);
    expect(pages[0].width).toBeCloseTo(595, 0);
    expect(pages[0].height).toBeCloseTo(842, 0);

    // Every box must quote the text it covers, on the page it came from.
    expect(boxes.map(box => [box.page, text.slice(box.start, box.end)])).toEqual([
      [0, 'Cena dila cini 1.500 Kc'],
      [0, 'bez DPH.'],
      [1, 'Priloha 1.'],
    ]);
  });

  it('keeps the ranges honest across a mended hyphen', () => {
    const { text, boxes } = assembleDocument([
      [run('splat-', { hasEOL: true }), run('na do 14 dnu.')],
    ]);
    expect(text).toBe('splatna do 14 dnu.');
    // The hyphen is gone from the text, so it is gone from the run that held it.
    expect(boxes.map(box => text.slice(box.start, box.end))).toEqual(['splat', 'na do 14 dnu.']);
  });

  it('turns a character range into rectangles on the right pages', () => {
    const boxes = [
      { start: 0, end: 10, page: 0, x: 50, y: 700, width: 100, height: 12 },
      { start: 11, end: 21, page: 1, x: 50, y: 600, width: 100, height: 12 },
    ];

    // A range inside one run is interpolated along that run's width.
    expect(rangeRectangles(boxes, 5, 10)).toEqual([
      { page: 0, x: 100, y: 700, width: 50, height: 12 },
    ]);

    // A range spanning both runs yields one rectangle per run, per page.
    const spanning = rangeRectangles(boxes, 8, 14);
    expect(spanning).toHaveLength(2);
    expect(spanning[0]).toMatchObject({ page: 0, x: 130 });
    expect(spanning[1]).toMatchObject({ page: 1, x: 50, width: 30 });

    // Nothing to draw where nothing changed.
    expect(rangeRectangles(boxes, 30, 40)).toEqual([]);
  });
});

describe('mapping changes back onto each document', () => {
  it('gives every segment its place in the source and in the target', () => {
    const a = 'Cena je 100 Kč.';
    const b = 'Cena je 200 Kč.';
    const ranges = segmentRanges(generateSmartDiff(a, b));

    for (const { segment, source, target } of ranges) {
      if (source) expect(a.slice(source[0], source[1])).toBe(segment.originalText ?? segment.text);
      if (target) expect(b.slice(target[0], target[1])).toBe(segment.text);
      if (segment.type === ChangeType.ADDED) expect(source).toBeNull();
      if (segment.type === ChangeType.REMOVED) expect(target).toBeNull();
    }
  });

  it('marks deletions on the source, insertions on the target, case on both', () => {
    const a = 'Smluvní strany do 14 dnů.';
    const b = 'SMLUVNÍ STRANY do 30 dnů od faktury.';
    const ranges = segmentRanges(generateSmartDiff(a, b));

    const onSource = changesOnSide(ranges, 'source').map(r => r.segment.type);
    const onTarget = changesOnSide(ranges, 'target').map(r => r.segment.type);
    expect(onSource).toContain(ChangeType.REMOVED);
    expect(onSource).toContain(ChangeType.CASE_CHANGED);
    expect(onSource).not.toContain(ChangeType.ADDED);
    expect(onTarget).toContain(ChangeType.ADDED);
    expect(onTarget).toContain(ChangeType.CASE_CHANGED);
    expect(onTarget).not.toContain(ChangeType.REMOVED);

    // What is marked on the source really is that document's own wording.
    for (const change of changesOnSide(ranges, 'source')) {
      const range = change.source!;
      expect(a.slice(range[0], range[1])).toBe(change.segment.originalText ?? change.segment.text);
    }
  });

  it('cuts a line into the stretches a change covers and those it does not', () => {
    const line = 'Cena je 100 Kč.';
    const marks = [{ start: 8, end: 11, type: ChangeType.REMOVED, id: 'seg-1' }];

    expect(splitRun(line, 0, marks)).toEqual([
      { text: 'Cena je ', mark: null },
      { text: '100', mark: marks[0] },
      { text: ' Kč.', mark: null },
    ]);

    // A line that is entirely covered, and one the change does not reach.
    expect(splitRun('100', 8, marks)).toEqual([{ text: '100', mark: marks[0] }]);
    expect(splitRun('jiný řádek', 100, marks)).toEqual([{ text: 'jiný řádek', mark: null }]);
  });

  it('cuts a change that runs off the end of one line onto the next', () => {
    const marks = [{ start: 5, end: 25, type: ChangeType.ADDED, id: 'seg-3' }];
    // The first line holds the start of the change, the second holds the rest.
    expect(splitRun('abcdefghij', 0, marks)).toEqual([
      { text: 'abcde', mark: null },
      { text: 'fghij', mark: marks[0] },
    ]);
    // This run covers offsets 10 to 26, and the change stops at 25.
    expect(splitRun('klmnopqrstuvwxyz', 10, marks)).toEqual([
      { text: 'klmnopqrstuvwxy', mark: marks[0] },
      { text: 'z', mark: null },
    ]);
  });

  it('keeps two changes on one line apart', () => {
    const marks = [
      { start: 3, end: 6, type: ChangeType.REMOVED, id: 'a' },
      { start: 9, end: 12, type: ChangeType.CASE_CHANGED, id: 'b' },
    ];
    expect(splitRun('012345678901234', 0, marks).map(p => [p.text, p.mark?.id ?? null])).toEqual([
      ['012', null],
      ['345', 'a'],
      ['678', null],
      ['901', 'b'],
      ['234', null],
    ]);
  });

  it('locates a change on the page of the PDF it came from', async () => {
    const original = buildPdf([line(800, 'Cena dila cini 1.500 Kc'), line(780, 'bez DPH.')].join('\n'));
    const { text, boxes } = await extractPdf(original.buffer as ArrayBuffer);
    const edited = text.replace('1.500', '1.750');

    const ranges = segmentRanges(generateSmartDiff(text, edited));
    const removed = changesOnSide(ranges, 'source').find(r => r.segment.text === '1.500');
    expect(removed).toBeDefined();

    const rectangles = rangeRectangles(boxes, removed!.source![0], removed!.source![1]);
    expect(rectangles).toHaveLength(1);
    // "1.500" sits on the first line, part way along it, not at its start.
    expect(rectangles[0].page).toBe(0);
    expect(rectangles[0].y).toBeCloseTo(800, 0);
    expect(rectangles[0].x).toBeGreaterThan(50);
    expect(rectangles[0].width).toBeGreaterThan(0);
  });
});

// --- dispatching ------------------------------------------------------------

describe('reading a dropped file', () => {
  it('reads plain text and names the document after the file', async () => {
    const imported = await readDocumentFile(asFile('Text smlouvy', 'Návrh v2.txt', 'text/plain'));
    expect(imported).toEqual({ text: 'Text smlouvy', name: 'Návrh v2' });
  });

  it('picks the reader by extension', async () => {
    const docx = await readDocumentFile(asFile(buildDocx(para(text('Z Wordu'))), 'a.docx'));
    expect(docx.text).toBe('Z Wordu');
    const pdf = await readDocumentFile(asFile(buildPdf(line(800, 'Z PDF')), 'b.pdf'));
    expect(pdf.text).toBe('Z PDF');
  });

  it('picks the reader by content type when the name has no extension', async () => {
    const pdf = await readDocumentFile(asFile(buildPdf(line(800, 'Z PDF')), 'sken', 'application/pdf'));
    expect(pdf.text).toBe('Z PDF');
    const docx = await readDocumentFile(
      asFile(
        buildDocx(para(text('Z Wordu'))),
        'dokument',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
      )
    );
    expect(docx.text).toBe('Z Wordu');
  });

  it('tells the user what to do with a format we cannot read', async () => {
    await expect(readDocumentFile(asFile('x', 'stara.doc'))).rejects.toThrow(/\.docx nebo PDF/);
    await expect(readDocumentFile(asFile('x', 'text.rtf'))).rejects.toThrow(/\.docx nebo PDF/);
    // An .rtf claims to be text: reading it as such would spill markup into the
    // panel and look like it worked.
    await expect(readDocumentFile(asFile('{\\rtf1\\ansi x}', 'text.rtf', 'text/rtf'))).rejects.toThrow(
      /\.docx nebo PDF/
    );
    await expect(readDocumentFile(asFile('x', 'stara.doc', 'application/msword'))).rejects.toThrow(
      /\.docx nebo PDF/
    );
    await expect(readDocumentFile(asFile('x', 'tabulka.xlsx'))).rejects.toThrow(/PDF/);
    await expect(readDocumentFile(asFile('x', 'obrazek.png', 'image/png'))).rejects.toThrow(
      new RegExp(ACCEPTED_HINT.replace(/[().]/g, '.'))
    );
  });

  it('refuses a file too large to compare in a browser tab', async () => {
    const huge = { name: 'velky.pdf', size: 80 * 1024 * 1024, type: 'application/pdf' } as File;
    await expect(readDocumentFile(huge)).rejects.toBeInstanceOf(FileImportError);
    await expect(readDocumentFile(huge)).rejects.toThrow(/velký/);
  });

  it('reports a damaged file as a readable message, not a stack trace', async () => {
    await expect(readDocumentFile(asFile('nonsense', 'rozbity.docx'))).rejects.toBeInstanceOf(
      FileImportError
    );
  });
});

// --- the point of all this --------------------------------------------------

describe('comparing an imported PDF with an imported Word document', () => {
  it('finds the wording change and ignores how each format broke its lines', async () => {
    // The same clause: the PDF has it typeset across two lines with a hyphen, the
    // Word document has it as one paragraph, and the amount differs.
    const pdf = await readDocumentFile(
      asFile(
        buildPdf([line(800, 'Cena dila cini 1.500 Kc bez DPH a je splat-'), line(780, 'na do 14 dnu.')].join('\n')),
        'navrh.pdf'
      )
    );
    const docx = await readDocumentFile(
      asFile(buildDocx(para(text('Cena dila cini 1.750 Kc bez DPH a je splatna do 14 dnu.'))), 'revize.docx')
    );

    const segments = generateSmartDiff(pdf.text, docx.text);
    const stats = summarizeDiff(segments, pdf.text, docx.text);
    expect(stats).toMatchObject({ added: 1, removed: 1, caseChanged: 0 });
    expect(
      segments
        .filter(s => s.type !== ChangeType.UNCHANGED)
        .map(s => s.text)
    ).toEqual(['1.500', '1.750']);
  });
});
