"""Print an image or text on a 58mm Bluetooth thermal printer (F2 family) from the PC, over Bluetooth LE.

Protocol: job = 10 FF F1 03, 1F 70 01 <heat>, 1F 11 51,
ESC/POS GS v 0 raster whose payload bytes go through a fixed 256-byte table (LUT), feed, 10 FF F1 45.
576 dots (48mm) across 58mm paper. The printer is not TSPL; it prints TSPL as plain text.

  python nanox.py pic.png --out job.bin --preview preview.png   # dry run, no printer needed
  python nanox.py --text "Hello" --ble AA:BB:CC:DD:EE:FF        # Bluetooth LE (needs: pip install bleak)
  python nanox.py pic.png --usb "Printer"                        # via a Windows print queue (USB)
"""
import argparse

from PIL import Image, ImageFilter, ImageOps

WIDTH = 576  # dots across the print head: 48mm printable at 12 dots/mm (304 dpi)
START, END = b"\x10\xff\xf1\x03", b"\x10\xff\xf1\x45"
# Byte substitution the printer expects for every raster byte; an involution.
LUT = bytes.fromhex("c24add8097f70f885b5737ec4b866d063afa1829bf281e7312db60b2516416da7dfdad3d62fb6f70151390f6a3cba29eee4c6159c869a50a8f9610c4d623f28cbdb650e3e57e76ca79b5010c31d198d2421caae4675ce109c5337c0855c9e8ac1a3224681dd9d8546335a874ab0ecf2627ccbb176b87468bc648b4ea5a2045e703b3a9c3ce8d0d7507fc9d773f8591382a8ee0c0feed39044eebf3a7a68a2fb0f1ae2e2cd4369c9b6a82526c5f22a1e99fef1b817a4941b9ffb7c172f540d71493ba00833b5878d0345d472d71f4846ec74d4fe6a4f93cbe66651f19f802dfde9256f0435344d37f5eaf7b990b9530b1e2a03e9acdbc2b05dcd51125892194b8")


def build_job(img, width=WIDTH, feed_dots=240, gamma=1.0, heat=0x0A, invert=False, dither="fs"):
    """Return (job bytes, 1-bit preview image)."""
    img = ImageOps.exif_transpose(img).convert("L")
    img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)  # use the full black-to-white range
    if gamma != 1:  # >1 lightens midtones; thermal paper prints dark
        img = img.point(lambda p: round(255 * (p / 255) ** (1 / gamma)))
    img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=70, threshold=2))  # dither softens edges
    # "fs" = Floyd-Steinberg dots for photos, "threshold" = crisp black/white for text and line art.
    # Pillow mode "1" packs MSB first, 1 = white.
    bw = img.convert("1", dither=Image.FLOYDSTEINBERG if dither == "fs" else Image.NONE)
    # The printer wants the page rotated 180 degrees and 1 = black before the LUT (confirmed on hardware).
    data = bw.transpose(Image.ROTATE_180).tobytes()
    if not invert:
        data = bytes(b ^ 0xFF for b in data)
    data = data.translate(LUT)
    head = b"\x1d\x76\x30\x00" + (width // 8).to_bytes(2, "little") + bw.height.to_bytes(2, "little")
    feed = b"".join(b"\x1b\x4a" + bytes([min(255, feed_dots - k)]) for k in range(0, feed_dots, 255))
    return START + b"\x1f\x70\x01" + bytes([heat]) + b"\x1f\x11\x51" + head + data + feed + END, bw


def load_image(path):
    """Open an image, or page 1 of a PDF (ponytail: first page only; loop over pages if you need more)."""
    if str(path).lower().endswith(".pdf"):
        import pymupdf

        pix = pymupdf.open(path)[0].get_pixmap(dpi=200)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return Image.open(path)


def find_printer(prefix="F2", timeout=10):
    """BLE address of the first nearby device whose name starts with prefix, or None."""
    import asyncio

    from bleak import BleakScanner

    dev = asyncio.run(BleakScanner.find_device_by_filter(
        lambda d, a: (d.name or a.local_name or "").startswith(prefix), timeout=timeout))
    return dev.address if dev else None


def render_text(text, width=WIDTH, pad=24):
    """Black text centred on white, as large as fits the width (max 96pt), with padding top and bottom."""
    from PIL import ImageDraw, ImageFont

    size = 96
    while True:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", size)
        l, t, r, b = font.getbbox(text)
        if r - l <= width - 2 * 8 or size <= 12:
            break
        size -= 2
    img = Image.new("L", (width, b - t + 2 * pad), 255)
    ImageDraw.Draw(img).text(((width - (r - l)) // 2 - l, pad - t), text, font=font, fill=0)
    return img


def send_ble(job, address, char="bef8d6c9-9c21-4c9e-b632-bd58c1009f9f", chunk=20, progress=None, cancel=None):
    """Send over Bluetooth LE. Each chunk waits for the printer's ack (write with response) = flow control.

    progress(sent, total) is called after every chunk; setting the cancel event (threading.Event) stops sending
    and disconnects. Returns True if the whole job was sent, False if cancelled.
    """
    import asyncio

    from bleak import BleakClient

    async def run():
        async with BleakClient(address, timeout=20) as c:
            for i in range(0, len(job), chunk):
                if cancel is not None and cancel.is_set():
                    return False  # leaving the block disconnects, which makes the printer drop the half-sent job
                await c.write_gatt_char(char, job[i:i + chunk], response=True)
                if progress:
                    progress(min(i + chunk, len(job)), len(job))
            await asyncio.sleep(1)  # let the last rows finish before disconnecting
            return True

    return asyncio.run(run())


def send_usb(job, printer):
    """Write raw bytes to a Windows print queue (spooler RAW datatype, bypasses any driver rendering)."""
    import ctypes
    from ctypes import wintypes as w

    sp = ctypes.WinDLL("winspool.drv", use_last_error=True)

    class DocInfo(ctypes.Structure):
        _fields_ = [("name", w.LPWSTR), ("out", w.LPWSTR), ("type", w.LPWSTR)]

    h = w.HANDLE()
    if not sp.OpenPrinterW(printer, ctypes.byref(h), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not sp.StartDocPrinterW(h, 1, ctypes.byref(DocInfo("nanox", None, "RAW"))):
            raise ctypes.WinError(ctypes.get_last_error())
        sp.StartPagePrinter(h)
        n = w.DWORD()
        ok = sp.WritePrinter(h, job, len(job), ctypes.byref(n))
        sp.EndPagePrinter(h)
        sp.EndDocPrinter(h)
        if not ok or n.value != len(job):
            raise OSError(f"wrote {n.value}/{len(job)} bytes")
    finally:
        sp.ClosePrinter(h)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?", help="image file (or use --text)")
    ap.add_argument("--text", help="print this text instead of an image")
    ap.add_argument("--out", help="write raw job bytes here instead of printing")
    ap.add_argument("--preview", help="save the dithered 1-bit image as PNG")
    ap.add_argument("--ble", metavar="ADDR", help="Bluetooth LE address, e.g. AA:BB:CC:DD:EE:FF, or \"auto\" to find it by name")
    ap.add_argument("--char", default="bef8d6c9-9c21-4c9e-b632-bd58c1009f9f", help="BLE write characteristic")
    ap.add_argument("--chunk", type=int, default=20, help="bytes per BLE write; raise if prints are clean")
    ap.add_argument("--usb", metavar="QUEUE", help="Windows print queue name of the USB printer")
    ap.add_argument("--heat", type=int, default=0x0A, help="density byte: 10 normal (default), 13 dark. Low values (1, 5) did not mark the paper")
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--dither", choices=["fs", "threshold"], default="fs", help="fs for photos, threshold for text/line art")
    ap.add_argument("--gamma", type=float, default=1.0, help="brightness of midtones: 1.4-2.0 for dark photos")
    ap.add_argument("--feed", type=int, default=240, help="blank dots fed after the print so it clears the head")
    ap.add_argument("--width", type=int, default=WIDTH, help="dots per row, multiple of 8")
    a = ap.parse_args()
    if not (a.text or a.image):
        ap.error('give an image/PDF path or --text "..."  (to open the window instead, run: python nanox_app.py)')
    src = render_text(a.text, a.width) if a.text else load_image(a.image)
    job, bw = build_job(src, a.width, a.feed, a.gamma, a.heat, a.invert, a.dither)
    if a.preview:
        bw.save(a.preview)
    if a.out:
        open(a.out, "wb").write(job)
    if a.ble:
        send_ble(job, find_printer() if a.ble == "auto" else a.ble, a.char, a.chunk)
    if a.usb:
        send_usb(job, a.usb)
    print(f"{len(job)} bytes, {bw.width}x{bw.height} dots")
    if not (a.out or a.ble or a.usb):
        print("Nothing was sent. Add --ble auto to print.")
