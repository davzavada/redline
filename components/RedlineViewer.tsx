import React, { useState } from 'react';
import { ChangeType, DiffSegment } from '../types';
import { Check, Copy, Table } from 'lucide-react';
import { escapeHtml } from '../utils/html';

interface RedlineViewerProps {
  segments: DiffSegment[];
  scrollRef?: React.RefObject<HTMLDivElement | null>;
  onScroll?: (e: React.UIEvent<HTMLDivElement>) => void;
  onOpenExport?: () => void;
  settings: {
    font: 'sans' | 'serif' | 'mono';
    theme: 'light' | 'dark' | 'slate';
    accentColor: string;
    fontSize: number;
  };
}

const RedlineViewer: React.FC<RedlineViewerProps> = ({ segments, scrollRef, onScroll, onOpenExport, settings }) => {
  const [copied, setCopied] = useState(false);

  const handleCopyRedline = async () => {
    const plainText = segments.map(s => {
      if (s.type === ChangeType.ADDED) return `[+${s.text}+]`;
      if (s.type === ChangeType.REMOVED) return `[-${s.text}-]`;
      return s.text;
    }).join('');

    const htmlText = `<div style="font-family: Arial, sans-serif; font-size: 10pt; white-space: pre-wrap;">${segments.map(s => {
      if (s.type === ChangeType.ADDED) return `<span style="color: #3b82f6; text-decoration: underline;">${escapeHtml(s.text)}</span>`;
      if (s.type === ChangeType.REMOVED) return `<span style="color: #ef4444; text-decoration: line-through;">${escapeHtml(s.text)}</span>`;
      return escapeHtml(s.text);
    }).join('')}</div>`;

    try {
      const blobHtml = new Blob([htmlText], { type: 'text/html' });
      const blobPlain = new Blob([plainText], { type: 'text/plain' });
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/html': blobHtml,
          'text/plain': blobPlain
        })
      ]);
    } catch (e) {
      navigator.clipboard.writeText(plainText);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (segments.length === 0) {
    return (
      <div className={`flex items-center justify-center h-full text-sm ${settings.theme === 'dark' ? 'text-slate-500' : 'text-slate-400'}`}>
        <p>Nebyly zjištěny žádné změny.</p>
      </div>
    );
  }

  const isDark = settings.theme === 'dark';
  const themeBg = isDark ? 'bg-slate-950' : 'bg-white';
  const textMain = isDark ? 'text-slate-200' : 'text-slate-800';
  const addedClass = isDark
    ? 'bg-blue-500/20 text-blue-300 underline decoration-blue-500/60 decoration-2 underline-offset-2 mx-0.5 rounded-sm px-0.5'
    : 'bg-blue-100 text-blue-700 underline decoration-blue-300 decoration-2 underline-offset-2 mx-0.5 rounded-sm px-0.5';
  const removedClass = isDark
    ? 'bg-red-500/10 text-red-400 line-through decoration-red-500/50 decoration-2 mx-0.5 opacity-80'
    : 'bg-red-50 text-red-600 line-through decoration-red-400/50 decoration-2 mx-0.5 opacity-80';

  return (
    <div className={`flex flex-col h-full relative ${themeBg}`}>
      <div 
        ref={scrollRef}
        onScroll={onScroll}
        className={`flex-1 leading-relaxed whitespace-pre-wrap p-4 overflow-y-auto ${textMain}`}
        style={{ fontSize: `${settings.fontSize}px` }}
      >
        {segments.map((segment) => {
          switch (segment.type) {
            case ChangeType.ADDED:
              return (
                <span
                  key={segment.id}
                  className={addedClass}
                  id={segment.id}
                >
                  {segment.text}
                </span>
              );
            case ChangeType.REMOVED:
              return (
                <span
                  key={segment.id}
                  className={removedClass}
                  id={segment.id}
                >
                  {segment.text}
                </span>
              );
            case ChangeType.UNCHANGED:
            default:
              return <span key={segment.id}>{segment.text}</span>;
          }
        })}
      </div>
      
      <div className="absolute bottom-4 right-4 flex items-center gap-2 group">
        <button 
          onClick={onOpenExport}
          className="bg-slate-800 text-white p-2 rounded-full shadow-lg hover:bg-slate-700 transition-colors flex items-center gap-2 px-4 text-xs font-bold ring-2 ring-white/20"
        >
          <Table className="w-4 h-4" />
          Tabulka
        </button>
        <button
          onClick={handleCopyRedline}
          className={`${copied ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-blue-600 hover:bg-blue-500'} text-white p-2 rounded-full shadow-lg transition-colors flex items-center gap-2 px-4 text-xs font-bold ring-2 ring-white/20`}
        >
          {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
          {copied ? 'Zkopírováno' : 'Kopírovat'}
        </button>
      </div>
    </div>
  );
};

export default RedlineViewer;