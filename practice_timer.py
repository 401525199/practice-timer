"""A small, local Windows desktop practice timer."""

from __future__ import annotations

import csv
import ctypes
import json
import logging
import math
import os
from pathlib import Path
import sys
import subprocess
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from timer_core import TimerModel
import taskbar_win


APP_TITLE = "练习计时器"
APP_ID = "PracticeTimer.Desktop"
APP_DIR = Path(__file__).resolve().parent
ICON_FILE = APP_DIR / "timer-light.ico"
LOCAL_DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
DATA_DIR = LOCAL_DATA_DIR / "CFAPracticeTimer"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "session.json"
logging.basicConfig(filename=DATA_DIR / "timer.log", level=logging.ERROR,
                    format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")

BG = "#F7F9FC"
CARD = "#E8EEF4"
TEXT = "#253348"
MUTED = "#627087"
LINE = "#D7E0EA"
MINT = "#137E69"
AMBER = "#A76705"
RED = "#C84353"
BUTTON_TEXT = "#FFFFFF"


def format_time(seconds: float, *, round_up: bool = False) -> str:
    value = max(0, math.ceil(seconds) if round_up else math.floor(seconds))
    minutes, second = divmod(value, 60)
    if minutes >= 60:
        hours, minute = divmod(minutes, 60)
        return f"{hours}:{minute:02d}:{second:02d}"
    return f"{minutes:02d}:{second:02d}"


def format_record_time(seconds: float) -> str:
    tenths = max(0, math.ceil(seconds*10-1e-9))
    whole, fraction = divmod(tenths, 10)
    return f"{format_time(whole)}.{fraction}"


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command, *, width=140, height=43,
                 fill=CARD, foreground=TEXT, keyboard_guard=None):
        super().__init__(parent, width=width, height=height, bg=BG,
                         bd=0, highlightthickness=0, takefocus=True)
        self.command = command
        self.fill = fill
        self.foreground = foreground
        self.button_text = text
        self.enabled = True
        self.keyboard_guard = keyboard_guard
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Return>", self._keyboard)
        self.bind("<space>", self._keyboard)
        self.bind("<FocusIn>", lambda _: self.redraw())
        self.bind("<FocusOut>", lambda _: self.redraw())
        self.configure(cursor="hand2")
        self.redraw()

    def _click(self, event):
        if self.enabled and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.command()

    def _keyboard(self, event):
        if self.enabled and (self.keyboard_guard is None or self.keyboard_guard(event)):
            self.command()
        return "break"

    def set_text(self, text):
        if text != self.button_text:
            self.button_text = text
            self.redraw()

    def set_enabled(self, enabled):
        if self.enabled != enabled:
            self.enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow")
            self.redraw()

    def redraw(self):
        self.delete("all")
        width, height = int(self["width"]), int(self["height"])
        radius = 12
        fill = self.fill if self.enabled else "#EDF1F5"
        self.create_polygon(radius, 1, width-radius, 1, width-1, radius,
                            width-1, height-radius, width-radius, height-1,
                            radius, height-1, 1, height-radius, 1, radius,
                            smooth=True, splinesteps=24, fill=fill, outline="")
        if self.focus_get() is self:
            self.create_rectangle(5, 5, width-5, height-5, outline=MINT, width=1)
        self.create_text(width/2, height/2, text=self.button_text,
                         fill=self.foreground if self.enabled else MUTED,
                         font=("Microsoft YaHei UI", 11, "bold"))


class TimerApp:
    def __init__(self, root):
        self.root = root
        self.model = TimerModel(target_seconds=90)
        self.topmost = True
        self.sound = False
        self.countdown = False
        self.compact = False
        self.alerted_question = None
        self.dialog = None
        self.last_save = time.monotonic()
        self.saved_position = None
        self.held_keys = set()
        self.taskbar_hwnd = None
        self.window_icons = None
        self.load_state()

        root.title(APP_TITLE)
        root.configure(bg=BG)
        root.resizable(False, False)
        root.attributes("-topmost", self.topmost)
        try:
            root.iconbitmap(default=str(ICON_FILE))
            root.iconbitmap(str(ICON_FILE))
        except tk.TclError:
            logging.exception("Could not load application icons")
        if sys.platform == "win32":
            try:
                self.window_icons = taskbar_win.WindowIcons(ICON_FILE)
            except (taskbar_win.TaskbarIntegrationError, OSError):
                logging.exception("Could not load native window icons")
        root.bind("<Map>", self.on_window_map, add="+")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<space>", self.toggle_key)
        root.bind("<Return>", self.next_key)
        root.bind("<Control-r>", lambda _: self.reset())
        root.bind("<Control-m>", lambda _: self.toggle_compact())
        root.bind_all("<KeyRelease>", self.release_key, add="+")
        root.bind("<FocusOut>", lambda _: root.after_idle(self.clear_lost_focus))
        root.report_callback_exception = self.callback_error

        self.content = tk.Frame(root, bg=BG)
        self.content.pack(fill="both", expand=True, padx=22, pady=(17, 12))
        header = tk.Frame(self.content, bg=BG)
        header.pack(fill="x")
        tk.Label(header, text=APP_TITLE, bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        self.pin_button = self.link_button(header, "置顶 ✓", self.toggle_pin)
        self.pin_button.pack(side="right")

        self.question_row = tk.Frame(self.content, bg=BG)
        self.question_row.pack(fill="x", pady=(18, 0))
        self.question_label = tk.Label(self.question_row, bg=BG, fg=TEXT,
                                       font=("Microsoft YaHei UI", 11, "bold"))
        self.question_label.pack(side="left")
        self.mode_button = self.link_button(self.question_row, "正计时 ▾", self.toggle_mode)
        self.mode_button.pack(side="right")

        self.time_label = tk.Label(self.content, text="00:00", bg=BG, fg=TEXT,
                                  font=("Consolas", 53, "bold"), pady=0)
        self.time_label.pack(fill="x", pady=(8, 0))
        self.status_label = tk.Label(self.content, bg=BG, fg=MUTED,
                                    font=("Microsoft YaHei UI", 9))
        self.status_label.pack(fill="x", pady=(0, 13))
        self.progress = tk.Canvas(self.content, height=4, bg=LINE, bd=0, highlightthickness=0)
        self.progress.pack(fill="x")
        self.progress.bind("<Configure>", lambda _: self.update_view())

        self.actions = tk.Frame(self.content, bg=BG)
        self.actions.pack(fill="x", pady=(19, 0))
        self.start_button = RoundedButton(self.actions, "开始  ▶", self.toggle,
                                          width=151, fill=MINT, foreground=BUTTON_TEXT, keyboard_guard=self.key_guard)
        self.start_button.pack(side="left")
        self.next_button = RoundedButton(self.actions, "下一题  →", self.next_question, width=151,
                                         keyboard_guard=self.key_guard)
        self.next_button.pack(side="right")

        self.stats_frame = tk.Frame(self.content, bg=BG)
        self.stats_frame.pack(fill="x", pady=(20, 0))
        tk.Frame(self.stats_frame, bg=LINE, height=1).pack(fill="x", pady=(0, 12))
        stat_grid = tk.Frame(self.stats_frame, bg=BG)
        stat_grid.pack(fill="x")
        self.stat_labels = []
        for column, title in enumerate(("已完成", "平均用时", "累计用时")):
            stat_grid.columnconfigure(column, weight=1)
            tk.Label(stat_grid, text=title, bg=BG, fg=MUTED,
                     font=("Microsoft YaHei UI", 8)).grid(row=0, column=column, sticky="n")
            label = tk.Label(stat_grid, bg=BG, fg=TEXT, font=("Consolas", 13, "bold"))
            label.grid(row=1, column=column, pady=(3, 0))
            self.stat_labels.append(label)

        self.footer = tk.Frame(self.content, bg=BG)
        self.footer.pack(fill="x", pady=(15, 0))
        for title, action in (("记录", self.show_history), ("设置", self.show_settings),
                              ("重置", self.reset)):
            self.link_button(self.footer, title, action).pack(side="left", padx=(0, 15))
        self.link_button(self.footer, "收起 ↗", self.toggle_compact).pack(side="right")
        self.shortcut_label = tk.Label(self.content, text="空格：开始 / 暂停    ·    回车：下一题",
                                       bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8))
        self.shortcut_label.pack(pady=(10, 0))
        self.compact_footer = tk.Frame(self.content, bg=BG)
        self.link_button(self.compact_footer, "展开 ↙", self.toggle_compact).pack(side="right")
        self.link_button(self.compact_footer, "记录", self.show_history).pack(side="left")

        self.apply_size(initial=True)
        self.configure_taskbar()
        self.update_view()
        root.after(100, self.tick)

    def configure_taskbar(self):
        if sys.platform != "win32":
            return
        try:
            self.root.update_idletasks()
            hwnd = int(self.root.tk.call("wm", "frame", self.root._w), 0)
            if getattr(sys, "frozen", False):
                executable = Path(sys.executable).resolve()
                command = subprocess.list2cmdline([str(executable)])
                taskbar_icon = executable
            else:
                pythonw = Path(sys.executable).with_name("pythonw.exe")
                executable = pythonw if pythonw.is_file() else Path(sys.executable)
                command = subprocess.list2cmdline([str(executable), str(Path(__file__).resolve())])
                taskbar_icon = ICON_FILE
            taskbar_win.configure_window(hwnd, APP_ID, str(taskbar_icon), command, APP_TITLE)
            self.taskbar_hwnd = hwnd
            self.apply_window_icon(self.root)
        except (taskbar_win.TaskbarIntegrationError, tk.TclError, ValueError, OSError):
            logging.exception("Could not configure taskbar identity")

    def apply_window_icon(self, window):
        if self.window_icons is None or not window.winfo_exists():
            return
        try:
            hwnd = int(window.tk.call("wm", "frame", window._w), 0)
            self.window_icons.apply(hwnd)
        except (taskbar_win.TaskbarIntegrationError, tk.TclError, ValueError):
            logging.exception("Could not apply native window icons")

    def on_window_map(self, event):
        if event.widget is self.root or isinstance(event.widget, tk.Toplevel):
            self.root.after_idle(lambda window=event.widget: self.apply_window_icon(window))

    def link_button(self, parent, text, command):
        button = tk.Button(parent, text=text, command=command, bg=BG, fg=MUTED,
                         activebackground=BG, activeforeground=MINT, bd=0,
                         highlightthickness=0, relief="flat", cursor="hand2", padx=0,
                         font=("Microsoft YaHei UI", 9))
        def invoke(event):
            if self.key_guard(event):
                command()
            return "break"
        button.bind("<space>", invoke)
        button.bind("<Return>", invoke)
        return button

    def key_guard(self, event):
        if event.keysym in self.held_keys:
            return False
        self.held_keys.add(event.keysym)
        return True

    def release_key(self, event):
        self.held_keys.discard(event.keysym)

    def clear_lost_focus(self):
        if self.root.focus_displayof() is None:
            self.held_keys.clear()

    def load_state(self):
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.model.restore(state["session"])
            self.topmost = bool(state.get("topmost", True))
            self.sound = bool(state.get("sound", False))
            self.countdown = bool(state.get("countdown", False))
            self.compact = bool(state.get("compact", False))
            position = state.get("position")
            if isinstance(position, list) and len(position) == 2 and all(isinstance(v, int) for v in position):
                self.saved_position = position
        except FileNotFoundError:
            pass
        except (ValueError, KeyError, TypeError, OSError):
            logging.exception("Could not restore saved session")

    def save_state(self):
        state = {"session": self.model.to_dict(), "topmost": self.topmost,
                 "sound": self.sound, "countdown": self.countdown, "compact": self.compact,
                 "position": [self.root.winfo_x(), self.root.winfo_y()]}
        try:
            temporary = STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(STATE_FILE)
        except OSError:
            logging.exception("Could not save session")
        self.last_save = time.monotonic()

    def apply_size(self, initial=False):
        width, height = (310, 303) if self.compact else (360, 462)
        if self.compact:
            self.stats_frame.pack_forget()
            self.footer.pack_forget()
            self.shortcut_label.pack_forget()
            self.compact_footer.pack(fill="x", pady=(10, 0))
            self.actions.pack_configure(pady=(14, 0))
            self.question_row.pack_configure(pady=(10, 0))
            self.time_label.configure(font=("Consolas", 43, "bold"))
            for button in (self.start_button, self.next_button):
                button.configure(width=126, height=40)
                button.redraw()
        else:
            self.compact_footer.pack_forget()
            self.stats_frame.pack(fill="x", pady=(20, 0))
            self.footer.pack(fill="x", pady=(15, 0))
            self.shortcut_label.pack(pady=(10, 0))
            self.actions.pack_configure(pady=(19, 0))
            self.question_row.pack_configure(pady=(18, 0))
            self.time_label.configure(font=("Consolas", 53, "bold"))
            for button in (self.start_button, self.next_button):
                button.configure(width=151, height=43)
                button.redraw()
        if initial:
            if self.saved_position:
                x, y = self.saved_position
            else:
                x, y = self.root.winfo_screenwidth()-width-45, 85
            x = max(0, min(x, self.root.winfo_screenwidth()-width))
            y = max(0, min(y, self.root.winfo_screenheight()-height-65))
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        else:
            self.root.geometry(f"{width}x{height}")

    def update_view(self):
        state = self.model.snapshot()
        elapsed = state.question_elapsed_seconds
        target = state.target_seconds
        overtime = elapsed >= target
        color = RED if overtime else (AMBER if elapsed >= target*0.8 else TEXT)
        if self.countdown:
            displayed = ("+" + format_time(elapsed-target)) if overtime else format_time(target-elapsed, round_up=True)
        else:
            displayed = format_time(elapsed)
        self.time_label.configure(text=displayed, fg=color)
        self.question_label.configure(text=f"第 {state.question_number:02d} 题")
        mode = "计时中" if state.running else ("已暂停" if elapsed else "准备开始")
        if overtime:
            status = f"{mode}  ·  超时 {format_time(elapsed-target)}"
        else:
            status = f"{mode}  ·  目标 {format_time(target)}"
        self.status_label.configure(text=status, fg=RED if overtime else MUTED)
        self.mode_button.configure(text="倒计时 ▾" if self.countdown else "正计时 ▾")
        self.pin_button.configure(text="置顶 ✓" if self.topmost else "置顶", fg=MINT if self.topmost else MUTED)
        self.start_button.set_text("暂停  Ⅱ" if state.running else ("继续  ▶" if elapsed else "开始  ▶"))
        self.next_button.set_enabled(elapsed >= 0.05)
        self.progress.delete("all")
        width = self.progress.winfo_width()
        self.progress.create_rectangle(0, 0, width*min(1, elapsed/target), 4,
                                       fill=RED if overtime else MINT, outline="")
        records = self.model.completed_records
        average = sum(record.elapsed_seconds for record in records)/len(records) if records else 0
        for label, value in zip(self.stat_labels, (f"{len(records)} 题", format_time(average),
                                                  format_time(state.session_elapsed_seconds))):
            label.configure(text=value)
        if state.running and overtime and self.alerted_question != state.question_number:
            self.alerted_question = state.question_number
            if self.sound:
                try:
                    import winsound
                    winsound.MessageBeep(winsound.MB_OK)
                except (ImportError, RuntimeError):
                    pass

    def tick(self):
        self.update_view()
        if self.model.running and time.monotonic()-self.last_save >= 5:
            self.save_state()
        self.root.after(100, self.tick)

    def toggle(self):
        self.model.toggle()
        self.update_view()
        self.save_state()

    def toggle_key(self, _):
        if self.dialog is None and self.key_guard(_):
            self.toggle()
        return "break"

    def next_key(self, _):
        if self.dialog is None and self.key_guard(_):
            self.next_question()
        return "break"

    def next_question(self):
        if self.model.question_elapsed_seconds < 0.05:
            return
        self.model.next_question()
        self.alerted_question = None
        self.update_view()
        self.save_state()

    def reset(self):
        if self.dialog is not None:
            return
        if self.model.session_elapsed_seconds > 0 or self.model.completed_records:
            if not messagebox.askyesno("重置练习", "清空本次计时和做题记录？\n需要保留时，可先在“记录”里导出。", parent=self.root):
                return
        self.model.reset()
        self.alerted_question = None
        self.update_view()
        self.save_state()

    def toggle_pin(self):
        self.topmost = not self.topmost
        self.root.attributes("-topmost", self.topmost)
        self.update_view()
        self.save_state()

    def toggle_mode(self):
        self.countdown = not self.countdown
        self.update_view()
        self.save_state()

    def toggle_compact(self):
        if self.dialog is not None:
            return
        self.compact = not self.compact
        self.apply_size()
        self.update_view()
        self.save_state()

    def new_dialog(self, title, width, height):
        if self.dialog is not None:
            self.dialog.lift()
            return None
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.bind("<Map>", self.on_window_map, add="+")
        dialog.transient(self.root)
        dialog.attributes("-topmost", self.topmost)
        x = max(0, min(self.root.winfo_x()-30, self.root.winfo_screenwidth()-width))
        y = max(0, min(self.root.winfo_y()+40, self.root.winfo_screenheight()-height-65))
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        self.dialog = dialog
        dialog.protocol("WM_DELETE_WINDOW", self.dismiss_dialog)
        dialog.bind("<Escape>", lambda _: self.dismiss_dialog())
        dialog.grab_set()
        return dialog

    def dismiss_dialog(self):
        if self.dialog is not None:
            self.dialog.grab_release()
            self.dialog.destroy()
            self.dialog = None
            self.root.focus_set()

    def show_settings(self):
        dialog = self.new_dialog("计时设置", 330, 292)
        if dialog is None:
            return
        frame = tk.Frame(dialog, bg=BG)
        frame.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(frame, text="每题目标用时", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(frame, text="按你的练习节奏设置，达到目标后继续计时。", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(7, 16))
        entry_row = tk.Frame(frame, bg=BG)
        entry_row.pack(anchor="w")
        target = int(self.model.target_seconds)
        minutes = tk.StringVar(value=str(target//60))
        seconds = tk.StringVar(value=f"{target%60:02d}")
        entry_options = dict(width=4, bg=CARD, fg=TEXT, insertbackground=TEXT,
                             selectbackground="#CCE8DE", selectforeground=TEXT,
                             relief="flat", justify="center", font=("Consolas", 20))
        minute_entry = tk.Entry(entry_row, textvariable=minutes, **entry_options)
        minute_entry.pack(side="left")
        tk.Label(entry_row, text="分", bg=BG, fg=MUTED).pack(side="left", padx=6)
        tk.Entry(entry_row, textvariable=seconds, **entry_options).pack(side="left")
        tk.Label(entry_row, text="秒", bg=BG, fg=MUTED).pack(side="left", padx=6)
        sound_var = tk.BooleanVar(value=self.sound)
        tk.Checkbutton(frame, text="到达目标时声音提醒", variable=sound_var, bg=BG, fg=TEXT,
                       selectcolor=CARD, activebackground=BG, activeforeground=TEXT,
                       font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(17, 7))
        error = tk.Label(frame, text="", bg=BG, fg=RED, font=("Microsoft YaHei UI", 8))
        error.pack(anchor="w")
        def save(event=None):
            if event is not None and not self.key_guard(event):
                return "break"
            try:
                minute, second = int(minutes.get()), int(seconds.get())
                if minute < 0 or minute > 180 or second < 0 or second > 59 or not 1 <= minute*60+second <= 10800:
                    raise ValueError
            except ValueError:
                error.configure(text="请输入 1 秒至 180 分钟；秒数为 0–59。")
                return
            self.model.set_target(minute*60+second)
            self.sound = sound_var.get()
            self.alerted_question = self.model.current_question_number if self.model.question_elapsed_seconds >= self.model.target_seconds else None
            self.dismiss_dialog()
            self.update_view()
            self.save_state()
        RoundedButton(frame, "保存设置", save, width=280, height=39, fill=MINT, foreground=BUTTON_TEXT,
                      keyboard_guard=self.key_guard).pack(pady=(8, 0))
        dialog.bind("<Return>", save)
        minute_entry.focus_set()
        minute_entry.select_range(0, "end")

    def show_history(self):
        dialog = self.new_dialog("做题记录", 430, 410)
        if dialog is None:
            return
        frame = tk.Frame(dialog, bg=BG)
        frame.pack(fill="both", expand=True, padx=20, pady=18)
        tk.Label(frame, text="本次练习记录", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(frame, text="“下一题”会记录刚完成的题；当前题继续计时。", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(7, 12))
        style = ttk.Style(dialog)
        style.theme_use("clam")
        style.configure("Timer.Treeview", background=CARD, foreground=TEXT,
                        fieldbackground=CARD, borderwidth=0, rowheight=28,
                        font=("Microsoft YaHei UI", 9))
        style.configure("Timer.Treeview.Heading", background=LINE, foreground=TEXT,
                        relief="flat", font=("Microsoft YaHei UI", 9))
        style.map("Timer.Treeview", background=[("selected", "#D9EEE7")],
                  foreground=[("selected", TEXT)])
        table_frame = tk.Frame(frame, bg=BG)
        table = ttk.Treeview(table_frame, columns=("question", "elapsed", "target", "result"),
                             show="headings", style="Timer.Treeview", selectmode="browse")
        for name, title, width in (("question", "题号", 55), ("elapsed", "用时", 80),
                                    ("target", "目标", 80), ("result", "状态", 75)):
            table.heading(name, text=title)
            table.column(name, width=width, anchor="center")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        table.pack(side="left", fill="both", expand=True)
        table.tag_configure("overtime", foreground=RED)
        for record in self.model.completed_records:
            table.insert("", "end", values=(record.question_number, format_record_time(record.elapsed_seconds),
                                               format_time(record.target_seconds), "按时" if record.on_target else "超时"),
                         tags=() if record.on_target else ("overtime",))
        if not self.model.completed_records:
            tk.Label(frame, text="完成一题后点“下一题”，记录会出现在这里。", bg=BG, fg=MUTED,
                     font=("Microsoft YaHei UI", 8)).pack(side="bottom", pady=(8, 0))
        RoundedButton(frame, "导出 CSV", self.export_history, width=385, height=39,
                      fill=MINT, foreground=BUTTON_TEXT, keyboard_guard=self.key_guard).pack(side="bottom", pady=(14, 0))
        table_frame.pack(fill="both", expand=True)

    def export_history(self):
        if not self.model.completed_records:
            messagebox.showinfo("做题记录", "还没有完成的题目。完成一题后点“下一题”即可记录。", parent=self.dialog)
            return
        path = filedialog.asksaveasfilename(parent=self.dialog, title="导出做题记录",
                                           defaultextension=".csv", filetypes=[("CSV 文件", "*.csv")],
                                           initialfile=f"练习记录_{time.strftime('%Y%m%d_%H%M')}.csv")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(["题号", "用时（秒）", "用时", "目标（秒）", "状态"])
                for record in self.model.completed_records:
                    writer.writerow([record.question_number, round(record.elapsed_seconds, 2),
                                     format_record_time(record.elapsed_seconds), record.target_seconds,
                                     "按时" if record.on_target else "超时"])
        except OSError as error:
            messagebox.showerror("导出失败", f"无法保存文件：{error}", parent=self.dialog)
            return
        messagebox.showinfo("已导出", "做题记录已保存。", parent=self.dialog)

    def close(self):
        self.model.pause()
        self.save_state()
        if self.taskbar_hwnd is not None:
            try:
                taskbar_win.clear_window(self.taskbar_hwnd)
            except taskbar_win.TaskbarIntegrationError:
                logging.exception("Could not clear taskbar properties")
        self.root.destroy()
        if self.window_icons is not None:
            self.window_icons.close()

    def callback_error(self, exception, value, traceback):
        logging.error("UI callback error", exc_info=(exception, value, traceback))
        messagebox.showerror(APP_TITLE, "刚才的操作没有完成，请重新尝试。计时记录会保留。", parent=self.root)


def acquire_instance():
    if sys.platform != "win32":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, "Local\\CFAPracticeTimerDesktopV1")
    if ctypes.get_last_error() == 183:
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user.FindWindowW.restype = ctypes.c_void_p
        window = user.FindWindowW(None, APP_TITLE)
        if window:
            user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            user.ShowWindow(window, 9)
            user.SetForegroundWindow(window)
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(handle)
        sys.exit(0)
    return handle


def main():
    if sys.platform == "win32":
        try:
            taskbar_win.set_process_app_id(APP_ID)
        except taskbar_win.TaskbarIntegrationError:
            logging.exception("Could not configure process app identity")
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    instance = acquire_instance()
    root = tk.Tk()
    root.withdraw()
    TimerApp(root)
    root.deiconify()
    root.mainloop()
    if instance:
        kernel = ctypes.WinDLL("kernel32")
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(instance)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Application startup failed")
        ctypes.windll.user32.MessageBoxW(None, "计时器启动失败，请查看程序文件夹或重新启动。", APP_TITLE, 0x10)
