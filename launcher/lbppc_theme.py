"""The launcher's look, after LittleBigPlanet 3's menus: night felt, dark see-through panels with lime borders,
flat hand-cut tabs for buttons, rounded type (Fredoka, SIL Open Font License, fonts/Fredoka-OFL.txt).

Everything is drawn here with Pillow, at twice the size and scaled down for smooth edges, and returned as images
the launcher puts on its canvas. Nothing of the game is used: the banner is the player's own dump's artwork.
"""
import math
import struct
from pathlib import Path
import random
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FONTS = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'fonts'
# The logo (the stitched globe): logo.png, and lbppc.ico for the window's and LBPPC.exe's icon.
ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets'


def logo_image(size):
    """The logo, size pixels square, or None when the file is missing."""
    try:
        return Image.open(ASSETS / 'logo.png').convert('RGBA').resize((size, size), Image.LANCZOS)
    except OSError:
        return None
S = 2  # drawing scale

NIGHT = (22, 26, 42)
PANEL_FILL = (14, 18, 30, 210)
FIELD = '#242a3e'
FIELD_EDGE = '#4e5a78'
LIME = (160, 210, 70)
BLUE = (33, 99, 196)    # LBP3's menu blue and green (its Pod menu on PS4)
GREEN = (135, 177, 42)
GREY = (96, 104, 124)
RED = (214, 72, 62)
ORANGE = (242, 140, 40)
CREAM = (255, 252, 245)
TEXT = '#f0f0e8'
MUTED = '#aab2c4'
GOOD = '#9ccc65'
ERROR = '#ff7b6b'
WARN = '#f2a33a'


def hex_colour(rgb):
    return '#%02x%02x%02x' % rgb[:3]


_FREDOKA = None


def fredoka_has(text):
    """Can Fredoka draw all of `text`? It has Western European letters, not Polish, Cyrillic or Arabic ones."""
    global _FREDOKA
    if _FREDOKA is None:
        _FREDOKA = set()
        data = (FONTS / 'Fredoka.ttf').read_bytes()
        count = struct.unpack('>H', data[4:6])[0]
        tables = {data[12 + i * 16:16 + i * 16]: struct.unpack('>I', data[20 + i * 16:24 + i * 16])[0]
                  for i in range(count)}
        cmap = tables[b'cmap']
        for i in range(struct.unpack('>H', data[cmap + 2:cmap + 4])[0]):
            sub = cmap + struct.unpack('>I', data[cmap + 8 + i * 8:cmap + 12 + i * 8])[0]
            if struct.unpack('>H', data[sub:sub + 2])[0] == 4:  # the BMP map
                pairs = struct.unpack('>H', data[sub + 6:sub + 8])[0]
                ends = struct.unpack(f'>{pairs // 2}H', data[sub + 14:sub + 14 + pairs])
                starts = struct.unpack(f'>{pairs // 2}H', data[sub + 16 + pairs:sub + 16 + 2 * pairs])
                for a, b in zip(starts, ends):
                    _FREDOKA.update(range(a, min(b, 0xfffe) + 1))
    return all(ord(c) in _FREDOKA or c.isspace() for c in text)


# Windows' own fonts for the letters Fredoka hasn't (Polish, Russian): the nearest weight of Segoe UI.
SEGOE = {'Regular': 'segoeui.ttf', 'Medium': 'seguisb.ttf', 'SemiBold': 'seguisb.ttf', 'Bold': 'segoeuib.ttf'}


def font(size, weight='SemiBold', text='', scale=S):
    """Fredoka at `size` points (times `scale`, the drawing scale unless given), one of its named weights; Segoe UI
    when `text` has letters Fredoka can't draw (a translation's)."""
    if text and not fredoka_has(text):
        try:
            return ImageFont.truetype(SEGOE.get(weight, 'segoeui.ttf'), size * scale)
        except OSError:
            pass  # no Segoe UI (Proton, Linux): Fredoka, with its missing letters
    f = ImageFont.truetype(str(FONTS / 'Fredoka.ttf'), size * scale)
    f.set_variation_by_name(weight)
    return f


def finish(img):
    """Back from the drawing scale. The drawing itself rides along (info['hires']): when the launcher is shown
    scaled (ui_scale.image), it is resized once from that instead of twice, so small text stays sharp."""
    out = img.resize((max(1, img.width // S), max(1, img.height // S)), Image.LANCZOS)
    # Tied to this very image: Pillow copies info into rotated, filtered or converted images, which then no longer
    # match the drawing (ui_scale.image checks the id).
    out.info['hires'] = (id(out), out.size, img)
    return out


def felt(w, h, base=NIGHT):
    """Felt: fine fibre noise over the base colour and short lighter fibres (seeded: the same every time)."""
    rng = random.Random(3)
    noise = Image.effect_noise((w, h), 40).filter(ImageFilter.GaussianBlur(0.8))
    img = Image.new('RGB', (w, h), base)
    light = Image.new('RGB', (w, h), tuple(min(255, c + 16) for c in base))
    img = Image.composite(light, img, noise.point(lambda v: max(0, v - 110) * 2))
    d = ImageDraw.Draw(img)
    for _ in range(w * h // 1800):
        x, y, a = rng.randrange(w), rng.randrange(h), rng.uniform(0, math.pi)
        length = rng.uniform(4, 10)
        d.line((x, y, x + length * math.cos(a), y + length * math.sin(a)),
               fill=tuple(min(255, v + rng.randint(8, 22)) for v in base), width=1)
    return img


def banner(w, h, art=None):
    """The player's dump's artwork (sce_sys/pic1.png), fading into the felt below a torn paper edge; plain felt
    without it."""
    try:
        picture = Image.open(art).convert('RGB') if art and Path(art).is_file() else None
    except OSError:
        picture = None
    if picture:
        picture = picture.resize((w, round(picture.height * w / picture.width)), Image.LANCZOS)
        top = max(0, (picture.height - h) // 3)
        img = picture.crop((0, top, w, top + h)).convert('RGBA')
    else:
        img = felt(w, h, (40, 46, 72)).convert('RGBA')
    fade = Image.new('L', (w, h))
    fd = ImageDraw.Draw(fade)
    for y in range(h):
        fd.line((0, y, w, y), fill=int(255 * max(0.0, (y / h - 0.45) / 0.55) ** 1.3))
    img = Image.composite(Image.new('RGBA', (w, h), NIGHT + (255,)), img, fade)
    edge = Image.new('L', (w, h), 255)
    rng = random.Random(7)
    pts = [(0, h)] + [(x, h - 3 - rng.random() * 4) for x in range(0, w + 6, 6)] + [(w, h)]
    ImageDraw.Draw(edge).polygon(pts, fill=0)
    img.putalpha(edge)
    return img


def shadowed(layer, blur=4, offset=(0, 3), alpha=150):
    """The layer over its own soft shadow, in one image (blur and offset in final pixels)."""
    pad = (blur * 3 + max(abs(offset[0]), abs(offset[1]))) * S
    out = Image.new('RGBA', (layer.width + 2 * pad, layer.height + 2 * pad), (0, 0, 0, 0))
    sh = Image.new('RGBA', layer.size, (5, 6, 12, 0))
    sh.putalpha(layer.split()[3].point(lambda v: v * alpha // 255))
    out.alpha_composite(sh, (pad + offset[0] * S, pad + offset[1] * S))
    out = out.filter(ImageFilter.GaussianBlur(blur * S))
    out.alpha_composite(layer, (pad, pad))
    return out, pad // S


def sticker_text(text, f, colour, outline, width):
    """`text` in `colour` with a round `outline` of `width` (drawing pixels) around it, as an image at the drawing
    scale; the text's origin is at (width + S, width + S) from its bounding box. The outline is the letters' shape
    spread outward in a circle, not FreeType's stroke: Fredoka is a variable font, its letters overlapping pieces,
    and the stroke leaves gaps where they overlap."""
    l, t, r, b = f.getbbox(text)
    m = width + S
    mask = Image.new('L', (r - l + 2 * m, b - t + 2 * m), 0)
    ImageDraw.Draw(mask).text((m - l, m - t), text, font=f, fill=255)
    spread = mask.copy()
    for radius in (width, width * 0.5):
        steps = max(12, int(radius * 2.5))
        for i in range(steps):
            a = 2 * math.pi * i / steps
            spread = ImageChops.lighter(spread, ImageChops.offset(mask, round(radius * math.cos(a)),
                                                                  round(radius * math.sin(a))))
    img = Image.new('RGBA', mask.size, outline + (0,))
    img.putalpha(spread)
    face = Image.new('RGBA', mask.size, colour + (0,))
    face.putalpha(mask)
    img.alpha_composite(face)
    return img


def rgb(colour):
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5)) if isinstance(colour, str) else tuple(colour[:3])


def redrawable(img, draw):
    """`img` (finished) with `draw(scale)`, which draws it again `scale` times its size: the launcher shown scaled
    (ui_scale.image) has text drawn at its real pixel size, sharp, instead of shrunk. Tied to this very image, as
    finish's drawing."""
    img.info['redraw'] = (id(img), img.size, draw)
    return img


def _line(text, size, colour, weight, scale):
    """One line of text drawn `scale` times the launcher's size, a scale-sized margin around it."""
    f = font(size, weight, text, scale)
    l, t, r, b = f.getbbox(text)
    pad = max(1, round(scale))
    img = Image.new('RGBA', (max(1, round(r - l) + 2 * pad), max(1, round(b - t) + 2 * pad)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - l, pad - t), text, font=f, fill=colour)
    return img


def text_image(text, size, colour=TEXT, weight='SemiBold', outline=None):
    """A line of Fredoka (with a sticker outline when `outline` is a colour)."""
    if outline:
        f = font(size, weight, text)
        return finish(sticker_text(text, f, rgb(colour), rgb(outline), (size // 6) * S))
    return redrawable(finish(_line(text, size, colour, weight, S)),
                      lambda scale: _line(text, size, colour, weight, scale))


def panel(w, h, title, radius=20):
    """LBP3's info panel: dark and see-through, a lime rounded border, the title in capitals over a dashed rule."""
    w2, h2, r = w * S, h * S, radius * S
    img = Image.new('RGBA', (w2, h2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w2 - 1, h2 - 1), r, fill=PANEL_FILL, outline=LIME + (255,), width=3 * S)
    f = font(14, 'Medium', title)
    d.text((20 * S, 11 * S), title.upper(), font=f, fill=(250, 248, 236))
    end = 20 * S + d.textlength(title.upper(), font=f) + 36 * S
    x = 20 * S
    while x < end:
        d.line((x, 33 * S, x + 7 * S, 33 * S), fill=LIME, width=2 * S)
        x += 12 * S
    return finish(img)


def handcut_mask(w, h, seed, corner=0.12, jitter=0.03, puff=0.05):
    """LBP3's menu shape (its Pod menu on PS4): a rounded rectangle puffed out, every edge curving outwards from its
    corners to its middle (top and bottom by `puff` x height, the sides by most of that), the corners rounded evenly
    (radius about `corner` x height) and moved by up to `jitter` x height, so no two are quite alike. Drawn at 4x,
    the corners rounded by blurring and cutting at half (the gentle curves keep their shape), and scaled down for
    clean edges. The shape fills w x h; the same seed gives the same shape. Returns (mask, the margin around it)."""
    rng = random.Random(seed)
    k = 4
    r = h * corner
    tb, lr = puff * h, puff * h * 0.7  # how far the middle of each edge stands out past its corners

    def nudge():
        return rng.uniform(0, jitter * h)

    # Clockwise from top left: the corners in by the puff (it brings each edge's middle back out to w x h), then
    # nudged.
    left, right, top, bottom = lr, w - lr, tb, h - tb
    corners = [(left + nudge(), top + nudge()), (right - nudge(), top + nudge()),
               (right - nudge(), bottom - nudge()), (left + nudge(), bottom - nudge())]
    pts = []
    for i in range(4):
        (x0, y0), (x1, y1) = corners[i], corners[(i + 1) % 4]
        length = math.hypot(x1 - x0, y1 - y0) or 1
        nx, ny = (y1 - y0) / length, (x0 - x1) / length  # outwards, for a clockwise outline with y down
        bulge = tb if i % 2 == 0 else lr
        steps = 24
        for j in range(steps):
            t = j / steps
            out = math.sin(math.pi * t) * bulge
            pts.append((x0 + (x1 - x0) * t + nx * out, y0 + (y1 - y0) * t + ny * out))
    pad = int(h * 0.4)
    big = Image.new('L', ((w + 2 * pad) * k, (h + 2 * pad) * k), 0)
    d = ImageDraw.Draw(big)
    pts = [((x + pad) * k, (y + pad) * k) for x, y in pts]
    d.polygon(pts, fill=255)
    big = big.filter(ImageFilter.GaussianBlur(r * k * 0.55)).point(lambda v: 255 if v > 128 else 0)
    return big.resize((w + 2 * pad, h + 2 * pad), Image.LANCZOS), pad


def tab(text, fill, size=15, pad=(12, 9), tilt=0.0, weight='Bold', fg=(255, 255, 255), scale=1.0):
    """LBP3's menu tab, the launcher's button: one flat colour in its shape (handcut_mask), bold white text, a tilt,
    a faint shadow; `scale` draws it larger (the hover pop). The same text gives the same shape at every scale.
    The text is centred on its capitals' height, not its full outline: a descender ("y" in Play) would push it up.
    Returns (image, the shadow margin around the shape in pixels)."""
    f = font(max(1, round(size * scale)), weight, text)
    px, py = round(pad[0] * scale * S), round(pad[1] * scale * S)
    l, _t, r, _b = f.getbbox(text)
    cap = f.getbbox('H')[3] - f.getbbox('H')[1]
    w, h = r - l + 2 * px, cap + 2 * py
    mask, mpad = handcut_mask(w, h, seed=text)
    img = Image.new('RGBA', mask.size, fill + (0,))
    img.putalpha(mask)
    ImageDraw.Draw(img).text((mpad + px - l, mpad + py + cap), text, font=f, fill=fg, anchor='ls')
    img = img.crop(img.getbbox())
    if tilt:
        img = img.rotate(tilt, resample=Image.BICUBIC, expand=True)
    out, margin = shadowed(img, blur=3, offset=(0, 2), alpha=90)
    return finish(out), margin


def lighter(rgb, amount=22):
    return tuple(min(255, c + amount) for c in rgb)


def checkbox(on):
    """The Play online tick box: a dark field, a lime edge, a green tick when on."""
    s = 22 * S
    img = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((S, S, s - S, s - S), 5 * S, fill=(36, 42, 62, 255), outline=LIME + (255,), width=2 * S)
    if on:
        d.line((5 * S, 11 * S, 10 * S, 16 * S, 18 * S, 5 * S), fill=GREEN, width=4 * S, joint='curve')
    return finish(img)


def tag(text, colour):
    """A small luggage tag (the game folder's status), its text in `colour`."""
    try:
        f = ImageFont.truetype('segoeuib.ttf', 12 * S) if sys.platform == 'win32' else font(12)
    except OSError:  # Proton and Wine have no Segoe UI: the bundled font instead
        f = font(12)
    l, t, r, b = f.getbbox(text)
    w, h = r - l + 44 * S, b - t + 14 * S
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(0, h // 2), (13 * S, 0), (w, 0), (w, h), (13 * S, h)], fill=(250, 244, 230, 255))
    d.ellipse((9 * S, h // 2 - 3 * S, 15 * S, h // 2 + 3 * S), fill=(150, 112, 72, 255))
    d.text((23 * S, h // 2), text, font=f, fill=colour, anchor='lm')
    img = img.rotate(-2, resample=Image.BICUBIC, expand=True)
    out, margin = shadowed(img, blur=3, offset=(0, 2))
    return finish(out), margin


def window_button(kind, fill):
    """The title bar's minimise ('min') or close button: a small hand-cut square, a white symbol."""
    w = h = 24 * S
    mask, pad = handcut_mask(w, h, seed=kind, corner=0.34)
    img = Image.new('RGBA', mask.size, fill + (0,))
    img.putalpha(mask)
    d = ImageDraw.Draw(img)
    cx, cy, r = pad + w // 2, pad + h // 2, 5 * S
    if kind == 'min':
        d.line((cx - r, cy + S, cx + r, cy + S), fill=(255, 255, 255), width=3 * S)
    else:
        d.line((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255), width=3 * S)
        d.line((cx - r, cy + r, cx + r, cy - r), fill=(255, 255, 255), width=3 * S)
    img = img.crop(img.getbbox()).rotate(3 if kind == 'close' else -3, resample=Image.BICUBIC, expand=True)
    out, margin = shadowed(img, blur=2, offset=(0, 2))
    return finish(out), margin


def title_bar(w, h, subtitle):
    """The window's title bar: night felt, a lime rule under it, a small LBPPC sticker and `subtitle`."""
    img = felt(w * S, h * S, (14, 17, 30)).convert('RGBA')
    d = ImageDraw.Draw(img)
    d.line((0, h * S - 2 * S, w * S, h * S - 2 * S), fill=LIME, width=2 * S)
    globe = logo_image(h - 10)
    if globe:
        globe = globe.resize((globe.width * S, globe.height * S), Image.LANCZOS)
        img.alpha_composite(globe, (9 * S, (h * S - globe.height) // 2))
    x = 9 * S + (globe.width + 5 * S if globe else 2 * S)
    f = font(17, 'Bold')
    logo = sticker_text('LBPPC', f, ORANGE, CREAM, 3 * S)
    img.alpha_composite(logo, (x, (h * S - logo.height) // 2))
    if subtitle:
        x += logo.width + 6 * S
        d.text((x, h // 2 * S + S), subtitle, font=font(12, 'Medium', subtitle), fill=(170, 178, 196), anchor='lm')
    return finish(img)


def card(w, h, accent):
    """A friends-list section: a slightly lighter rounded card with a stripe of the section's colour down its left."""
    img = Image.new('RGBA', (w * S, h * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w * S - 1, h * S - 1), 12 * S, fill=(30, 36, 56, 255))
    stripe = Image.new('L', img.size, 0)
    ImageDraw.Draw(stripe).rounded_rectangle((0, 0, w * S - 1, h * S - 1), 12 * S, fill=255)
    bar = Image.new('L', img.size, 0)
    ImageDraw.Draw(bar).rectangle((0, 0, 5 * S, h * S), fill=255)
    img.paste(Image.new('RGBA', img.size, accent + (255,)), (0, 0), ImageChops.multiply(stripe, bar))
    return finish(img)


def paragraph(text, size, width, colour=TEXT, weight='Regular', spacing=1.35):
    """`text` in Fredoka, wrapped to `width` pixels (lines kept apart by `spacing` x size)."""
    f = font(size, weight, text)
    lines, line = [], ''
    for word in text.split():
        trial = f'{line} {word}'.strip()
        if line and f.getlength(trial) > width * S:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)

    def draw_at(scale):  # the lines as wrapped above, at any scale
        f2 = font(size, weight, text, scale)
        step = round(size * spacing * scale)
        img = Image.new('RGBA', (max(1, round(width * scale)), max(1, step * len(lines) + round(size * scale / 2))),
                        (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        for i, text_line in enumerate(lines):
            draw.text((0, i * step), text_line, font=f2, fill=colour)
        return img
    return redrawable(finish(draw_at(S)), draw_at)


def badge(number, size=26):
    """A round lime badge with a number on it, for numbered steps."""
    d = size * S
    img = Image.new('RGBA', (d, d), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, d - 1, d - 1), fill=LIME + (255,))
    f = font(int(size * 0.58), 'Bold')
    draw.text((d / 2, d / 2 + S), str(number), font=f, fill=NIGHT, anchor='mm')
    return finish(img)


def notice_card(w, h, title, radius=20):
    """A panel (as on the launcher) on felt, cut to its rounded shape: corners transparent."""
    base = felt(w, h).convert('RGBA')
    mask = Image.new('L', (w * 4, h * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * 4 - 1, h * 4 - 1), radius * 4, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    out.paste(base, (0, 0), mask)
    out.alpha_composite(panel(w, h, title, radius))
    return out
