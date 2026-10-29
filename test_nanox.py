from PIL import Image

from nanox import END, LUT, START, WIDTH, build_job

BPR = WIDTH // 8  # bytes per row


def test_lut_is_involution():
    assert len(LUT) == 256 and all(LUT[LUT[i]] == i for i in range(256))
    assert LUT[0x00] == 0xC2 and LUT[0xFF] == 0xB8  # black / white


def test_job_shape():
    job, bw = build_job(Image.new("L", (100, 50), 0))  # all black
    h = bw.height
    head = START + b"\x1f\x70\x01\x0a\x1f\x11\x51" + b"\x1d\x76\x30\x00" + BPR.to_bytes(2, "little") + h.to_bytes(2, "little")
    tail = b"\x1b\x4a\xf0" + END  # default feed 240 dots
    assert bw.width == WIDTH and job.startswith(head) and job.endswith(tail)
    body = job[len(head):-len(tail)]
    assert len(body) == BPR * h and set(body) == {0xB8}  # black pixel -> 1 bit before LUT -> LUT[FF] = B8


def test_white_is_c2():
    job, _ = build_job(Image.new("L", (10, 10), 255))
    assert b"\xc2" * BPR in job


def test_threshold_has_no_grey():
    img = Image.linear_gradient("L").resize((64, 64))
    _, bw = build_job(img, dither="threshold")
    assert set(bw.getdata()) == {0, 255}  # only black and white


def _fake_bleak(writes):
    """Stand-in for bleak.BleakClient that records writes instead of touching Bluetooth."""
    import sys
    import types

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def write_gatt_char(self, char, data, response=True): writes.append(bytes(data))

    sys.modules["bleak"] = types.SimpleNamespace(BleakClient=Client)


def test_ble_progress_and_cancel():
    import threading

    from nanox import send_ble

    writes, seen = [], []
    _fake_bleak(writes)
    job = bytes(range(100))
    assert send_ble(job, "x", chunk=20, progress=lambda s, t: seen.append((s, t))) is True
    assert b"".join(writes) == job and seen[-1] == (100, 100) and len(seen) == 5

    writes.clear()
    stop = threading.Event()
    assert send_ble(job, "x", chunk=20, cancel=stop, progress=lambda s, t: stop.set() if s == 40 else None) is False
    assert b"".join(writes) == job[:40]  # nothing was sent after cancelling
