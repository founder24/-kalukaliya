#!/usr/bin/env python3
"""
Task #17 — Generate 1200×630 Open Graph banner PNGs for all Syrabit.ai
SPA routes that have og:image injection via the edge worker (Task #14).

Outputs: scripts/og-images/generated/<slug>.png

Run:
    pip install Pillow   # already available in the backend venv
    python scripts/og-images/generate_og_images.py

After generation, upload with:
    python scripts/og-images/upload_to_r2.py
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── output ────────────────────────────────────────────────────────────────────
OUT_DIR = Path(__file__).parent / "generated"
OUT_DIR.mkdir(parents=True, exist_ok=True)

W, H = 1200, 630

# ── fonts (DejaVu Sans — always available on NixOS/Debian/Ubuntu) ─────────────
FONT_BASE = "/usr/share/fonts/truetype/dejavu"
FONT_BOLD   = FONT_BASE + "/DejaVuSans-Bold.ttf"
FONT_REGULAR = FONT_BASE + "/DejaVuSans.ttf"

def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)

# ── brand palette ─────────────────────────────────────────────────────────────
BG_TOP    = (13, 10, 40)     # #0d0a28  deep navy
BG_BOT    = (22, 18, 60)     # #16123c  slightly lighter navy
ACCENT    = (124, 58, 237)   # #7c3aed  Syrabit violet
ACCENT2   = (167, 139, 250)  # #a78bfa  light violet (accent text)
WHITE     = (255, 255, 255)
GREY      = (180, 180, 200)
DIM_GREY  = (120, 120, 150)

# ── image map: slug → (headline, sub_label) ───────────────────────────────────
# sub_label appears below the headline in a smaller accent colour.
IMAGES: dict[str, tuple[str, str]] = {
    # --- Subject routes -------------------------------------------------------
    "accountancy":                    ("Accountancy",                   "AHSEC · Syrabit.ai"),
    "biology":                        ("Biology",                       "AHSEC · Syrabit.ai"),
    "business-studies":               ("Business Studies",              "AHSEC · Syrabit.ai"),
    "chemistry":                      ("Chemistry",                     "AHSEC · Syrabit.ai"),
    "economics":                      ("Economics",                     "AHSEC · Syrabit.ai"),
    "education":                      ("Education",                     "AHSEC · Syrabit.ai"),
    "english-core":                   ("English",                       "AHSEC · Syrabit.ai"),
    "environmental-education":        ("Environmental Education",       "AHSEC · Syrabit.ai"),
    "geography":                      ("Geography",                     "AHSEC · Syrabit.ai"),
    "history":                        ("History",                       "AHSEC · Syrabit.ai"),
    "logic-philosophy":               ("Logic & Philosophy",            "AHSEC · Syrabit.ai"),
    "mathematics":                    ("Mathematics",                   "AHSEC · Syrabit.ai"),
    "modern-indian-language-assamese":("Modern Indian Language",        "Assamese · AHSEC · Syrabit.ai"),
    "physics":                        ("Physics",                       "AHSEC · Syrabit.ai"),
    "political-science":              ("Political Science",             "AHSEC · Syrabit.ai"),
    "sociology":                      ("Sociology",                     "AHSEC · Syrabit.ai"),
    # --- Board hub pages ------------------------------------------------------
    "ahsec":                          ("AHSEC Study Materials",         "HS 1st & 2nd Year · Syrabit.ai"),
    "seba":                           ("SEBA Study Materials",          "Class 9 & 10 · Syrabit.ai"),
    "degree":                         ("Degree Study Materials",        "FYUGP / NEP · Syrabit.ai"),
    # --- Board+class hub pages ------------------------------------------------
    "ahsec-class-11":                 ("AHSEC Class 11",                "Study Materials · Syrabit.ai"),
    "ahsec-class-12":                 ("AHSEC Class 12",                "Study Materials · Syrabit.ai"),
    # --- Notes hub pages ------------------------------------------------------
    "notes":                          ("Study Notes",                   "AHSEC · Degree · Syrabit.ai"),
    "notes-class-11":                 ("Class 11 Notes",                "All Subjects · Syrabit.ai"),
    "notes-class-12":                 ("Class 12 Notes",                "All Subjects · Syrabit.ai"),
}


def _gradient_bg(draw: ImageDraw.ImageDraw) -> None:
    """Vertical linear gradient from BG_TOP to BG_BOT."""
    for y in range(H):
        t = y / (H - 1)
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))


def _accent_stripe(draw: ImageDraw.ImageDraw) -> None:
    """4 px violet strip at the very top + a 2 px strip at the bottom."""
    draw.rectangle([(0, 0), (W, 3)], fill=ACCENT)
    draw.rectangle([(0, H - 2), (W, H - 1)], fill=ACCENT)


def _decorative_circle(img: Image.Image) -> None:
    """Subtle large blurred circle in the lower-right as a design accent."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    cx, cy, r = W - 60, H + 80, 380
    d.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=(*ACCENT, 18))
    img.alpha_composite(overlay)


def _logo_text(draw: ImageDraw.ImageDraw) -> None:
    """'SYRABIT.AI' logotype in top-left."""
    f = _font(FONT_BOLD, 26)
    draw.text((50, 36), "SYRABIT.AI", font=f, fill=ACCENT2)


def _tagline(draw: ImageDraw.ImageDraw) -> None:
    """Bottom tagline."""
    f = _font(FONT_REGULAR, 22)
    tag = "AI-powered notes · MCQs · Flashcards · PYQs"
    draw.text((50, H - 44), tag, font=f, fill=DIM_GREY)


def _centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    y_center: int,
    font: ImageFont.FreeTypeFont,
    fill: tuple,
    max_width: int = W - 100,
) -> int:
    """Draw text centred horizontally, wrapping if wider than max_width.
    Returns the bottom y of the last line drawn."""
    words = text.split()
    lines: list[str] = []
    line: list[str] = []
    for word in words:
        trial = " ".join(line + [word])
        bb = draw.textbbox((0, 0), trial, font=font)
        if bb[2] - bb[0] <= max_width or not line:
            line.append(word)
        else:
            lines.append(" ".join(line))
            line = [word]
    if line:
        lines.append(" ".join(line))

    bb0 = draw.textbbox((0, 0), "Ag", font=font)
    line_h = (bb0[3] - bb0[1]) + 10
    total_h = len(lines) * line_h
    y = y_center - total_h // 2

    for ln in lines:
        bb = draw.textbbox((0, 0), ln, font=font)
        x = (W - (bb[2] - bb[0])) // 2
        draw.text((x, y), ln, font=font, fill=fill)
        y += line_h

    return y


def make_image(slug: str, headline: str, sub_label: str) -> Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)

    _gradient_bg(draw)
    _decorative_circle(img)
    draw = ImageDraw.Draw(img)
    _accent_stripe(draw)
    _logo_text(draw)
    _tagline(draw)

    # ── headline ──────────────────────────────────────────────────────────────
    # Auto-size: start at 90 px and shrink until it fits in one or two lines.
    for size in range(90, 44, -4):
        f_head = _font(FONT_BOLD, size)
        bb = draw.textbbox((0, 0), headline, font=f_head)
        if bb[2] - bb[0] <= W - 100:
            break

    head_y = 260  # vertical centre target
    sub_top = _centered_text(draw, headline, head_y, f_head, WHITE)

    # ── sub-label ─────────────────────────────────────────────────────────────
    f_sub = _font(FONT_REGULAR, 30)
    sub_top += 20
    _centered_text(draw, sub_label, sub_top + 22, f_sub, ACCENT2)

    # ── divider line below logo ───────────────────────────────────────────────
    draw.line([(50, 82), (W - 50, 82)], fill=(*ACCENT, 80), width=1)

    out = OUT_DIR / f"{slug}.png"
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out


def main() -> None:
    print(f"Generating {len(IMAGES)} OG banner images → {OUT_DIR}")
    for slug, (headline, sub_label) in IMAGES.items():
        path = make_image(slug, headline, sub_label)
        print(f"  ✓ {path.name}")
    print(f"\nDone. {len(IMAGES)} files written to {OUT_DIR}/")
    print("Next step: set R2 credentials and run  python scripts/og-images/upload_to_r2.py")


if __name__ == "__main__":
    main()
