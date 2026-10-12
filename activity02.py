"""
Discrete-Time Sequence Operations Studio
----------------------------------------
Enter a sequence x[n], its starting index n0, and a shift amount k.
The app computes and plots:
    - Time Delay      y[n] = x[n - k]
    - Time Advance    y[n] = x[n + k]
    - Time Reversal   y[n] = x[-n]
    - Composite Method 1 (Shift then Fold)
    - Composite Method 2 (Fold then Shift)

Layout: control sidebar on the left, tabbed plots/results on the right.
Requires: numpy, matplotlib (tkinter ships with Python).
"""

import re
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# Sample (worked example): x[n] = {1,2,3,4,5}, n = -2..2, k = 2
SAMPLE_X = "1, 2, 3, 4, 5"
SAMPLE_N0 = "-2"
SAMPLE_K = "2"

COLORS = {
    "original": "#4c566a",
    "delay": "#2e86de",
    "advance": "#e67e22",
    "reversal": "#8e44ad",
    "method1": "#27ae60",
    "method2": "#c0392b",
}


# --------------------------------------------------------------------------
# DSP core
# --------------------------------------------------------------------------
def sorted_pair(n, y):
    """Return (n, y) sorted by ascending n so stem plots/prints are clean."""
    n = np.asarray(n)
    y = np.asarray(y)
    order = np.argsort(n)
    return n[order], y[order]


def compute_operations(x, n0, k):
    """Compute every operation. Only the index axis changes; values stay."""
    x = np.asarray(x, dtype=float)
    n = np.arange(n0, n0 + len(x))

    return {
        "original": sorted_pair(n, x),
        "delay": sorted_pair(n + k, x),                 # x[n-k]
        "advance": sorted_pair(n - k, x),               # x[n+k]
        "reversal": sorted_pair(-n, x),                 # x[-n]
        # Method 1: shift (advance by k) then fold
        "m1_shift": sorted_pair(n - k, x),              # v[n] = x[n+k]
        "m1_final": sorted_pair(-(n - k), x),           # y[n] = v[-n]
        # Method 2: fold then shift (delay by k)
        "m2_fold": sorted_pair(-n, x),                  # v[n] = x[-n]
        "m2_final": sorted_pair(-n + k, x),             # y[n] = v[n-k]
        "energy": float(np.sum(x ** 2)),
    }


def describe(name, n, y):
    """Readable text for one sequence."""
    samples = ", ".join(f"{name}[{int(a)}]={b:g}" for a, b in zip(n, y))
    values = ", ".join(f"{b:g}" for b in y)
    return (f"  {samples}\n"
            f"  => {name}[n] = {{{values}}}  for n = {int(n[0])}..{int(n[-1])}")


# --------------------------------------------------------------------------
# A reusable tab that holds a matplotlib figure
# --------------------------------------------------------------------------
class PlotTab(ttk.Frame):
    def __init__(self, parent, rows, cols, figsize=(8, 5)):
        super().__init__(parent)
        self.fig = Figure(figsize=figsize, dpi=100)
        self.axes = np.atleast_1d(self.fig.subplots(rows, cols)).flatten()
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def draw_stems(self, panels):
        """panels: list of (n, y, title, color) matching self.axes order."""
        for ax, (n, y, title, color) in zip(self.axes, panels):
            ax.clear()
            markerline, stemlines, baseline = ax.stem(n, y)
            plt_setp(markerline, color=color, markersize=7)
            plt_setp(stemlines, color=color, linewidth=1.8)
            plt_setp(baseline, visible=False)
            ax.set_title(title, fontsize=10, fontweight="bold")
            ax.set_xlabel("n", fontsize=9)
            ax.set_ylabel("value", fontsize=9)
            ax.grid(True, linestyle="--", alpha=0.4)
            ax.axhline(0, color="black", lw=0.8)
            ax.axvline(0, color="black", lw=0.8)
            lo, hi = int(np.min(n)) - 1, int(np.max(n)) + 1
            ax.set_xlim(lo, hi)
            ax.set_xticks(range(lo, hi + 1) if hi - lo <= 20 else ax.get_xticks())
            ymax = max(float(np.max(y)), 0)
            ymin = min(float(np.min(y)), 0)
            pad = 0.15 * max(ymax - ymin, 1)
            ax.set_ylim(ymin - pad, ymax + pad)
        self.fig.tight_layout(pad=2.0)
        self.canvas.draw()


def plt_setp(artist, **kwargs):
    """Small wrapper around matplotlib.pyplot.setp without importing pyplot."""
    from matplotlib.artist import setp
    setp(artist, **kwargs)


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------
class SequenceStudio:
    def __init__(self, root):
        self.root = root
        root.title("Sequence Operations Studio")
        root.geometry("1300x800")
        root.minsize(1000, 650)

        self._build_sidebar()
        self._build_tabs()
        self.compute_and_plot()          # show the sample on launch

    # ---------------- layout ----------------
    def _build_sidebar(self):
        side = ttk.Frame(self.root, padding=12)
        side.pack(side=tk.LEFT, fill=tk.Y)

        ttk.Label(side, text="Sequence Inputs",
                  font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 10))

        ttk.Label(side, text="x[n] values (comma or space separated)").pack(anchor="w")
        self.x_var = tk.StringVar(value=SAMPLE_X)
        ttk.Entry(side, textvariable=self.x_var, width=32).pack(anchor="w", pady=(2, 10))

        ttk.Label(side, text="Starting index n0").pack(anchor="w")
        self.n0_var = tk.StringVar(value=SAMPLE_N0)
        ttk.Entry(side, textvariable=self.n0_var, width=12).pack(anchor="w", pady=(2, 10))

        ttk.Label(side, text="Shift amount k").pack(anchor="w")
        self.k_var = tk.StringVar(value=SAMPLE_K)
        ttk.Entry(side, textvariable=self.k_var, width=12).pack(anchor="w", pady=(2, 14))

        ttk.Button(side, text="Compute & Plot",
                   command=self.compute_and_plot).pack(fill=tk.X, pady=3)
        ttk.Button(side, text="Reset Sample",
                   command=self.reset_sample).pack(fill=tk.X, pady=3)

        ttk.Separator(side).pack(fill=tk.X, pady=14)

        ttk.Label(side, text="Quick summary",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.summary = tk.Label(side, justify="left", anchor="w",
                                font=("Consolas", 10), wraplength=280)
        self.summary.pack(anchor="w", pady=6)

        # Enter key triggers compute
        self.root.bind("<Return>", lambda e: self.compute_and_plot())

    def _build_tabs(self):
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(0, 10), pady=10)

        self.tab_overview = PlotTab(self.tabs, 2, 3)
        self.tab_shift = PlotTab(self.tabs, 1, 3)
        self.tab_reverse = PlotTab(self.tabs, 1, 2)
        self.tab_composite = PlotTab(self.tabs, 2, 3)

        self.tabs.add(self.tab_overview, text="  Overview  ")
        self.tabs.add(self.tab_shift, text="  Delay / Advance  ")
        self.tabs.add(self.tab_reverse, text="  Reversal  ")
        self.tabs.add(self.tab_composite, text="  Composite Methods  ")

        text_frame = ttk.Frame(self.tabs)
        self.text = tk.Text(text_frame, wrap="word", font=("Consolas", 10), padx=10, pady=10)
        scroll = ttk.Scrollbar(text_frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(fill=tk.BOTH, expand=True)
        self.tabs.add(text_frame, text="  Numeric Results  ")

    # ---------------- actions ----------------
    def reset_sample(self):
        self.x_var.set(SAMPLE_X)
        self.n0_var.set(SAMPLE_N0)
        self.k_var.set(SAMPLE_K)
        self.compute_and_plot()

    def parse_inputs(self):
        raw = self.x_var.get().strip()
        tokens = [t for t in re.split(r"[,\s]+", raw) if t]
        if not tokens:
            raise ValueError("Enter at least one value for x[n].")
        try:
            x = [float(t) for t in tokens]
        except ValueError:
            raise ValueError("x[n] must contain only numbers, e.g. 1, 2, 3, 4, 5")
        try:
            n0 = int(self.n0_var.get().strip())
        except ValueError:
            raise ValueError("Starting index n0 must be an integer.")
        try:
            k = int(self.k_var.get().strip())
        except ValueError:
            raise ValueError("Shift amount k must be an integer.")
        return x, n0, k

    def compute_and_plot(self):
        try:
            x, n0, k = self.parse_inputs()
        except ValueError as err:
            messagebox.showerror("Invalid input", str(err))
            return

        r = compute_operations(x, n0, k)
        self.update_summary(r, x, n0, k)
        self.update_text(r, x, n0, k)
        self.update_plots(r, k)

    # ---------------- rendering ----------------
    def update_summary(self, r, x, n0, k):
        self.summary.config(text=(
            f"Length : {len(x)}\n"
            f"Range  : n = {n0}..{n0 + len(x) - 1}\n"
            f"Shift k: {k}\n"
            f"Energy : {r['energy']:g}"
        ))

    def update_text(self, r, x, n0, k):
        same = (np.array_equal(r["m1_final"][0], r["m2_final"][0])
                and np.allclose(r["m1_final"][1], r["m2_final"][1]))
        out = [
            f"ORIGINAL  x[n], n = {n0}..{n0 + len(x) - 1}",
            describe("x", *r["original"]), "",
            f"1) TIME DELAY   y[n] = x[n - {k}]",
            describe("y", *r["delay"]), "",
            f"2) TIME ADVANCE   y[n] = x[n + {k}]",
            describe("y", *r["advance"]), "",
            "3) TIME REVERSAL   y[n] = x[-n]",
            describe("y", *r["reversal"]), "",
            f"4) COMPOSITE METHOD 1 (Shift then Fold)",
            f"   Step a: v[n] = x[n + {k}]",
            describe("v", *r["m1_shift"]),
            "   Step b: y[n] = v[-n]",
            describe("y", *r["m1_final"]), "",
            f"5) COMPOSITE METHOD 2 (Fold then Shift)",
            "   Step a: v[n] = x[-n]",
            describe("v", *r["m2_fold"]),
            f"   Step b: y[n] = v[n - {k}]",
            describe("y", *r["m2_final"]), "",
            f"Both methods give y[n] = x[-n + {k}]: "
            f"{'results match' if same else 'MISMATCH, check inputs'}.", "",
            f"Energy: E = sum |x[n]|^2 = {r['energy']:g}",
        ]
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, "\n".join(out))

    def update_plots(self, r, k):
        o = r["original"]
        panels = {
            "original": (*o, "Original x[n]", COLORS["original"]),
            "delay": (*r["delay"], f"Delay: y[n] = x[n - {k}]", COLORS["delay"]),
            "advance": (*r["advance"], f"Advance: y[n] = x[n + {k}]", COLORS["advance"]),
            "reversal": (*r["reversal"], "Reversal: y[n] = x[-n]", COLORS["reversal"]),
            "m1": (*r["m1_final"], f"Shift then Fold: y[n] = x[-n + {k}]", COLORS["method1"]),
            "m2": (*r["m2_final"], f"Fold then Shift: y[n] = x[-n + {k}]", COLORS["method2"]),
        }

        self.tab_overview.draw_stems([
            panels["original"], panels["delay"], panels["advance"],
            panels["reversal"], panels["m1"], panels["m2"],
        ])
        self.tab_shift.draw_stems([panels["original"], panels["delay"], panels["advance"]])
        self.tab_reverse.draw_stems([panels["original"], panels["reversal"]])
        self.tab_composite.draw_stems([
            (*o, "Original x[n]", COLORS["original"]),
            (*r["m1_shift"], f"M1 step a: v[n] = x[n + {k}]", COLORS["advance"]),
            (*r["m1_final"], f"M1 step b: y[n] = v[-n]", COLORS["method1"]),
            (*o, "Original x[n]", COLORS["original"]),
            (*r["m2_fold"], "M2 step a: v[n] = x[-n]", COLORS["reversal"]),
            (*r["m2_final"], f"M2 step b: y[n] = v[n - {k}]", COLORS["method2"]),
        ])


def main():
    root = tk.Tk()
    SequenceStudio(root)
    root.mainloop()


if __name__ == "__main__":
    main()