"""Build self-contained report.html and slides.html from docs/*_src.html.

Replaces every {{img:NAME}} with a base64 data URI of docs/img/NAME, so the files open
anywhere by double-click with all screenshots visible.
"""
import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def inline(match: re.Match) -> str:
    path = DOCS / "img" / match.group(1)
    mime = "image/png" if path.suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


for name in ("report", "slides"):
    src = DOCS / f"{name}_src.html"
    if src.exists():
        html = re.sub(r"\{\{img:([^}]+)\}\}", inline, src.read_text(encoding="utf-8"))
        (ROOT / f"{name}.html").write_text(html, encoding="utf-8")
        print(f"wrote {name}.html ({len(html) / 1e6:.1f} MB)")
