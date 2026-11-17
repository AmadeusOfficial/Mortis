"""把 web/public/favicon.svg 渲染成多尺寸 PNG，拼成 assets/app.ico（托盘 Mortis.exe 的图标）。

用已安装的 Playwright 和本机 Edge 渲染，ICO 目录项直接装 PNG 数据，不加新依赖。
"""

import struct
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "web" / "public" / "favicon.svg"
ICO = ROOT / "assets" / "app.ico"
SIZES = (16, 24, 32, 48, 64, 256)


def render(svg: str, sizes: tuple[int, ...]) -> dict[int, bytes]:
    images: dict[int, bytes] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        try:
            for size in sizes:
                page = browser.new_page(
                    viewport={"width": size, "height": size}, device_scale_factor=1
                )
                page.set_content(
                    "<!doctype html><html><body style='margin:0;background:transparent'>"
                    f"<div style='width:{size}px;height:{size}px'>{svg}</div></body></html>"
                )
                page.eval_on_selector(
                    "svg",
                    "(el, size) => { el.setAttribute('width', size); "
                    "el.setAttribute('height', size); el.style.display = 'block'; }",
                    str(size),
                )
                images[size] = page.locator("div").screenshot(omit_background=True, type="png")
                page.close()
        finally:
            browser.close()
    return images


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 数据")
    return struct.unpack(">II", data[16:24])


def build_ico(images: dict[int, bytes]) -> bytes:
    entries = sorted(images.items())
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = len(header) + 16 * len(entries)
    directory = b""
    body = b""
    for size, data in entries:
        width, height = png_size(data)
        if (width, height) != (size, size):
            raise ValueError(f"{size} 尺寸渲染成了 {width}x{height}")
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    return header + directory + body


def main() -> int:
    images = render(SVG.read_text(encoding="utf-8"), SIZES)
    ICO.parent.mkdir(parents=True, exist_ok=True)
    ICO.write_bytes(build_ico(images))
    for size, data in sorted(images.items()):
        print(f"{size}x{size}: {len(data)} 字节")
    print(f"ICO={ICO} {ICO.stat().st_size} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
