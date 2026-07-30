import React, { useState, useEffect } from 'react';
import { X, Table, Copy, Check } from 'lucide-react';
import { Document, DiffSegment, ChangeType } from '../types';
import { escapeHtml } from '../utils/html';

interface ExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  sourceDoc: Document;
  targetDoc: Document;
  segments: DiffSegment[];
  settings: {
    accentColor: string;
    theme: 'light' | 'dark' | 'slate';
  };
}

const ExportModal: React.FC<ExportModalProps> = ({ 
  isOpen, 
  onClose, 
  sourceDoc, 
  targetDoc, 
  segments,
  settings 
}) => {
  const [copied, setCopied] = useState(false);
  const redlineTextPlain = segments.map(s => s.text).join('');

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isOpen, onClose]);

  const getSegmentHtml = (s: DiffSegment) => {
    if (s.type === ChangeType.ADDED) return `<span style="color: #3b82f6; text-decoration: underline;">${escapeHtml(s.text)}</span>`;
    if (s.type === ChangeType.REMOVED) return `<span style="color: #ef4444; text-decoration: line-through;">${escapeHtml(s.text)}</span>`;
    // Same word, different case: underlined in the body colour.
    if (s.type === ChangeType.CASE_CHANGED) return `<span style="text-decoration: underline;">${escapeHtml(s.text)}</span>`;
    return escapeHtml(s.text);
  };

  const handleCopyTable = () => {
    const redlineHtml = segments.map(getSegmentHtml).join('');
    
    const tableHtml = `
      <table border="1" style="border-collapse: collapse; font-family: Arial, sans-serif; font-size: 10pt;">
        <thead>
          <tr>
            <th style="padding: 8px; text-align: left; background-color: #eeeeee;">${escapeHtml(sourceDoc.name)}</th>
            <th style="padding: 8px; text-align: left; background-color: #eeeeee;">${escapeHtml(targetDoc.name)}</th>
            <th style="padding: 8px; text-align: left; background-color: #eeeeee;">Redline</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td style="padding: 8px; vertical-align: top; white-space: pre-wrap;">${escapeHtml(sourceDoc.text)}</td>
            <td style="padding: 8px; vertical-align: top; white-space: pre-wrap;">${escapeHtml(targetDoc.text)}</td>
            <td style="padding: 8px; vertical-align: top; white-space: pre-wrap;">${redlineHtml}</td>
          </tr>
        </tbody>
      </table>
    `;

    const blob = new Blob([tableHtml], { type: 'text/html' });
    const data = [new ClipboardItem({ 
      'text/html': blob, 
      'text/plain': new Blob([`${sourceDoc.name}\t${targetDoc.name}\tRedline\n${sourceDoc.text}\t${targetDoc.text}\t${redlineTextPlain}`], { type: 'text/plain' }) 
    })];
    
    navigator.clipboard.write(data).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center p-4">
      <div 
        onClick={onClose}
        className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm"
      />
      <div 
        className="relative w-full max-w-5xl bg-white rounded-2xl shadow-2xl overflow-hidden border border-slate-200 flex flex-col max-h-[90vh]"
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 shrink-0">
          <h2 className="text-lg font-bold text-slate-800 flex items-center gap-2">
            <Table className="w-5 h-5" style={{ color: settings.accentColor }} />
            Export do tabulky
          </h2>
          <button 
            onClick={onClose}
            className="p-1 rounded-full hover:bg-slate-100 text-slate-400"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 overflow-auto flex-1">
          <div className="border border-slate-300 w-full">
            <table className="w-full border-collapse table-fixed" style={{ fontFamily: 'Arial, sans-serif', fontSize: '10pt' }}>
              <thead>
                <tr className="bg-slate-100 border-b border-slate-300">
                  <th className="px-3 py-2 text-left border-r border-slate-300 text-slate-700 w-1/3 overflow-hidden text-ellipsis">{sourceDoc.name}</th>
                  <th className="px-3 py-2 text-left border-r border-slate-300 text-slate-700 w-1/3 overflow-hidden text-ellipsis">{targetDoc.name}</th>
                  <th className="px-3 py-2 text-left text-slate-700 w-1/3 overflow-hidden text-ellipsis">Redline</th>
                </tr>
              </thead>
              <tbody>
                <tr className="align-top">
                  <td className="px-3 py-3 border-r border-slate-200 whitespace-pre-wrap text-slate-800">{sourceDoc.text}</td>
                  <td className="px-3 py-3 border-r border-slate-200 whitespace-pre-wrap text-slate-800">{targetDoc.text}</td>
                  <td className="px-3 py-3 whitespace-pre-wrap text-slate-800">
                    {segments.map((s, i) => {
                      if (s.type === ChangeType.ADDED) return <span key={i} className="text-blue-600 underline decoration-blue-300">{s.text}</span>;
                      if (s.type === ChangeType.REMOVED) return <span key={i} className="text-red-500 line-through decoration-red-300">{s.text}</span>;
                      if (s.type === ChangeType.CASE_CHANGED) return <span key={i} className="underline decoration-dotted decoration-amber-500">{s.text}</span>;
                      return <span key={i}>{s.text}</span>;
                    })}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div className="px-6 py-4 bg-slate-50 border-t border-slate-100 flex justify-between items-center shrink-0">
          <p className="text-xs text-slate-400 italic">Tabulka je formátována písmem Arial 10pt s barevným vyznačením změn.</p>
          <div className="flex gap-3">
            <button 
              onClick={handleCopyTable}
              className="px-6 py-2 text-white rounded-lg text-sm font-bold shadow-lg flex items-center gap-2 min-w-[140px] justify-center"
              style={{ backgroundColor: copied ? '#10b981' : settings.accentColor, boxShadow: `0 10px 15px -3px ${copied ? '#10b981' : settings.accentColor}33` }}
            >
              {copied ? (
                <>
                  <Check className="w-4 h-4" />
                  Zkopírováno
                </>
              ) : (
                <>
                  <Copy className="w-4 h-4" />
                  Kopírovat
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ExportModal;
