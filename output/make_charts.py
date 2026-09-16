#!/usr/bin/env python3
"""Generate PDF: The Most Profitable Trading Strategy Ever Documented - Explained Simply"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import os

OUT_DIR = "/home/user/714Bot/output"
os.makedirs(OUT_DIR, exist_ok=True)

CHART1 = os.path.join(OUT_DIR, "_chart_growth.png")
CHART2 = os.path.join(OUT_DIR, "_chart_year2008.png")
CHART3 = os.path.join(OUT_DIR, "_chart_edges.png")

plt.rcParams["font.family"] = "DejaVu Sans"

# --- Chart 1: Growth of $100 (log scale) Medallion vs S&P 1988-2018 ---
years = list(range(1988, 2019))
# Medallion gross CAGR 63.3% per Cornell; S&P ~9.98%
med = [100 * (1.633 ** (y - 1988)) for y in years]
spx = [100 * (1.0998 ** (y - 1988)) for y in years]

fig, ax = plt.subplots(figsize=(8, 4.2))
ax.plot(years, med, linewidth=2.8, color="#0B7A5F", label="Medallion Fund (~63% /yr gross)")
ax.plot(years, spx, linewidth=2.2, color="#888888", linestyle="--", label="S&P 500 (~10% /yr)")
ax.set_yscale("log")
ax.set_title("Growth of $100: Medallion Fund vs S&P 500 (1988-2018, log scale)", fontsize=11, fontweight="bold", pad=12)
ax.set_xlabel("Year")
ax.set_ylabel("$ (log scale)")
ax.grid(True, which="both", alpha=0.25)
ax.legend(fontsize=9, loc="upper left")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
# annotate endpoints
ax.annotate("$398.7 Million", xy=(2018, med[-1]), xytext=(2008, med[-1]/30),
             fontsize=9, fontweight="bold", color="#0B7A5F",
             arrowprops=dict(arrowstyle="->", color="#0B7A5F"))
ax.annotate("$1,910", xy=(2018, spx[-1]), xytext=(2011, spx[-1]*8),
             fontsize=9, color="#555555",
             arrowprops=dict(arrowstyle="->", color="#555555"))
fig.tight_layout()
fig.savefig(CHART1, dpi=180, bbox_inches="tight")
plt.close(fig)

# --- Chart 2: 2008 crisis comparison ---
labels = ["Medallion\n2008", "S&P 500\n2008", "Medallion\n2000\n(dot-com)", "S&P 500\n2000-02\n(total)"]
values = [82, -37, 128, -49]  # approx: medallion 82% net 2008 (some say 152 gross), sp -37; medallion 2000 huge
colors = ["#0B7A5F" if v > 0 else "#C0392B" for v in values]
fig2, ax2 = plt.subplots(figsize=(8, 3.6))
bars = ax2.bar(labels, values, color=colors, edgecolor="white", linewidth=1.2)
ax2.axhline(0, color="black", linewidth=1)
ax2.set_title("When markets crashed, Medallion still won (annual returns %)", fontsize=11, fontweight="bold", pad=12)
ax2.set_ylabel("Return %")
for b, v in zip(bars, values):
    ax2.text(b.get_x() + b.get_width()/2, v + (3 if v > 0 else -7), f"{v:+}%", ha="center", fontsize=10, fontweight="bold",
             color="#0B7A5F" if v > 0 else "#C0392B")
ax2.set_ylim(min(values)-15, max(values)+20)
fig2.tight_layout()
fig2.savefig(CHART2, dpi=180, bbox_inches="tight")
plt.close(fig2)

# --- Chart 3: Tiny edge x many trades concept ---
fig3, ax3 = plt.subplots(figsize=(8, 3.4))
cats = ["Your trade\nedge", "Casino\nroulette edge", "Medallion\nper-trade edge"]
vals = [0.0, 2.7, 0.5]
cols = ["#C0392B", "#2C3E50", "#0B7A5F"]
bars3 = ax3.barh(cats, [2.7, 2.7, 0.5], color=["#EAEAEA", "#2C3E50", "#0B7A5F"], edgecolor="white")
ax3.set_title("You don't need a BIG edge — you need a SMALL edge, repeated thousands of times", fontsize=10, fontweight="bold", pad=12)
ax3.set_xlabel("Edge per bet / trade (%) — illustrative")
ax3.set_xlim(0, 4)
for i, v in enumerate([0, 2.7, 0.5]):
    label = "most humans: ~0% (or negative after fees)" if i == 0 else f"{v}%"
    ax3.text((vals[i] if vals[i]>0 else 0.1)+0.08, i, label, va="center", fontsize=9,
             color="#555555" if i==0 else "white" if i==1 else "#0B7A5F", fontweight="bold")
fig3.tight_layout()
fig3.savefig(CHART3, dpi=180, bbox_inches="tight")
plt.close(fig3)

print("Charts saved:", CHART1, CHART2, CHART3)
