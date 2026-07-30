import React, { useState, useEffect, useRef } from 'react';
import { Document, DiffSegment, ChangeType } from './types';
import InputPanel from './components/InputPanel';
import RedlineViewer from './components/RedlineViewer';
import ExportModal from './components/ExportModal';
import { DiffStats, generateSmartDiff, summarizeDiff } from './utils/diffEngine';
import { Settings as SettingsIcon } from 'lucide-react';

const DEMO_ORIGINAL = `Příliš žluťoučký kůň úpěl ďábelské ódy.`;

const DEMO_MODIFIED = `Příliš žluťoučký kůň úpěl ďábelské ódy na měsíci.`;

interface Settings {
  font: 'sans' | 'serif' | 'mono';
  theme: 'light' | 'dark' | 'slate';
  accentColor: string;
  fontSize: number;
  removeLineBreaks: boolean;
  removeExtraSpaces: boolean;
}

const SETTINGS_KEY = 'redline-settings';
const WORKSPACE_KEY = 'redline-workspace';

// Czech pluralization helper: 1 → one, 2–4 → few, 0 & 5+ → many
const plural = (n: number, one: string, few: string, many: string) =>
  n === 1 ? one : n >= 2 && n <= 4 ? few : many;

interface Workspace {
  documents: Document[];
  leftDocId: string;
  rightDocId: string;
}

const loadWorkspace = (): Workspace | null => {
  try {
    const raw = localStorage.getItem(WORKSPACE_KEY);
    if (!raw) return null;
    const ws = JSON.parse(raw);
    if (
      Array.isArray(ws.documents) &&
      ws.documents.length >= 2 &&
      ws.documents.every((d: Document) => d && typeof d.id === 'string' && typeof d.text === 'string')
    ) {
      return ws;
    }
  } catch (e) {
    console.error('Failed to parse workspace', e);
  }
  return null;
};

const savedWorkspace = loadWorkspace();

const App: React.FC = () => {
  const [documents, setDocuments] = useState<Document[]>(() => {
    return savedWorkspace?.documents ?? [
      { id: '1', name: '1', text: DEMO_ORIGINAL },
      { id: '2', name: '2', text: DEMO_MODIFIED },
    ];
  });

  const [leftDocId, setLeftDocId] = useState(() => {
    const id = savedWorkspace?.leftDocId;
    return id && savedWorkspace!.documents.some(d => d.id === id) ? id : (savedWorkspace?.documents[0]?.id ?? '1');
  });
  const [rightDocId, setRightDocId] = useState(() => {
    const id = savedWorkspace?.rightDocId;
    return id && savedWorkspace!.documents.some(d => d.id === id) ? id : (savedWorkspace?.documents[1]?.id ?? '2');
  });
  const [diffSegments, setDiffSegments] = useState<DiffSegment[]>([]);
  const [diffStats, setDiffStats] = useState<DiffStats>({
    added: 0,
    removed: 0,
    caseChanged: 0,
    total: 0,
    formattingOnly: false,
  });

  // Navigation State
  const [activeChangeIndex, setActiveChangeIndex] = useState(-1);
  const [isExportOpen, setIsExportOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isDownloadingApp, setIsDownloadingApp] = useState(false);
  const settingsPopoverRef = useRef<HTMLDivElement>(null);

  const handleDownloadApp = async () => {
    setIsDownloadingApp(true);
    try {
      const response = await fetch('/api/download-html');
      if (!response.ok) throw new Error('Build failed');
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.style.display = 'none';
      a.href = url;
      a.download = 'LegalLens-Redline-Offline.html';
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      a.remove();
    } catch (e) {
      console.error(e);
      alert('Stahování selhalo. Zkuste to prosím znovu.');
    } finally {
      setIsDownloadingApp(false);
    }
  };

  const [settings, setSettings] = useState<Settings>(() => {
    const saved = localStorage.getItem(SETTINGS_KEY);
    if (saved) {
      try {
        return JSON.parse(saved);
      } catch (e) {
        console.error('Failed to parse settings', e);
      }
    }
    return {
      font: 'sans',
      theme: 'light',
      accentColor: '#3b82f6', // blue-500
      fontSize: 14,
      removeLineBreaks: false,
      removeExtraSpaces: false,
    };
  });

  useEffect(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  }, [settings]);

  // Persist workspace (debounced) so a refresh never loses work
  useEffect(() => {
    const handler = setTimeout(() => {
      try {
        localStorage.setItem(WORKSPACE_KEY, JSON.stringify({ documents, leftDocId, rightDocId }));
      } catch (e) {
        console.error('Failed to save workspace', e);
      }
    }, 300);
    return () => clearTimeout(handler);
  }, [documents, leftDocId, rightDocId]);

  // Close settings popover on outside click
  useEffect(() => {
    if (!isSettingsOpen) return;
    const onMouseDown = (e: MouseEvent) => {
      if (settingsPopoverRef.current && !settingsPopoverRef.current.contains(e.target as Node)) {
        setIsSettingsOpen(false);
      }
    };
    document.addEventListener('mousedown', onMouseDown);
    return () => document.removeEventListener('mousedown', onMouseDown);
  }, [isSettingsOpen]);

  // Sync Scroll Refs
  const leftRef = useRef<HTMLTextAreaElement>(null);
  const rightRef = useRef<HTMLTextAreaElement>(null);
  const redlineRef = useRef<HTMLDivElement>(null);
  const isSyncing = useRef(false);

  const leftDoc = documents.find(d => d.id === leftDocId) || documents[0];
  const rightDoc = documents.find(d => d.id === rightDocId) || documents[1] || documents[0];

  useEffect(() => {
    const handler = setTimeout(() => {
      if (leftDoc && rightDoc) {
        let t1 = leftDoc.text;
        let t2 = rightDoc.text;

        if (settings.removeLineBreaks) {
          t1 = t1.replace(/[\r\n]+/g, ' ');
          t2 = t2.replace(/[\r\n]+/g, ' ');
        }
        if (settings.removeExtraSpaces) {
          t1 = t1.replace(/[ \t]{2,}/g, ' ');
          t2 = t2.replace(/[ \t]{2,}/g, ' ');
        }

        const segments = generateSmartDiff(t1, t2);
        setDiffSegments(segments);
        setDiffStats(summarizeDiff(segments, t1, t2));
        setActiveChangeIndex(-1);
      }
    }, 200);
    return () => clearTimeout(handler);
  }, [leftDoc, rightDoc, settings.removeLineBreaks, settings.removeExtraSpaces]);

  // Synchronized Scrolling Logic
  const handleScroll = (e: React.UIEvent<HTMLElement>) => {
    if (isSyncing.current) return;
    isSyncing.current = true;
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    const scrollable = scrollHeight - clientHeight;
    const ratio = scrollable > 0 ? scrollTop / scrollable : 0;

    const targets = [leftRef.current, rightRef.current, redlineRef.current];
    targets.forEach(target => {
      if (target && target !== e.currentTarget) {
        target.scrollTop = ratio * (target.scrollHeight - target.clientHeight);
      }
    });

    setTimeout(() => { isSyncing.current = false; }, 50);
  };

  const handleUpdateText = (id: string, newText: string) => {
    setDocuments(prev => prev.map(doc => doc.id === id ? { ...doc, text: newText } : doc));
  };

  const handleRenameDoc = (id: string, newName: string) => {
    setDocuments(prev => prev.map(doc => doc.id === id ? { ...doc, name: newName } : doc));
  };

  const handleSwapPanels = () => {
    const temp = leftDocId;
    setLeftDocId(rightDocId);
    setRightDocId(temp);
  };

  const handleReset = () => {
    if (window.confirm('Opravdu chcete resetovat pracovní prostor? Tímto vymažete veškerý text a dokumenty.')) {
      setDocuments([
        { id: '1', name: '1', text: '' },
        { id: '2', name: '2', text: '' }
      ]);
      setLeftDocId('1');
      setRightDocId('2');
      setActiveChangeIndex(-1);
    }
  };

  const navigateChange = (direction: 'next' | 'prev') => {
    const changes = diffSegments.filter(s => s.type !== ChangeType.UNCHANGED);
    if (changes.length === 0) return;

    let nextIndex = direction === 'next' ? activeChangeIndex + 1 : activeChangeIndex - 1;
    if (nextIndex >= changes.length) nextIndex = 0;
    if (nextIndex < 0) nextIndex = changes.length - 1;

    setActiveChangeIndex(nextIndex);
    const element = document.getElementById(changes[nextIndex].id);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      element.classList.add('ring-4', 'ring-blue-400', 'ring-opacity-50');
      setTimeout(() => element.classList.remove('ring-4', 'ring-blue-400', 'ring-opacity-50'), 2000);
    }
  };

  // Keyboard shortcuts: Alt+↓ / Alt+↑ steps through changes
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.altKey && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        e.preventDefault();
        navigateChange(e.key === 'ArrowDown' ? 'next' : 'prev');
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [diffSegments, activeChangeIndex]);

  const handleAddDocument = () => {
    if (documents.length >= 8) return;
    const newId = Date.now().toString();

    // Find next available number
    const existingNames = new Set(documents.map(d => d.name));
    let nextNum = 1;
    while (existingNames.has(nextNum.toString())) {
      nextNum++;
    }

    const newDoc: Document = {
      id: newId,
      name: nextNum.toString(),
      text: ''
    };
    setDocuments([...documents, newDoc]);
    setRightDocId(newId);
  };

  const handleDeleteDocument = (id: string) => {
    if (documents.length <= 2) return;
    const remaining = documents.filter(d => d.id !== id);
    setDocuments(remaining);
    if (leftDocId === id) setLeftDocId(remaining[0].id);
    if (rightDocId === id) setRightDocId(remaining[1]?.id || remaining[0].id);
  };

  const { added: addedCount, removed: removedCount, caseChanged: caseCount, total: changesCount } = diffStats;

  const fontClasses = {
    sans: 'font-sans',
    serif: 'font-serif',
    mono: 'font-mono'
  };

  const themeClasses = {
    light: 'bg-slate-50 text-slate-900',
    dark: 'bg-slate-950 text-slate-100',
    slate: 'bg-slate-100 text-slate-800'
  };

  const isDark = settings.theme === 'dark';

  const settingChipClass = (active: boolean) =>
    `text-[10px] px-2 py-1 rounded transition-colors border ${
      active
        ? (isDark ? 'bg-slate-700 text-slate-100 font-medium shadow-sm border-slate-600' : 'bg-slate-100 text-slate-800 font-medium shadow-sm border-slate-200')
        : (isDark ? 'text-slate-500 hover:bg-slate-700/50 hover:text-slate-300 border-transparent' : 'text-slate-400 hover:bg-slate-50 hover:text-slate-600 border-transparent')
    }`;

  return (
    <div className={`flex flex-col h-screen overflow-hidden ${themeClasses[settings.theme]} ${fontClasses[settings.font]}`}>
      <main className="flex-1 flex overflow-hidden relative p-1">
        <div className="flex-1 grid grid-cols-1 lg:grid-cols-3 gap-1 overflow-hidden">

          {/* Column 1: Source */}
          <div className="flex flex-col h-full overflow-hidden">
             <InputPanel
                label="Zdroj"
                roleDescription="Původní"
                selectedDocId={leftDocId}
                documents={documents}
                onSelectDoc={setLeftDocId}
                onChangeText={handleUpdateText}
                onRenameDoc={handleRenameDoc}
                onDeleteDoc={handleDeleteDocument}
                onAddDoc={handleAddDocument}
                scrollRef={leftRef}
                onScroll={handleScroll}
                settings={settings}
             />
          </div>

          {/* Column 2: Target */}
          <div className="flex flex-col h-full overflow-hidden">
             <InputPanel
                label="Cíl"
                roleDescription="Upravený"
                selectedDocId={rightDocId}
                documents={documents}
                onSelectDoc={setRightDocId}
                onChangeText={handleUpdateText}
                onRenameDoc={handleRenameDoc}
                onDeleteDoc={handleDeleteDocument}
                onAddDoc={handleAddDocument}
                scrollRef={rightRef}
                onScroll={handleScroll}
                settings={settings}
                headerAction={
                   <div className="flex items-center gap-2">
                       <button
                         onClick={handleSwapPanels}
                         className={`transition-colors p-1.5 rounded-md ${isDark ? 'text-slate-500 hover:text-blue-400 hover:bg-slate-700' : 'text-slate-400 hover:text-blue-600 hover:bg-slate-100'}`}
                         title="Prohodit Zdroj a Cíl"
                       >
                         <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" /></svg>
                       </button>
                       <div className={`h-4 w-px ${isDark ? 'bg-slate-600' : 'bg-slate-200'}`}></div>
                       <button
                           onClick={handleReset}
                           className={`transition-colors p-1.5 rounded-md flex items-center gap-1 ${isDark ? 'text-slate-500 hover:text-red-400 hover:bg-red-950/40' : 'text-slate-400 hover:text-red-600 hover:bg-red-50'}`}
                           title="Resetovat pracovní prostor"
                       >
                           <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                           <span className="text-[10px] font-bold uppercase tracking-wide hidden xl:inline">Reset</span>
                       </button>
                   </div>
                }
             />
          </div>

          {/* Column 3: Redline */}
          <div className="flex flex-col h-full overflow-hidden">
             <div className={`flex flex-col h-full rounded-lg shadow-sm border overflow-hidden ${isDark ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-200'}`}>
                <div className={`${isDark ? 'bg-slate-800 border-slate-700' : 'bg-slate-50 border-slate-200'} border-b flex flex-col shrink-0`}>
                  <div className={`px-3 flex justify-between items-center h-[38px] border-b ${isDark ? 'border-slate-700/50' : 'border-slate-200/50'}`}>
                     <div className="flex items-center gap-3">
                        <span className="text-xs font-bold text-slate-500 uppercase tracking-wider">Změny</span>
                     </div>

                     <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => setSettings(s => ({ ...s, removeLineBreaks: !s.removeLineBreaks }))}
                          className={settingChipClass(settings.removeLineBreaks)}
                          title="Spojí odstavce do jednoho bloku"
                        >
                          Ignorovat odřádkování
                        </button>
                        <button
                          onClick={() => setSettings(s => ({ ...s, removeExtraSpaces: !s.removeExtraSpaces }))}
                          className={settingChipClass(settings.removeExtraSpaces)}
                          title="Odstraní vícenásobné mezery a tabulátory"
                        >
                          Slučovat mezery
                        </button>
                     </div>

                     <div className="flex items-center gap-2">
                        <div className={`flex items-center border rounded-md shadow-sm ${isDark ? 'bg-slate-900 border-slate-700' : 'bg-white border-slate-200'}`}>
                          <button
                            onClick={() => navigateChange('prev')}
                            disabled={changesCount === 0}
                            title="Předchozí změna (Alt+↑)"
                            className={`p-1 rounded-l-md disabled:opacity-30 ${isDark ? 'hover:bg-slate-800 text-slate-500 hover:text-slate-300' : 'hover:bg-slate-50 text-slate-400 hover:text-slate-600'}`}
                          >
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
                          </button>

                          <div className={`px-2 text-[10px] font-mono text-slate-500 font-medium min-w-[3rem] text-center border-x h-full flex items-center justify-center ${isDark ? 'border-slate-700' : 'border-slate-100'}`}>
                             {changesCount > 0 ? activeChangeIndex + 1 : 0} / {changesCount}
                          </div>

                          <button
                            onClick={() => navigateChange('next')}
                            disabled={changesCount === 0}
                            title="Další změna (Alt+↓)"
                            className={`p-1 rounded-r-md disabled:opacity-30 ${isDark ? 'hover:bg-slate-800 text-slate-500 hover:text-slate-300' : 'hover:bg-slate-50 text-slate-400 hover:text-slate-600'}`}
                          >
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
                          </button>
                        </div>

                        {/* Settings */}
                        <div className="relative" ref={settingsPopoverRef}>
                          <button
                            onClick={() => setIsSettingsOpen(o => !o)}
                            className={`p-1.5 rounded-md transition-colors ${
                              isSettingsOpen
                                ? (isDark ? 'bg-slate-700 text-slate-200' : 'bg-slate-100 text-slate-600')
                                : (isDark ? 'text-slate-500 hover:text-slate-300 hover:bg-slate-700' : 'text-slate-400 hover:text-slate-600 hover:bg-slate-100')
                            }`}
                            title="Nastavení zobrazení"
                          >
                            <SettingsIcon className="w-3.5 h-3.5" />
                          </button>

                          {isSettingsOpen && (
                            <div className={`absolute right-0 top-8 z-50 w-56 rounded-lg shadow-xl border p-3 flex flex-col gap-3 ${
                              isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'
                            }`}>
                              <div>
                                <div className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">Motiv</div>
                                <div className="flex gap-1">
                                  {([['light', 'Světlý'], ['slate', 'Slate'], ['dark', 'Tmavý']] as const).map(([id, lbl]) => (
                                    <button key={id} onClick={() => setSettings(s => ({ ...s, theme: id }))} className={`flex-1 ${settingChipClass(settings.theme === id)}`}>
                                      {lbl}
                                    </button>
                                  ))}
                                </div>
                              </div>
                              <div>
                                <div className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">Písmo</div>
                                <div className="flex gap-1">
                                  {([['sans', 'Sans', 'font-sans'], ['serif', 'Serif', 'font-serif'], ['mono', 'Mono', 'font-mono']] as const).map(([id, lbl, cls]) => (
                                    <button key={id} onClick={() => setSettings(s => ({ ...s, font: id }))} className={`flex-1 ${cls} ${settingChipClass(settings.font === id)}`}>
                                      {lbl}
                                    </button>
                                  ))}
                                </div>
                              </div>
                              <div>
                                <div className="text-[9px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">Velikost písma</div>
                                <div className={`flex items-center justify-between border rounded-md ${isDark ? 'border-slate-700' : 'border-slate-200'}`}>
                                  <button
                                    onClick={() => setSettings(s => ({ ...s, fontSize: Math.max(11, s.fontSize - 1) }))}
                                    disabled={settings.fontSize <= 11}
                                    className={`px-3 py-1 text-sm disabled:opacity-30 ${isDark ? 'text-slate-400 hover:bg-slate-700' : 'text-slate-500 hover:bg-slate-50'} rounded-l-md`}
                                  >−</button>
                                  <span className="text-[10px] font-mono text-slate-500">{settings.fontSize} px</span>
                                  <button
                                    onClick={() => setSettings(s => ({ ...s, fontSize: Math.min(20, s.fontSize + 1) }))}
                                    disabled={settings.fontSize >= 20}
                                    className={`px-3 py-1 text-sm disabled:opacity-30 ${isDark ? 'text-slate-400 hover:bg-slate-700' : 'text-slate-500 hover:bg-slate-50'} rounded-r-md`}
                                  >+</button>
                                </div>
                              </div>
                            </div>
                          )}
                        </div>
                     </div>
                  </div>

                  <div className="h-[36px] w-full flex items-center justify-between px-3">
                     <div className="flex gap-3 text-[9px] font-bold uppercase tracking-tight">
                        <div className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span><span className="text-slate-500">Přidáno · {addedCount}</span></div>
                        <div className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-red-400"></span><span className="text-slate-500">Smazáno · {removedCount}</span></div>
                        <div className="flex items-center gap-1" title="Stejné slovo, jiná velikost písmen — pouze podtrženo"><span className="w-1.5 h-1.5 rounded-full bg-amber-400"></span><span className="text-slate-500">Velikost · {caseCount}</span></div>
                     </div>
                     <span className="text-[10px] text-slate-400 italic">
                        {changesCount === 0
                          ? (diffStats.formattingOnly ? 'Beze změn · jen formátování' : 'Beze změn')
                          : `${changesCount} ${plural(changesCount, 'úprava', 'úpravy', 'úprav')}`}
                     </span>
                  </div>
                </div>

                <div className="flex-1 overflow-hidden">
                   <RedlineViewer segments={diffSegments} scrollRef={redlineRef} onScroll={handleScroll} onOpenExport={() => setIsExportOpen(true)} settings={settings} />
                </div>

                <div className={`px-3 h-[24px] border-t text-[9px] flex justify-between items-center ${
                  isDark ? 'bg-slate-900 border-slate-800 text-slate-500' : 'bg-white border-slate-100 text-slate-400'
                }`}>
                      <span
                        onDoubleClick={!isDownloadingApp ? handleDownloadApp : undefined}
                        className={`cursor-default select-none transition-colors ${isDark ? 'text-slate-600 hover:text-slate-500' : 'text-slate-300 hover:text-slate-400'}`}
                        title="Dvojklikem stáhnete offline HTML aplikaci"
                      >
                        {isDownloadingApp ? 'Kompilace release...' : 'Build 25.10.42'}
                      </span>
                      <span>{changesCount} {plural(changesCount, 'změna', 'změny', 'změn')} celkem</span>
                </div>
             </div>
          </div>
        </div>
      </main>

      {/* Export Modal */}
      <ExportModal
        isOpen={isExportOpen}
        onClose={() => setIsExportOpen(false)}
        sourceDoc={leftDoc}
        targetDoc={rightDoc}
        segments={diffSegments}
        settings={{ accentColor: settings.accentColor, theme: settings.theme }}
      />
    </div>
  );
};

export default App;
