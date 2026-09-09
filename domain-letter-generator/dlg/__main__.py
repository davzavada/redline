"""Spuštění aplikace: ``python -m dlg``."""

from __future__ import annotations

import sys


def _report(text: str) -> None:
    """Oznámí selhání i tam, kde ``sys.stderr`` neexistuje.

    Zabalené ``.exe`` běží bez konzole, takže ``sys.stderr`` je ``None``
    a ``print`` tiše nic neudělá. Tady ještě není jisté, že jde naimportovat
    ``dlg.app`` (chybějící tkinter), proto se hláška zapíše i do souboru.
    """

    try:
        if sys.stderr is not None:
            print(text, file=sys.stderr)
    except Exception:  # noqa: BLE001 - hlášení chyby nesmí vyrobit další chybu
        pass
    try:
        from datetime import datetime

        from . import config

        path = config.app_home() / "start-error.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"--- {datetime.now().isoformat(timespec='seconds')} ---\n{text}\n")
    except Exception:  # noqa: BLE001
        pass


def _main() -> int:
    try:
        from .app import main
    except ImportError as exc:  # pragma: no cover - závisí na prostředí
        missing = getattr(exc, "name", "") or ""
        if missing.split(".")[0] in ("tkinter", "_tkinter", "Tkinter"):
            _report(
                "Chybí knihovna tkinter, bez které nelze zobrazit okno aplikace.\n"
                "Na Windows ji doinstalujete opravou instalace Pythonu "
                "(volba „tcl/tk and IDLE“), na Linuxu balíčkem python3-tk."
            )
        else:
            _report(f"Aplikaci se nepodařilo spustit — chybí část programu: {exc}.")
        return 1

    return int(main() or 0)


if __name__ == "__main__":
    raise SystemExit(_main())
