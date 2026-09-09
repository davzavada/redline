"""Spuštění aplikace: ``python -m dlg``."""

from __future__ import annotations

import sys


def _main() -> int:
    try:
        from .app import main
    except ImportError as exc:  # pragma: no cover - závisí na prostředí
        missing = getattr(exc, "name", "") or ""
        if missing.split(".")[0] in ("tkinter", "_tkinter", "Tkinter"):
            print(
                "Chybí knihovna tkinter, bez které nelze zobrazit okno aplikace.\n"
                "Na Windows ji doinstalujete opravou instalace Pythonu "
                "(volba „tcl/tk and IDLE“), na Linuxu balíčkem python3-tk.",
                file=sys.stderr,
            )
        else:
            print(
                f"Aplikaci se nepodařilo spustit — chybí část programu: {exc}.",
                file=sys.stderr,
            )
        return 1

    return int(main() or 0)


if __name__ == "__main__":
    raise SystemExit(_main())
