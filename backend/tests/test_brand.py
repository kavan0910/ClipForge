import numpy as np
from PIL import Image, ImageDraw

from clipforge import brand as b
from clipforge.captions.spec import load_template
from clipforge.captions.timeline import FRAME_H, FRAME_W


def make_logo(path, color=(255, 80, 40)):
    img = Image.new("RGBA", (300, 120), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, 299, 119], radius=30, fill=(*color, 255))
    img.save(path)


def kit(tmp_path, **kw):
    d = tmp_path / "acme"
    d.mkdir()
    make_logo(d / "logo.png")
    k = b.BrandKit(
        id="acme",
        name="Acme",
        logo=b.Logo(file="logo.png", position="top_right", opacity=1.0, width=0.2),
        **kw,
    )
    b.save_kit(k, tmp_path)
    return k, d


def test_kit_roundtrip_and_listing(tmp_path):
    k, _ = kit(tmp_path, accent_color="#00FF88", vocabulary=["Acme", "Zorp"])
    assert b.load_kit("acme", tmp_path) == k and b.list_kits(tmp_path) == ["acme"]


def test_kit_colours_override_template_tokens(tmp_path):
    k, _ = kit(tmp_path, text_color="#F0F0F0", accent_color="#00FF88")
    t = b.apply_kit(load_template("clean-minimal"), k)
    assert (
        t.fill == "#F0F0F0"
        and t.active.color == "#00FF88"
        and t.active.underline_color == "#00FF88"
    )
    assert (
        b.apply_kit(load_template("clean-minimal"), None).fill
        == load_template("clean-minimal").fill
    )


def test_watermark_lands_in_the_requested_corner_with_opacity(tmp_path):
    k, d = kit(tmp_path)
    frame = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
    out = b.watermark_overlay(k, d)(frame.copy(), 0)  # type: ignore[misc]
    ys, xs = np.where(out.max(axis=2) > 0)
    assert (
        xs.min() > FRAME_W * 0.6 and ys.max() < FRAME_H * 0.2
    )  # top-right, clear of the bottom UI zone
    assert out[ys[len(ys) // 2], xs[len(xs) // 2]][2] > 200  # BGR: the logo's red channel
    half = b.BrandKit(id="h", name="h", logo=b.Logo(file="logo.png", opacity=0.5, width=0.2))
    o2 = b.watermark_overlay(half, d)(frame.copy(), 0)  # type: ignore[misc]
    assert 90 < o2[ys[len(ys) // 2], xs[len(xs) // 2]][2] < 170  # blended with black at 50%


def test_cards_have_exact_length_fade_and_brand_colours(tmp_path):
    k, d = kit(tmp_path)
    card = b.Card(title="Watch this", subtitle="Acme weekly", seconds=1.6, background="#102030")
    frames = b.card_frames(card, k, d, 25.0)
    assert (
        len(frames) == 40 and frames[0].max() == 0 and frames[-1].max() == 0
    )  # fades from and to black
    mid = frames[20]
    assert (
        mid.shape == (FRAME_H, FRAME_W, 3) and abs(int(mid[5, 5, 0]) - 0x30) <= 2
    )  # BGR background blue
