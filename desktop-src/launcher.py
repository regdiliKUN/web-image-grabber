"""原生窗口入口：tkinter 主界面 + 本地 HTTP 服务（队列调度 + 自动分目录）"""

import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import traceback
import webbrowser

import image_grabber as core


class Win:
    def __init__(self, root):
        self.root = root
        self.last_id = 0
        cfg = core.load_config()

        root.title(core.APP_NAME + "  v1.2  ·  本地桌面版")
        root.geometry("980x780")
        root.minsize(860, 680)
        ico = os.path.join(core.app_dir(), "app.ico")
        if os.path.isfile(ico):
            try:
                root.iconbitmap(ico)
            except Exception:
                pass
        self._style()

        head = ttk.Frame(root, padding=(14, 10, 14, 4))
        head.pack(fill="x")
        ttk.Label(head, text=core.APP_NAME, font=("Microsoft YaHei", 14)).pack(side="left")
        self.srv = ttk.Label(head, text="", foreground="#9aa3af")
        self.srv.pack(side="right")

        f1 = ttk.LabelFrame(
            root,
            text=" 网页地址（每行一个，可批量；留空则等待浏览器扩展推送） ",
            padding=(10, 8),
        )
        f1.pack(fill="x", padx=14, pady=4)
        self.urls = tk.Text(
            f1,
            height=4,
            bg="#12151a",
            fg="#d8dde5",
            insertbackground="#fff",
            relief="flat",
            font=("Consolas", 10),
            wrap="none",
        )
        self.urls.pack(fill="x", pady=(0, 6))
        r = ttk.Frame(f1)
        r.pack(fill="x")
        ttk.Button(r, text="粘贴剪贴板网址", command=self.paste).pack(side="left")
        ttk.Button(
            r, text="清空网址", command=lambda: self.urls.delete("1.0", "end")
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            r,
            text="需要登录 / JS 渲染的网页，请用随包的浏览器扩展推送",
            foreground="#9aa3af",
        ).pack(side="right")

        f2 = ttk.LabelFrame(
            root,
            text=" 保存位置（真实本地绝对路径，可任意指定） ",
            padding=(10, 8),
        )
        f2.pack(fill="x", padx=14, pady=4)
        self.save = tk.StringVar(value=cfg.get("save_dir") or core.DEFAULT_DIR)
        ttk.Entry(f2, textvariable=self.save, font=("Consolas", 10)).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(f2, text="浏览选择…", command=self.pick).pack(
            side="left", padx=(8, 4)
        )
        ttk.Button(f2, text="打开该文件夹", command=self.open_dir).pack(side="left")

        f3 = ttk.LabelFrame(root, text=" 抓取选项 ", padding=(10, 8))
        f3.pack(fill="x", padx=14, pady=4)
        self.v_css = tk.BooleanVar(value=bool(cfg.get("css", True)))
        self.v_up = tk.BooleanVar(value=bool(cfg.get("upscale", True)))
        self.v_subdir = tk.BooleanVar(value=bool(cfg.get("auto_subdir", True)))
        self.v_unknown = tk.BooleanVar(value=bool(cfg.get("unknown", False)))
        self.v_ssl = tk.BooleanVar(value=bool(cfg.get("ignore_ssl", False)))
        for text, var in (
            ("CSS 背景与源码图片", self.v_css),
            ("尝试还原原图", self.v_up),
            ("按域名+日期建子文件夹", self.v_subdir),
            ("保留未知格式", self.v_unknown),
            ("忽略 HTTPS 证书", self.v_ssl),
        ):
            ttk.Checkbutton(f3, text=text, variable=var).pack(
                side="left", padx=(0, 12)
            )

        r2 = ttk.Frame(f3)
        r2.pack(fill="x", pady=(8, 0))
        ttk.Label(r2, text="最小宽度").pack(side="left")
        self.v_minw = tk.StringVar(value=str(cfg.get("min_w", "0")))
        ttk.Entry(r2, textvariable=self.v_minw, width=6).pack(side="left", padx=(4, 4))
        ttk.Label(r2, text="px   并发").pack(side="left")
        self.v_workers = tk.StringVar(value=str(cfg.get("workers", "6")))
        ttk.Entry(r2, textvariable=self.v_workers, width=4).pack(
            side="left", padx=(4, 10)
        )
        ttk.Label(r2, text="超时(秒)").pack(side="left")
        self.v_timeout = tk.StringVar(value=str(cfg.get("timeout", "20")))
        ttk.Entry(r2, textvariable=self.v_timeout, width=4).pack(
            side="left", padx=(4, 10)
        )
        ttk.Label(r2, text="Cookie（可选）").pack(side="left")
        self.v_cookie = tk.StringVar(value=str(cfg.get("cookie", "")))
        ttk.Entry(r2, textvariable=self.v_cookie).pack(
            side="left", fill="x", expand=True, padx=(4, 0)
        )

        f4 = ttk.Frame(root, padding=(14, 6))
        f4.pack(fill="x")
        ttk.Button(
            f4, text="加入抓取队列", style="Accent.TButton", command=self.start
        ).pack(side="left")
        ttk.Button(f4, text="停止全部", command=self.stop).pack(side="left", padx=(6, 0))
        ttk.Button(f4, text="清空日志", command=self.clear).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(f4, text="打开扩展文件夹", command=self.open_ext).pack(
            side="left", padx=(14, 0)
        )
        ttk.Button(f4, text="网页版界面", command=self.open_web).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(f4, text="使用说明", command=self.help).pack(
            side="left", padx=(6, 0)
        )
        self.status = ttk.Label(f4, text="就绪", foreground="#9aa3af")
        self.status.pack(side="right")

        pf = ttk.Frame(root, padding=(14, 0, 14, 6))
        pf.pack(fill="x")
        self.pb = ttk.Progressbar(pf, mode="determinate", maximum=100)
        self.pb.pack(fill="x")

        lf = ttk.LabelFrame(root, text=" 运行日志 ", padding=(8, 6))
        lf.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.logbox = tk.Text(
            lf,
            bg="#101216",
            fg="#c9d1dc",
            relief="flat",
            font=("Consolas", 10),
            wrap="word",
            state="disabled",
        )
        sb = ttk.Scrollbar(lf, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.logbox.pack(side="left", fill="both", expand=True)
        for k, c in (
            ("ok", "#4ec9a0"),
            ("err", "#f2777a"),
            ("warn", "#e5c07b"),
            ("info", "#9fb2c9"),
        ):
            self.logbox.tag_config(k, foreground=c)

        engine = "curl_cffi" if core.HAS_CFFI else "标准库"
        self.srv.configure(
            text="127.0.0.1:%d · %s" % (core.PORT, engine), foreground="#4ec9a0"
        )
        core.STATE["folder_picker"] = self._pick_from_worker
        self._tick()

    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        bg, fg, field = "#1b1e24", "#e6e8ec", "#242831"
        self.root.configure(bg=bg)
        st.configure(
            ".",
            background=bg,
            foreground=fg,
            fieldbackground=field,
            bordercolor="#39404a",
            lightcolor="#39404a",
            darkcolor="#39404a",
            troughcolor="#12151a",
        )
        st.configure("TLabel", background=bg, foreground=fg)
        st.configure("TFrame", background=bg)
        st.configure("TLabelframe", background=bg, bordercolor="#39404a")
        st.configure("TLabelframe.Label", background=bg, foreground="#9aa3af")
        st.configure(
            "TButton",
            background="#2e3540",
            foreground=fg,
            bordercolor="#454d59",
            padding=(10, 5),
        )
        st.map("TButton", background=[("active", "#3c4654")])
        st.configure("TCheckbutton", background=bg, foreground=fg)
        st.configure(
            "TEntry", fieldbackground=field, foreground=fg, insertcolor="#fff"
        )
        st.configure(
            "TProgressbar",
            background="#4c9aff",
            troughcolor="#12151a",
            bordercolor="#39404a",
        )
        st.configure(
            "Accent.TButton",
            background="#2c5f9e",
            foreground="#fff",
            bordercolor="#3b76c4",
        )
        st.map("Accent.TButton", background=[("active", "#356fb8")])

    def opts(self):
        return {
            "css": self.v_css.get(),
            "upscale": self.v_up.get(),
            "auto_subdir": self.v_subdir.get(),
            "unknown": self.v_unknown.get(),
            "ignore_ssl": self.v_ssl.get(),
            "min_w": self.v_minw.get().strip(),
            "workers": self.v_workers.get().strip(),
            "timeout": self.v_timeout.get().strip(),
            "cookie": self.v_cookie.get().strip(),
        }

    def persist(self):
        c = dict(self.opts())
        c["save_dir"] = self.save.get().strip()
        core.save_config(c)

    def paste(self):
        try:
            t = self.root.clipboard_get().strip()
            if t:
                cur = self.urls.get("1.0", "end").strip()
                self.urls.insert("end", ("\n" if cur else "") + t)
        except Exception:
            pass

    def pick(self):
        from tkinter import filedialog

        try:
            d = filedialog.askdirectory(
                parent=self.root,
                title="选择图片保存文件夹",
                initialdir=self.save.get().strip() or core.DEFAULT_DIR,
            )
            if d:
                d = os.path.normpath(d)
                self.save.set(d)
                with core.LOCK:
                    core.STATE["save_dir"] = d
                self.persist()
        except Exception as e:
            messagebox.showwarning(
                core.APP_NAME,
                "打开文件夹对话框失败，可直接粘贴路径：\n" + str(e),
            )

    def _pick_from_worker(self):
        from tkinter import filedialog

        ev = threading.Event()
        box = {}

        def do():
            try:
                d = filedialog.askdirectory(
                    parent=self.root,
                    title="选择图片保存文件夹",
                    initialdir=self.save.get().strip() or core.DEFAULT_DIR,
                )
                if d:
                    d = os.path.normpath(d)
                    self.save.set(d)
                    with core.LOCK:
                        core.STATE["save_dir"] = d
                    self.persist()
                box["d"] = d or ""
            except Exception as e:
                box["e"] = str(e)
            finally:
                ev.set()

        self.root.after(0, do)
        ev.wait(120)
        if "e" in box:
            raise RuntimeError(box["e"])
        return box.get("d", "")

    def open_dir(self):
        d = self.save.get().strip() or core.DEFAULT_DIR
        try:
            os.makedirs(d, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(d)
        except Exception as e:
            core.log("打开文件夹失败：" + str(e), "err")

    def start(self):
        urls = [
            x.strip()
            for x in self.urls.get("1.0", "end").splitlines()
            if x.strip()
        ]
        d = self.save.get().strip()
        if not d:
            messagebox.showwarning(core.APP_NAME, "请先设置保存位置。")
            return
        self.persist()
        if not urls:
            messagebox.showinfo(
                core.APP_NAME,
                "网址为空。已就绪等待浏览器扩展推送；\n也可填写网址后点「加入抓取队列」。",
            )
            return
        ok, _ = core.enqueue_job(urls, [], d, core.norm_opts(self.opts()))
        if not ok:
            messagebox.showwarning(core.APP_NAME, "创建保存文件夹失败，请检查路径。")

    def stop(self):
        core.stop_all_jobs()

    def clear(self):
        with core.LOCK:
            core.STATE["logs"] = []
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")
        self.last_id = 0

    def open_ext(self):
        d = os.path.join(core.app_dir(), "browser-extension")
        if not os.path.isdir(d):
            messagebox.showwarning(
                core.APP_NAME, "未找到 browser-extension 文件夹，请检查包体是否完整。"
            )
            return
        if hasattr(os, "startfile"):
            os.startfile(d)
        messagebox.showinfo(
            core.APP_NAME,
            "已打开扩展文件夹。安装：\n\n"
            "1. Chrome / Edge 打开 chrome://extensions（Edge 为 edge://extensions）\n"
            "2. 开启「开发者模式」\n"
            "3. 点「加载已解压的扩展程序」，选中刚打开的 browser-extension 文件夹。",
        )

    def open_web(self):
        webbrowser.open("http://127.0.0.1:%d/" % core.PORT)

    def help(self):
        messagebox.showinfo(
            core.APP_NAME,
            "两种用法：\n\n"
            "A. 直接抓网页：填网址（每行一个）→ 设置保存位置 → 加入抓取队列。\n"
            "B. 抓需要登录或 JS 渲染的网页：使用随包 browser-extension 扩展，\n"
            "   扫描后点「发送到桌面程序」即可自动排队写入。\n\n"
            "提示：如需绕过 Cloudflare 等强 TLS 指纹风控，可安装可选依赖：\n"
            "pip install curl_cffi",
        )

    def _tick(self):
        with core.LOCK:
            logs = list(core.STATE["logs"])
            done = core.STATE["done"]
            total = core.STATE["total"]
            running = core.STATE["running"]
            pending = core.STATE["pending_jobs"]

        fresh = [x for x in logs if x["id"] > self.last_id]
        if fresh:
            self.logbox.configure(state="normal")
            for x in fresh:
                self.logbox.insert(
                    "end",
                    "[%s] %s\n" % (x["t"], x["msg"]),
                    x.get("kind") or "info",
                )
                self.last_id = x["id"]
            self.logbox.see("end")
            self.logbox.configure(state="disabled")

        self.pb["value"] = (done / total * 100) if total else 0
        q_txt = f" (排队 {pending})" if pending else ""
        if total:
            self.status.configure(text=f"{done} / {total}{q_txt}")
        else:
            self.status.configure(text=("抓取中…" if running else "就绪") + q_txt)
        self.root.after(350, self._tick)


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cfg = core.load_config()
    with core.LOCK:
        save_dir = cfg.get("save_dir") or core.DEFAULT_DIR
        core.STATE["save_dir"] = save_dir
    os.makedirs(save_dir, exist_ok=True)
    core.ensure_worker()

    srv = core.Server(("127.0.0.1", core.PORT), core.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    core.log(
        "本地服务已启动，网址 http://127.0.0.1:%d/ ；扩展推送端口 /push" % core.PORT,
        "ok",
    )
    core.log("默认保存位置：" + save_dir, "info")

    root = tk.Tk()
    Win(root)
    try:
        root.mainloop()
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        with open(os.path.join(core.app_dir(), "error.log"), "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
