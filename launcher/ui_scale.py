"""The launcher at the screen's size.

The launcher is drawn at fixed sizes for a 1440p screen (1180 x 956). Here every Tk canvas works in those sizes while
showing them scaled by one factor: positions and sizes going in are multiplied by it, positions read back (coords,
bbox, the canvas's size, pointer events) are divided by it, images placed through the launcher's Scene are resized, and
fonts follow Tk's scaling. So the launcher's own code keeps its numbers.

The factor follows the screen's height (1440 is 1), at least 0.75 so text stays readable on small screens (1080p, the
Steam Deck's 1280 x 800), and never more than fits the screen's work area. The launcher isn't DPI-aware, so on a 4K
screen with Windows' usual 150% Windows already enlarges it and the screen reads as 1440 high here. LBPPC_UI_SCALE
overrides the factor (tests).
"""
import os
import tkinter as tk

DESIGN_HEIGHT = 1440
U = 1.0


def work_area():
    """The screen's usable width and height (taskbar excluded), in the launcher's (unscaled) pixels."""
    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
                return rect.right - rect.left, rect.bottom - rect.top
        except Exception:  # noqa: BLE001 - Wine or an unusual setup: fall back to Tk
            pass
    root = tk.Tk()
    root.withdraw()
    size = root.winfo_screenwidth(), root.winfo_screenheight() - 48
    root.destroy()
    return size


def choose(width, height):
    """The factor for a launcher `width` x `height` (its tallest state) on this screen."""
    forced = os.environ.get('LBPPC_UI_SCALE')
    if forced:
        try:
            return max(0.3, min(3.0, float(forced)))
        except ValueError:
            pass
    area_w, area_h = work_area()
    fit = min(area_w * 0.96 / width, area_h * 0.96 / height)
    return round(min(fit, max(area_h / DESIGN_HEIGHT, 0.75)), 3)


def s(value):
    """A launcher size in screen pixels."""
    return round(value * U)


def _scaled(values):
    return [v * U if isinstance(v, (int, float)) and not isinstance(v, bool) else v for v in values]


def _flat(args):
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        return list(args[0])
    return list(args)


def _scale_options(kw):
    for key in ('width', 'height'):
        if isinstance(kw.get(key), (int, float)) and not isinstance(kw.get(key), bool):
            kw[key] = kw[key] * U
    region = kw.get('scrollregion')
    if isinstance(region, (list, tuple)):
        kw['scrollregion'] = tuple(_scaled(region))
    return kw


def install(factor):
    """Scales every tk.Canvas by `factor` from now on."""
    global U
    U = factor
    if abs(U - 1.0) < 0.01:
        U = 1.0
        return
    C = tk.Canvas

    original_init = C.__init__

    def __init__(self, master=None, cnf=None, **kw):
        original_init(self, master, _scale_options(dict(cnf or {})), **_scale_options(kw))
    C.__init__ = __init__

    original_create = C._create

    def _create(self, item_type, args, kw):
        args = list(tk._flatten(args))
        cnf = _scale_options(dict(args.pop())) if args and isinstance(args[-1], dict) else None
        return original_create(self, item_type, tuple(_scaled(args)) + ((cnf,) if cnf else ()),
                               _scale_options(dict(kw)))
    C._create = _create

    original_coords = C.coords

    def coords(self, *args):
        if len(args) <= 1:
            return [v / U for v in original_coords(self, *args)]
        return original_coords(self, args[0], *_scaled(_flat(args[1:])))
    C.coords = coords

    original_move = C.move

    def move(self, tag, dx, dy):
        return original_move(self, tag, dx * U, dy * U)
    C.move = move

    original_bbox = C.bbox

    def bbox(self, *args):
        box = original_bbox(self, *args)
        return tuple(round(v / U) for v in box) if box else box
    C.bbox = bbox

    original_itemconfigure = C.itemconfigure

    def itemconfigure(self, tag, cnf=None, **kw):
        if cnf is None and not kw:
            return original_itemconfigure(self, tag)
        return original_itemconfigure(self, tag, _scale_options(dict(cnf)) if isinstance(cnf, dict) else cnf,
                                      **_scale_options(kw))
    C.itemconfigure = C.itemconfig = itemconfigure

    original_configure = C.configure

    def configure(self, cnf=None, **kw):
        if cnf is None and not kw:
            return original_configure(self)
        return original_configure(self, _scale_options(dict(cnf)) if isinstance(cnf, dict) else cnf,
                                  **_scale_options(kw))
    C.configure = C.config = configure

    original_cget = C.cget

    def cget(self, key):
        value = original_cget(self, key)
        if key in ('width', 'height'):
            try:
                return str(round(float(value) / U))
            except (TypeError, ValueError):
                return value
        return value
    C.cget = cget
    C.__getitem__ = cget

    original_winfo_width, original_winfo_height = C.winfo_width, C.winfo_height
    C.winfo_width = lambda self: round(original_winfo_width(self) / U)
    C.winfo_height = lambda self: round(original_winfo_height(self) / U)

    def pointer(func):
        if func is None or isinstance(func, str):
            return func

        def handler(event):
            if isinstance(getattr(event, 'x', None), int):
                event.x = round(event.x / U)
            if isinstance(getattr(event, 'y', None), int):
                event.y = round(event.y / U)
            return func(event)
        return handler

    original_bind, original_tag_bind = C.bind, C.tag_bind
    C.bind = lambda self, sequence=None, func=None, add=None: original_bind(self, sequence, pointer(func), add)
    C.tag_bind = lambda self, tag, sequence=None, func=None, add=None: original_tag_bind(
        self, tag, sequence, pointer(func), add)


def fonts(root):
    """Tk's fonts at the factor: points through Tk's scaling, the named fonts given in pixels directly."""
    if U == 1.0:
        return
    root.tk.call('tk', 'scaling', float(root.tk.call('tk', 'scaling')) * U)
    from tkinter import font as tkfont
    for name in tkfont.names(root):
        f = tkfont.nametofont(name, root)
        size = int(f.cget('size'))
        if size < 0:
            f.configure(size=min(-1, round(size * U)))


def image(pil):
    """A PIL image placed on a canvas, at the factor. An image the theme drew at its drawing scale (info['hires'],
    lbppc_theme.finish) is resized once from that drawing; shrunk images get their edges' contrast back (small text
    on a 1080p screen, at 0.75, otherwise reads soft)."""
    if U == 1.0:
        return pil
    from PIL import Image, ImageFilter
    size = (max(1, round(pil.width * U)), max(1, round(pil.height * U)))
    owner, owner_size, redraw = pil.info.get('redraw') or (None, None, None)
    if owner == id(pil) and owner_size == pil.size and redraw is not None:
        # Text drawn again at twice its real pixel size and halved once (drawn at 1x, FreeType's whole-pixel
        # advances space letters unevenly; shrunk twice from the design size, it reads soft), on a canvas of
        # exactly the scaled size.
        big = redraw(U * 2)
        drawn = big.resize((max(1, round(big.width / 2)), max(1, round(big.height / 2))), Image.LANCZOS)
        if drawn.size == size:
            return drawn
        out = Image.new('RGBA', size, (0, 0, 0, 0))
        out.alpha_composite(drawn.crop((0, 0, min(drawn.width, size[0]), min(drawn.height, size[1]))))
        return out
    owner, owner_size, hires = pil.info.get('hires') or (None, None, None)
    if owner == id(pil) and owner_size == pil.size and hires is not None and hires.width >= size[0]:
        return hires.resize(size, Image.LANCZOS, reducing_gap=3.0)
    out = pil.resize(size, Image.LANCZOS)
    if U < 1.0:
        sharp = out.convert('RGBA')
        alpha = sharp.getchannel('A')
        sharp = sharp.filter(ImageFilter.UnsharpMask(radius=0.8, percent=70, threshold=0))
        sharp.putalpha(alpha)
        return sharp
    return out
