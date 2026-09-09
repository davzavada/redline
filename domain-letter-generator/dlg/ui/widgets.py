"""Znovupoužitelné komponenty uživatelského rozhraní.

Vše stojí jen na ``tkinter``/``ttk`` — žádná knihovna třetí strany. Modul jde
naimportovat i na stroji bez displeje; import nevytváří žádné okno ani
interpret Tk.

Obsah:

* :class:`ScrollableFrame` — svisle rolovatelná plocha (kolečko myši na
  Windows i X11),
* :class:`LabeledEntry`, :class:`LabeledCombobox`, :class:`LabeledText`,
  :class:`LabeledDate` — formulářová pole s popiskem, nápovědou a místem pro
  chybovou hlášku (společný základ :class:`LabeledField`),
* :class:`AutocompleteCombobox` — combobox napovídající z historie hodnot,
* :class:`DateEntry` — datum ve tvaru ``DD.MM.RRRR`` s tlačítkem *Dnes*,
* :class:`Toolbar`, :class:`StatusBar`, :class:`Card` — drobné stavební prvky,
* :func:`ask_yes_no`, :func:`show_error`, :func:`show_info`,
  :func:`show_warning` — obálky nad ``messagebox`` s českým titulkem,
* :func:`busy_cursor` — kontextový manažer s přesýpacími hodinami,
* :func:`run_in_thread` — výpočet ve vlákně, výsledek zpátky do Tk vlákna.

Pravidlo pro vlákna: **z pracovního vlákna se nesmí sahat na Tk objekty.**
:func:`run_in_thread` proto výsledek jen uloží a vyzvedne si ho hlavní vlákno
přes ``widget.after``.
"""

from __future__ import annotations

import queue
import re
import threading
import traceback
import unicodedata
from contextlib import contextmanager
from datetime import date
from typing import Any, Callable, Iterable, Iterator, Sequence

import tkinter as tk
from tkinter import messagebox, ttk

from ..version import APP_NAME
from . import theme

__all__ = [
    "AutocompleteCombobox",
    "Card",
    "DATE_FORMAT_HINT",
    "DateEntry",
    "LabeledCombobox",
    "LabeledDate",
    "LabeledEntry",
    "LabeledField",
    "LabeledText",
    "REQUIRED_MARK",
    "ScrollableFrame",
    "StatusBar",
    "Toolbar",
    "ask_yes_no",
    "busy_cursor",
    "center_on",
    "format_date",
    "parse_date",
    "plural_places",
    "run_in_thread",
    "show_error",
    "show_info",
    "show_warning",
    "shorten",
    "validate_date_text",
]

#: Značka za popiskem povinného pole.
REQUIRED_MARK = " *"
#: Do jaké šířky se zalamují nápovědy a chybové hlášky (v pixelech).
WRAPLENGTH = 460
#: Hláška u prázdného povinného pole.
REQUIRED_ERROR = "Toto pole je potřeba vyplnit."
#: Tvar data, jak ho čekáme od uživatele.
DATE_FORMAT_HINT = "DD.MM.RRRR"
DATE_INVALID_ERROR = f"Zadejte datum ve tvaru {DATE_FORMAT_HINT} (například 09.09.2026)."
DATE_IMPOSSIBLE_ERROR = "Takové datum v kalendáři neexistuje."
DATE_REQUIRED_ERROR = "Datum je potřeba vyplnit."


# ---------------------------------------------------------------------------
# dialogy
# ---------------------------------------------------------------------------


def _dialog_kwargs(parent: tk.Misc | None) -> dict[str, Any]:
    """``messagebox`` chce rodiče jako widget; jinak ho vůbec nepředáváme."""

    if isinstance(parent, tk.Misc):
        try:
            if parent.winfo_exists():
                return {"parent": parent}
        except tk.TclError:  # pragma: no cover - widget mezitím zanikl
            return {}
    return {}


def _dialog_text(message: str, detail: str = "") -> str:
    text = str(message or "")
    extra = str(detail or "").strip()
    if extra:
        text = f"{text}\n\n{extra}"
    return text


def show_error(
    parent: tk.Misc | None, message: str, *, title: str = "", detail: str = ""
) -> None:
    """Chybové okno s českým titulkem."""

    messagebox.showerror(
        title or f"{APP_NAME} — chyba",
        _dialog_text(message, detail),
        **_dialog_kwargs(parent),
    )


def show_warning(
    parent: tk.Misc | None, message: str, *, title: str = "", detail: str = ""
) -> None:
    """Varování — akce proběhla, ale s výhradami."""

    messagebox.showwarning(
        title or f"{APP_NAME} — upozornění",
        _dialog_text(message, detail),
        **_dialog_kwargs(parent),
    )


def show_info(
    parent: tk.Misc | None, message: str, *, title: str = "", detail: str = ""
) -> None:
    """Informační okno."""

    messagebox.showinfo(
        title or APP_NAME, _dialog_text(message, detail), **_dialog_kwargs(parent)
    )


def ask_yes_no(
    parent: tk.Misc | None,
    message: str,
    *,
    title: str = "",
    detail: str = "",
    default_yes: bool = False,
) -> bool:
    """Otázka Ano/Ne. Vrací ``True`` pro *Ano*."""

    kwargs = _dialog_kwargs(parent)
    kwargs["default"] = messagebox.YES if default_yes else messagebox.NO
    return bool(
        messagebox.askyesno(
            title or APP_NAME, _dialog_text(message, detail), icon=messagebox.QUESTION, **kwargs
        )
    )


# ---------------------------------------------------------------------------
# vlákna a kurzor
# ---------------------------------------------------------------------------


@contextmanager
def busy_cursor(widget: tk.Misc | None, cursor: str = "watch") -> Iterator[None]:
    """Na dobu bloku přepne kurzor okna na přesýpací hodiny.

    Používej jen pro krátké operace; delší práce patří do :func:`run_in_thread`.
    """

    target: tk.Misc | None = None
    previous = ""
    if isinstance(widget, tk.Misc):
        try:
            target = widget.winfo_toplevel()
            previous = str(target.cget("cursor"))
            target.configure(cursor=cursor)
            target.update_idletasks()
        except tk.TclError:  # pragma: no cover - okno mezitím zaniklo
            target = None

    try:
        yield
    finally:
        if target is not None:
            try:
                target.configure(cursor=previous)
                target.update_idletasks()
            except tk.TclError:  # pragma: no cover
                pass


def run_in_thread(
    widget: tk.Misc,
    work: Callable[[], Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[BaseException], None] | None = None,
    *,
    poll_ms: int = 50,
    name: str = "dlg-worker",
) -> threading.Thread:
    """Spustí ``work()`` ve vlákně a výsledek doručí do Tk vlákna.

    ``work`` běží v démonském vlákně a **nesmí** sahat na žádný Tk objekt.
    Výsledek se předá do ``on_done(result)``, výjimka do ``on_error(exc)``;
    obojí se volá už v hlavním (Tk) vlákně přes ``widget.after``. Když
    ``on_error`` chybí, vypíše se traceback na standardní chybový výstup.

    Volej z hlavního vlákna — plánování vyzvednutí výsledku je Tk operace.
    Vrací spuštěné vlákno (kvůli testům, ne kvůli ``join`` v UI).
    """

    box: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
    delay = max(1, int(poll_ms))

    def runner() -> None:
        try:
            result = work()
        except BaseException as exc:  # noqa: BLE001 - výjimku předáváme dál
            box.put(("chyba", exc))
            return
        box.put(("hotovo", result))

    def deliver(kind: str, payload: Any) -> None:
        if kind == "chyba":
            if on_error is not None:
                on_error(payload)
            else:
                traceback.print_exception(type(payload), payload, payload.__traceback__)
        elif on_done is not None:
            on_done(payload)

    def poll() -> None:
        try:
            kind, payload = box.get_nowait()
        except queue.Empty:
            _schedule(widget, delay, poll)
            return
        deliver(kind, payload)

    thread = threading.Thread(target=runner, name=name, daemon=True)
    thread.start()
    _schedule(widget, delay, poll)
    return thread


def _schedule(widget: tk.Misc, delay: int, callback: Callable[[], None]) -> None:
    """``widget.after`` odolné vůči zavřenému oknu."""

    try:
        widget.after(delay, callback)
    except (tk.TclError, RuntimeError, AttributeError):  # pragma: no cover
        pass  # okno zaniklo — výsledek už nemá komu doručit


# ---------------------------------------------------------------------------
# rolovatelná plocha
# ---------------------------------------------------------------------------

#: Sekvence kolečka myši: Windows/macOS + X11.
WHEEL_SEQUENCES: tuple[str, ...] = ("<MouseWheel>", "<Button-4>", "<Button-5>")
#: Atribut na okně, kde je seznam rolovatelných ploch daného okna.
_WHEEL_TARGETS_ATTR = "_dlg_wheel_targets"


def _wheel_units(event: Any) -> int:
    """Z události kolečka udělá počet „řádků“ (kladné = dolů)."""

    number = int(getattr(event, "num", 0) or 0)
    if number == 4:
        return -1
    if number == 5:
        return 1

    try:
        delta = int(getattr(event, "delta", 0) or 0)
    except (TypeError, ValueError):  # pragma: no cover - divná událost
        return 0
    if delta == 0:
        return 0
    if abs(delta) >= 120:  # Windows posílá násobky 120
        return -(delta // 120)
    return -1 if delta > 0 else 1  # macOS posílá malá čísla


def _register_wheel(frame: "ScrollableFrame") -> None:
    """Zaregistruje plochu u svého okna (na okno se váže jediná obsluha)."""

    try:
        top = frame.winfo_toplevel()
    except tk.TclError:  # pragma: no cover
        return

    targets = getattr(top, _WHEEL_TARGETS_ATTR, None)
    if not isinstance(targets, list):
        targets = []
        setattr(top, _WHEEL_TARGETS_ATTR, targets)
        for sequence in WHEEL_SEQUENCES:
            try:
                top.bind(sequence, lambda event, w=top: _dispatch_wheel(w, event), add="+")
            except tk.TclError:  # pragma: no cover
                continue
    if frame not in targets:
        targets.append(frame)


def _unregister_wheel(frame: "ScrollableFrame") -> None:
    try:
        top = frame.winfo_toplevel()
    except tk.TclError:  # pragma: no cover
        return
    targets = getattr(top, _WHEEL_TARGETS_ATTR, None)
    if isinstance(targets, list) and frame in targets:
        targets.remove(frame)


def _dispatch_wheel(top: tk.Misc, event: Any) -> str | None:
    """Kolečko doručí té ploše, nad kterou je myš."""

    targets = getattr(top, _WHEEL_TARGETS_ATTR, None)
    if not isinstance(targets, list) or not targets:
        return None

    alive: list[ScrollableFrame] = []
    for frame in targets:
        try:
            if frame.winfo_exists():
                alive.append(frame)
        except tk.TclError:  # pragma: no cover
            continue
    targets[:] = alive

    for frame in alive:
        if frame.handle_wheel(event):
            return "break"
    return None


class ScrollableFrame(ttk.Frame):
    """Svisle rolovatelná plocha; obsah se vkládá do :attr:`body`.

    Šířka vnitřního rámce se drží šířky plátna, takže se obsah roztahuje
    do stran a roluje jen svisle. Kolečko myši funguje i nad vnořenými
    widgety (obsluha je na okně, ne na plátně).
    """

    def __init__(
        self,
        master: tk.Misc | None = None,
        *,
        style: str = "TFrame",
        body_style: str = "",
        padding: Any = 0,
        autohide: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, style=style, **kwargs)
        self._destroyed = False
        self._autohide = bool(autohide)
        self._scrollbar_visible = True

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            self,
            borderwidth=0,
            highlightthickness=0,
            background=theme.COLOR_SURFACE,
            takefocus=0,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self._on_scroll_set)

        self.body = ttk.Frame(self.canvas, style=body_style or style, padding=padding)
        #: Alias pro čitelnost na straně pohledů.
        self.interior = self.body
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.bind("<Destroy>", self._on_destroy)

        _register_wheel(self)

    # -- rozměry -------------------------------------------------------
    def _on_body_configure(self, event: Any = None) -> None:
        try:
            region = self.canvas.bbox("all")
            if region:
                self.canvas.configure(scrollregion=region)
        except tk.TclError:  # pragma: no cover
            pass

    def _on_canvas_configure(self, event: Any) -> None:
        width = max(int(getattr(event, "width", 0) or 0), 1)
        try:
            self.canvas.itemconfigure(self._window, width=width)
        except tk.TclError:  # pragma: no cover
            pass

    def _on_scroll_set(self, first: str, last: str) -> None:
        try:
            self.scrollbar.set(first, last)
        except tk.TclError:  # pragma: no cover
            return
        if not self._autohide:
            return
        try:
            needed = not (float(first) <= 0.0 and float(last) >= 1.0)
        except (TypeError, ValueError):  # pragma: no cover
            needed = True
        if needed == self._scrollbar_visible:
            return
        self._scrollbar_visible = needed
        try:
            if needed:
                self.scrollbar.grid()
            else:
                self.scrollbar.grid_remove()
        except tk.TclError:  # pragma: no cover
            pass

    # -- kolečko myši --------------------------------------------------
    def scrollable(self) -> bool:
        """Je vůbec co rolovat?"""

        try:
            first, last = self.canvas.yview()
        except (tk.TclError, ValueError):  # pragma: no cover
            return False
        return not (first <= 0.0 and last >= 1.0)

    def _containing(self, event: Any) -> tk.Misc | None:
        """Widget pod ukazatelem myši."""

        try:
            return self.winfo_containing(
                int(getattr(event, "x_root", 0) or 0), int(getattr(event, "y_root", 0) or 0)
            )
        except (tk.TclError, KeyError, TypeError, ValueError):  # pragma: no cover
            return None

    def _is_inside(self, widget: Any) -> bool:
        """Leží widget uvnitř téhle plochy?"""

        node = widget
        while node is not None:
            if node is self:
                return True
            node = getattr(node, "master", None)
        return False

    def pointer_inside(self, event: Any) -> bool:
        """Je ukazatel myši nad touhle plochou (včetně vnořených widgetů)?"""

        return self._is_inside(self._containing(event))

    def _consumed_by_inner(self, widget: Any) -> bool:
        """Rolovalo si kolečko už nějaké vnořené okno (Text, tabulka, seznam)?

        Tk doručí kolečko nejdřív widgetu pod myší; kdybychom rolovali i my,
        posunulo by se všechno dvakrát. Vnořené okno má přednost jen tehdy,
        když je v něm vůbec co rolovat.
        """

        node = widget
        while node is not None and node is not self:
            if node is not self.canvas and isinstance(
                node, (tk.Text, tk.Listbox, tk.Canvas, ttk.Treeview)
            ):
                try:
                    first, last = node.yview()  # type: ignore[union-attr]
                except (tk.TclError, ValueError, AttributeError):  # pragma: no cover
                    return True
                if not (float(first) <= 0.0 and float(last) >= 1.0):
                    return True
            node = getattr(node, "master", None)
        return False

    def handle_wheel(self, event: Any) -> bool:
        """Zpracuje událost kolečka. Vrací ``True``, když se opravdu rolovalo."""

        if self._destroyed:
            return False
        widget: Any = self._containing(event)
        if not self._is_inside(widget):
            return False
        if self._consumed_by_inner(widget):
            return False
        units = _wheel_units(event)
        if not units or not self.scrollable():
            return False
        try:
            self.canvas.yview_scroll(units, "units")
        except tk.TclError:  # pragma: no cover
            return False
        return True

    # -- ostatní -------------------------------------------------------
    def scroll_to_top(self) -> None:
        try:
            self.canvas.yview_moveto(0.0)
        except tk.TclError:  # pragma: no cover
            pass

    def scroll_to_bottom(self) -> None:
        try:
            self.canvas.yview_moveto(1.0)
        except tk.TclError:  # pragma: no cover
            pass

    def clear(self) -> None:
        """Zahodí celý obsah plochy (typicky před překreslením formuláře)."""

        for child in list(self.body.winfo_children()):
            child.destroy()
        self.scroll_to_top()

    def _on_destroy(self, event: Any) -> None:
        if getattr(event, "widget", None) is not self:
            return
        self._destroyed = True
        _unregister_wheel(self)


# ---------------------------------------------------------------------------
# combobox s napovídáním
# ---------------------------------------------------------------------------


def center_on(window: tk.Misc, master: tk.Misc | None) -> None:
    """Vystředí okno nad rodičovským oknem (vodorovně na střed, svisle do třetiny).

    Sdílená pomůcka všech modálních dialogů aplikace. Když okno mezitím zaniklo
    nebo rodič není widget, nedělá nic — vystředění není důvod ke spadnutí.
    """

    if not isinstance(master, tk.Misc):
        return
    try:
        window.update_idletasks()
        top = master.winfo_toplevel()
        x = top.winfo_rootx() + max(0, (top.winfo_width() - window.winfo_width()) // 2)
        y = top.winfo_rooty() + max(0, (top.winfo_height() - window.winfo_height()) // 3)
        window.geometry(f"+{max(0, x)}+{max(0, y)}")
    except tk.TclError:  # pragma: no cover - okno zaniklo dřív
        pass


def shorten(text: str, limit: int) -> str:
    """Jednořádkový náhled textu, delší se zkrátí a ukončí výpustkou.

    Sdílená pomůcka pohledů — ať se v každém neopisuje znovu.
    """

    value = " ".join(str(text or "").replace("\u00a0", " ").split())
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "\u2026"


def plural_places(count: int) -> str:
    """„1 místo“ / „3 místa“ / „7 míst“ — česká číslovka pro počet míst."""

    if count == 1:
        return "1 místo"
    if 2 <= count <= 4:
        return f"{count} místa"
    return f"{count} míst"


def _fold(text: str) -> str:
    """Porovnávací tvar: bez diakritiky a bez ohledu na velikost písmen."""

    normalized = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return stripped.casefold()


#: Klávesy, po kterých se seznam nepřepočítává (pohyb, potvrzení, modifikátory).
_IGNORED_KEYS = frozenset(
    {
        "Up", "Down", "Left", "Right", "Home", "End", "Prior", "Next",
        "Return", "KP_Enter", "Tab", "ISO_Left_Tab",
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
        "Meta_L", "Meta_R", "Super_L", "Super_R", "Win_L", "Win_R",
        "Caps_Lock", "Num_Lock", "Scroll_Lock", "Insert", "Menu", "Pause",
    }
    | {f"F{i}" for i in range(1, 13)}
)
#: Mazací klávesy — po nich se nikdy nedoplňuje text (jinak by nešlo mazat).
_DELETE_KEYS = frozenset({"BackSpace", "Delete", "KP_Delete"})


class AutocompleteCombobox(ttk.Combobox):
    """Combobox, který při psaní filtruje nabídku podle historie hodnot.

    Filtruje se bez ohledu na diakritiku a velikost písmen; nejdřív shody na
    začátku, potom shody uvnitř. Widget **nikdy sám nepřebírá kurzor** ani
    nerozbaluje seznam, takže psaní ani mazání znaků nic neruší.

    Volitelné doplňování zbytku hodnoty (``inline_completion=True``) se po
    ``Backspace``/``Delete`` neprovádí — mazání tak zůstane mazáním.
    """

    def __init__(
        self,
        master: tk.Misc | None = None,
        *,
        completion_values: Iterable[str] = (),
        inline_completion: bool = False,
        match_anywhere: bool = True,
        **kwargs: Any,
    ) -> None:
        values = kwargs.pop("values", None)
        super().__init__(master, **kwargs)
        self._all_values: list[str] = []
        self._inline = bool(inline_completion)
        self._match_anywhere = bool(match_anywhere)
        self.set_completion_values(completion_values or values or ())

        self.bind("<KeyRelease>", self._on_key_release, add="+")
        self.bind("<<ComboboxSelected>>", lambda _event: self.reset_suggestions(), add="+")

    # -- nabídka -------------------------------------------------------
    def completion_values(self) -> list[str]:
        """Celá zásoba hodnot (historie), ze které se napovídá."""

        return list(self._all_values)

    def set_completion_values(self, values: Iterable[str]) -> None:
        """Nastaví zásobu hodnot a zobrazí ji celou."""

        cleaned: list[str] = []
        for value in values or ():
            text = "" if value is None else str(value)
            if text and text not in cleaned:
                cleaned.append(text)
        self._all_values = cleaned
        self._apply_values(cleaned)

    def _apply_values(self, values: Sequence[str]) -> None:
        try:
            self.configure(values=tuple(values))
        except tk.TclError:  # pragma: no cover
            pass

    def matches(self, text: str) -> list[str]:
        """Hodnoty odpovídající zadanému textu; shody na začátku první."""

        needle = _fold(text)
        if not needle:
            return list(self._all_values)
        prefix: list[str] = []
        inside: list[str] = []
        for value in self._all_values:
            folded = _fold(value)
            if folded.startswith(needle):
                prefix.append(value)
            elif self._match_anywhere and needle in folded:
                inside.append(value)
        return prefix + inside

    def reset_suggestions(self) -> None:
        """Vrátí do nabídky všechny hodnoty."""

        self._apply_values(self._all_values)

    def refresh_suggestions(self, *, deleting: bool = False) -> list[str]:
        """Přepočítá nabídku podle napsaného textu. Vrací shody.

        ``deleting=True`` znamená „uživatel právě maže“ — text se pak
        nikdy nedoplňuje.
        """

        try:
            text = self.get()
        except tk.TclError:  # pragma: no cover
            return []

        found = self.matches(text)
        self._apply_values(found if (text and found) else self._all_values)

        if self._inline and not deleting and text and found:
            self._complete_inline(text, found[0])
        return found

    def _complete_inline(self, text: str, best: str) -> None:
        """Doplní zbytek nejlepší shody a nechá ho vybraný."""

        if len(best) <= len(text) or not _fold(best).startswith(_fold(text)):
            return
        try:
            self.delete(0, "end")
            self.insert(0, best)
            self.icursor(len(text))
            self.select_range(len(text), "end")
        except tk.TclError:  # pragma: no cover
            pass

    # -- události ------------------------------------------------------
    def _on_key_release(self, event: Any) -> None:
        keysym = str(getattr(event, "keysym", "") or "")
        if keysym == "Escape":
            self.reset_suggestions()
            return
        if keysym in _IGNORED_KEYS:
            return
        self.refresh_suggestions(deleting=keysym in _DELETE_KEYS)


# ---------------------------------------------------------------------------
# datum
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\s*(\d{1,2})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{2,4})\s*$")
_ISO_DATE_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")


def _date_parts(text: str) -> tuple[int, int, int] | None:
    """Rozebere zápis data na (rok, měsíc, den); neřeší, jestli existuje."""

    raw = str(text or "")
    iso = _ISO_DATE_RE.match(raw)
    if iso:
        return (int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    match = _DATE_RE.match(raw)
    if not match:
        return None
    day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if year < 100:
        year += 2000
    return (year, month, day)


def parse_date(text: str) -> date | None:
    """Z textu udělá datum. Vrací ``None``, když zápis nedává smysl.

    Bere ``9.9.2026``, ``09. 09. 2026``, ``9/9/26`` i ISO ``2026-09-09``.
    """

    parts = _date_parts(text)
    if parts is None:
        return None
    try:
        return date(*parts)
    except ValueError:
        return None


def format_date(value: date) -> str:
    """Datum v českém tvaru ``DD.MM.RRRR``."""

    return f"{value.day:02d}.{value.month:02d}.{value.year:04d}"


def validate_date_text(text: str, *, allow_empty: bool = True) -> str:
    """Vrátí českou chybovou hlášku, nebo prázdný řetězec, je-li datum v pořádku."""

    raw = str(text or "").strip()
    if not raw:
        return "" if allow_empty else DATE_REQUIRED_ERROR
    parts = _date_parts(raw)
    if parts is None:
        return DATE_INVALID_ERROR
    try:
        date(*parts)
    except ValueError:
        return DATE_IMPOSSIBLE_ERROR
    return ""


class DateEntry(ttk.Frame):
    """Pole pro datum ve tvaru ``DD.MM.RRRR`` s tlačítkem *Dnes*.

    Bez jakékoli knihovny navíc (žádný ``tkcalendar``). Kontrola se spouští
    při opuštění pole a při volání :meth:`validate`.
    """

    def __init__(
        self,
        master: tk.Misc | None = None,
        *,
        value: str = "",
        textvariable: tk.StringVar | None = None,
        width: int = 12,
        today_text: str = "Dnes",
        allow_empty: bool = True,
        show_error: bool = True,
        on_change: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, **kwargs)
        self.allow_empty = bool(allow_empty)
        self._on_change = on_change
        self._error = ""

        self.variable = textvariable if textvariable is not None else tk.StringVar(master=self)
        if value:
            self.variable.set(str(value))

        self.columnconfigure(0, weight=1)
        self.entry = ttk.Entry(self, textvariable=self.variable, width=int(width))
        self.entry.grid(row=0, column=0, sticky="ew")
        self.today_button = ttk.Button(self, text=str(today_text), width=7, command=self.set_today)
        self.today_button.grid(row=0, column=1, sticky="w", padx=(theme.PAD_S, 0))

        self.error_label: ttk.Label | None = None
        if show_error:
            self.error_label = ttk.Label(
                self, style="Chyba.TLabel", text="", wraplength=WRAPLENGTH, justify="left"
            )
            self.error_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
            self.error_label.grid_remove()

        self.entry.bind("<FocusOut>", self._on_focus_out, add="+")
        self.variable.trace_add("write", self._on_variable_write)

    # -- hodnota -------------------------------------------------------
    def get(self) -> str:
        """Napsaný text tak, jak je."""

        return self.variable.get()

    def set(self, value: str) -> None:
        self.variable.set("" if value is None else str(value))

    def get_date(self) -> date | None:
        """Zadané datum, nebo ``None`` (prázdné i chybné pole)."""

        return parse_date(self.variable.get())

    def set_date(self, value: date | None) -> None:
        self.set("" if value is None else format_date(value))

    def set_today(self) -> None:
        """Vyplní dnešek a rovnou zkontroluje pole."""

        self.set_date(date.today())
        self.validate()

    # -- kontrola ------------------------------------------------------
    @property
    def error(self) -> str:
        return self._error

    def validate(self) -> str:
        """Zkontroluje pole, zobrazí případnou hlášku a vrátí ji."""

        message = validate_date_text(self.variable.get(), allow_empty=self.allow_empty)
        self.set_error(message)
        return message

    def is_valid(self) -> bool:
        return not validate_date_text(self.variable.get(), allow_empty=self.allow_empty)

    def set_error(self, message: str) -> None:
        """Zobrazí (nebo skryje) chybovou hlášku pod polem."""

        self._error = str(message or "")
        try:
            self.entry.configure(style="Chyba.TEntry" if self._error else "TEntry")
        except tk.TclError:  # pragma: no cover
            pass
        if self.error_label is None:
            return
        try:
            self.error_label.configure(text=self._error)
            if self._error:
                self.error_label.grid()
            else:
                self.error_label.grid_remove()
        except tk.TclError:  # pragma: no cover
            pass

    def clear_error(self) -> None:
        self.set_error("")

    def set_enabled(self, enabled: bool = True) -> None:
        state = "!disabled" if enabled else "disabled"
        for widget in (self.entry, self.today_button):
            try:
                widget.state([state])
            except tk.TclError:  # pragma: no cover
                pass

    def focus_set(self) -> None:  # noqa: D102 - viz tkinter
        self.entry.focus_set()

    # -- události ------------------------------------------------------
    def _on_focus_out(self, _event: Any = None) -> None:
        self.validate()

    def _on_variable_write(self, *_args: Any) -> None:
        if self._error:
            self.clear_error()
        if self._on_change is not None:
            self._on_change(self.variable.get())


# ---------------------------------------------------------------------------
# formulářová pole
# ---------------------------------------------------------------------------


class LabeledField(ttk.Frame):
    """Základ formulářového pole: popisek, ovládací prvek, chyba a nápověda.

    Rozvržení (mřížka, jeden sloupec):

    ==== =====================================
    řádek obsah
    ==== =====================================
    0     popisek (``Popisek.TLabel``)
    1     ovládací prvek
    2     chybová hláška (``Chyba.TLabel``)
    3     nápověda (``Napoveda.TLabel``)
    ==== =====================================

    Potomci doplní ovládací prvek metodou :meth:`_place_control` a přepíšou
    :meth:`get` / :meth:`set`.
    """

    def __init__(
        self,
        master: tk.Misc | None = None,
        label: str = "",
        *,
        help_text: str = "",
        required: bool = False,
        wraplength: int = WRAPLENGTH,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, **kwargs)
        self.columnconfigure(0, weight=1)

        self._label_text = str(label)
        self._required = bool(required)
        self._error = ""
        self._change_callbacks: list[Callable[[str], None]] = []
        self.control: tk.Misc | None = None

        self.label = ttk.Label(self, style="Popisek.TLabel", text=self._render_label())
        self.label.grid(row=0, column=0, sticky="w")

        self.error_label = ttk.Label(
            self, style="Chyba.TLabel", text="", wraplength=wraplength, justify="left"
        )
        self.error_label.grid(row=2, column=0, sticky="w", pady=(2, 0))
        self.error_label.grid_remove()

        self.help_label = ttk.Label(
            self,
            style="Napoveda.TLabel",
            text=str(help_text),
            wraplength=wraplength,
            justify="left",
        )
        self.help_label.grid(row=3, column=0, sticky="w", pady=(2, 0))
        if not help_text:
            self.help_label.grid_remove()

    # -- popisek, nápověda ---------------------------------------------
    def _render_label(self) -> str:
        if not self._label_text:
            return ""
        return self._label_text + (REQUIRED_MARK if self._required else "")

    def set_label(self, text: str) -> None:
        self._label_text = str(text or "")
        self.label.configure(text=self._render_label())

    @property
    def required(self) -> bool:
        return self._required

    @required.setter
    def required(self, value: bool) -> None:
        self._required = bool(value)
        self.label.configure(text=self._render_label())

    def set_help(self, text: str) -> None:
        message = str(text or "")
        self.help_label.configure(text=message)
        if message:
            self.help_label.grid()
        else:
            self.help_label.grid_remove()

    # -- ovládací prvek -------------------------------------------------
    def _place_control(self, widget: tk.Misc, **grid: Any) -> None:
        options: dict[str, Any] = {
            "row": 1,
            "column": 0,
            "sticky": "ew",
            "pady": (2, 0),
        }
        options.update(grid)
        widget.grid(**options)
        self.control = widget

    def focus_set(self) -> None:  # noqa: D102 - viz tkinter
        if self.control is not None:
            self.control.focus_set()
        else:  # pragma: no cover - pole vždy nějaký prvek má
            super().focus_set()

    def set_enabled(self, enabled: bool = True) -> None:
        """Zapne/vypne ovládací prvek pole."""

        widget = self.control
        if widget is None:  # pragma: no cover
            return
        if isinstance(widget, ttk.Widget):
            try:
                widget.state(["!disabled" if enabled else "disabled"])
            except tk.TclError:  # pragma: no cover
                pass
            return
        try:
            widget.configure(state="normal" if enabled else "disabled")  # type: ignore[call-arg]
        except tk.TclError:  # pragma: no cover
            pass

    # -- hodnota --------------------------------------------------------
    def get(self) -> str:
        raise NotImplementedError

    def set(self, value: str) -> None:
        raise NotImplementedError

    def bind_change(self, callback: Callable[[str], None]) -> None:
        """Zaregistruje funkci volanou při každé změně hodnoty."""

        self._change_callbacks.append(callback)

    def _notify_change(self) -> None:
        if not self._change_callbacks:
            return
        value = self.get()
        for callback in list(self._change_callbacks):
            callback(value)

    # -- chyby ----------------------------------------------------------
    @property
    def error(self) -> str:
        return self._error

    @property
    def has_error(self) -> bool:
        return bool(self._error)

    def set_error(self, message: str) -> None:
        """Zobrazí (nebo prázdným textem skryje) chybovou hlášku pod polem."""

        self._error = str(message or "")
        self.error_label.configure(text=self._error)
        if self._error:
            self.error_label.grid()
        else:
            self.error_label.grid_remove()
        self._apply_error_style()

    def clear_error(self) -> None:
        self.set_error("")

    def _error_styles(self) -> tuple[str, str]:
        """Dvojice (styl s chybou, běžný styl) pro ovládací prvek."""

        return ("", "")

    def _apply_error_style(self) -> None:
        error_style, normal_style = self._error_styles()
        if not error_style or self.control is None:
            return
        try:
            self.control.configure(  # type: ignore[call-arg]
                style=error_style if self._error else normal_style
            )
        except tk.TclError:  # pragma: no cover
            pass

    def validate(self) -> str:
        """Zkontroluje pole (povinnost), zobrazí hlášku a vrátí ji."""

        message = REQUIRED_ERROR if (self._required and not self.get().strip()) else ""
        self.set_error(message)
        return message


class LabeledEntry(LabeledField):
    """Jednořádkové textové pole s popiskem."""

    def __init__(
        self,
        master: tk.Misc | None = None,
        label: str = "",
        *,
        value: str = "",
        help_text: str = "",
        required: bool = False,
        textvariable: tk.StringVar | None = None,
        width: int | None = None,
        show: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, label, help_text=help_text, required=required, **kwargs)

        self.variable = textvariable if textvariable is not None else tk.StringVar(master=self)
        if value:
            self.variable.set(str(value))

        options: dict[str, Any] = {"textvariable": self.variable}
        if width is not None:
            options["width"] = int(width)
        if show is not None:
            options["show"] = show
        self.entry = ttk.Entry(self, **options)
        self._place_control(self.entry)

        self.variable.trace_add("write", lambda *_a: self._on_write())

    def _on_write(self) -> None:
        if self._error:
            self.clear_error()
        self._notify_change()

    def _error_styles(self) -> tuple[str, str]:
        return ("Chyba.TEntry", "TEntry")

    def get(self) -> str:
        return self.variable.get()

    def set(self, value: str) -> None:
        self.variable.set("" if value is None else str(value))


class LabeledCombobox(LabeledField):
    """Výběr z variant. S ``autocomplete=True`` napovídá z historie hodnot."""

    def __init__(
        self,
        master: tk.Misc | None = None,
        label: str = "",
        *,
        values: Iterable[str] = (),
        value: str = "",
        help_text: str = "",
        required: bool = False,
        readonly: bool = True,
        autocomplete: bool = False,
        inline_completion: bool = False,
        textvariable: tk.StringVar | None = None,
        width: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, label, help_text=help_text, required=required, **kwargs)

        self.variable = textvariable if textvariable is not None else tk.StringVar(master=self)
        items = [str(v) for v in (values or ())]

        options: dict[str, Any] = {"textvariable": self.variable, "values": tuple(items)}
        if width is not None:
            options["width"] = int(width)
        if readonly:
            options["state"] = "readonly"

        self.combobox: ttk.Combobox
        if autocomplete:
            self.combobox = AutocompleteCombobox(
                self, completion_values=items, inline_completion=inline_completion, **options
            )
        else:
            self.combobox = ttk.Combobox(self, **options)
        self._place_control(self.combobox)

        if value:
            self.variable.set(str(value))

        self.variable.trace_add("write", lambda *_a: self._on_write())

    def _on_write(self) -> None:
        if self._error:
            self.clear_error()
        self._notify_change()

    def _error_styles(self) -> tuple[str, str]:
        return ("Chyba.TCombobox", "TCombobox")

    @property
    def values(self) -> list[str]:
        try:
            return [str(v) for v in self.combobox.cget("values")]
        except tk.TclError:  # pragma: no cover
            return []

    def set_values(self, values: Iterable[str], *, keep_value: bool = True) -> None:
        """Vymění nabídku; hodnotu ponechá, jde-li o ni."""

        items = [str(v) for v in (values or ())]
        current = self.variable.get()
        if isinstance(self.combobox, AutocompleteCombobox):
            self.combobox.set_completion_values(items)
        else:
            self.combobox.configure(values=tuple(items))
        if keep_value and current:
            self.variable.set(current)

    def get(self) -> str:
        return self.variable.get()

    def set(self, value: str) -> None:
        self.variable.set("" if value is None else str(value))


class LabeledText(LabeledField):
    """Víceřádkové textové pole s popiskem a vlastním posuvníkem."""

    def __init__(
        self,
        master: tk.Misc | None = None,
        label: str = "",
        *,
        value: str = "",
        help_text: str = "",
        required: bool = False,
        height: int = 4,
        width: int | None = None,
        wrap: str = "word",
        **kwargs: Any,
    ) -> None:
        super().__init__(master, label, help_text=help_text, required=required, **kwargs)

        holder = ttk.Frame(self)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        options: dict[str, Any] = {
            "height": int(height),
            "wrap": wrap,
            "undo": True,
            "borderwidth": 1,
            "relief": "solid",
            "highlightthickness": 0,
            "background": theme.COLOR_SURFACE,
            "foreground": theme.COLOR_TEXT,
            "insertbackground": theme.COLOR_TEXT,
            "selectbackground": theme.COLOR_ACCENT_SOFT,
            "selectforeground": theme.COLOR_TEXT,
            "padx": theme.PAD_S,
            "pady": theme.PAD_S - 2,
            "font": theme.font_name(theme.FONT_TEXT, self),
        }
        if width is not None:
            options["width"] = int(width)
        self.text = tk.Text(holder, **options)
        self.text.grid(row=0, column=0, sticky="nsew")

        self.scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.text.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=self.scrollbar.set)

        # Do mřížky pole jde obal; ovládacím prvkem zůstává samotný Text.
        self._place_control(holder)
        self.control = self.text
        self.holder = holder

        if value:
            self.set(value)

        self.text.edit_modified(False)
        self.text.bind("<<Modified>>", self._on_modified, add="+")

    def _on_modified(self, _event: Any = None) -> None:
        try:
            if not self.text.edit_modified():
                return
            self.text.edit_modified(False)
        except tk.TclError:  # pragma: no cover
            return
        if self._error:
            self.clear_error()
        self._notify_change()

    def _apply_error_style(self) -> None:
        """``tk.Text`` nemá ttk styl — chybu ukážeme podbarvením."""

        try:
            self.text.configure(
                background=theme.COLOR_ERROR_SOFT if self._error else theme.COLOR_SURFACE
            )
        except tk.TclError:  # pragma: no cover
            pass

    def get(self) -> str:
        try:
            return self.text.get("1.0", "end-1c")
        except tk.TclError:  # pragma: no cover
            return ""

    def set(self, value: str) -> None:
        try:
            self.text.delete("1.0", "end")
            if value:
                self.text.insert("1.0", str(value))
            self.text.edit_modified(False)
        except tk.TclError:  # pragma: no cover
            pass


class LabeledDate(LabeledField):
    """Pole pro datum s popiskem — obal nad :class:`DateEntry`."""

    def __init__(
        self,
        master: tk.Misc | None = None,
        label: str = "",
        *,
        value: str = "",
        help_text: str = "",
        required: bool = False,
        textvariable: tk.StringVar | None = None,
        width: int = 12,
        today_text: str = "Dnes",
        **kwargs: Any,
    ) -> None:
        super().__init__(master, label, help_text=help_text, required=required, **kwargs)

        self.date_entry = DateEntry(
            self,
            value=value,
            textvariable=textvariable,
            width=width,
            today_text=today_text,
            allow_empty=not required,
            show_error=False,
            on_change=self._on_date_change,
        )
        self.variable = self.date_entry.variable
        self._place_control(self.date_entry)

    def _on_date_change(self, _value: str) -> None:
        if self._error:
            self.clear_error()
        self._notify_change()

    def _apply_error_style(self) -> None:
        try:
            self.date_entry.entry.configure(
                style="Chyba.TEntry" if self._error else "TEntry"
            )
        except tk.TclError:  # pragma: no cover
            pass

    def get(self) -> str:
        return self.date_entry.get()

    def set(self, value: str) -> None:
        self.date_entry.set(value)

    def get_date(self) -> date | None:
        return self.date_entry.get_date()

    def set_date(self, value: date | None) -> None:
        self.date_entry.set_date(value)

    def validate(self) -> str:
        message = validate_date_text(self.get(), allow_empty=not self._required)
        if not message and self._required and not self.get().strip():
            message = DATE_REQUIRED_ERROR
        self.set_error(message)
        return message


# ---------------------------------------------------------------------------
# drobné stavební prvky
# ---------------------------------------------------------------------------


class Toolbar(ttk.Frame):
    """Vodorovná lišta tlačítek nad obsahem pohledu."""

    def __init__(
        self,
        master: tk.Misc | None = None,
        *,
        padding: Any = (0, 0, 0, theme.PAD_M),
        gap: int = theme.PAD_S,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, padding=padding, **kwargs)
        self.gap = int(gap)
        self.buttons: dict[str, ttk.Button] = {}

    def add_button(
        self,
        text: str,
        command: Callable[[], Any] | None = None,
        *,
        primary: bool = False,
        danger: bool = False,
        side: str = "left",
        enabled: bool = True,
        width: int | None = None,
        key: str = "",
    ) -> ttk.Button:
        """Přidá tlačítko. ``primary`` = hlavní akce, ``danger`` = destruktivní."""

        style = "Primary.TButton" if primary else ("Nebezpecne.TButton" if danger else "TButton")
        options: dict[str, Any] = {"text": str(text), "style": style}
        if command is not None:
            options["command"] = command
        if width is not None:
            options["width"] = int(width)
        button = ttk.Button(self, **options)
        button.pack(side=side, padx=(0, self.gap))
        if not enabled:
            button.state(["disabled"])
        self.buttons[key or str(text)] = button
        return button

    def add_widget(self, widget: tk.Misc, *, side: str = "left", padx: Any = None) -> tk.Misc:
        widget.pack(side=side, padx=(0, self.gap) if padx is None else padx)
        return widget

    def add_label(
        self, text: str = "", *, style: str = "Popisek.TLabel", side: str = "left"
    ) -> ttk.Label:
        label = ttk.Label(self, text=str(text), style=style)
        label.pack(side=side, padx=(0, self.gap))
        return label

    def add_separator(self, *, side: str = "left") -> ttk.Separator:
        separator = ttk.Separator(self, orient="vertical")
        separator.pack(side=side, fill="y", padx=(0, self.gap))
        return separator

    def add_spacer(self) -> ttk.Frame:
        """Pružná mezera — co přijde potom, odsune se doprava."""

        spacer = ttk.Frame(self)
        spacer.pack(side="left", fill="x", expand=True)
        return spacer

    def set_enabled(self, key: str, enabled: bool = True) -> None:
        button = self.buttons.get(key)
        if button is None:
            return
        button.state(["!disabled" if enabled else "disabled"])


class StatusBar(ttk.Frame):
    """Stavový řádek u spodního okraje okna."""

    def __init__(
        self, master: tk.Misc | None = None, *, text: str = "", progress: bool = True, **kwargs: Any
    ) -> None:
        super().__init__(master, **kwargs)
        self.columnconfigure(0, weight=1)

        self.separator = ttk.Separator(self, orient="horizontal")
        self.separator.grid(row=0, column=0, columnspan=2, sticky="ew")

        self.label = ttk.Label(
            self, style="Napoveda.TLabel", text=str(text), anchor="w", justify="left"
        )
        self.label.grid(
            row=1, column=0, sticky="ew", padx=theme.PAD_M, pady=theme.PAD_S
        )

        self.progress: ttk.Progressbar | None = None
        if progress:
            self.progress = ttk.Progressbar(self, mode="indeterminate", length=120)
            self.progress.grid(row=1, column=1, sticky="e", padx=(0, theme.PAD_M))
            self.progress.grid_remove()

    def set(self, text: str) -> None:
        """Běžná hláška."""

        self.label.configure(text=str(text or ""), style="Napoveda.TLabel")

    def set_error(self, text: str) -> None:
        """Chybová hláška (červeně)."""

        self.label.configure(text=str(text or ""), style="Chyba.TLabel")

    def set_success(self, text: str) -> None:
        """Potvrzení (zeleně)."""

        self.label.configure(text=str(text or ""), style="Uspech.TLabel")

    def clear(self) -> None:
        self.set("")

    def start_progress(self, text: str = "") -> None:
        """Rozběhne neurčitý ukazatel průběhu (u operací ve vlákně)."""

        if text:
            self.set(text)
        if self.progress is None:
            return
        try:
            self.progress.grid()
            self.progress.start(12)
        except tk.TclError:  # pragma: no cover
            pass

    def stop_progress(self, text: str | None = None) -> None:
        if self.progress is not None:
            try:
                self.progress.stop()
                self.progress.grid_remove()
            except tk.TclError:  # pragma: no cover
                pass
        if text is not None:
            self.set(text)


class Card(ttk.Frame):
    """Rámeček s nadpisem; obsah se vkládá do :attr:`body`."""

    def __init__(
        self,
        master: tk.Misc | None = None,
        title: str = "",
        *,
        subtitle: str = "",
        padding: Any = theme.PAD_M,
        style: str = "Karta.TFrame",
        **kwargs: Any,
    ) -> None:
        super().__init__(master, style=style, padding=padding, **kwargs)
        self.columnconfigure(0, weight=1)

        self.title_label = ttk.Label(self, style="Podnadpis.TLabel", text=str(title))
        self.title_label.grid(row=0, column=0, sticky="w")
        if not title:
            self.title_label.grid_remove()

        self.subtitle_label = ttk.Label(
            self, style="Napoveda.TLabel", text=str(subtitle), wraplength=WRAPLENGTH, justify="left"
        )
        self.subtitle_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        if not subtitle:
            self.subtitle_label.grid_remove()

        self.body = ttk.Frame(self, style=style)
        self.body.columnconfigure(0, weight=1)
        self.body.grid(row=2, column=0, sticky="nsew", pady=(theme.PAD_M, 0))
        self.rowconfigure(2, weight=1)

    def set_title(self, text: str) -> None:
        self.title_label.configure(text=str(text or ""))
        if text:
            self.title_label.grid()
        else:
            self.title_label.grid_remove()

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.configure(text=str(text or ""))
        if text:
            self.subtitle_label.grid()
        else:
            self.subtitle_label.grid_remove()
