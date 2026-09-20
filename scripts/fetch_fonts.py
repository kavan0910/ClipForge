"""Fetch the bundled open-licence fonts (SIL OFL 1.1) from google/fonts into captions/fonts/."""

from __future__ import annotations

from pathlib import Path

import httpx

DEST = Path(__file__).resolve().parent.parent / "captions" / "fonts"
BASE = "https://github.com/google/fonts/raw/main/"
FONTS = {  # file: (path in google/fonts, purpose)
    "Inter.ttf": (
        "ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
        "clean UI/subtitle sans, Latin/Cyrillic/Greek",
    ),
    "BebasNeue-Regular.ttf": ("ofl/bebasneue/BebasNeue-Regular.ttf", "condensed display caps"),
    "Anton-Regular.ttf": ("ofl/anton/Anton-Regular.ttf", "heavy display caps"),
    "Montserrat.ttf": ("ofl/montserrat/Montserrat%5Bwght%5D.ttf", "geometric sans, bold captions"),
    "PlayfairDisplay.ttf": ("ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf", "editorial serif"),
    "JetBrainsMono.ttf": (
        "ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
        "monospace / typewriter",
    ),
    "NotoSans.ttf": (
        "ofl/notosans/NotoSans%5Bwdth%2Cwght%5D.ttf",
        "fallback: broad Latin/Cyrillic/Greek/Vietnamese",
    ),
    "NotoSansArabic.ttf": (
        "ofl/notosansarabic/NotoSansArabic%5Bwdth%2Cwght%5D.ttf",
        "fallback: Arabic script (RTL)",
    ),
    "NotoSansSC.ttf": (
        "ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf",
        "fallback: Simplified Chinese / CJK",
    ),
}


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=180) as c:
        for name, (path, purpose) in FONTS.items():
            f = DEST / name
            if f.exists() and f.stat().st_size > 10_000:
                print(f"{name}: cached")
                continue
            r = c.get(BASE + path)
            r.raise_for_status()
            f.write_bytes(r.content)
            print(f"{name}: {len(r.content) / 1e6:.1f} MB ({purpose})")


if __name__ == "__main__":
    main()
