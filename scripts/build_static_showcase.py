#!/usr/bin/env python3
"""Build a serverless portfolio site from the archived public case."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
ARCHIVE = ROOT / "data" / "showcase.json"
OUTPUT = ROOT / "showcase-site"


def main() -> None:
    archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    required = ("task", "records", "analysis", "plan", "archive_counts")
    missing = [key for key in required if not archive.get(key)]
    if missing:
        raise SystemExit(f"展示快照缺少字段：{', '.join(missing)}")

    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir()

    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        "<title>有据 · 小红书内容策划</title>",
        "<title>有据 · 小红书内容策划案例</title>",
    )
    html = html.replace('href="/static/style.css"', 'href="./style.css"')
    html = html.replace(
        '<script src="/static/app.js"></script>',
        '<script>window.STATIC_SHOWCASE_URL="./showcase.json";</script>'
        '<script src="./app.js"></script>',
    )
    html = html.replace('href="/api/export"', 'href="#"')

    (OUTPUT / "index.html").write_text(html, encoding="utf-8")
    (OUTPUT / "404.html").write_text(html, encoding="utf-8")
    shutil.copy2(FRONTEND / "style.css", OUTPUT / "style.css")
    shutil.copy2(FRONTEND / "app.js", OUTPUT / "app.js")
    shutil.copy2(ARCHIVE, OUTPUT / "showcase.json")
    (OUTPUT / ".nojekyll").touch()

    public_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in OUTPUT.iterdir()
        if path.is_file()
    )
    forbidden = ("xsec_token=", "browser-profile")
    found = [value for value in forbidden if value in public_text]
    if re.search(r"\bsk-[A-Za-z0-9_-]{20,}\b", public_text):
        found.append("OpenAI 风格密钥")
    if found:
        raise SystemExit(f"公开目录隐私检查未通过：{', '.join(found)}")

    print(f"静态展示站已生成：{OUTPUT}")


if __name__ == "__main__":
    main()
