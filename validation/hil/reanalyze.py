#!/usr/bin/env python3
"""Re-evaluate saved HIL runs against the current hil_config.json, without touching any measured value.

Reads every hil_<id>_log.csv in a results folder (the per-capture measurements), rebuilds the per-point statistics,
and rewrites the _summary.csv, the _plot.png and the calc/band columns of the _log.csv using the calculation and tolerance
band in hil_config.json (regenerate that with make_config.py first).

    python3 reanalyze.py results/

Use this when the calculation changes. The measured amplitudes (amp_fix_counts, meas_db_rel_ref, raw_min, raw_max) are never modified.
"""
import csv, datetime as dt, glob, json, os, sys
from types import SimpleNamespace
import numpy as np
import hil_sweep as H


def main(folder, config=os.path.join(os.path.dirname(os.path.abspath(__file__)), "hil_config.json")):
    cfg = json.load(open(config))
    for log_path in sorted(glob.glob(os.path.join(folder, "hil_*_log.csv"))):
        rid = os.path.basename(log_path)[4:-8]
        meta_path = os.path.join(folder, f"hil_{rid}_meta.json")
        meta = json.load(open(meta_path))
        a = meta["args"]
        args = SimpleNamespace(known_rolloff_gains=a["known_rolloff_gains"], clip_hi=a["clip_hi"], clip_lo=a["clip_lo"],
                               ref_hz=a["ref_hz"], limited_counts=a["limited_counts"], limit_db=a["limit_db"])
        rows = list(csv.DictReader(open(log_path)))
        fieldnames = list(rows[0].keys())
        gains = sorted({int(r["gain_setting"]) for r in rows})
        all_rows = {}
        for g in gains:
            sel = [r for r in rows if int(r["gain_setting"]) == g]
            freqs = sorted({int(r["freq_nom_hz"]) for r in sel}, key=lambda f: (f != args.ref_hz, f))
            per_freq = {f: [] for f in freqs}
            for r in sel:
                per_freq[int(r["freq_nom_hz"])].append(dict(meas_db=float(r["meas_db_rel_ref"]), amp_fix=float(r["amp_fix_counts"]),
                                                            raw_min=int(r["raw_min"]), raw_max=int(r["raw_max"])))
            all_rows[g] = H.evaluate(g, freqs, per_freq, cfg, args)
        # log csv: refresh the calc / band columns only
        for r in rows:
            k = str(int(r["freq_nom_hz"]))
            lo, hi = cfg["band_db"].get(k, [None, None])
            r["calc_db"], r["band_lo_db"], r["band_hi_db"] = cfg["calc_db"].get(k), lo, hi
        with open(log_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        # summary csv
        first = rows[0]
        cols = ["timestamp", "run_id", "source", "firmware", "gain_setting", "freq_nom_hz", "reps", "mean_db", "min_db", "max_db", "spread_db", "std_db",
                "calc_db", "band_lo_db", "band_hi_db", "sim_db", "diff_vs_calc_db", "min_amp_counts", "status", "earlier_manual_db", "delta_vs_earlier_db"]
        with open(os.path.join(folder, f"hil_{rid}_summary.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            rnd = lambda v: "" if v is None else round(v, 3)
            for g in gains:
                for r in all_rows[g]:
                    w.writerow([first["timestamp"], rid, first["source"], first["firmware"], g, int(r["freq"]), r["reps"], rnd(r["mean"]), rnd(r["mn"]), rnd(r["mx"]),
                                rnd(r["spread"]), rnd(r["std"]), rnd(r["calc"]), rnd(r["lo"]), rnd(r["hi"]), rnd(r["sim"]), rnd(r["diff"]), round(r["min_amp"], 2),
                                r["status"], rnd(r["earlier"]), rnd(r["d_earlier"])])
        H.make_plot(all_rows, cfg, dict(stamp=rid, fw=first["firmware"], source=first["source"], known_gains=args.known_rolloff_gains),
                    os.path.join(folder, f"hil_{rid}_plot.png"))
        n = {s: sum(r["status"] == s for g in gains for r in all_rows[g]) for s in ("PASS", "FAIL", "LIMITED", "CLIPPED", "KNOWN-ROLLOFF", "NO-CALC")}
        print(rid, "gain", gains, {k: v for k, v in n.items() if v})


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1])
