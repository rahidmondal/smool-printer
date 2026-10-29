"""Desktop app for the F2 thermal printer: drop/open an image or PDF, paste, or type text; preview; print. Run via print.bat."""
import threading
from tkinter import filedialog

import customtkinter as ctk
from PIL import ImageGrab, ImageOps

import nanox

try:  # drag and drop is optional: the app works without it
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DnD = TkinterDnD.DnDWrapper
except ImportError:
    DND_FILES = TkinterDnD = DnD = None

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

HEAT = {"Normal": 10, "Dark": 13}  # Light (1) and 5 did not mark the paper on this printer
STYLE = {"Photo": "fs", "Text / drawing": "threshold"}
MUTED, OK, BAD = "#6b7280", "#15803d", "#b91c1c"
PAPER, INK = "#fbfaf5", "#1c1c1c"
EMPTY = "Drop an image or PDF here\n\nor use Open, Paste,\nor type some text"


class App(ctk.CTk, *([DnD] if DnD else [])):
    def __init__(self):
        super().__init__()
        self.title("Smool Printer")
        # Tk reports the screen in the same scaled units geometry() uses, so no extra division by the DPI scale.
        # Sized to leave room for the title bar and taskbar, and placed near the top so Print is never off-screen.
        w, h = min(1100, int(self.winfo_screenwidth() * 0.8)), min(780, int(self.winfo_screenheight() * 0.8))
        self.geometry(f"{w}x{h}+{(self.winfo_screenwidth() - w) // 2}+12")
        self.minsize(760, 520)
        self.src = self.job = self.bw = self.pending = self.photo = None
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.build_side()
        self.build_preview()
        if DnD:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", lambda e: self.open_path(self.tk.splitlist(e.data)[0]))
            except Exception:
                pass  # no drag and drop, buttons still work
        self.busy, self.timer = False, None  # busy = a scan or a print is using Bluetooth
        self.after(600, self.check_printer)

    def build_side(self):
        side = ctk.CTkFrame(self, width=340, corner_radius=16)
        side.grid(row=0, column=0, sticky="ns", padx=(16, 8), pady=16)
        side.grid_propagate(False)
        side.columnconfigure(0, weight=1)  # let header, controls and footer fill the panel width
        side.rowconfigure(1, weight=1)  # controls scroll; the footer below never moves
        head = ctk.CTkFrame(side, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 0))
        ctk.CTkLabel(head, text="Smool Printer", font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(head, text="Photos, PDFs and text over Bluetooth", text_color=MUTED).pack(anchor="w")
        bar = ctk.CTkFrame(head, fg_color="transparent")
        bar.pack(fill="x", pady=(10, 0))
        self.dot = ctk.CTkLabel(bar, text="●", text_color=MUTED, font=ctk.CTkFont(size=16))
        self.dot.pack(side="left")
        self.pstate = ctk.CTkLabel(bar, text="Looking for printer...", text_color=MUTED)
        self.pstate.pack(side="left", padx=(4, 0))
        ctk.CTkButton(bar, text="Recheck", width=66, height=26, command=self.check_printer, fg_color="transparent",
                      border_width=1, text_color=("gray10", "gray90")).pack(side="right")

        body = ctk.CTkScrollableFrame(side, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        self.section(body, "1  What to print")
        ctk.CTkButton(body, text="Open image or PDF...", command=self.open).pack(fill="x", padx=14)
        ctk.CTkButton(body, text="Paste from clipboard", command=self.paste, fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90")).pack(fill="x", padx=14, pady=(8, 0))
        self.entry = ctk.CTkEntry(body, placeholder_text="...or type a line of text")
        self.entry.pack(fill="x", padx=14, pady=(8, 0))
        self.entry.bind("<KeyRelease>", self.use_text)

        self.section(body, "2  Adjust")
        self.label(body, "Darkness")
        self.heat = ctk.CTkSegmentedButton(body, values=list(HEAT), command=self.refresh)
        self.heat.set("Normal")
        self.heat.pack(fill="x", padx=14)
        self.label(body, "Brightness")
        self.gamma = ctk.CTkSlider(body, from_=0.8, to=2.2, number_of_steps=14, command=self.refresh)
        self.gamma.set(1.3)
        self.gamma.pack(fill="x", padx=14)
        self.label(body, "Style")
        self.style = ctk.CTkSegmentedButton(body, values=list(STYLE), command=self.refresh)
        self.style.set("Photo")
        self.style.pack(fill="x", padx=14)

        foot = ctk.CTkFrame(side, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 18))
        self.status = ctk.CTkLabel(foot, text="Pick something to print.", text_color=MUTED, wraplength=280, justify="left")
        self.status.pack(anchor="w", pady=(0, 6))
        self.progress = ctk.CTkProgressBar(foot)  # shown only while a job is being sent
        self.progress.set(0)
        row = ctk.CTkFrame(foot, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        self.btn = ctk.CTkButton(row, text="Print", height=48, font=ctk.CTkFont(size=16, weight="bold"), command=self.print_)
        self.btn.pack(side="left", fill="x", expand=True)
        self.cancel_btn = ctk.CTkButton(row, text="Cancel", height=48, width=90, fg_color="transparent", border_width=2,
                                        border_color=BAD, text_color=BAD, hover_color=("#fee2e2", "#450a0a"),
                                        command=self.cancel_job)  # packed only while a job is active

    def build_preview(self):
        right = ctk.CTkFrame(self, corner_radius=16, fg_color=("#e5e7eb", "#1f2937"))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=16)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.view = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self.view.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.view.columnconfigure(0, weight=1)
        self.paper = ctk.CTkFrame(self.view, fg_color=PAPER, corner_radius=4, border_width=1, border_color="#d1d5db")
        self.paper.grid(row=0, column=0, pady=14)
        self.preview = ctk.CTkLabel(self.paper, text=EMPTY, text_color=MUTED, font=ctk.CTkFont(size=15), width=320, height=240)
        self.preview.pack(padx=8, pady=8)
        self.caption = ctk.CTkLabel(right, text="", text_color=MUTED)
        self.caption.grid(row=1, column=0, pady=(0, 10))
        right.bind("<Configure>", lambda e: self.refresh_preview())  # refit when the window is resized

    @staticmethod
    def section(parent, title):
        ctk.CTkLabel(parent, text=title, font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=14, pady=(18, 8))

    @staticmethod
    def label(parent, text):
        ctk.CTkLabel(parent, text=text, text_color=MUTED).pack(anchor="w", padx=14, pady=(8, 2))

    def set_status(self, text, color=MUTED):
        self.status.configure(text=text, text_color=color)

    def open(self):
        path = filedialog.askopenfilename(filetypes=[("Images and PDF", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.pdf")])
        if path:
            self.open_path(path)

    def open_path(self, path):
        self.load(lambda: nanox.load_image(path), "Photo")

    def paste(self):
        clip = ImageGrab.grabclipboard()
        if isinstance(clip, list):  # files copied in Explorer
            return self.open_path(clip[0])
        if clip is None:
            return self.set_status("The clipboard has no image. Copy a picture first.", BAD)
        self.load(lambda: clip, "Photo")

    def use_text(self, *_):
        if self.entry.get().strip():
            self.load(lambda: nanox.render_text(self.entry.get()), "Text / drawing")

    def load(self, get, style):
        try:
            self.src = get()
        except Exception as e:
            return self.set_status(f"Could not open that: {e}", BAD)
        self.style.set(style)  # text and drawings look best crisp, photos as dots
        self.refresh()

    def refresh(self, *_):
        if self.src is not None:  # debounce: sliders fire many times while dragging
            if self.pending:
                self.after_cancel(self.pending)
            self.pending = self.after(120, self._render)

    def _render(self):
        self.pending = None
        self.job, self.bw = nanox.build_job(self.src, gamma=round(self.gamma.get(), 2), heat=HEAT[self.heat.get()],
                                            dither=STYLE[self.style.get()])
        self.refresh_preview()
        self.set_status("Ready to print.", OK)

    def refresh_preview(self):
        if self.bw is None:
            return
        # Ink-coloured dots on paper-coloured background, scaled to the pane width; tall pages scroll.
        shown = ImageOps.colorize(self.bw.convert("L"), INK, PAPER)
        avail = max(200, self.view.winfo_width() / self._get_window_scaling() - 70)
        scale = min(avail / shown.width, 1.0)
        self.photo = ctk.CTkImage(light_image=shown, size=(round(shown.width * scale), round(shown.height * scale)))
        self.preview.configure(image=self.photo, text="", width=0, height=0)
        self.caption.configure(text=f"Prints {self.bw.width / 120:.1f} cm wide and {self.bw.height / 120:.1f} cm long"
                                    f"  -  preview at {round(scale * 100)}%")

    def set_printer(self, text, color):
        self.dot.configure(text_color=color)
        self.pstate.configure(text=text, text_color=color)

    def check_printer(self):
        """Scan for the printer in the background; repeats every 30 s. Skipped while a print is using Bluetooth."""
        if self.busy:
            return
        self.busy = True
        self.set_printer("Looking for printer...", MUTED)
        threading.Thread(target=self.scan, daemon=True).start()

    def scan(self):
        try:
            addr = nanox.find_printer(timeout=6)
        except Exception:
            addr = None
        self.after(0, lambda: self.scan_done(addr))

    def scan_done(self, addr):
        self.busy = False
        if addr:
            self.set_printer("Printer ready", OK)
        else:  # off, out of range, or still connected to the phone app (it only advertises when free)
            self.set_printer("Printer not found", BAD)
        if self.timer:
            self.after_cancel(self.timer)
        self.timer = self.after(30000, self.check_printer)

    def print_(self):
        if self.job is None:
            return self.set_status("Pick an image or type some text first.", BAD)
        if self.busy:
            return self.set_status("One moment, still checking the printer. Try again in a few seconds.")
        self.busy = True
        self.cancel, self.last_pct = threading.Event(), -1
        self.btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.cancel_btn.pack(side="right", padx=(8, 0))
        self.progress.set(0)
        self.progress.pack(fill="x", before=self.btn.master)
        self.set_status("Looking for the printer...")
        threading.Thread(target=self.send, args=(self.job, self.cancel), daemon=True).start()

    def cancel_job(self):
        self.cancel.set()
        self.cancel_btn.configure(state="disabled")
        self.set_status("Cancelling...")

    def on_progress(self, sent, total):
        pct = sent * 100 // total
        if pct != self.last_pct:  # called from the worker thread, so hand the UI update to the main thread
            self.last_pct = pct
            self.after(0, lambda: (self.progress.set(pct / 100), self.set_status(f"Sending to printer... {pct}%"),
                                   self.set_printer(f"Printing... {pct}%", MUTED)))

    def send(self, job, cancel):
        found, done = False, False
        try:
            addr = nanox.find_printer()
            if not addr:
                raise RuntimeError("Printer not found. Turn it on and close the printer's phone app.")
            found = True
            if cancel.is_set():
                done = None  # cancelled while searching, nothing was sent
            else:
                done = nanox.send_ble(job, addr, progress=self.on_progress, cancel=cancel)
            if done:
                msg, color = "Sent. Tear the paper off when it stops.", OK
            elif done is None:
                msg, color = "Cancelled. Nothing was sent.", MUTED
            else:
                msg, color = "Cancelled. Part of it may already be printed. If the next print looks wrong, switch the printer off and on.", MUTED
        except Exception as e:
            msg, color = f"Failed: {e}", BAD
        self.after(0, lambda: self.print_done(msg, color, found))

    def print_done(self, msg, color, found):
        self.busy = False
        self.set_status(msg, color)
        self.btn.configure(state="normal")
        self.cancel_btn.pack_forget()
        self.progress.pack_forget()
        self.set_printer("Printer ready" if found else "Printer not found", OK if found else BAD)
        if self.timer:  # restart the periodic printer check that was skipped while the job ran
            self.after_cancel(self.timer)
        self.timer = self.after(30000, self.check_printer)


if __name__ == "__main__":
    App().mainloop()
