"""Render assets/logo.svg to the brand icons (256 and 512 pixels).

Needs Playwright with Chromium: python scripts/make_brand.py
The logo is the same on light and dark backgrounds, so the dark icons are
copies of the light ones.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "assets" / "logo.svg"
BRAND = ROOT / "custom_components" / "daily_routine" / "brand"


async def main() -> None:
    svg = SVG.read_text()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
            page = await browser.new_page(viewport={"width": size, "height": size})
            await page.set_content(
                "<html><body style='margin:0;background:transparent'>"
                f"<div style='width:{size}px;height:{size}px'>{svg}</div></body></html>"
            )
            await page.locator("svg").evaluate(
                "(el, s) => { el.setAttribute('width', s); el.setAttribute('height', s); }",
                size,
            )
            await page.locator("div").screenshot(
                path=BRAND / name, omit_background=True
            )
            await page.close()
        await browser.close()
    for name in ("icon.png", "icon@2x.png"):
        shutil.copyfile(BRAND / name, BRAND / f"dark_{name}")


asyncio.run(main())
