#!/usr/bin/env python3
"""Plot the effect of the ESP32 ADC sample-pair swap on one saved HIL run.

Reads the per-capture log (amp_raw_counts = samples as delivered, amp_fix_counts = after swap-back),
normalises both to 1 kHz and plots them against the calculated filter response. No value is re-measured.

    python3 plot_swap_effect.py results/hil_20261122_225122_log.csv ../../docs/img/swap-fix-before-after.png
"""
import collections, csv, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main(log_path, out_path, gain="2"):
    rows = [r for r in csv.DictReader(open(log_path)) if r["gain_setting"] == gain]
    amp = collections.defaultdict(lambda: {"raw": [], "fix": []})
    calc, lo, hi = {}, {}, {}
    for r in rows:
        f = int(r["freq_nom_hz"])
        amp[f]["raw"].append(float(r["amp_raw_counts"]))
        amp[f]["fix"].append(float(r["amp_fix_counts"]))
        calc[f], lo[f], hi[f] = float(r["calc_db"]), float(r["band_lo_db"]), float(r["band_hi_db"])
    freqs = sorted(amp)
    db = lambda key, f: 20 * np.log10(np.mean(amp[f][key]) / np.mean(amp[1000][key]))
    raw = [db("raw", f) for f in freqs]
    fix = [db("fix", f) for f in freqs]
    c = [calc[f] for f in freqs]
    k = [f / 1000 for f in freqs]
    in_band = [i for i, f in enumerate(freqs) if 2000 <= f <= 25000]
    worst_raw = max(abs(raw[i] - c[i]) for i in in_band)
    worst_fix = max(abs(fix[i] - c[i]) for i in in_band)

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    ax.fill_between(k, [lo[f] for f in freqs], [hi[f] for f in freqs], color="#9ec5e8", alpha=0.5, label="Tolerance band")
    ax.plot(k, c, color="#333333", lw=1.5, label="Calculated")
    ax.plot(k, raw, "o--", color="#d1495b", label=f"As delivered by ESP32 ADC (worst {worst_raw:.1f} dB off, 2-25 kHz)")
    ax.plot(k, fix, "o-", color="#1f6fb2", label=f"After pair swap-back (worst {worst_fix:.2f} dB off, 2-25 kHz)")
    ax.set_xscale("log")
    ax.set_xticks([1, 2, 5, 10, 15, 20, 25, 30, 38, 55])
    ax.set_xticklabels(["1", "2", "5", "10", "15", "20", "25", "30", "38", "55"])
    ax.set_xlabel("Frequency (kHz)")
    ax.set_ylabel("Response relative to 1 kHz (dB)")
    ax.set_title("Filter response at gain 2: effect of the ADC sample-pair swap")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path)
    print(f"worst raw {worst_raw:.2f} dB, worst fixed {worst_fix:.2f} dB -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
