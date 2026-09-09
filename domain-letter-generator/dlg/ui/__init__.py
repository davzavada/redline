"""Uživatelské rozhraní aplikace (Tkinter/ttk).

Balík obsahuje:

``theme``
    barvy, odsazení, písma a pojmenované ttk styly (:func:`dlg.ui.theme.apply_theme`,
    :func:`dlg.ui.theme.enable_dpi_awareness`),
``widgets``
    znovupoužitelné komponenty — rolovatelná plocha, formulářová pole,
    combobox s napovídáním, pole pro datum, lišta, stavový řádek, karta,
    dialogy a spouštění práce ve vlákně,
``templates_view``, ``mapping_view``, ``generate_view``, ``settings_view``
    jednotlivé pohledy, které skládá :mod:`dlg.app`.

Podmoduly se schválně neimportují automaticky — ``import dlg.ui`` tak nevyžaduje
``tkinter`` a nevytváří žádné okno. Pohledy si je importují samy::

    from dlg.ui import theme, widgets
"""

from __future__ import annotations

__all__ = [
    "theme",
    "widgets",
]
