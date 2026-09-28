import tkinter as tk
from tkinter import ttk, messagebox
import keyboard
import pystray
from PIL import Image
import threading
import time
import random
import os
import sys
import ctypes

# ----------------- Resource & System Helpers -----------------

def resource_path(filename):
    """Get absolute path to resource, works for dev and PyInstaller."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


# Windows native clipboard helpers (safe across 64-bit Python 3.14 / Tcl 9)
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

def set_windows_clipboard(text):
    """Safely copy text into Windows Unicode clipboard."""
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16le") + b"\x00\x00"
        GMEM_MOVEABLE = 0x0002
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if h_mem:
            p_mem = kernel32.GlobalLock(h_mem)
            if p_mem:
                ctypes.memmove(p_mem, data, len(data))
                kernel32.GlobalUnlock(h_mem)
                CF_UNICODETEXT = 13
                user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        return True
    finally:
        user32.CloseClipboard()


# ----------------- Application State -----------------

is_typing_lock = threading.Lock()
stop_typing_event = threading.Event()
active_hotkey_handler = None
active_esc_handler = None
tray_icon = None


def wait_for_modifiers_released(timeout=0.6):
    """
    Releases any logical modifier keys and waits briefly until physical
    modifier keys (especially Win, Alt, Ctrl, Shift) are released so they
    never interfere with typing or trigger Windows OS shortcuts.
    """
    for key in ("windows", "left windows", "right windows", "alt", "ctrl", "shift"):
        try:
            keyboard.release(key)
        except Exception:
            pass

    start = time.time()
    while time.time() - start < timeout:
        try:
            if not (keyboard.is_pressed("win") or keyboard.is_pressed("alt") or keyboard.is_pressed("ctrl")):
                break
        except Exception:
            break
        time.sleep(0.02)


# ----------------- Typing Worker -----------------

def trigger_typing():
    """Trigger typing in a non-blocking background daemon thread."""
    threading.Thread(target=_typing_worker, daemon=True).start()


def cancel_typing():
    """Request typing cancellation."""
    if stop_typing_event.is_set():
        return
    stop_typing_event.set()
    set_status("Cancelling...", "#ff6b6b")


def _typing_worker():
    # Safely retrieve parameters from UI variables
    text = ""
    def _read_ui():
        nonlocal text
        text = entry.get("1.0", "end-1c")

    root.after(0, _read_ui)
    time.sleep(0.05)  # brief wait for UI read

    if not text:
        set_status("No text entered to write!", "#ff6b6b")
        return

    if not is_typing_lock.acquire(blocking=False):
        set_status("Already typing! Press Esc to cancel.", "#feca57")
        return

    stop_typing_event.clear()

    try:
        start_delay = float(start_delay_var.get())
        char_delay = float(delay_var.get())
        mode = mode_var.get()
        use_jitter = jitter_var.get()
    except (ValueError, tk.TclError):
        start_delay = 0.3
        char_delay = 0.01
        mode = "type"
        use_jitter = False

    try:
        # 1. Countdown / Start Delay (allows releasing hotkey and focusing target window)
        if start_delay > 0:
            set_status(f"Starting in {start_delay:.1f}s... (Release keys)", "#feca57")
            # Wait in small increments so Esc can abort early
            t_waited = 0.0
            while t_waited < start_delay:
                if stop_typing_event.is_set():
                    set_status("Aborted before start.", "#ff6b6b")
                    return
                step = min(0.05, start_delay - t_waited)
                time.sleep(step)
                t_waited += step

        # 2. Release physical modifiers so Win+Y never triggers Windows shortcuts
        wait_for_modifiers_released(timeout=0.4)

        if stop_typing_event.is_set():
            set_status("Aborted before start.", "#ff6b6b")
            return

        # 3. Instant Paste Mode
        if mode == "paste":
            set_status("Pasting via Clipboard...", "#48dbfb")
            set_windows_clipboard(text)
            time.sleep(0.05)
            keyboard.send("ctrl+v")
            time.sleep(0.05)
            set_status("Finished pasting!", "#1dd1a1")
            return

        # 4. Character-by-Character Type Mode
        set_status("Typing... (Press Esc to stop)", "#1dd1a1")
        total = len(text)

        for i, char in enumerate(text):
            if stop_typing_event.is_set():
                set_status(f"Stopped by Esc at char {i}/{total}.", "#ff6b6b")
                return

            # restore_state_after=False ensures Win / modifiers are NEVER re-pressed
            keyboard.write(char, restore_state_after=False)

            if char_delay > 0:
                sleep_dur = char_delay
                if use_jitter:
                    # Realistic human typing variance: ±20% jitter
                    sleep_dur = max(0.001, char_delay * random.uniform(0.8, 1.2))
                    # Natural slight pause on punctuation or spaces
                    if char in " ,.!?\n":
                        sleep_dur += char_delay * 0.5
                time.sleep(sleep_dur)

        set_status("Finished typing!", "#1dd1a1")

    except Exception as ex:
        set_status(f"Error: {ex}", "#ff6b6b")
    finally:
        is_typing_lock.release()


# ----------------- UI Status & Hotkey Helpers -----------------

def set_status(msg, color="#ffffff"):
    """Update status bar label safely from any thread."""
    def _update():
        status_label.config(text=f"Status: {msg}", fg=color)
    root.after(0, _update)


def register_hotkeys():
    """Register selected trigger hotkey and Esc cancel key."""
    global active_hotkey_handler, active_esc_handler

    # Remove existing hotkeys safely
    if active_hotkey_handler:
        try:
            keyboard.remove_hotkey(active_hotkey_handler)
        except Exception:
            pass
        active_hotkey_handler = None

    if active_esc_handler:
        try:
            keyboard.remove_hotkey(active_esc_handler)
        except Exception:
            pass
        active_esc_handler = None

    hotkey_str = hotkey_var.get().strip().lower()

    # Map friendly names to actual shortcuts
    if "f8" in hotkey_str:
        hotkey_str = "f8"
    elif "win+y" in hotkey_str:
        hotkey_str = "win+y"
    elif "ctrl+shift+v" in hotkey_str:
        hotkey_str = "ctrl+shift+v"
    elif "f6" in hotkey_str:
        hotkey_str = "f6"

    try:
        active_hotkey_handler = keyboard.add_hotkey(hotkey_str, trigger_typing)
        active_esc_handler = keyboard.add_hotkey("esc", cancel_typing)
        set_status(f"Ready ({hotkey_str.upper()} to write | Esc to stop)", "#1dd1a1")
    except Exception as e:
        set_status(f"Failed to bind hotkey: {e}", "#ff6b6b")


def update_stats(event=None):
    """Update word and character counter."""
    content = entry.get("1.0", "end-1c")
    chars = len(content)
    words = len(content.split())
    stats_label.config(text=f"{words} words | {chars} characters")


def on_delay_slider_change(val):
    """Sync slider with entry box and calculate estimated WPM."""
    try:
        d = float(val)
        delay_entry_var.set(f"{d:.4f}")
        # Approx: 5 characters per word
        if d > 0:
            wpm = int(60 / (d * 5))
            wpm_label.config(text=f"~{wpm} WPM")
        else:
            wpm_label.config(text="Instant")
    except Exception:
        pass


def on_delay_entry_change(*args):
    """Sync entry box with slider."""
    try:
        d = float(delay_entry_var.get())
        if 0.0 <= d <= 0.1:
            delay_slider.set(d)
    except Exception:
        pass


def clear_text():
    """Clear the text area."""
    entry.delete("1.0", "end")
    update_stats()


# ----------------- System Tray Helpers -----------------

def hide_window():
    """Minimize GUI to background system tray."""
    root.withdraw()


def show_window(icon=None, item=None):
    """Restore GUI from system tray."""
    root.after(0, _restore_window)


def _restore_window():
    root.deiconify()
    root.lift()
    root.focus_force()


def quit_app(icon=None, item=None):
    """Cleanly exit application."""
    global tray_icon
    if tray_icon:
        try:
            tray_icon.stop()
        except Exception:
            pass
    root.after(0, root.destroy)


# ----------------- GUI Setup -----------------

root = tk.Tk()
root.geometry("520x640")
root.minsize(450, 520)
root.title("Auto Writer v2")
root.configure(bg="#121212")

# Styling constants
BG_DARK = "#121212"
BG_PANEL = "#1e1e1e"
BG_ENTRY = "#252526"
FG_LIGHT = "#e0e0e0"
FG_MUTED = "#9e9e9e"
ACCENT = "#007acc"
ACCENT_GREEN = "#1dd1a1"
ACCENT_RED = "#ff6b6b"

# Title bar frame
header_frame = tk.Frame(root, bg=BG_DARK)
header_frame.pack(fill="x", padx=14, pady=(10, 4))

title_label = tk.Label(
    header_frame, text="Auto Writer v2", font=("Segoe UI", 14, "bold"), fg=FG_LIGHT, bg=BG_DARK
)
title_label.pack(side="left")

stats_label = tk.Label(
    header_frame, text="0 words | 0 characters", font=("Segoe UI", 9), fg=FG_MUTED, bg=BG_DARK
)
stats_label.pack(side="right")

# Text Editor Frame
text_frame = tk.Frame(root, bg=BG_PANEL, bd=1, relief="solid")
text_frame.pack(fill="both", expand=True, padx=14, pady=6)

scrollbar = tk.Scrollbar(text_frame)
scrollbar.pack(side="right", fill="y")

entry = tk.Text(
    text_frame,
    wrap="word",
    bg=BG_ENTRY,
    fg="#ffffff",
    insertbackground="#ffffff",
    selectbackground=ACCENT,
    font=("Consolas", 10),
    yscrollcommand=scrollbar.set,
    bd=0,
    padx=8,
    pady=8,
)
entry.pack(fill="both", expand=True)
scrollbar.config(command=entry.yview)
entry.bind("<KeyRelease>", update_stats)

# Controls Panel
panel = tk.Frame(root, bg=BG_PANEL, padx=12, pady=10)
panel.pack(fill="x", padx=14, pady=(0, 6))

# Row 1: Delay Control
delay_frame = tk.Frame(panel, bg=BG_PANEL)
delay_frame.pack(fill="x", pady=3)

tk.Label(delay_frame, text="Typing Delay:", font=("Segoe UI", 9, "bold"), fg=FG_LIGHT, bg=BG_PANEL).pack(side="left")

delay_var = tk.DoubleVar(value=0.01)
delay_entry_var = tk.StringVar(value="0.0100")
delay_entry_var.trace_add("write", on_delay_entry_change)

delay_slider = tk.Scale(
    delay_frame,
    from_=0.0,
    to=0.08,
    resolution=0.001,
    orient="horizontal",
    variable=delay_var,
    command=on_delay_slider_change,
    bg=BG_PANEL,
    fg=FG_LIGHT,
    highlightthickness=0,
    troughcolor=BG_ENTRY,
    activebackground=ACCENT,
    length=150,
)
delay_slider.pack(side="left", padx=8)

delay_entry = tk.Entry(
    delay_frame,
    textvariable=delay_entry_var,
    width=7,
    font=("Segoe UI", 9),
    bg=BG_ENTRY,
    fg=FG_LIGHT,
    insertbackground=FG_LIGHT,
    justify="center",
)
delay_entry.pack(side="left", padx=4)
tk.Label(delay_frame, text="sec", font=("Segoe UI", 8), fg=FG_MUTED, bg=BG_PANEL).pack(side="left")

wpm_label = tk.Label(delay_frame, text="~1200 WPM", font=("Segoe UI", 9, "bold"), fg=ACCENT_GREEN, bg=BG_PANEL)
wpm_label.pack(side="right")

# Row 2: Start Delay & Hotkey
row2 = tk.Frame(panel, bg=BG_PANEL)
row2.pack(fill="x", pady=4)

tk.Label(row2, text="Start Delay:", font=("Segoe UI", 9, "bold"), fg=FG_LIGHT, bg=BG_PANEL).pack(side="left")

start_delay_var = tk.DoubleVar(value=0.3)
start_delay_spin = tk.Spinbox(
    row2,
    from_=0.0,
    to=5.0,
    increment=0.1,
    format="%.1f",
    textvariable=start_delay_var,
    width=5,
    font=("Segoe UI", 9),
    bg=BG_ENTRY,
    fg=FG_LIGHT,
    justify="center",
)
start_delay_spin.pack(side="left", padx=(4, 15))

tk.Label(row2, text="Hotkey:", font=("Segoe UI", 9, "bold"), fg=FG_LIGHT, bg=BG_PANEL).pack(side="left")

hotkey_var = tk.StringVar(value="F8 (Recommended)")
hotkey_dropdown = ttk.Combobox(
    row2,
    textvariable=hotkey_var,
    values=["F8 (Recommended)", "Win+Y (Original)", "Ctrl+Shift+V", "F6"],
    state="readonly",
    width=17,
)
hotkey_dropdown.pack(side="left", padx=4)
hotkey_dropdown.bind("<<ComboboxSelected>>", lambda e: register_hotkeys())

# Row 3: Options (Jitter & Mode)
row3 = tk.Frame(panel, bg=BG_PANEL)
row3.pack(fill="x", pady=4)

mode_var = tk.StringVar(value="type")
rb_type = tk.Radiobutton(
    row3,
    text="Type (Key Simulation)",
    variable=mode_var,
    value="type",
    bg=BG_PANEL,
    fg=FG_LIGHT,
    selectcolor=BG_ENTRY,
    activebackground=BG_PANEL,
    font=("Segoe UI", 9),
)
rb_type.pack(side="left")

rb_paste = tk.Radiobutton(
    row3,
    text="Paste (Instant Ctrl+V)",
    variable=mode_var,
    value="paste",
    bg=BG_PANEL,
    fg=FG_LIGHT,
    selectcolor=BG_ENTRY,
    activebackground=BG_PANEL,
    font=("Segoe UI", 9),
)
rb_paste.pack(side="left", padx=10)

jitter_var = tk.BooleanVar(value=False)
cb_jitter = tk.Checkbutton(
    row3,
    text="Human Jitter (±20%)",
    variable=jitter_var,
    bg=BG_PANEL,
    fg=FG_LIGHT,
    selectcolor=BG_ENTRY,
    activebackground=BG_PANEL,
    font=("Segoe UI", 9),
)
cb_jitter.pack(side="right")

# Row 4: Action Buttons
btn_frame = tk.Frame(root, bg=BG_DARK)
btn_frame.pack(fill="x", padx=14, pady=4)

btn_write = tk.Button(
    btn_frame,
    text="▶ Write Now",
    command=trigger_typing,
    bg="#2e7d32",
    fg="#ffffff",
    activebackground="#388e3c",
    font=("Segoe UI", 9, "bold"),
    padx=12,
    pady=4,
    bd=0,
    cursor="hand2",
)
btn_write.pack(side="left", padx=(0, 6))

btn_stop = tk.Button(
    btn_frame,
    text="⏹ Stop (Esc)",
    command=cancel_typing,
    bg="#c62828",
    fg="#ffffff",
    activebackground="#d32f2f",
    font=("Segoe UI", 9, "bold"),
    padx=12,
    pady=4,
    bd=0,
    cursor="hand2",
)
btn_stop.pack(side="left", padx=6)

btn_clear = tk.Button(
    btn_frame,
    text="Clear",
    command=clear_text,
    bg="#424242",
    fg="#ffffff",
    activebackground="#616161",
    font=("Segoe UI", 9),
    padx=10,
    pady=4,
    bd=0,
    cursor="hand2",
)
btn_clear.pack(side="left", padx=6)

btn_tray = tk.Button(
    btn_frame,
    text="Minimize to Tray",
    command=hide_window,
    bg="#37474f",
    fg="#ffffff",
    activebackground="#455a64",
    font=("Segoe UI", 9),
    padx=10,
    pady=4,
    bd=0,
    cursor="hand2",
)
btn_tray.pack(side="right")

# Bottom Status Bar
status_frame = tk.Frame(root, bg="#0a0a0a", height=24)
status_frame.pack(fill="x", side="bottom")

status_label = tk.Label(
    status_frame,
    text="Status: Ready",
    font=("Segoe UI", 8),
    fg=ACCENT_GREEN,
    bg="#0a0a0a",
    padx=10,
    pady=3,
)
status_label.pack(side="left")

# ----------------- Tray Icon Initialization -----------------

try:
    icon_image = Image.open(resource_path("icon.png"))
except Exception:
    icon_image = Image.new("RGB", (64, 64), color="#007acc")

tray_menu = pystray.Menu(
    pystray.MenuItem("Show Window", show_window, default=True),
    pystray.MenuItem("Write Now", lambda icon, item: trigger_typing()),
    pystray.MenuItem("Quit", quit_app),
)
tray_icon = pystray.Icon("Auto Writer", icon_image, "Auto Writer", tray_menu)

# Run tray icon continuously in background daemon thread
tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
tray_thread.start()

# Window Protocol
root.protocol("WM_DELETE_WINDOW", hide_window)

# Register Hotkeys initially
register_hotkeys()

# Start Tkinter Event Loop
root.mainloop()