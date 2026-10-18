"""The desktop window (tkinter): a small always-on-top panel in the corner that shows what was
heard, what Custodian is doing and its answer; the confirmation dialog for PC actions; and
the sign-in prompt. tkinter must run on the main thread, so the other threads call the
thread-safe methods here, which hand the work to the window's event loop.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

# The app's warm palette (dark): charcoal, cream text, amber.
BG, FG, SOFT, ACCENT, DANGER = "#26201C", "#F7EEE6", "#C3B5A9", "#F2A65A", "#FF917C"
ON_ACCENT, RULE = "#2A1708", "#3A312B"
TITLE_FONT = "Segoe UI Semibold"  # titles: Windows' own friendly sans (the app's Nunito isn't installed system-wide)
CONFIRM_SECONDS = 60
ARM_MS = 800  # "Allow once" can't be clicked for this long after the dialog appears


class Window:
    def __init__(self, on_quit) -> None:  # noqa: ANN001
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Custodian")
        self._calls: queue.Queue = queue.Queue()
        self._on_quit = on_quit
        self._hide_job = None
        self._build_panel()
        self.root.after(50, self._drain)

    # Thread-safe entry points ------------------------------------------------------------------

    def call(self, fn, *args) -> None:  # noqa: ANN001
        self._calls.put((fn, args))

    def show(self, status: str, heard: str | None = None, reply: str | None = None, *, hide_after: float | None = None) -> None:
        self.call(self._show, status, heard, reply, hide_after)

    def announce(self, title: str, body: str | None, button: str | None = None, on_button=None, *,  # noqa: ANN001
                 hide_after: float = 25) -> None:
        """Something Custodian says first (a reminder, a meeting about to start), with an optional
        button - "Open the link" - that does the one thing offered."""
        self.call(self._announce, title, body, button, on_button, hide_after)

    def confirm(self, title: str, details: str) -> bool:
        """Ask before a PC action; blocks the calling thread. No answer in a minute = no."""
        answer: dict = {}
        done = threading.Event()
        self.call(self._confirm, title, details, answer, done)
        done.wait(CONFIRM_SECONDS + 5)
        return bool(answer.get("ok"))

    def ask_sign_in(self, server: str) -> tuple[str, str] | None:
        result: dict = {}
        done = threading.Event()
        self.call(self._sign_in, server, result, done)
        done.wait()
        return (result["server"], result["passcode"]) if result else None

    def quit(self) -> None:
        self.call(self.root.quit)

    def mainloop(self) -> None:
        self.root.mainloop()

    # Main-thread work ----------------------------------------------------------------------------

    def _drain(self) -> None:
        try:
            while True:
                fn, args = self._calls.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.root.after(50, self._drain)

    def _build_panel(self) -> None:
        panel = self.panel = tk.Toplevel(self.root)
        panel.withdraw()
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        panel.attributes("-alpha", 0.96)
        panel.configure(bg=BG, padx=16, pady=12, highlightthickness=1, highlightbackground=ACCENT)
        self.status = tk.Label(panel, text="", bg=BG, fg=ACCENT, font=(TITLE_FONT, 12, "bold"), anchor="w")
        self.status.pack(fill="x")
        self.heard = tk.Label(panel, text="", bg=BG, fg=SOFT, font=("Segoe UI", 10, "italic"), anchor="w", justify="left", wraplength=380)
        self.heard.pack(fill="x", pady=(4, 0))
        self.reply = tk.Label(panel, text="", bg=BG, fg=FG, font=("Segoe UI", 11), anchor="w", justify="left", wraplength=380)
        self.reply.pack(fill="x", pady=(6, 0))
        self.action = tk.Button(panel, text="", bg=ACCENT, fg=ON_ACCENT, relief="flat", padx=12, pady=4)  # packed when offered
        for widget in (panel, self.status, self.heard, self.reply):
            widget.bind("<Button-1>", lambda _e: panel.withdraw())

    def _place(self) -> None:
        self.panel.update_idletasks()
        w, h = 420, self.panel.winfo_reqheight()
        x = self.panel.winfo_screenwidth() - w - 24
        y = self.panel.winfo_screenheight() - h - 72
        self.panel.geometry(f"{w}x{h}+{x}+{y}")

    def _announce(self, title: str, body: str | None, button: str | None, on_button, hide_after: float) -> None:  # noqa: ANN001
        if button and on_button is not None:
            def pressed() -> None:
                self.action.pack_forget()
                self.panel.withdraw()
                on_button()

            self.action.config(text=button, command=pressed)
            self.action.pack(anchor="e", pady=(10, 0))
        self._show(title, "", body or "", hide_after, keep_action=True)

    def _show(self, status: str, heard: str | None, reply: str | None, hide_after: float | None, *,
              keep_action: bool = False) -> None:
        if not keep_action:
            self.action.pack_forget()  # anything new replaces an offer that wasn't taken
        self.status.config(text=status)
        if heard is not None:
            self.heard.config(text=f"“{heard}”" if heard else "")
        if reply is not None:
            self.reply.config(text=reply[:700])
        self._place()
        self.panel.deiconify()
        if self._hide_job:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        if hide_after:
            self._hide_job = self.root.after(int(hide_after * 1000), self.panel.withdraw)

    def _confirm(self, title: str, details: str, answer: dict, done: threading.Event) -> None:
        win = tk.Toplevel(self.root)
        win.title("Custodian wants to…")
        win.attributes("-topmost", True)
        win.configure(bg=BG, padx=20, pady=16)
        win.resizable(False, False)
        tk.Label(win, text=title, bg=BG, fg=ACCENT, font=(TITLE_FONT, 15, "bold"), anchor="w").pack(fill="x")
        tk.Frame(win, bg=RULE, height=1).pack(fill="x", pady=(6, 8))
        tk.Label(win, text=details, bg=BG, fg=SOFT, font=("Consolas", 10), justify="left", anchor="w",
                 wraplength=460).pack(fill="x", pady=(8, 12))
        countdown = tk.Label(win, text="", bg=BG, fg=SOFT, font=("Segoe UI", 9))
        countdown.pack(anchor="w")
        buttons = tk.Frame(win, bg=BG)
        buttons.pack(fill="x", pady=(12, 0))

        def finish(ok: bool) -> None:
            if not done.is_set():
                answer["ok"] = ok
                done.set()
            win.destroy()

        # Only a deliberate click allows: "Allow once" can't take keyboard focus (a stray Enter or
        # Space lands on Deny), and it's live only after ARM_MS, so a click already under way when
        # the dialog pops up can't land on it.
        allow = tk.Button(buttons, text="Allow once", command=lambda: finish(True), bg=ACCENT, fg=ON_ACCENT, relief="flat",
                          padx=14, pady=6, state="disabled", takefocus=0)
        allow.pack(side="right")
        deny = tk.Button(buttons, text="Deny", command=lambda: finish(False), bg=BG, fg=DANGER, relief="flat",
                         padx=14, pady=6)
        deny.pack(side="right", padx=8)
        deny.focus_set()
        win.after(ARM_MS, lambda: allow.winfo_exists() and allow.config(state="normal"))
        win.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        win.bind("<Escape>", lambda _e: finish(False))
        win.bind("<Return>", lambda _e: finish(False))
        remaining = [CONFIRM_SECONDS]

        def tick() -> None:
            if done.is_set():
                return
            if remaining[0] <= 0:
                finish(False)
                return
            countdown.config(text=f"Denied automatically in {remaining[0]} s")
            remaining[0] -= 1
            win.after(1000, tick)

        tick()
        win.update_idletasks()
        win.geometry(f"+{(win.winfo_screenwidth() - win.winfo_reqwidth()) // 2}+{(win.winfo_screenheight() - win.winfo_reqheight()) // 3}")
        win.lift()
        win.focus_force()

    def _sign_in(self, server: str, result: dict, done: threading.Event) -> None:
        win = tk.Toplevel(self.root)
        win.title("Sign in to Custodian")
        win.attributes("-topmost", True)
        win.configure(padx=20, pady=16)
        ttk.Label(win, text="Custodian server").pack(anchor="w")
        server_var = tk.StringVar(value=server)
        ttk.Entry(win, textvariable=server_var, width=40).pack(fill="x", pady=(2, 10))
        ttk.Label(win, text="Passcode").pack(anchor="w")
        pass_var = tk.StringVar()
        entry = ttk.Entry(win, textvariable=pass_var, show="•", width=40)
        entry.pack(fill="x", pady=(2, 12))

        def submit(_e=None) -> None:  # noqa: ANN001
            if pass_var.get():
                result.update(server=server_var.get().strip(), passcode=pass_var.get())
                done.set()
                win.destroy()

        def cancel() -> None:
            done.set()
            win.destroy()

        ttk.Button(win, text="Sign in", command=submit).pack(anchor="e")
        win.bind("<Return>", submit)
        win.protocol("WM_DELETE_WINDOW", cancel)
        entry.focus_force()
