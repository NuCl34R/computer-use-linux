#!/usr/bin/env python3
"""Check committed documentation targets, SVG syntax and public tool inventory."""
import ast
import html.parser
import pathlib
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("href", "src") and value:
                self.urls.append(value)


def main():
    errors = []
    documents = [ROOT / name for name in ("README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md")]
    documents += list((ROOT / "docs").rglob("*.md")) + list((ROOT / "skills").rglob("*.md"))
    for path in documents:
        content = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
        parser = Links()
        parser.feed(content)
        urls = re.findall(r"\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)", content) + parser.urls
        for url in urls:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme or parsed.netloc:
                continue
            target = (path.parent / urllib.parse.unquote(parsed.path)).resolve() if parsed.path else path
            if not target.exists():
                errors.append(f"{path.relative_to(ROOT)}: missing {url}")
            elif parsed.fragment and target.suffix == ".md":
                headings = re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", target.read_text(), flags=re.M)
                anchors = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}
                if urllib.parse.unquote(parsed.fragment) not in anchors:
                    errors.append(f"{path.relative_to(ROOT)}: unknown anchor {url}")
    for path in (ROOT / "docs/assets").glob("*.svg"):
        ET.parse(path)
    tree = ast.parse((ROOT / "skills/linux-computer-use/scripts/cul/engine.py").read_text())
    tools = next(node.value for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOLS" for t in node.targets))
    names = [node.value for node in tools.keys]
    readme = (ROOT / "README.md").read_text()
    for name in names:
        if "`" + name + "`" not in readme:
            errors.append("README tool inventory missing " + name)
    if len(names) != 20:
        errors.append("Update the documented tool count")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Documentation OK: {len(documents)} Markdown files, SVG syntax, {len(names)} tools")
    return 0


if __name__ == "__main__":
    sys.exit(main())
