#!/usr/bin/env python3
"""End-to-end acoustic test: phone tone -> capsule -> preamp -> filter -> ESP32 ADC -> Python.

Needs acoustic_hil.ino on the board, the capsule connected, and NO jumpers from GPIO25 (nothing is injected electrically).
A phone plays pure tones; the script asks you to put the phone at set distances and captures the signal.

It checks two things against physics that need no calibration:
  1. Level against distance: for a small source in free field the level falls 6.02 dB per doubling of distance.
  2. Frequency read-out: the measured tone frequency against the frequency the phone was set to.

The pass criteria below are fixed BEFORE any data is taken and are printed and saved with every run.

    python3 acoustic_test.py --port /dev/cu.usbserial-0001 --baud 115200

Numbers from --port mock are SIMULATED and must never be used as measurements.
"""
import argparse, atexit, csv, datetime as dt, json, os, sys, time
import numpy as np
import hil_sweep as H

# ------------------------------------------------------------------ pre-registered criteria (do not edit after taking data)
CRITERIA = dict(
    gain=2,
    distances_cm=[10, 20, 40, 10],            # the last 10 cm is a repeat to expose drift
    captures_per_distance=3,
    level_tone_hz=1000.0,
    freq_tones_hz=[500.0, 2000.0, 4000.0],    # measured at 10 cm; 1000 Hz comes from the distance runs
    freq_captures=2,
    n_samples_level=8192,
    n_samples_freq=16384,
    expected_db_per_doubling=-20.0 * np.log10(2.0),
    slope_tolerance_db=1.5,                   # PASS if |measured slope - expected| <= this
    repeat_tolerance_db=1.0,                  # the repeat at 10 cm must agree within this, else INCONCLUSIVE
    freq_tolerance_fraction=0.002,            # PASS if |f_measured - f_set| <= 0.2 % of f_set
    min_snr_db=20.0,                          # every tone must be this far above the silence floor
    level_ok_counts=(40.0, 450.0),            # tone amplitude window for the level check
    mean_ok_counts=(1500.0, 1900.0),          # ADC mean when the capsule bias is connected and healthy
)


# ------------------------------------------------------------------ simulated board for testing the flow without hardware
class SimDevice:
    """SIMULATED board: returns made-up samples (pairs swapped) for a made-up scene. Never a measurement."""
    retries = 0

    def __init__(self):
        self.scene = dict(f=1000.0, d=10.0, on=False)
        self.rng = np.random.default_rng(7)
        self.fw = "SIMULATED"

    def connect(self, baud):
        return self.fw

    def cmd(self, line, expect="OK", timeout=1.0):
        return "OK"

    def restore(self):
        pass

    def capture(self, n):
        t = np.arange(n) / H.FS
        x = 1713.0 + self.rng.normal(0, 1.2, n)
        if self.scene["on"]:
            exp = float(os.environ.get("MOCK_DISTANCE_EXPONENT", "1"))    # 1 = inverse distance; test hook for failure cases
            amp = 80.0 * (10.0 / self.scene["d"]) ** exp * (1.0 + self.rng.normal(0, 0.01))
            x = x + amp * np.sin(2 * np.pi * self.scene["f"] * 1.0004 * t)
        x = np.round(x).astype(np.uint16)
        y = x.copy()
        y[0::2], y[1::2] = x[1::2], x[0::2]
        return y, int(y.min()), int(y.max())


# ------------------------------------------------------------------ helpers
def measure(dev, n, f, mode):
    raw, mn, mx = dev.capture(n)
    fixed = H.swap_back(raw, mode)
    t = H.tone_amplitude(fixed, f)
    return dict(amp=t["amp"], f_peak=t["f_peak"], snr_db=t["snr_db"], mean=float(np.mean(fixed)), rms=float(np.std(fixed)), raw_min=mn, raw_max=mx, fixed=fixed)


def step(dev, sim, args, text, scene=None):
    """Tell the user what to do and wait for Enter (or set the scene in the simulator)."""
    if sim:
        dev.scene.update(scene or {})
        print(f"  [sim] {text}")
        return
    input(f"\n>>> {text}\n    Press Enter when ready: ")
    time.sleep(args.settle_s)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial port or 'mock'")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--settle-s", type=float, default=1.0, help="wait after you press Enter before capturing")
    ap.add_argument("--outdir", default="acoustic_results")
    args = ap.parse_args()
    C = CRITERIA
    sim = args.port == "mock"
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = ("MOCK_" if sim else "") + stamp
    os.makedirs(args.outdir, exist_ok=True)
    if sim:
        print("=" * 78 + "\n SIMULATED BOARD. Nothing below is a measurement.\n" + "=" * 78)
        dev = SimDevice()
    else:
        import serial
        dev = H.Device(serial.Serial(args.port, 115200, timeout=1), print)
        atexit.register(dev.restore)
    print(f"Acoustic end-to-end test {run_id}")
    print("Pass criteria, fixed before the data was taken:")
    print(f"  level falls {-C['expected_db_per_doubling']:.2f} dB per doubling of distance, slope within +/-{C['slope_tolerance_db']} dB per doubling; "
          f"repeat at 10 cm within {C['repeat_tolerance_db']} dB")
    print(f"  measured frequency within {C['freq_tolerance_fraction'] * 100:.1f} % of the set frequency; every tone at least {C['min_snr_db']:.0f} dB above the silence floor")
    fw = dev.connect(args.baud)
    print(f"  firmware {fw}")
    dev.cmd("STOP")
    dev.cmd(f"GAIN {C['gain']}")
    meta = dict(run_id=run_id, firmware=fw, source="MOCK" if sim else "HARDWARE", criteria={k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in C.items()}, notes=[])
    rows = []

    # ---- 1. level check and sample order, tone at 10 cm
    mode = None
    for attempt in range(3):
        step(dev, sim, args, f"Phone: play a pure {int(C['level_tone_hz'])} Hz tone. Speaker grille {C['distances_cm'][0]} cm from the capsule, facing it. "
                             "Choose a moderate volume and DO NOT change it for the rest of the test. Do not touch the board.", dict(f=C["level_tone_hz"], d=10.0, on=True))
        raw, _, _ = dev.capture(C["n_samples_level"])
        x = np.asarray(raw, float)
        r0, r1, r2 = H.roughness(x), H.roughness(H.swap_back(x, 1)), H.roughness(H.swap_back(x, 2))
        t = H.tone_amplitude(H.swap_back(x, 1), C["level_tone_hz"])
        print(f"  level check: tone {t['amp']:.1f} counts, ADC mean {np.mean(x):.0f}; roughness as delivered {r0:.2f}, swap(0,1) {r1:.2f}, swap(1,2) {r2:.2f}")
        if not (C["mean_ok_counts"][0] <= np.mean(x) <= C["mean_ok_counts"][1]):
            print(f"  WARNING: ADC mean {np.mean(x):.0f} is outside {C['mean_ok_counts']}: the capsule bias may not be connected (TP1 should read 2.6 to 2.9 V).")
        if t["amp"] < C["level_ok_counts"][0]:
            print(f"  tone too weak ({t['amp']:.1f} counts, want {C['level_ok_counts'][0]:.0f} to {C['level_ok_counts'][1]:.0f}): raise the phone volume, or move the phone closer; then continue.")
            continue
        if t["amp"] > C["level_ok_counts"][1]:
            print(f"  tone too strong ({t['amp']:.1f} counts, want up to {C['level_ok_counts'][1]:.0f}): lower the phone volume; then continue.")
            continue
        if not (r0 > 3.0 * min(r1, r2)):
            print("ABORTED: the sample order check could not be made (no clear zigzag in the delivered samples). Check the capsule and the tone.")
            sys.exit(2)
        mode = 1 if r1 <= r2 else 2
        if mode != 1:
            print(f"ABORTED: sample order is swap mode {mode}, expected 1. Not continuing.")
            sys.exit(2)
        print(f"  sample order check passed: swap mode {mode}")
        break
    if mode is None:
        print("ABORTED: could not get a tone between %d and %d counts after 3 tries." % C["level_ok_counts"])
        sys.exit(3)

    # ---- 2. silence floor
    step(dev, sim, args, "Phone: STOP the tone (silence). Leave the phone where it is.", dict(on=False))
    floor = []
    for _ in range(3):
        m = measure(dev, C["n_samples_level"], C["level_tone_hz"], mode)
        floor.append(m)
        rows.append(dict(kind="silence", distance_cm="", f_set=C["level_tone_hz"], rep=len(floor), amp=m["amp"], f_peak=m["f_peak"], snr_db=m["snr_db"], mean=m["mean"], rms=m["rms"]))
    floor_amp = float(np.mean([m["amp"] for m in floor]))
    floor_rms = float(np.mean([m["rms"] for m in floor]))
    print(f"  silence: ADC rms {floor_rms:.2f} counts, apparent 1 kHz amplitude {floor_amp:.2f} counts")

    # ---- 3. distance series
    lvl = {}
    seq = C["distances_cm"]
    for i, d in enumerate(seq):
        step(dev, sim, args, f"Phone: play the {int(C['level_tone_hz'])} Hz tone again (same volume). Speaker grille {d} cm from the capsule. Measure with a ruler.",
             dict(f=C["level_tone_hz"], d=float(d), on=True))
        for rep in range(C["captures_per_distance"]):
            m = measure(dev, C["n_samples_level"], C["level_tone_hz"], mode)
            rows.append(dict(kind="distance", distance_cm=d, f_set=C["level_tone_hz"], rep=rep + 1, order=i + 1, amp=m["amp"], f_peak=m["f_peak"], snr_db=m["snr_db"], mean=m["mean"], rms=m["rms"]))
            print(".", end="", flush=True)
        print(f" {d} cm done")
    dist_rows = [r for r in rows if r["kind"] == "distance"]

    # ---- 4. frequency series at 10 cm
    for f in C["freq_tones_hz"]:
        step(dev, sim, args, f"Phone: play a pure {int(f)} Hz tone (same volume). Speaker grille 10 cm from the capsule.", dict(f=f, d=10.0, on=True))
        for rep in range(C["freq_captures"]):
            m = measure(dev, C["n_samples_freq"], f, mode)
            rows.append(dict(kind="frequency", distance_cm=10, f_set=f, rep=rep + 1, amp=m["amp"], f_peak=m["f_peak"], snr_db=m["snr_db"], mean=m["mean"], rms=m["rms"]))
            print(".", end="", flush=True)
        print(f" {int(f)} Hz done")
    dev.cmd("STOP")

    # ---- analysis
    def db(a):
        return 20 * np.log10(a)
    first = [r for r in dist_rows if r.get("order") in (1, 2, 3)]
    ds = sorted({r["distance_cm"] for r in first})
    mean_amp = {d: float(np.mean([r["amp"] for r in first if r["distance_cm"] == d])) for d in ds}
    spread = {d: float(np.ptp([db(r["amp"]) for r in first if r["distance_cm"] == d])) for d in ds}
    x = np.log2(np.array([r["distance_cm"] for r in first], float))
    y = np.array([db(r["amp"]) for r in first])
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (slope * x + intercept)
    n = len(x)
    slope_se = float(np.sqrt(np.sum(resid ** 2) / (n - 2) / np.sum((x - x.mean()) ** 2))) if n > 2 else float("nan")
    rep10 = [r for r in dist_rows if r.get("order") == 4]
    d_repeat = db(np.mean([r["amp"] for r in rep10])) - db(mean_amp[10])
    exp = C["expected_db_per_doubling"]
    min_snr_all = min(r["snr_db"] for r in rows if r["kind"] != "silence")
    snr_ok = min_snr_all >= C["min_snr_db"]

    out = []
    out.append(f"\nLEVEL AGAINST DISTANCE (gain setting {C['gain']}, {int(C['level_tone_hz'])} Hz)")
    out.append("  distance_cm   mean_counts   level_re_10cm_dB   expected_dB   spread_of_3_dB")
    for d in ds:
        out.append(f"  {d:11d}   {mean_amp[d]:11.1f}   {db(mean_amp[d]) - db(mean_amp[10]):16.2f}   {exp * np.log2(d / 10.0):11.2f}   {spread[d]:14.2f}")
    out.append(f"  repeat at 10 cm: {d_repeat:+.2f} dB against the first 10 cm run (limit +/-{C['repeat_tolerance_db']})")
    out.append(f"  fitted slope {slope:.2f} dB per doubling (+/-{slope_se:.2f} fit), expected {exp:.2f}, difference {slope - exp:+.2f} (limit +/-{C['slope_tolerance_db']})")
    if abs(d_repeat) > C["repeat_tolerance_db"]:
        level_status = "INCONCLUSIVE (the 10 cm repeat disagrees: drift, phone volume or position)"
    elif abs(slope - exp) <= C["slope_tolerance_db"]:
        level_status = "PASS"
    else:
        level_status = "FAIL"
    out.append(f"  level against distance: {level_status}")

    out.append("\nFREQUENCY READ-OUT")
    out.append("  f_set_Hz   f_measured_Hz (mean of reps)   error_Hz   error_%    limit_%   status")
    freq_status = []
    fset = [C["level_tone_hz"]] + C["freq_tones_hz"]
    for f in sorted(fset):
        if f == C["level_tone_hz"]:
            sel = [r["f_peak"] for r in dist_rows if r.get("order") in (1, 4)]
        else:
            sel = [r["f_peak"] for r in rows if r["kind"] == "frequency" and r["f_set"] == f]
        fm = float(np.mean(sel))
        err = fm - f
        ok = abs(err) <= C["freq_tolerance_fraction"] * f
        freq_status.append(ok)
        out.append(f"  {f:8.0f}   {fm:27.2f}   {err:+8.2f}   {err / f * 100:+7.3f}   {C['freq_tolerance_fraction'] * 100:7.2f}   {'PASS' if ok else 'FAIL'}")
    out.append(f"\nSIGNAL ABOVE SILENCE: smallest tone-to-floor ratio {min_snr_all:.1f} dB (need {C['min_snr_db']:.0f}): {'PASS' if snr_ok else 'FAIL'}")
    overall = level_status == "PASS" and all(freq_status) and snr_ok
    out.append("\nEND-TO-END RESULT: " + ("PASS (all criteria met)" if overall else "NOT PASSED: " + "; ".join(
        s for s in [("level: " + level_status) if level_status != "PASS" else "", "frequency" if not all(freq_status) else "", "signal level" if not snr_ok else ""] if s)))
    if sim:
        out.append("(SIMULATED data: not a measurement)")
    text = "\n".join(out)
    print(text)

    # ---- outputs
    base = os.path.join(args.outdir, f"acoustic_{run_id}")
    with open(base + "_log.csv", "w", newline="") as fh:
        cols = ["kind", "distance_cm", "f_set", "rep", "order", "amp", "f_peak", "snr_db", "mean", "rms"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in {c: r.get(c, "") for c in cols}.items()})
    meta.update(result=text, level_status=level_status, overall_pass=bool(overall), slope_db_per_doubling=float(slope), slope_fit_se=slope_se, repeat_10cm_db=float(d_repeat),
                silence_rms_counts=floor_rms)
    json.dump(meta, open(base + "_meta.json", "w"), indent=1, default=float)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(11, 4))
        dd = np.array(ds, float)
        ax[0].plot(dd, [db(mean_amp[d]) - db(mean_amp[10]) for d in ds], "o", color="#d1495b", label="measured (mean of 3)")
        ax[0].plot([r["distance_cm"] for r in first], [db(r["amp"]) - db(mean_amp[10]) for r in first], ".", color="#d1495b", alpha=0.35)
        dl = np.geomspace(9, 44, 50)
        ax[0].plot(dl, exp * np.log2(dl / 10.0), "-", color="#2060a8", label="theory: -6.02 dB per doubling")
        ax[0].plot([10], [d_repeat], "s", color="#444", label="repeat at 10 cm")
        ax[0].set_xscale("log")
        ax[0].set_xticks(ds)
        ax[0].set_xticklabels([str(d) for d in ds])
        ax[0].set_xlabel("distance (cm)")
        ax[0].set_ylabel("level re 10 cm (dB)")
        ax[0].set_title(f"level against distance: {level_status.split(' ')[0]}")
        ax[0].grid(alpha=0.3)
        ax[0].legend(fontsize=8)
        fs_ = sorted(fset)
        errs = [np.mean([r["f_peak"] for r in dist_rows if r.get("order") in (1, 4)]) - 1000.0 if f == 1000.0 else
                np.mean([r["f_peak"] for r in rows if r["kind"] == "frequency" and r["f_set"] == f]) - f for f in fs_]
        ax[1].bar([str(int(f)) for f in fs_], [e / f * 100 for e, f in zip(errs, fs_)], color="#d1495b")
        ax[1].axhline(C["freq_tolerance_fraction"] * 100, color="#2060a8", ls="--")
        ax[1].axhline(-C["freq_tolerance_fraction"] * 100, color="#2060a8", ls="--", label="limit")
        ax[1].set_xlabel("tone set on the phone (Hz)")
        ax[1].set_ylabel("measured minus set (%)")
        ax[1].set_title("frequency read-out error")
        ax[1].legend(fontsize=8)
        ax[1].grid(alpha=0.3)
        fig.suptitle(f"End-to-end acoustic test {run_id}   source {'SIMULATED' if sim else 'HARDWARE'}   gain {C['gain']}")
        fig.tight_layout()
        fig.savefig(base + "_plot.png", dpi=130)
    except Exception as e:  # plotting must never lose the data
        print("plot not written:", e)
    print(f"\nfiles: {base}_log.csv\n       {base}_meta.json\n       {base}_plot.png")


if __name__ == "__main__":
    main()
