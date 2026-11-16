"""Take the README screenshots from the preview pages (sample home only).

    python scripts/panel-preview/shoot.py [out dir] [--mdi DIR]

Serves the repo, opens scripts/panel-preview/index.html (the sidebar) and
card.html (the dashboard card) in Chromium (Playwright) and saves
docs/images/*.png. The pages have no Home Assistant, so `<ha-icon>` draws
nothing; with --mdi pointing at a folder where `npm install @mdi/js` was
run, the icons are drawn from that package. Fails on page errors and on
sideways scrolling.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
from pathlib import Path
import re
import sys
import threading

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
PANEL = "scripts/panel-preview/index.html"
CARD = "scripts/panel-preview/card.html"

DRAW = """(paths) => {
  const draw = (root) => {
    for (const el of root.querySelectorAll('ha-icon')) {
      const d = paths[el.getAttribute('icon')];
      if (!d) continue;
      el.innerHTML = `<svg viewBox="0 0 24 24" style="width:var(--mdc-icon-size,24px);height:var(--mdc-icon-size,24px);fill:currentColor"><path d="${d}"/></svg>`;
      el.style.display = 'inline-flex';
    }
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) draw(el.shadowRoot);
  };
  draw(document);
}"""

# Opens the first routine in the editor the way the panel does.
EDIT_FIRST = """() => {
  const panel = window.__panel;
  panel._view = {name: 'editor', room: null};
  panel._draft = panel._draftFromRoutine(panel._data.routines[0]);
  panel._render();
}"""

# (file, page, query, width, height, tab to open or a script)
SHOTS = [
    ("rooms.png", PANEL, "", 1280, 470, None),
    ("routines.png", PANEL, "", 1280, 800, "routines"),
    ("today.png", PANEL, "", 1280, 800, "today"),
    ("editor.png", PANEL, "", 1280, 800, EDIT_FIRST),
    ("activity.png", PANEL, "", 1280, 800, "activity"),
    ("settings.png", PANEL, "", 1280, 800, "settings"),
    ("rooms-phone.png", PANEL, "", 390, 844, None),
    ("routines-dark.png", PANEL, "?theme=dark", 1280, 800, "routines"),
    ("card.png", CARD, "", 480, 340, None),
]


def mdi_paths(folder: str | None) -> dict[str, str]:
    """mdi:<name> -> path data from @mdi/js, or nothing."""
    if not folder:
        return {}
    source = Path(folder) / "node_modules" / "@mdi" / "js" / "mdi.js"
    if not source.exists():
        sys.exit(f"{source} not found: run `npm install @mdi/js` in {folder}")
    paths = {}
    for name, data in re.findall(
        r'export (?:var|const) mdi(\w+) ?= ?"([^"]+)"', source.read_text()
    ):
        paths["mdi:" + re.sub(r"(?<!^)(?=[A-Z0-9])", "-", name).lower()] = data
    return paths


def serve() -> int:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

    handler = functools.partial(Quiet, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


async def main(out: Path, paths: dict[str, str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{serve()}/"
    errors: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        for name, page_path, query, width, height, step in SHOTS:
            page = await browser.new_page(viewport={"width": width, "height": height})
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(base + page_path + query)
            await page.wait_for_timeout(800)
            if step and step.startswith("()"):
                await page.evaluate(step)
            elif step:
                tab = page.locator("daily-routine-panel").locator(
                    f"[data-act='tab'][data-tab='{step}']"
                )
                await tab.click()
            await page.wait_for_timeout(400)
            if paths:
                await page.evaluate(DRAW, paths)
            if await page.evaluate("document.documentElement.scrollWidth > innerWidth"):
                errors.append(f"{name}: scrolls sideways")
            await page.screenshot(path=str(out / name))
            print("saved", out / name)
            await page.close()
        await browser.close()
    if errors:
        sys.exit("\n".join(errors))


if __name__ == "__main__":
    args = sys.argv[1:]
    mdi = None
    if "--mdi" in args:
        at = args.index("--mdi")
        mdi = args[at + 1]
        del args[at : at + 2]
    target = Path(args[0]) if args else ROOT / "docs" / "images"
    asyncio.run(main(target, mdi_paths(mdi)))
