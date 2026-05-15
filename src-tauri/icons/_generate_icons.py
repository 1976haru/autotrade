"""
Agent Trader v1 — icon asset generator.

CLAUDE.md 정책:
- 외부 폰트 / 저작권 파일 0건 — Pillow 내장 default font 사용.
- 단색 배경 + "AT" 텍스트 — 자체 생성, 외부 trademark 0건.
- 산출물은 src-tauri/icons/ 에 저장. .gitignore 영향 없음 (icons/ 는 tracked).

본 스크립트는 *PR 시점 한 번* 실행하는 generator. 산출 PNG/ICO/ICNS 는
모두 git 에 commit 한다 (binary 작아서 영향 미미). 추후 정식 브랜딩 시
별도 PR 에서 디자이너 자산으로 교체.

실행: python src-tauri/icons/_generate_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent

# 브랜드 색 — frontend/public/icons/*.svg 의 메인 톤과 정렬 (sky-500 / 0ea5e9).
# 그라데이션 없이 단색 (외부 자산 0건 원칙).
BG_COLOR = (14, 165, 233, 255)   # #0ea5e9
FG_COLOR = (255, 255, 255, 255)  # white
LABEL = "AT"

# 생성할 PNG 사이즈 + 파일명. .ico / .icns 는 별도 처리.
PNG_TARGETS: list[tuple[str, int]] = [
    ("32x32.png", 32),
    ("128x128.png", 128),
    ("128x128@2x.png", 256),
]
# .ico 는 multi-resolution 형식. Tauri Windows resource 가 256 까지 요구.
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
# .icns 는 macOS 용 (Windows-only 빌드에서는 미사용이지만 conf 참조 일치 위해 생성).
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]


def _draw_icon(size: int) -> Image.Image:
    """단색 배경 + 'AT' 중앙 텍스트로 size×size 아이콘 생성."""
    img = Image.new("RGBA", (size, size), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # 모서리 둥글게 — alpha mask 합성.
    radius = max(2, size // 6)
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255
    )
    rounded = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rounded.paste(img, (0, 0), mask)
    img = rounded
    draw = ImageDraw.Draw(img)

    # 폰트 — Pillow default. 외부 폰트 파일 0건.
    # size 비율로 동적 결정. 작은 size 는 default 만 사용 가능 (truetype 없으므로).
    target_pt = max(8, int(size * 0.55))
    try:
        # Pillow >=10 supports load_default(size=...)
        font = ImageFont.load_default(size=target_pt)
    except TypeError:
        font = ImageFont.load_default()

    # 텍스트 중심 정렬.
    bbox = draw.textbbox((0, 0), LABEL, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) / 2 - bbox[0]
    y = (size - text_h) / 2 - bbox[1]
    draw.text((x, y), LABEL, fill=FG_COLOR, font=font)
    return img


def _save_png(path: Path, size: int) -> None:
    img = _draw_icon(size)
    img.save(path, format="PNG", optimize=True)
    print(f"  wrote {path.name} ({size}x{size}, {path.stat().st_size} bytes)")


def _save_ico(path: Path, sizes: list[int]) -> None:
    # 가장 큰 size 를 base 로 그리고, Pillow 가 sizes 인자로 multi-resolution
    # ICO 를 생성. format=ICO 는 PIL 의 IcoImagePlugin 이 처리.
    base = _draw_icon(max(sizes))
    base.save(
        path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )
    print(f"  wrote {path.name} (sizes={sizes}, {path.stat().st_size} bytes)")


def _save_icns(path: Path, sizes: list[int]) -> None:
    # ICNS 는 macOS 용. Pillow 가 지원 — 가장 큰 base + sizes 로 multi-res 생성.
    base = _draw_icon(max(sizes))
    base.save(
        path,
        format="ICNS",
        sizes=[(s, s) for s in sizes],
    )
    print(f"  wrote {path.name} (sizes={sizes}, {path.stat().st_size} bytes)")


def main() -> None:
    print(f"Agent Trader icon generator → {OUT_DIR}")
    for name, size in PNG_TARGETS:
        _save_png(OUT_DIR / name, size)
    _save_ico(OUT_DIR / "icon.ico", ICO_SIZES)
    _save_icns(OUT_DIR / "icon.icns", ICNS_SIZES)
    print("done.")


if __name__ == "__main__":
    main()
