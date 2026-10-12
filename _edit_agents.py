# -*- coding: utf-8 -*-
BT = chr(96)
P = r"D:\Shammpool\myCode\python_start\yys\AGENTS.md"
t = open(P, "rb").read().decode("utf-8")
old1 = ("Python 3 with {b}pyautogui{b}, {b}opencv-python{b}, {b}numpy{b}, {b}pygetwindow{b}; "
        "{b}pywin32{b} is optional and enables background (后台) mode.").format(b=BT)
new1 = ("Python 3 with {b}opencv-python{b} and {b}numpy{b}; "
        "{b}pywin32{b} is required and enables background (后台) mode.").format(b=BT)
old2 = "Install dependencies with {b}pip install pyautogui opencv-python numpy pygetwindow pywin32{b}.".format(b=BT)
new2 = "Install dependencies with {b}pip install opencv-python numpy pywin32{b}.".format(b=BT)
assert t.count(old1) == 1, ("old1", t.count(old1))
assert t.count(old2) == 1, ("old2", t.count(old2))
t = t.replace(old1, new1).replace(old2, new2)
open(P, "wb").write(t.encode("utf-8"))
print("AGENTS.md updated")
