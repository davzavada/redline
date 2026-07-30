import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ChangeType, DiffSegment, Document } from '../types';
import {
  DocumentSide,
  Mark,
  RunPart,
  changesOnSide,
  marksOnSide,
  segmentRanges,
  sourceTextOf,
  splitRun,
  targetTextOf,
} from '../utils/segmentRanges';
import { PdfSource, PdfTextBox, documentOptions, loadPdfjs } from '../utils/pdfText';

// Reading view: the comparison shown as the document it came from, one page after
// another, with the changes marked where they happened.
//
// A PDF is rendered by pdf.js onto a canvas — the real pages, as they print — and
// the marks are laid over it, positioned from the geometry recorded while the text
// was extracted. Anything else (a Word document, pasted text) has no pages to
// render, so it is typeset here into a sheet of the same proportions; printing that
// from the browser is what turns a Word document into a PDF.

interface ViewerDocument {
  document: Document;
  /** Present only for a document imported from a PDF and not edited since. */
  pdf?: PdfSource;
}

interface DocumentViewerProps {
  segments: DiffSegment[];
  source: ViewerDocument;
  target: ViewerDocument;
  settings: {
    font: 'sans' | 'serif' | 'mono';
    theme: 'light' | 'dark' | 'slate';
    fontSize: number;
  };
  onExit: () => void;
}

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 2.5;

const fontClass = { sans: 'font-sans', serif: 'font-serif', mono: 'font-mono' };

/** A4 in CSS pixels at 96 dpi, the sheet a typeset document is laid out on. */
const SHEET_WIDTH = 794;
const SHEET_HEIGHT = 1123;

const markClasses = (type: ChangeType, isDark: boolean): string => {
  if (type === ChangeType.ADDED) {
    return isDark
      ? 'bg-blue-500/25 text-blue-200 underline decoration-blue-400 decoration-2 underline-offset-2'
      : 'bg-blue-100 text-blue-700 underline decoration-blue-400 decoration-2 underline-offset-2';
  }
  if (type === ChangeType.REMOVED) {
    return isDark
      ? 'bg-red-500/20 text-red-300 line-through decoration-red-400 decoration-2'
      : 'bg-red-50 text-red-600 line-through decoration-red-400 decoration-2';
  }
  return isDark
    ? 'underline decoration-dotted decoration-amber-400 decoration-2 underline-offset-2'
    : 'underline decoration-dotted decoration-amber-500 decoration-2 underline-offset-2';
};

/**
 * How a change is drawn over a rendered page. The glyphs themselves come from the
 * canvas underneath, so these only tint and underline — never paint over the text.
 */
const pageMarkStyle = (type: ChangeType): React.CSSProperties => {
  if (type === ChangeType.ADDED) {
    return { backgroundColor: 'rgba(59, 130, 246, 0.26)', boxShadow: 'inset 0 -2px 0 #3b82f6' };
  }
  if (type === ChangeType.REMOVED) {
    return {
      backgroundColor: 'rgba(239, 68, 68, 0.26)',
      backgroundImage: 'linear-gradient(transparent 45%, #ef4444 45%, #ef4444 58%, transparent 58%)',
    };
  }
  return { boxShadow: 'inset 0 -2px 0 #f59e0b' };
};

// -----------------------------------------------------------------------------
// A single rendered PDF page, drawn when it comes into view
// -----------------------------------------------------------------------------

/** Turns a PDF-space point into a point on the rendered page. */
type ViewportPoint = (x: number, y: number) => number[];

interface PdfPageProxy {
  getViewport: (options: { scale: number }) => {
    width: number;
    height: number;
    convertToViewportPoint: ViewportPoint;
  };
  render: (options: { canvasContext: CanvasRenderingContext2D; viewport: unknown }) => {
    promise: Promise<void>;
    cancel: () => void;
  };
  cleanup: () => void;
}

interface PdfDocumentProxy {
  numPages: number;
  getPage: (pageNumber: number) => Promise<PdfPageProxy>;
}

/**
 * One run of text off a PDF page, laid over the rendered canvas as transparent,
 * selectable text. The horizontal scale is normalised so the run occupies exactly
 * the width the PDF says it does — which is what makes a mark sit on the very
 * characters that changed, rather than drifting along the line as a plain
 * character-by-character estimate does.
 */
const PageTextRun: React.FC<{
  left: number;
  top: number;
  fontSize: number;
  fontFamily: string;
  width: number;
  parts: RunPart[];
  activeId: string | null;
}> = ({ left, top, fontSize, fontFamily, width, parts, activeId }) => {
  const ref = useRef<HTMLSpanElement>(null);
  const text = parts.map(part => part.text).join('');

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element || !width) return;

    element.style.transform = 'none';
    element.style.wordSpacing = 'normal';
    const natural = element.getBoundingClientRect().width;
    if (natural <= 0) return;

    // A justified line is wider than its words because the PDF widened the gaps
    // between them, not the letters. Widening the gaps here too keeps every word
    // where the page has it; smearing the difference across the glyphs instead
    // walks the later words out of place. Everything else — a font we do not have,
    // so the browser substituted another — is a glyph-width difference, and that
    // is what a horizontal scale is for.
    const spaces = (text.match(/ /g) ?? []).length;
    const residual = width - natural;
    if (spaces > 0 && residual > 0.5) {
      element.style.wordSpacing = `${residual / spaces}px`;
    } else {
      element.style.transform = `scaleX(${width / natural})`;
    }
  }, [left, top, fontSize, fontFamily, width, text]);

  return (
    <span
      ref={ref}
      className="absolute whitespace-pre text-transparent origin-top-left"
      style={{ left, top, fontSize, fontFamily, lineHeight: 1 }}
    >
      {parts.map((part, index) =>
        part.mark ? (
          <span
            key={index}
            id={`view-${part.mark.id}`}
            className={`rounded-sm ${part.mark.id === activeId ? 'outline outline-2 outline-slate-900/50' : ''}`}
            style={pageMarkStyle(part.mark.type)}
          >
            {part.text}
          </span>
        ) : (
          <span key={index}>{part.text}</span>
        )
      )}
    </span>
  );
};

const PdfPageView: React.FC<{
  pdf: PdfDocumentProxy;
  pageNumber: number;
  width: number;
  height: number;
  zoom: number;
  text: string;
  boxes: PdfTextBox[];
  marks: Mark[];
  activeId: string | null;
  isDark: boolean;
  /** Draw regardless of where the page is — printing needs all of them. */
  force: boolean;
  /** Called with true once the page is on the canvas, false when it is let go. */
  onDrawn: (pageNumber: number, drawn: boolean) => void;
}> = ({ pdf, pageNumber, width, height, zoom, text, boxes, marks, activeId, isDark, force, onDrawn }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const holderRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(pageNumber <= 2);
  const [viewport, setViewport] = useState<{ convertToViewportPoint: ViewportPoint } | null>(null);

  // Draw only what is on screen or nearly, and let a page go once it is well out
  // of the way: a hundred-page contract would otherwise draw a hundred canvases
  // before showing anything, and hold every one of them in memory afterwards.
  useEffect(() => {
    const holder = holderRef.current;
    if (!holder || typeof IntersectionObserver !== 'function') {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      entries => setVisible(entries.some(entry => entry.isIntersecting)),
      { rootMargin: '1500px 0px' }
    );
    observer.observe(holder);
    return () => observer.disconnect();
  }, []);

  const draw = visible || force;

  useEffect(() => {
    if (draw) return;
    setViewport(null);
    onDrawn(pageNumber, false);
    const canvas = canvasRef.current;
    if (canvas) {
      canvas.width = 0;
      canvas.height = 0;
    }
  }, [draw, pageNumber, onDrawn]);

  useEffect(() => {
    if (!draw) return;
    let cancelled = false;
    let task: { cancel: () => void } | null = null;

    (async () => {
      const page = await pdf.getPage(pageNumber);
      if (cancelled) return;
      const scale = zoom * Math.min(2, typeof devicePixelRatio === 'number' ? devicePixelRatio : 1);
      const rendered = page.getViewport({ scale });
      const canvas = canvasRef.current;
      const context = canvas?.getContext('2d');
      if (!canvas || !context) return;
      canvas.width = Math.floor(rendered.width);
      canvas.height = Math.floor(rendered.height);
      const render = page.render({ canvasContext: context, viewport: rendered });
      task = render;
      try {
        await render.promise;
      } catch {
        // A cancelled render is the normal outcome of scrolling or zooming away.
        return;
      }
      if (cancelled) return;
      setViewport(page.getViewport({ scale: zoom }));
      onDrawn(pageNumber, true);
    })();

    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [pdf, pageNumber, zoom, draw]);

  return (
    <div
      ref={holderRef}
      data-page={pageNumber}
      data-drawn={viewport ? 'true' : 'false'}
      className={`relative shrink-0 shadow-lg ${isDark ? 'bg-slate-800' : 'bg-white'}`}
      style={{ width: width * zoom, height: height * zoom }}
    >
      <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" />
      {viewport && (
        <div className="absolute inset-0 cursor-text select-text" data-text-layer>
          {boxes.map(box => {
            const runText = text.slice(box.start, box.end);
            if (!runText) return null;
            // The stored y is the baseline; the line of text stands on it.
            const fontSize = (box.height || 10) * zoom;
            const [x, baseline] = viewport.convertToViewportPoint(box.x, box.y);
            return (
              <PageTextRun
                key={box.start}
                left={x}
                top={baseline - fontSize * (box.ascent ?? 0.82)}
                fontSize={fontSize}
                fontFamily={box.fontFamily || 'serif'}
                width={box.width * zoom}
                parts={splitRun(runText, box.start, marks)}
                activeId={activeId}
              />
            );
          })}
        </div>
      )}
    </div>
  );
};

// -----------------------------------------------------------------------------
// The viewer
// -----------------------------------------------------------------------------

const DocumentViewer: React.FC<DocumentViewerProps> = ({
  segments,
  source,
  target,
  settings,
  onExit,
}) => {
  const [side, setSide] = useState<DocumentSide>('target');
  const [zoom, setZoom] = useState(1);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [pdf, setPdf] = useState<PdfDocumentProxy | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [printing, setPrinting] = useState(false);
  const pagesRef = useRef<HTMLDivElement>(null);
  /** Pages currently on a canvas — what printing has to wait for. */
  const drawnPages = useRef<Set<number>>(new Set());
  const notePageDrawn = useCallback((pageNumber: number, drawn: boolean) => {
    if (drawn) drawnPages.current.add(pageNumber);
    else drawnPages.current.delete(pageNumber);
  }, []);

  const isDark = settings.theme === 'dark';
  const shown = side === 'source' ? source : target;

  // The recorded geometry only describes the text as it was imported; once the
  // panel has been edited the pages no longer match, and the typeset sheet is the
  // honest thing to show.
  const pdfSource = shown.pdf && shown.pdf.text === shown.document.text ? shown.pdf : undefined;

  const ranges = useMemo(() => segmentRanges(segments), [segments]);
  const changes = useMemo(() => changesOnSide(ranges, side), [ranges, side]);
  const marks = useMemo(() => marksOnSide(ranges, side), [ranges, side]);

  /** Which page each change is on, for jumping to one that is not drawn yet. */
  const pageOfMark = useMemo(() => {
    const pages = new Map<string, number>();
    for (const mark of marks) {
      const box = pdfSource?.boxes.find(entry => entry.end > mark.start && entry.start < mark.end);
      if (box) pages.set(mark.id, box.page);
    }
    return pages;
  }, [marks, pdfSource]);

  /** The runs of text on each page, so a page only lays out its own. */
  const boxesByPage = useMemo(() => {
    const byPage = new Map<number, PdfTextBox[]>();
    for (const box of pdfSource?.boxes ?? []) {
      const list = byPage.get(box.page);
      if (list) list.push(box);
      else byPage.set(box.page, [box]);
    }
    return byPage;
  }, [pdfSource]);

  // Load (and unload) the rendered document for whichever side is on screen.
  useEffect(() => {
    if (!pdfSource) {
      setPdf(null);
      setPdfError(null);
      return;
    }
    let cancelled = false;
    let task: { promise: Promise<unknown>; destroy: () => Promise<void> } | null = null;
    setPdfError(null);
    (async () => {
      try {
        const pdfjs = await loadPdfjs();
        if (cancelled) return;
        task = pdfjs.getDocument(documentOptions(pdfSource.bytes));
        const loaded = (await task.promise) as PdfDocumentProxy;
        if (cancelled) return;
        setPdf(loaded);
      } catch (error) {
        if (!cancelled) {
          setPdf(null);
          setPdfError(error instanceof Error ? error.message : 'PDF se nepodařilo zobrazit.');
        }
      }
    })();
    return () => {
      cancelled = true;
      setPdf(null);
      void task?.destroy().catch(() => undefined);
    };
  }, [pdfSource]);

  /**
   * Pages are only drawn while they are near the screen, which keeps a long
   * document from holding a canvas per page in memory. Paper does not scroll, so
   * printing first draws every page and waits for them — otherwise the pages that
   * happened to be off screen would come out blank.
   */
  const handlePrint = async () => {
    if (!pdfSource) {
      window.print();
      return;
    }
    setPrinting(true);
    try {
      const total = pdfSource.pages.length;
      const deadline = Date.now() + 30000;
      await new Promise<void>(resolve => {
        const check = () => {
          if (drawnPages.current.size >= total || Date.now() > deadline) resolve();
          else window.setTimeout(check, 100);
        };
        window.setTimeout(check, 100);
      });
      window.print();
    } finally {
      setPrinting(false);
    }
  };

  const goToChange = (direction: 1 | -1) => {
    if (!changes.length) return;
    let next = activeIndex + direction;
    if (next >= changes.length) next = 0;
    if (next < 0) next = changes.length - 1;
    setActiveIndex(next);

    const id = changes[next].segment.id;
    const element = document.getElementById(`view-${id}`);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    // The change is on a page far enough away that it has not been drawn. Go to
    // the page first; the mark can be reached once it is there.
    const page = pageOfMark.get(id);
    if (page === undefined) return;
    document
      .querySelector(`[data-page="${page + 1}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    window.setTimeout(() => {
      document.getElementById(`view-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 500);
  };

  useEffect(() => setActiveIndex(-1), [side, segments]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.altKey && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
        event.preventDefault();
        goToChange(event.key === 'ArrowDown' ? 1 : -1);
      }
      if (event.key === 'Escape') onExit();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  });

  const activeId = activeIndex >= 0 ? changes[activeIndex]?.segment.id ?? null : null;

  const chipClass = (active: boolean) =>
    `text-[10px] px-2 py-1 rounded border transition-colors ${
      active
        ? isDark
          ? 'bg-slate-700 text-slate-100 border-slate-600 font-medium'
          : 'bg-white text-slate-800 border-slate-300 font-medium shadow-sm'
        : isDark
          ? 'text-slate-500 border-transparent hover:text-slate-300'
          : 'text-slate-500 border-transparent hover:text-slate-800'
    }`;

  const iconButton = `p-1 rounded disabled:opacity-30 ${
    isDark ? 'hover:bg-slate-700 text-slate-400' : 'hover:bg-slate-100 text-slate-500'
  }`;

  return (
    <div className={`flex flex-col h-full overflow-hidden ${isDark ? 'bg-slate-950' : 'bg-slate-100'}`}>
      {/* Toolbar */}
      <div
        className={`shrink-0 flex items-center gap-3 px-3 h-[42px] border-b print:hidden ${
          isDark ? 'bg-slate-900 border-slate-800' : 'bg-slate-50 border-slate-200'
        }`}
      >
        <span className="text-xs font-bold uppercase tracking-wider text-slate-500">Dokument</span>

        <div className={`flex gap-1 rounded-md p-0.5 ${isDark ? 'bg-slate-800' : 'bg-slate-200/60'}`}>
          <button onClick={() => setSide('source')} className={chipClass(side === 'source')}>
            Původní · {source.document.name}
          </button>
          <button onClick={() => setSide('target')} className={chipClass(side === 'target')}>
            Upravený · {target.document.name}
          </button>
        </div>

        <div className="flex items-center gap-1 ml-auto">
          <button onClick={() => goToChange(-1)} disabled={!changes.length} className={iconButton} title="Předchozí změna (Alt+↑)">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
          </button>
          <span className="text-[10px] font-mono text-slate-500 min-w-[3.5rem] text-center">
            {changes.length ? activeIndex + 1 : 0} / {changes.length}
          </span>
          <button onClick={() => goToChange(1)} disabled={!changes.length} className={iconButton} title="Další změna (Alt+↓)">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
          </button>
        </div>

        <div className={`flex items-center border rounded-md ${isDark ? 'border-slate-700' : 'border-slate-200 bg-white'}`}>
          <button onClick={() => setZoom(z => Math.max(MIN_ZOOM, Math.round((z - 0.1) * 10) / 10))} disabled={zoom <= MIN_ZOOM} className={`${iconButton} px-2`}>−</button>
          <span className="text-[10px] font-mono text-slate-500 w-10 text-center">{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom(z => Math.min(MAX_ZOOM, Math.round((z + 0.1) * 10) / 10))} disabled={zoom >= MAX_ZOOM} className={`${iconButton} px-2`}>+</button>
        </div>

        <button
          onClick={handlePrint}
          className={`text-[10px] px-2 py-1 rounded border font-bold uppercase tracking-wide ${
            isDark ? 'border-slate-700 text-slate-400 hover:text-slate-200' : 'border-slate-200 bg-white text-slate-500 hover:text-slate-800'
          }`}
          title="Vytisknout, nebo uložit jako PDF"
        >
          Tisk / PDF
        </button>

        <button onClick={onExit} className={iconButton} title="Zpět na sloupce (Esc)">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
        </button>
      </div>

      {/* Pages */}
      <div className="flex-1 overflow-auto print:overflow-visible">
        <div
          ref={pagesRef}
          className="flex flex-col items-center gap-4 py-6 print:py-0 print:gap-0"
          data-viewer-pages
        >
          {pdfError && (
            <p className="text-xs text-red-500 px-4 py-8">{pdfError}</p>
          )}

          {pdf && pdfSource
            ? pdfSource.pages.map((page, index) => (
                <PdfPageView
                  key={index}
                  pdf={pdf}
                  pageNumber={index + 1}
                  width={page.width}
                  height={page.height}
                  zoom={zoom}
                  text={pdfSource.text}
                  boxes={boxesByPage.get(index) ?? []}
                  marks={marks}
                  activeId={activeId}
                  isDark={isDark}
                  force={printing}
                  onDrawn={notePageDrawn}
                />
              ))
            : !pdfError && (
                <TypesetSheet
                  segments={segments}
                  side={side}
                  zoom={zoom}
                  activeId={activeId}
                  settings={settings}
                  isPdfPending={Boolean(pdfSource)}
                />
              )}
        </div>
      </div>

      <div
        className={`shrink-0 px-3 h-[24px] border-t text-[9px] flex items-center justify-between print:hidden ${
          isDark ? 'bg-slate-900 border-slate-800 text-slate-500' : 'bg-white border-slate-200 text-slate-400'
        }`}
      >
        <span>
          {pdfSource
            ? `${pdfSource.pages.length} ${pdfSource.pages.length === 1 ? 'strana' : pdfSource.pages.length < 5 ? 'strany' : 'stran'} · vykresleno z PDF`
            : shown.pdf
              ? 'Text byl upraven — zobrazuji vysázený text místo původních stran PDF'
              : 'Vysázeno z textu dokumentu'}
        </span>
        <span>
          {side === 'source' ? 'Škrtnutý text = smazáno' : 'Podtržený text = přidáno'} · tečkovaně = velikost písmen
        </span>
      </div>
    </div>
  );
};

// -----------------------------------------------------------------------------
// The sheet a document without pages of its own is typeset onto
// -----------------------------------------------------------------------------

const TypesetSheet: React.FC<{
  segments: DiffSegment[];
  side: DocumentSide;
  zoom: number;
  activeId: string | null;
  settings: DocumentViewerProps['settings'];
  isPdfPending: boolean;
}> = ({ segments, side, zoom, activeId, settings, isPdfPending }) => {
  const isDark = settings.theme === 'dark';
  const pick = side === 'source' ? sourceTextOf : targetTextOf;

  return (
    <div
      className={`shrink-0 shadow-lg print:shadow-none ${isDark ? 'bg-slate-900' : 'bg-white'} ${fontClass[settings.font]}`}
      style={{
        width: SHEET_WIDTH * zoom,
        minHeight: SHEET_HEIGHT * zoom,
        padding: `${76 * zoom}px ${76 * zoom}px`,
      }}
    >
      {isPdfPending && (
        <p className="text-[10px] text-slate-400 mb-4 print:hidden">Vykresluji strany PDF…</p>
      )}
      <div
        className={`whitespace-pre-wrap leading-relaxed ${isDark ? 'text-slate-200' : 'text-slate-800'}`}
        style={{ fontSize: `${settings.fontSize * zoom}px` }}
      >
        {segments.map(segment => {
          const text = pick(segment);
          if (!text) return null;
          if (segment.type === ChangeType.UNCHANGED) return <span key={segment.id}>{text}</span>;
          return (
            <span
              key={segment.id}
              id={`view-${segment.id}`}
              className={`${markClasses(segment.type, isDark)} rounded-sm ${
                segment.id === activeId ? 'ring-2 ring-slate-900/30' : ''
              }`}
            >
              {text}
            </span>
          );
        })}
      </div>
    </div>
  );
};

export default DocumentViewer;
