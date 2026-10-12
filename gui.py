# -*- coding: utf-8 -*-
"""阴阳师自动助手 - Tkinter 图形控制面板。

在 auto_click.py 的自动化核心之上提供可视化界面：
选择运行模式、设定挑战次数、开始/停止任务，并实时显示日志与统计。
启动方式：python gui.py
"""
import math
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auto_click as ac


class _GuiStdout:
    """把 worker 线程的 print 输出逐行送入界面日志队列，同时保留到原 stdout。

    仅在线程执行期间替换 sys.stdout，线程结束即恢复，不影响主线程。
    """

    def __init__(self, msg_queue, original):
        self.msg_queue = msg_queue
        self.original = original
        self._buf = ""

    def write(self, text):
        if not text:
            return
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            if line.strip():
                self.msg_queue.put(("log", line))
        try:
            self.original.write(text)
        except Exception:
            pass

    def flush(self):
        if self._buf and self._buf.strip():
            self.msg_queue.put(("log", self._buf.rstrip("\r\n")))
        self._buf = ""
        try:
            self.original.flush()
        except Exception:
            pass


class App:
    MODES = [
        ("爬塔挑战", "tower"),
        ("结界突破", "tpjj"),
        ("御魂-队长", "yuhun_captain"),
        ("御魂-队员", "yuhun_member"),
        ("契灵挑战", "qiling"),
    ]

    def __init__(self, root):
        self.root = root
        self.default_window_title = ac.WINDOWS_TITLE
        self.root.title("阴阳师护肝助手")
        self.root.geometry("680x620")
        self.root.minsize(580, 520)
        self.worker = None
        self.msg_queue = queue.Queue()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.drain_queue)

    # ---------- 界面 ----------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 8}
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        # 运行模式
        ttk.Label(frame, text="运行模式:").grid(row=0, column=0, sticky="w", **pad)
        self.mode_var = tk.StringVar(value=self.MODES[0][1])
        combo = ttk.Combobox(frame, textvariable=self.mode_var, state="readonly", width=18)
        combo["values"] = [name for name, _ in self.MODES]
        combo.current(0)
        combo.grid(row=0, column=1, sticky="w", **pad)
        combo.bind("<<ComboboxSelected>>", self._on_mode_changed)

        # 挑战次数
        ttk.Label(frame, text="挑战次数:").grid(row=1, column=0, sticky="w", **pad)
        self.count_var = tk.StringVar(value="0")
        ttk.Entry(frame, textvariable=self.count_var, width=10).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(frame, text="(0=无限循环)").grid(row=1, column=2, sticky="w", **pad)

        # 窗口标题
        ttk.Label(frame, text="窗口标题:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Label(frame, text="(多开时,下拉指定窗口)").grid(row=2, column=2, sticky="w", **pad)
        self.title_var = tk.StringVar(value="自动选择")
        self.title_combo = ttk.Combobox(
            frame, textvariable=self.title_var, state="readonly", width=28,
            values=("自动选择",), postcommand=self._refresh_window_titles)
        self.title_combo.grid(row=2, column=1, sticky="w", **pad)

        # 启动前等待
        ttk.Label(frame, text="开始前等待(秒):").grid(row=3, column=0, sticky="w", **pad)
        self.wait_var = tk.StringVar(value=str(ac.INITIAL_WAIT_TIME))
        ttk.Entry(frame, textvariable=self.wait_var, width=10).grid(row=3, column=1, sticky="w", **pad)

        # 战斗时长
        ttk.Label(frame, text="预计战斗时长(秒):").grid(row=4, column=0, sticky="w", **pad)
        self.duration_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.duration_var, width=12).grid(
            row=4, column=1, sticky="w", **pad)
        ttk.Label(frame, text="(填写单个数字，如 16 或 16.5)").grid(row=4, column=2, sticky="w", **pad)

        # 结果等待上限
        ttk.Label(frame, text="结果等待上限(秒):").grid(row=5, column=0, sticky="w", **pad)
        self.timeout_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.timeout_var, width=12).grid(
            row=5, column=1, sticky="w", **pad)
        ttk.Label(frame, text="(应大于预计时长)").grid(row=5, column=2, sticky="w", **pad)
        self._on_mode_changed()

        # 按钮区
        btn_row = ttk.Frame(frame)
        btn_row.grid(row=6, column=0, columnspan=3, sticky="w", **pad)
        self.start_btn = ttk.Button(btn_row, text="启动", command=self.start)
        self.start_btn.pack(side="left", padx=(0, 6))
        self.stop_btn = ttk.Button(btn_row, text="停止", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(btn_row, text="截图自检", command=self.selftest).pack(side="left", padx=6)

        # 状态与统计
        state_row = ttk.Frame(frame)
        state_row.grid(row=7, column=0, columnspan=3, sticky="we", **pad)
        self.status_var = tk.StringVar(value="○ 已就绪")
        ttk.Label(state_row, textvariable=self.status_var, font=("", 10, "bold")).pack(side="left")
        self.stats_var = tk.StringVar(value="")
        ttk.Label(state_row, textvariable=self.stats_var).pack(side="right")

        # 日志区
        log_frame = ttk.LabelFrame(frame, text="运行日志", padding=6)
        log_frame.grid(row=8, column=0, columnspan=3, sticky="nsew", **pad)
        self.log_text = tk.Text(log_frame, wrap="word", state="disabled", height=16)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=scroll.set)

        frame.columnconfigure(0, weight=0)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(8, weight=1)

    # ---------- 队列 / 日志 ----------
    def drain_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self.status_var.set(payload)
        except queue.Empty:
            pass
        self.root.after(100, self.drain_queue)

    def _append_log(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def log_now(self, text):
        self._append_log(text)

    # ---------- 动作 ----------
    def _mode_key(self):
        return dict((name, key) for name, key in self.MODES).get(self.mode_var.get())

    def _refresh_window_titles(self):
        """展开窗口标题下拉框时，重新识别当前可用的模拟器窗口。"""
        titles = []
        try:
            titles = [title for _, title in ac.find_all_device_windows() if title]
        except Exception:
            # 没有 pywin32 或枚举窗口失败时仍保留手动输入功能。
            titles = []
        values = ["自动选择"]
        for title in titles:
            if title not in values:
                values.append(title)
        current = self.title_var.get().strip()
        if current and current not in values:
            values.append(current)
        self.title_combo["values"] = values

    def _on_mode_changed(self, _event=None):
        defaults = {
            "tower": (ac.TOWER_BATTLE_DURATION, ac.TOWER_BATTLE_TIMEOUT),
            "tpjj": (ac.TPJJ_BATTLE_DURATION, ac.TPJJ_BATTLE_TIMEOUT),
            "yuhun_captain": (ac.YUHUN_BATTLE_DURATION, ac.YUHUN_BATTLE_TIMEOUT),
            "yuhun_member": (ac.YUHUN_BATTLE_DURATION, ac.YUHUN_BATTLE_TIMEOUT),
            "qiling": (ac.QILING_BATTLE_DURATION, ac.QILING_BATTLE_TIMEOUT),
        }
        duration, timeout = defaults.get(self._mode_key(), (ac.TOWER_BATTLE_DURATION,
                                                              ac.TOWER_BATTLE_TIMEOUT))
        self.duration_var.set(f"{duration:g}")
        self.timeout_var.set(f"{timeout:g}")

    @staticmethod
    def _parse_duration(value):
        try:
            duration = float(value.strip())
        except ValueError as exc:
            raise ValueError("预计战斗时长必须是单个数字，例如 16 或 16.5") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("战斗时长必须是大于 0 的有限数字")
        return duration

    def _collect_options(self):
        mode = self._mode_key()
        try:
            count = int(self.count_var.get().strip() or "0")
        except ValueError:
            count = 0
        try:
            wait = int(self.wait_var.get().strip() or "0")
        except ValueError:
            wait = 1
        duration = self._parse_duration(self.duration_var.get())
        try:
            timeout = float(self.timeout_var.get().strip())
        except ValueError as exc:
            raise ValueError("结果等待上限必须是数字") from exc
        if timeout <= 0:
            raise ValueError("结果等待上限必须大于 0")
        if mode != "tpjj" and timeout < duration:
            raise ValueError("结果等待上限不能小于预计战斗时长")
        title = self.title_var.get().strip()
        if title == "自动选择":
            title = ""
        return mode, max(0, count), max(0, wait), title, duration, timeout

    def set_title(self, title):
        """设置窗口标题查找方式。

        留空（默认）：恢复程序原宽匹配（百崽 / MuMu安卓设备 / LDPlayer 等，取面积最大者），
        与命令行直接运行行为一致；填写标题：才启用精确标题匹配，等同 --title。
        """
        # 每次启动都重新绑定窗口。后台模式会缓存上一次找到的窗口句柄
        # 和 adb 连接；如果只修改 EXPLICIT_TITLE，后续启动可能继续使用旧实例。
        ac.WINDOWS_TITLE = title or self.default_window_title
        ac.EXPLICIT_TITLE = bool(title)
        ac.TARGET_HWND = None
        ac.RENDER_CHILD = None
        ac.WINDOW_REGION = None
        ac.RENDER_REGION = None
        ac._ADB['serial'] = None
        ac._ADB['size'] = None
        ac._ADB['tap_size'] = None
        ac._ADB['fail_time'] = 0.0
        ac._ADB_ASPECT_WARNED = False

    def reset_title_mode(self):
        """每次启动/自检都恢复宽匹配模式，防止上一次运行遗留精确标题。"""
        self.set_title("")

    def start(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("提示", "任务正在运行中，请先停止。")
            return
        try:
            mode, count, wait, title, duration, timeout = self._collect_options()
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        if mode is None:
            messagebox.showerror("错误", "请选择有效的运行模式。")
            return
        if title:
            self.set_title(title)
        else:
            self.reset_title_mode()
        err = ac.prepare_backend()
        if err:
            messagebox.showerror("启动失败", err)
            self.log_now("[错误] " + err)
            return
        ac.set_run_options(mode, count=count, wait_time=wait,
                           battle_duration=duration, battle_timeout=timeout)

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set("● 运行中…")
        self.worker = threading.Thread(target=self._run_task, args=(mode,), daemon=True)
        self.worker.start()

    def _run_task(self, mode):
        old_stdout = sys.stdout
        sys.stdout = _GuiStdout(self.msg_queue, old_stdout)
        try:
            ok = ac.gui_run_once(mode)
            if not ok:
                self.msg_queue.put(("log", "[错误] 模式执行失败"))
        except Exception as e:
            self.msg_queue.put(("log", "[异常] %s" % e))
        finally:
            sys.stdout = old_stdout
            self.msg_queue.put(("status", "○ 已就绪"))
            self.msg_queue.put(("log", "任务结束"))
            self.root.after(0, self._on_task_done)

    def _on_task_done(self):
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.stats_var.set(
            "已挑战: %d 场" % ac.challenge_count
            if not ac.current_mode == "tpjj"
            else "结界: %d 场" % ac.TPJJ_STATS["battle"]
        )

    def stop(self):
        ac.request_stop()
        self.status_var.set("○ 停止中…（等待当前画面步骤完成，最长约一轮识别超时）")
        self.log_now("收到停止指令，正在等待当前画面步骤完成…")

    def selftest(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("提示", "任务正在运行中，请先停止。")
            return
        title = self.title_var.get().strip()
        if title == "自动选择":
            title = ""
        if title:
            self.set_title(title)
        else:
            self.reset_title_mode()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="disabled")
        self.status_var.set("● 自检中…")
        self.worker = threading.Thread(target=self._run_selftest, daemon=True)
        self.worker.start()

    def _run_selftest(self):
        old_stdout = sys.stdout
        sys.stdout = _GuiStdout(self.msg_queue, old_stdout)
        try:
            err = ac.prepare_backend()
            if err:
                self.msg_queue.put(("log", "[错误] " + err))
            else:
                ac.run_backend_selftest()
        except Exception as e:
            self.msg_queue.put(("log", "[异常] " + str(e)))
        finally:
            sys.stdout = old_stdout
            self.msg_queue.put(("status", "○ 已就绪"))
            self.msg_queue.put(("log", "自检结束"))
            self.root.after(0, self._on_task_done)

    def on_close(self):
        ac.request_stop()
        try:
            self.root.destroy()
        except Exception:
            pass
        # worker 是 daemon 线程，窗口销毁后进程直接退出，无需等待识别超时


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
