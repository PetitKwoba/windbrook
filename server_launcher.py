from __future__ import annotations

import sys
import traceback
from pathlib import Path

from aw_portal.web import run


ROOT = Path(__file__).resolve().parent


def main() -> None:
    out = (ROOT / "server.out.log").open("a", encoding="utf-8")
    err = (ROOT / "server.err.log").open("a", encoding="utf-8")
    sys.stdout = out
    sys.stderr = err
    try:
        run()
    except Exception:
        traceback.print_exc(file=err)
        err.flush()
        raise


if __name__ == "__main__":
    main()
