"""Build the static demo into _site/: the page, and the repo's slot/booking modules as pkg.zip."""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
# Only the modules the booking logic needs. main.py, routers, deps and auth need a server.
MODULES = ["__init__.py", "config.py", "models.py", "slots.py", "booking.py"]


def build(out: Path = ROOT / "_site") -> Path:
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for name in ("index.html", "app.js"):
        shutil.copy2(DEMO / name, out / name)
    (out / ".nojekyll").write_text("")
    with zipfile.ZipFile(out / "pkg.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for m in MODULES:
            z.write(ROOT / "app" / m, f"app/{m}")
        z.write(DEMO / "booking_demo.py", "booking_demo.py")
    sys.path.insert(0, str(DEMO))
    from page_meta import apply  # title, description and share preview

    apply(out, json.loads((DEMO / "meta.json").read_text(encoding="utf-8")), ROOT)
    return out


if __name__ == "__main__":
    print(f"built {build()}")
