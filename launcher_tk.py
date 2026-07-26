#!/usr/bin/env python3
"""Novel Agent 启动器 — 桌面对话框 (tkinter)

双击运行或 ./start_tk.sh:
1. 弹出一个原生对话框窗口 (不是浏览器网页)
2. 显示服务状态 + 启动按钮
3. 点「启动」→ 后台拉起主服务 → 自动打开浏览器到 http://localhost:8000/
4. 对话框可关闭,主服务继续在后台跑
"""
import os
import sys
import time
import subprocess
import threading
import webbrowser
from urllib.request import urlopen
from urllib.error import URLError

try:
    import tkinter as tk
    from tkinter import ttk, font as tkfont
except ImportError:
    print("错误: 系统未安装 tkinter (Python GUI 库)")
    if sys.platform == "win32":
        print("Windows: 重新安装 Python 时勾选 'tcl/tk and IDLE' 选项")
        print("  或下载安装: https://www.python.org/downloads/")
    elif sys.platform == "darwin":
        print("macOS: brew install python-tk@3.12  (Homebrew)")
        print("  或使用官方 Python 安装包 (自带 tkinter)")
    else:
        print("Ubuntu/Debian: sudo apt install python3-tk")
        print("CentOS/RHEL: sudo yum install python3-tkinter")
        print("Arch: sudo pacman -S tk")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NA = os.path.join(SCRIPT_DIR, "na")
APP_URL = "http://localhost:8000/"
HEALTH_URL = "http://localhost:8000/api/health"


def is_app_running() -> bool:
    try:
        with urlopen(HEALTH_URL, timeout=1) as r:
            return r.status == 200
    except (URLError, ConnectionError, OSError):
        return False


def start_app() -> bool:
    """启动主服务,最多等 25 秒。"""
    if is_app_running():
        return True
    if not os.path.exists(NA):
        return False
    subprocess.Popen(
        [NA, "start"],
        cwd=SCRIPT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(25):
        if is_app_running():
            return True
        time.sleep(1)
    return is_app_running()


class LauncherApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Novel Agent 启动器")
        self.root.geometry("440x360")
        self.root.resizable(False, False)
        self.root.configure(bg="#faf6ed")

        # 居中显示
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"+{x}+{y}")

        # 字体
        title_font = tkfont.Font(family="PingFang SC", size=22, weight="bold")
        sub_font = tkfont.Font(family="PingFang SC", size=11)
        btn_font = tkfont.Font(family="PingFang SC", size=13, weight="bold")
        status_font = tkfont.Font(family="PingFang SC", size=11)
        hint_font = tkfont.Font(family="PingFang SC", size=10)

        # 主容器
        container = tk.Frame(root, bg="#faf6ed", padx=36, pady=28)
        container.pack(expand=True, fill="both")

        # Logo
        tk.Label(
            container, text="✦", bg="#faf6ed", fg="#a13d3d",
            font=tkfont.Font(size=44, weight="bold"),
        ).pack(pady=(0, 8))

        # 标题
        tk.Label(
            container, text="Novel Agent", bg="#faf6ed", fg="#2e1f15",
            font=title_font,
        ).pack(pady=(0, 4))

        # 副标题
        tk.Label(
            container, text="小说创作 8 阶段工作流 · 技能市场",
            bg="#faf6ed", fg="#9a8062", font=sub_font,
        ).pack(pady=(0, 18))

        # 状态标签
        self.status_var = tk.StringVar(value="● 检查中...")
        self.status_label = tk.Label(
            container, textvariable=self.status_var,
            bg="#fef3d6", fg="#9a6b1f",
            font=status_font,
            padx=14, pady=5,
            relief="flat",
        )
        self.status_label.pack(pady=(0, 16), fill="x")

        # 启动按钮
        self.launch_btn = tk.Button(
            container, text="启动并进入",
            bg="#a13d3d", fg="white",
            activebackground="#8b2e2e", activeforeground="white",
            font=btn_font,
            relief="flat", bd=0,
            padx=24, pady=12,
            cursor="hand2",
            command=self.on_launch,
        )
        self.launch_btn.pack(fill="x", pady=(0, 8))
        self.launch_btn.bind("<Enter>", lambda e: self.launch_btn.config(bg="#8b2e2e"))
        self.launch_btn.bind("<Leave>", lambda e: self.launch_btn.config(bg="#a13d3d"))

        # 直接进入按钮 (服务已起时显示)
        self.enter_btn = tk.Button(
            container, text="直接进入应用",
            bg="#5a6b3f", fg="white",
            activebackground="#4a5b2f", activeforeground="white",
            font=btn_font,
            relief="flat", bd=0,
            padx=24, pady=10,
            cursor="hand2",
            command=self.on_enter,
            state="disabled",
        )
        self.enter_btn.pack(fill="x")

        # 提示文字
        self.hint_var = tk.StringVar(value="")
        tk.Label(
            container, textvariable=self.hint_var,
            bg="#faf6ed", fg="#9a8062", font=hint_font,
        ).pack(pady=(14, 0))

        # 启动后初始检查
        self.root.after(100, self.check_status)

    def set_status(self, text: str, bg: str, fg: str):
        self.status_var.set(text)
        self.status_label.config(bg=bg, fg=fg)

    def check_status(self):
        """异步检查主服务状态。"""
        def worker():
            running = is_app_running()
            self.root.after(0, lambda: self._update_status(running))
        threading.Thread(target=worker, daemon=True).start()

    def _update_status(self, running: bool):
        if running:
            self.set_status("● 服务运行中", "#e6f4e6", "#2d6a2d")
            self.launch_btn.config(text="已在运行 — 点击进入", state="normal",
                                    bg="#5a6b3f", activebackground="#4a5b2f")
            self.enter_btn.config(state="normal")
        else:
            self.set_status("○ 服务未启动", "#fde8e8", "#8b2222")
            self.launch_btn.config(text="启动并进入", state="normal",
                                    bg="#a13d3d", activebackground="#8b2e2e")
            self.enter_btn.config(state="disabled")

    def on_launch(self):
        """点击启动按钮。"""
        if is_app_running():
            # 已在运行,直接进入
            self.on_enter()
            return

        self.launch_btn.config(state="disabled", text="启动中...")
        self.set_status("● 启动中...", "#fef3d6", "#9a6b1f")
        self.hint_var.set("首次启动约需 3-5 秒,请稍候...")

        def worker():
            ok = start_app()
            self.root.after(0, lambda: self._on_launch_done(ok))
        threading.Thread(target=worker, daemon=True).start()

    def _on_launch_done(self, ok: bool):
        if ok:
            self.set_status("● 启动成功", "#e6f4e6", "#2d6a2d")
            self.hint_var.set("正在打开浏览器...")
            # 打开浏览器
            try:
                webbrowser.open(APP_URL)
            except Exception:
                pass
            # 1.2 秒后自动关闭对话框
            self.root.after(1200, self.root.destroy)
        else:
            self.set_status("✗ 启动失败", "#fde8e8", "#8b2222")
            self.hint_var.set("请查看终端日志: na log")
            self.launch_btn.config(state="normal", text="重试启动",
                                    bg="#a13d3d", activebackground="#8b2e2e")

    def on_enter(self):
        """直接进入应用。"""
        try:
            webbrowser.open(APP_URL)
        except Exception:
            pass
        self.root.after(300, self.root.destroy)


def main():
    root = tk.Tk()
    app = LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
