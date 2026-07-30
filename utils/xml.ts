// Just enough XML walking to pull the text out of an Office file part.
//
// DOMParser would do this in the browser, but a hand-rolled scanner keeps the
// extraction identical in Node, so the importers can be tested for real instead
// of against a DOM shim.

export interface XmlTag {
  /** Qualified name, e.g. "w:t". */
  name: string;
  /** True for `</w:p>`. */
  closing: boolean;
  /** True for `<w:tab/>`. */
  selfClosing: boolean;
  /** The raw attribute text, for the rare attribute we care about. */
  attributes: string;
}

const ENTITIES: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
};

/** Resolves the entities an Office document part can contain. */
export const decodeXmlText = (text: string): string =>
  text.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (whole, body: string) => {
    if (body[0] === '#') {
      const code =
        body[1] === 'x' || body[1] === 'X'
          ? Number.parseInt(body.slice(2), 16)
          : Number.parseInt(body.slice(1), 10);
      return Number.isFinite(code) && code > 0 ? String.fromCodePoint(code) : whole;
    }
    return ENTITIES[body] ?? whole;
  });

/**
 * Walks `xml` start to finish, reporting every tag and every run of text between
 * tags. Comments, CDATA, declarations and processing instructions are skipped;
 * quoted attribute values may contain `>`.
 */
export const walkXml = (
  xml: string,
  onTag: (tag: XmlTag) => void,
  onText: (text: string) => void
): void => {
  let i = 0;
  while (i < xml.length) {
    const open = xml.indexOf('<', i);
    if (open < 0) {
      if (i < xml.length) onText(xml.slice(i));
      return;
    }
    if (open > i) onText(xml.slice(i, open));

    if (xml.startsWith('<!--', open)) {
      const end = xml.indexOf('-->', open + 4);
      i = end < 0 ? xml.length : end + 3;
      continue;
    }
    if (xml.startsWith('<![CDATA[', open)) {
      const end = xml.indexOf(']]>', open + 9);
      const stop = end < 0 ? xml.length : end;
      onText(xml.slice(open + 9, stop));
      i = end < 0 ? xml.length : end + 3;
      continue;
    }

    let cursor = open + 1;
    let quote = '';
    while (cursor < xml.length) {
      const char = xml[cursor];
      if (quote) {
        if (char === quote) quote = '';
      } else if (char === '"' || char === "'") {
        quote = char;
      } else if (char === '>') {
        break;
      }
      cursor++;
    }
    if (cursor >= xml.length) return;

    const raw = xml.slice(open + 1, cursor);
    i = cursor + 1;

    // <?xml ... ?> and <!DOCTYPE ...>
    if (raw[0] === '?' || raw[0] === '!') continue;

    const closing = raw[0] === '/';
    const body = closing ? raw.slice(1) : raw;
    const selfClosing = body.endsWith('/');
    const inner = selfClosing ? body.slice(0, -1) : body;
    const split = inner.search(/[\s/]/);
    onTag({
      name: split < 0 ? inner : inner.slice(0, split),
      closing,
      selfClosing,
      attributes: split < 0 ? '' : inner.slice(split),
    });
  }
};
