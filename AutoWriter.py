import tkinter as tk
import keyboard
import pystray
from PIL import Image
import threading
import os
import sys

def resource_path(filename):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

def type_current_text():
    text = entry.get("1.0", "end-1c")
    keyboard.write(text, delay=0.01)

def hide_window():
    root.withdraw()
    tray_icon.run()

def show_window(icon, item):
    icon.stop()
    root.deiconify()

def quit_app(icon, item):
    icon.stop()
    root.destroy()  

root = tk.Tk()
root.geometry("400x400")
root.title("Auto Writer")
root.configure(bg="#000000")

entry = tk.Text(root, width=50, height=50)
entry.pack(padx=10, pady=10)

keyboard.add_hotkey('win+y', type_current_text)

image = Image.open(resource_path("icon.png"))
menu = pystray.Menu(
    pystray.MenuItem("Show", show_window),
    pystray.MenuItem("Quit", quit_app)
)
tray_icon = pystray.Icon("Auto Writer", image, "Auto Writer", menu)

root.protocol("WM_DELETE_WINDOW", hide_window)

root.mainloop()
