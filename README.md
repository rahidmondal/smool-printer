# Smool Printer

A small Windows desktop app that prints images, PDFs (first page), pasted images and text on a
58mm Bluetooth thermal printer (F2 family) over **Bluetooth LE**, from the PC.

Unofficial and not affiliated with the printer's maker.

## Run

```
pip install pillow bleak pymupdf customtkinter tkinterdnd2
python nanox_app.py        # or double-click print.bat
```

Switch the printer on and close the printer's phone app first: the printer only advertises
when nothing else is connected. The app finds it by name (`F2...`).

CLI: `python nanox.py pic.png --out job.bin --preview preview.png` (dry run), or
`python nanox.py --text "Hello" --ble auto` to print.

## How it works

- Job = `10 FF F1 03`, `1F 70 01 <heat>`, `1F 11 51`, ESC/POS `GS v 0` raster, feed, `10 FF F1 45`.
- The raster is rotated 180 degrees, 1 = black, and each byte goes through a fixed 256-byte table
  (`LUT` in `nanox.py`). Without it prints show vertical stripes.
- Head width is 576 dots (48mm printable at 12 dots/mm).
- BLE writes go to `bef8d6c9-9c21-4c9e-b632-bd58c1009f9f` in 20-byte chunks with response.
- Heat byte: 10 (Normal) and 13 (Dark) work; very low values printed nothing.

## Limits

Only this printer family is supported. Text is a single line; PDFs print page 1 only.
Windows only (font path and `print.bat`).

Tests: `python -c "import test_nanox as t; [getattr(t, n)() for n in dir(t) if n.startswith('test_')]"`
