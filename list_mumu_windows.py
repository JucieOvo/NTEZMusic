import win32gui

def enum_cb(hwnd, results):
    if win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd)
        if title and "mumu" in title.lower() or "模拟器" in title:
            results.append((hwnd, title))

results = []
win32gui.EnumWindows(enum_cb, results)
if not results:
    print("未找到包含 mumu 或 '模拟器' 的窗口")
for hwnd, title in results:
    print(f"HWND: {hwnd}, Title: '{title}'")
