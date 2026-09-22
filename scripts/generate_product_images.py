"""
Generate the demo product illustrations shipped in assets/products/.

Run:  python scripts/generate_product_images.py

HONESTY NOTE (read this before assuming these are real photos): this
sandbox has no internet access, so nothing here was downloaded, scraped,
or licensed from anywhere - these are procedurally generated, offline,
with Pillow. They are styled to look closer to studio product photography
than a flat vector icon (neutral seamless background, soft drop shadow,
gradient-shaded object instead of a flat-fill silhouette, no text baked
into the image), but they are still illustrations of the product's shape,
not photographs of a real object. If you want real product photography,
see README.md "Product images" for exactly where to drop replacement
files - the pipeline resolves by filename, so swapping a file in
assets/products/ with a real photo of the same name requires no code
changes at all.

Safe to re-run: overwrites its own output deterministically.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFilter

import config

SIZE = 900
# Neutral, seamless "studio backdrop" - the same light gray a real product
# photo booth uses, not a colored brand gradient.
BG_TOP = (246, 247, 249)
BG_BOTTOM = (233, 235, 239)
SHADOW_COLOR = (30, 30, 35)


def _studio_background():
    img = Image.new("RGB", (SIZE, SIZE), BG_TOP)
    draw = ImageDraw.Draw(img)
    for y in range(SIZE):
        t = y / SIZE
        r = int(BG_TOP[0] * (1 - t) + BG_BOTTOM[0] * t)
        g = int(BG_TOP[1] * (1 - t) + BG_BOTTOM[1] * t)
        b = int(BG_TOP[2] * (1 - t) + BG_BOTTOM[2] * t)
        draw.line([(0, y), (SIZE, y)], fill=(r, g, b))
    return img


def _shaded_fill(mask_img, base_color, light_from="top-left"):
    """
    Fill a shape's alpha mask with a soft directional gradient (lighter on
    the lit side, darker on the shadow side) instead of one flat color -
    this is what reads as "an object catching studio light" rather than
    "a flat icon."
    """
    w, h = mask_img.size
    gradient = Image.new("L", (w, h))
    gpix = gradient.load()
    lighten = 60
    darken = 55
    for y in range(h):
        for x in range(w):
            if light_from == "top-left":
                t = (x / w + y / h) / 2.0
            else:
                t = (x / w + (1 - y / h)) / 2.0
            gpix[x, y] = int(255 * (1 - t))
    colored = Image.new("RGB", (w, h), base_color)
    lit = Image.new("RGB", (w, h), tuple(min(255, c + lighten) for c in base_color))
    shadowed = Image.new("RGB", (w, h), tuple(max(0, c - darken) for c in base_color))
    colored = Image.composite(lit, colored, gradient.point(lambda v: max(0, v - 140) * 2))
    colored = Image.composite(shadowed, colored, gradient.point(lambda v: max(0, 140 - v) * 2))
    return Image.composite(colored, Image.new("RGB", (w, h), (0, 0, 0)), mask_img)


def _drop_shadow(canvas, mask, offset=(14, 22), blur=26, opacity=70):
    shadow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    solid = Image.new("RGBA", mask.size, (*SHADOW_COLOR, opacity))
    shadow_layer.paste(solid, offset, mask)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(shadow_layer)


def _paste_shape(canvas, shape_mask, base_color, box, light_from="top-left"):
    """Render one shape: drop shadow, then the directionally-shaded fill, onto canvas."""
    _drop_shadow(canvas, shape_mask, offset=(box[0] + 16, box[1] + 24))
    shaded = _shaded_fill(shape_mask, base_color, light_from)
    shaded_rgba = shaded.convert("RGBA")
    shaded_rgba.putalpha(shape_mask)
    canvas.alpha_composite(shaded_rgba, box[:2])


def _mask_polygon(size, points, blur=1.2):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).polygon(points, fill=255)
    return m.filter(ImageFilter.GaussianBlur(blur))


def _mask_ellipse(size, box, blur=1.2):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).ellipse(box, fill=255)
    return m.filter(ImageFilter.GaussianBlur(blur))


def _mask_rounded_rect(size, box, radius, blur=1.2):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle(box, radius=radius, fill=255)
    return m.filter(ImageFilter.GaussianBlur(blur))


def _mask_ring(size, box, width, blur=1.2):
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.ellipse(box, fill=255)
    inner = (box[0] + width, box[1] + width, box[2] - width, box[3] - width)
    d.ellipse(inner, fill=0)
    return m.filter(ImageFilter.GaussianBlur(blur))


def _mask_arc_band(size, box, start, end, width, blur=1.2):
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.arc(box, start, end, fill=255, width=width)
    return m.filter(ImageFilter.GaussianBlur(blur))


CANVAS_SIZE = (SIZE, SIZE)


def render_knife(canvas):
    blade = _mask_polygon(CANVAS_SIZE, [(230, 470), (610, 260), (650, 300), (280, 520)])
    _paste_shape(canvas, blade, (200, 205, 212), (0, 0, SIZE, SIZE))
    handle = _mask_rounded_rect(CANVAS_SIZE, (170, 460, 300, 520), 14)
    _paste_shape(canvas, handle, (58, 40, 30), (0, 0, SIZE, SIZE))


def render_pan(canvas):
    body = _mask_ring(CANVAS_SIZE, (240, 320, 620, 560), 26)
    _paste_shape(canvas, body, (40, 40, 42), (0, 0, SIZE, SIZE))
    inner = _mask_ellipse(CANVAS_SIZE, (266, 346, 594, 534))
    _paste_shape(canvas, inner, (74, 44, 32), (0, 0, SIZE, SIZE))
    handle = _mask_rounded_rect(CANVAS_SIZE, (600, 420, 760, 450), 12)
    _paste_shape(canvas, handle, (30, 30, 32), (0, 0, SIZE, SIZE))


def render_earbuds(canvas):
    case = _mask_rounded_rect(CANVAS_SIZE, (330, 470, 570, 620), 40)
    _paste_shape(canvas, case, (250, 250, 252), (0, 0, SIZE, SIZE))
    bud_l = _mask_ellipse(CANVAS_SIZE, (300, 300, 400, 420))
    bud_r = _mask_ellipse(CANVAS_SIZE, (500, 300, 600, 420))
    _paste_shape(canvas, bud_l, (255, 255, 255), (0, 0, SIZE, SIZE))
    _paste_shape(canvas, bud_r, (255, 255, 255), (0, 0, SIZE, SIZE))


def render_speaker(canvas):
    body = _mask_rounded_rect(CANVAS_SIZE, (300, 260, 600, 640), 46)
    _paste_shape(canvas, body, (35, 35, 38), (0, 0, SIZE, SIZE))
    ring1 = _mask_ring(CANVAS_SIZE, (355, 330, 545, 520), 14)
    _paste_shape(canvas, ring1, (70, 70, 75), (0, 0, SIZE, SIZE))
    ring2 = _mask_ring(CANVAS_SIZE, (390, 545, 510, 610), 8)
    _paste_shape(canvas, ring2, (70, 70, 75), (0, 0, SIZE, SIZE))


def render_dumbbell(canvas):
    bar = _mask_rounded_rect(CANVAS_SIZE, (330, 400, 570, 460), 10)
    _paste_shape(canvas, bar, (60, 60, 64), (0, 0, SIZE, SIZE))
    for cx in (250, 650):
        plate = _mask_ellipse(CANVAS_SIZE, (cx - 90, 330, cx + 30, 530))
        _paste_shape(canvas, plate, (25, 25, 28), (0, 0, SIZE, SIZE))


def render_yogamat(canvas):
    body = _mask_rounded_rect(CANVAS_SIZE, (260, 320, 680, 420), 40)
    _paste_shape(canvas, body, (48, 120, 100), (0, 0, SIZE, SIZE))
    roll = _mask_ellipse(CANVAS_SIZE, (620, 300, 720, 440))
    _paste_shape(canvas, roll, (34, 90, 74), (0, 0, SIZE, SIZE))


def render_serum(canvas):
    bottle = _mask_rounded_rect(CANVAS_SIZE, (370, 360, 530, 620), 24)
    _paste_shape(canvas, bottle, (210, 225, 220), (0, 0, SIZE, SIZE))
    cap = _mask_rounded_rect(CANVAS_SIZE, (390, 280, 510, 370), 10)
    _paste_shape(canvas, cap, (30, 30, 32), (0, 0, SIZE, SIZE))


def render_hairbrush(canvas):
    head = _mask_rounded_rect(CANVAS_SIZE, (280, 280, 620, 420), 34)
    _paste_shape(canvas, head, (150, 110, 70), (0, 0, SIZE, SIZE))
    handle = _mask_rounded_rect(CANVAS_SIZE, (400, 400, 500, 620), 20)
    _paste_shape(canvas, handle, (120, 85, 52), (0, 0, SIZE, SIZE))


def render_blocks(canvas):
    colors = [(196, 92, 62), (72, 128, 104), (66, 96, 150), (208, 168, 66)]
    positions = [(260, 400), (380, 400), (500, 400), (340, 300)]
    for (x, y), c in zip(positions, colors):
        cube = _mask_rounded_rect(CANVAS_SIZE, (x, y, x + 110, y + 110), 12)
        _paste_shape(canvas, cube, c, (0, 0, SIZE, SIZE))


def render_boardgame(canvas):
    board = _mask_rounded_rect(CANVAS_SIZE, (270, 300, 630, 580), 16)
    _paste_shape(canvas, board, (232, 224, 205), (0, 0, SIZE, SIZE))
    d = ImageDraw.Draw(canvas)
    for x in range(270, 631, 60):
        d.line([(x, 300), (x, 580)], fill=(190, 178, 150, 255), width=4)
    for y in range(300, 581, 56):
        d.line([(270, y), (630, y)], fill=(190, 178, 150, 255), width=4)


def render_book(canvas, cover_color):
    body = _mask_rounded_rect(CANVAS_SIZE, (300, 260, 600, 640), 6)
    _paste_shape(canvas, body, cover_color, (0, 0, SIZE, SIZE))
    spine = _mask_rounded_rect(CANVAS_SIZE, (300, 260, 330, 640), 4)
    _paste_shape(canvas, spine, tuple(max(0, c - 40) for c in cover_color), (0, 0, SIZE, SIZE))


def render_shirt(canvas, color):
    body = _mask_polygon(CANVAS_SIZE, [
        (320, 360), (380, 280), (520, 280), (580, 360),
        (540, 420), (520, 390), (520, 640), (380, 640),
        (380, 390), (360, 420),
    ])
    _paste_shape(canvas, body, color, (0, 0, SIZE, SIZE))


def render_leggings(canvas, color):
    body = _mask_polygon(CANVAS_SIZE, [
        (370, 280), (530, 280), (530, 640), (470, 640),
        (450, 460), (430, 640), (370, 640),
    ])
    _paste_shape(canvas, body, color, (0, 0, SIZE, SIZE))


def render_shears(canvas):
    d = ImageDraw.Draw(canvas)
    d.line([(300, 300), (620, 620)], fill=(150, 150, 155, 255), width=22)
    d.line([(620, 300), (300, 620)], fill=(150, 150, 155, 255), width=22)
    ring1 = _mask_ring(CANVAS_SIZE, (270, 440, 360, 530), 14)
    ring2 = _mask_ring(CANVAS_SIZE, (560, 440, 650, 530), 14)
    _paste_shape(canvas, ring1, (40, 100, 60), (0, 0, SIZE, SIZE))
    _paste_shape(canvas, ring2, (40, 100, 60), (0, 0, SIZE, SIZE))


def render_hose(canvas):
    band = _mask_arc_band(CANVAS_SIZE, (280, 280, 640, 640), 0, 320, 34)
    _paste_shape(canvas, band, (60, 140, 130), (0, 0, SIZE, SIZE))
    nozzle = _mask_rounded_rect(CANVAS_SIZE, (600, 300, 680, 360), 10)
    _paste_shape(canvas, nozzle, (60, 60, 64), (0, 0, SIZE, SIZE))


CATALOG = [
    ("chef_knife.png", "Stainless Steel Chef Knife 8-inch", render_knife, {}),
    ("frying_pan.png", "Non-Stick Ceramic Frying Pan 28cm", render_pan, {}),
    ("wireless_earbuds.png", "Wireless Earbuds with Charging Case", render_earbuds, {}),
    ("bluetooth_speaker.png", "Portable Bluetooth Speaker", render_speaker, {}),
    ("dumbbell_set.png", "Adjustable Dumbbell Set 2x10kg", render_dumbbell, {}),
    ("yoga_mat.png", "Yoga Mat with Carry Strap", render_yogamat, {}),
    ("vitamin_c_serum.png", "Vitamin C Serum 30ml", render_serum, {}),
    ("hair_brush_set.png", "Bamboo Hair Brush Set (3-pack)", render_hairbrush, {}),
    ("building_blocks.png", "Wooden Building Blocks 100pc", render_blocks, {}),
    ("board_game.png", "Strategy Board Game - Trade Routes", render_boardgame, {}),
    ("novel_book.png", "The Quiet Algorithm - Novel", render_book, {"cover_color": (58, 46, 96)}),
    ("cookbook.png", "Everyday Sourdough - Cookbook", render_book, {"cover_color": (168, 98, 40)}),
    ("mens_shirt.png", "Men's Slim-Fit Oxford Shirt", render_shirt, {"color": (70, 110, 160)}),
    ("womens_leggings.png", "Women's High-Waist Leggings", render_leggings, {"color": (60, 30, 50)}),
    ("pruning_shears.png", "Pruning Shears - Titanium Coated", render_shears, {}),
    ("garden_hose.png", "Garden Hose 15m with Nozzle", render_hose, {}),
]


def main():
    out_dir = config.BASE_DIR / "assets" / "products"
    out_dir.mkdir(parents=True, exist_ok=True)

    for filename, name, render_fn, kwargs in CATALOG:
        canvas = _studio_background().convert("RGBA")
        render_fn(canvas, **kwargs)
        canvas.convert("RGB").save(out_dir / filename, "PNG")
        print(f"  wrote {filename}  ({name})")

    print(f"\n{len(CATALOG)} product images written to {out_dir}")
    print("These are offline, procedurally generated illustrations styled like "
          "studio product photography (neutral background, shading, drop shadow) "
          "- NOT real photographs. See README.md 'Product images' to swap in real ones.")
    return {name: filename for filename, name, *_ in CATALOG}


if __name__ == "__main__":
    main()

