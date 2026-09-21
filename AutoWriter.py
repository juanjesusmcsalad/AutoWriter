import tkinter as tk
import keyboard

def type_current_text():
    text = entry.get("1.0", "end-1c")
    keyboard.write(text, delay=0.01)


root = tk.Tk()
root.geometry("200x200")
root.title("Auto Writer")

root.configure(bg="#000000")

entry = tk.Text(root, width=50, height=50)
entry.pack(padx=10, pady=10)

keyboard.add_hotkey('win+y', type_current_text)

root.mainloop()
