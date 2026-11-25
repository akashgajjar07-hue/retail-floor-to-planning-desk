"""Embed outputs/dashboard_data.json into docs/template.html -> docs/index.html (GitHub Pages ready)."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
data = (ROOT / "outputs/dashboard_data.json").read_text().replace("</", "<\\/")
html = (ROOT / "docs/template.html").read_text().replace("/*__DATA__*/null", data)
(ROOT / "docs/index.html").write_text(html)
print("wrote docs/index.html", len(html) // 1024, "KB")
