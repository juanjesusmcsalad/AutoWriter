import tkinter as tk
import keyboard

def type_current_text():
    text = entry.get()
    keyboard.write(text, delay=0.01)


root = tk.Tk()
root.title("AutoWriter")

entry = tk.Entry(root, width=50)
entry.pack(padx=10, pady=10)

keyboard.add_hotkey('win+y', type_current_text)

root.mainloop()