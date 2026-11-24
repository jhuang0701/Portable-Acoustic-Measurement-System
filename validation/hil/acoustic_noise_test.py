#!/usr/bin/env python3
"""End-to-end acoustic level test with a band of NOISE instead of a pure tone.

Why this exists: the pure 1 kHz tone level test (acoustic_test.py) gave erratic level against distance on hardware
(three runs on 23 Nov 2026, none passed). At 1 kHz the wavelength is about 34 cm, so room reflections make a single
tone swing by many dB with a 1 cm change in position. A 1/3-octave band of noise averages that interference over
many frequencies, which is the usual way to measure level in a room. The criteria below are the same as before
(slope, 10 cm repeat, signal above the silence floor); only the stimulus, the level metric and the distances changed
(10/20/30/10 cm so that a 30 cm ruler is enough). The change was made AFTER the pure-tone runs and is recorded in
the repository.

Metric: noise-floor-corrected power in the 891-1122 Hz band (1/3 octave around 1 kHz) of the ADC signal, converted
to dB re the first 10 cm measurement. Expected slope: -6.02 dB per doubling of distance.

Phone: play white or pink noise (any noise app or video) at a fixed volume.

Run:  python3 acoustic_noise_test.py --port /dev/cu.usbserial-0001 --baud 115200
      python3 acoustic_noise_test.py --port mock          # simulated board, labelled SIMULATED, never a measurement
"""
import argparse, atexit, csv, datetime as dt, json, os, sys, time
import numpy as np
import hil_sweep as H

# ------------------------------------------------------------------ pre-registered criteria (do not edit after taking data)
CRITERIA = dict(
    gain=2,
    distances_cm=[10, 20, 30, 10],            # last 10 cm is a repeat to expose drift
    captures_per_distance=3,
    n_samples=16384,
    band_hz=(891.0, 1122.0),                  # 1/3 octave around 1 kHz
    expected_db_per_doubling=-20.0 * np.log10(2.0),
    slope_tolerance_db=1.5,
    repeat_tolerance_db=1.0,
    min_snr_db=20.0,                          # in-band power at every distance vs the silence floor in the same band
    level_ok_rms=(30.0, 300.0),               # in-band rms (counts) at 10 cm in the level check
    mean_ok_counts=(1500.0, 1900.0),
    max_capture_spread_db=3.0,                # captures at one position must agree within this, else the position is unstable
)


class SimDevice:
    """SIMULATED board: band-limited noise for a made-up scene (pairs swapped). Never a measurement."""
    retries = 0

    def __init__(self):
        self.scene = dict(d=10.0, on=False)
        self.rng = np.random.default_rng(11)
        self.fw = "SIMULATED"

    def connect(self, baud):
        return self.fw

    def cmd(self, line, expect="OK", timeout=1.0):
        return "OK"

    def restore(self):
        pass

    def capture(self, n):
        x = 1713.0 + self.rng.normal(0, 1.2, n)
        if self.scene["on"]:
            exp = float(os.environ.get("MOCK_DISTANCE_EXPONENT", "1"))
            nz = self.rng.normal(0, 1, n)
            f = np.fft.rfftfreq(n, 1 / H.FS)
            s = np.fft.rfft(nz)
            s[(f < 700) | (f > 1400)] = 0
            nz = np.fft.irfft(s, n)
            nz = nz / nz.std() * 80.0 * (10.0 / self.scene["d"]) ** exp * (1.0 + self.rng.normal(0, 0.02))
            x = x + nz
        x = np.round(x).astype(np.uint16)
        y = x.copy()
        y[0::2], y[1::2] = x[1::2], x[0::2]
        return y, int(y.min()), int(y.max())


def band_power(x, band, fs=H.FS):
    """One-sided power (counts^2) in the band, Hann window; Parseval-scaled so that it is the variance in the band."""
    n = len(x)
    w = np.hanning(n)
    sw2 = float(np.sum(w * w))
    spec = np.fft.rfft((x - np.mean(x)) * w)
    p = spec.real ** 2 + spec.imag ** 2
    f = np.fft.rfftfreq(n, 1 / fs)
    sel = (f >= band[0]) & (f <= band[1])
    return float(2.0 * np.sum(p[sel]) / (n * sw2)), int(np.sum(sel))


def measure(dev, C, mode):
    raw, mn, mx = dev.capture(C["n_samples"])
    fixed = H.swap_back(raw, mode).astype(float)
    p, nb = band_power(fixed, C["band_hz"])
    return dict(p=p, bins=nb, mean=float(np.mean(fixed)), rms=float(np.std(fixed)), raw_min=mn, raw_max=mx)


def step(dev, sim, args, text, scene=None):
    if sim:
        dev.scene.update(scene or {})
        print(f"  [sim] {text}")
        return
    input(f"\n>>> {text}\n    Press Enter when ready: ")
    time.sleep(args.settle_s)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--settle-s", type=float, default=1.0)
    ap.add_argument("--outdir", default="acoustic_results")
    args = ap.parse_args()
    C = CRITERIA
    sim = args.port == "mock"
    run_id = ("MOCK_" if sim else "") + "noise_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(args.outdir, exist_ok=True)
    if sim:
        print("=" * 78 + "\n SIMULATED BOARD. Nothing below is a measurement.\n" + "=" * 78)
        dev = SimDevice()
    else:
        import serial
        dev = H.Device(serial.Serial(args.port, 115200, timeout=1), print)
        atexit.register(dev.restore)
    print(f"Acoustic noise-band level test {run_id}")
    print("Pass criteria, fixed before the data was taken:")
    print(f"  in-band ({C['band_hz'][0]:.0f}-{C['band_hz'][1]:.0f} Hz) level falls {-C['expected_db_per_doubling']:.2f} dB per doubling of distance, slope within +/-{C['slope_tolerance_db']} dB; "
          f"repeat at 10 cm within {C['repeat_tolerance_db']} dB; every distance at least {C['min_snr_db']:.0f} dB above the silence floor in the band")
    fw = dev.connect(args.baud)
    print(f"  firmware {fw}")
    dev.cmd("STOP")
    dev.cmd(f"GAIN {C['gain']}")
    meta = dict(run_id=run_id, test="noise-band level", firmware=fw, source="MOCK" if sim else "HARDWARE",
                criteria={k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in C.items()}, notes=[])
    rows = []

    # ---- 1. level check and sample order at 10 cm
    mode = None
    for attempt in range(3):
        step(dev, sim, args, f"Phone: play WHITE or PINK NOISE. Speaker grille {C['distances_cm'][0]} cm from the capsule, facing it. "
                             "Pick a moderate volume and DO NOT change it for the rest of the test. Do not touch the board.", dict(d=10.0, on=True))
        raw, mn, mx = dev.capture(C["n_samples"])
        x = np.asarray(raw, float)
        r0, r1, r2 = H.roughness(x), H.roughness(H.swap_back(x, 1)), H.roughness(H.swap_back(x, 2))
        p, _ = band_power(H.swap_back(x, 1).astype(float), C["band_hz"])
        rms_b = float(np.sqrt(p))
        print(f"  level check: in-band rms {rms_b:.1f} counts, ADC mean {np.mean(x):.0f}, raw min/max {mn}/{mx}; roughness as delivered {r0:.2f}, swap(0,1) {r1:.2f}, swap(1,2) {r2:.2f}")
        if not (C["mean_ok_counts"][0] <= np.mean(x) <= C["mean_ok_counts"][1]):
            print(f"  WARNING: ADC mean {np.mean(x):.0f} is outside {C['mean_ok_counts']}: capsule bias may not be connected (TP1 should read about 2.4 to 2.9 V).")
        if mn < 50 or mx > 4045:
            print("  signal is clipping: lower the phone volume; then continue.")
            continue
        if rms_b < C["level_ok_rms"][0]:
            print(f"  noise too weak (in-band rms {rms_b:.1f}, want {C['level_ok_rms'][0]:.0f} to {C['level_ok_rms'][1]:.0f}): raise the phone volume; then continue.")
            continue
        if rms_b > C["level_ok_rms"][1]:
            print(f"  noise too strong (in-band rms {rms_b:.1f}, want up to {C['level_ok_rms'][1]:.0f}): lower the phone volume; then continue.")
            continue
        if not (r0 > 3.0 * min(r1, r2)):
            print("ABORTED: the sample order check could not be made. Check the capsule and the noise source.")
            sys.exit(2)
        mode = 1 if r1 <= r2 else 2
        if mode != 1:
            print(f"ABORTED: sample order is swap mode {mode}, expected 1. Not continuing.")
            sys.exit(2)
        print(f"  sample order check passed: swap mode {mode}")
        break
    if mode is None:
        print("ABORTED: could not get an in-band level between %d and %d counts rms after 3 tries." % C["level_ok_rms"])
        sys.exit(3)

    # ---- 2. silence floor in the same band
    step(dev, sim, args, "Phone: STOP the noise (silence). Leave the phone where it is.", dict(on=False))
    floor = []
    for _ in range(3):
        m = measure(dev, C, mode)
        floor.append(m)
        rows.append(dict(kind="silence", distance_cm="", rep=len(floor), order=0, band_power=m["p"], mean=m["mean"], rms=m["rms"]))
    p_floor = float(np.mean([m["p"] for m in floor]))
    print(f"  silence: in-band rms {np.sqrt(p_floor):.2f} counts")

    # ---- 3. distance series
    for i, d in enumerate(C["distances_cm"]):
        step(dev, sim, args, f"Phone: play the noise again (same volume). Speaker grille {d} cm from the capsule opening. Mark and measure with the ruler; keep the aim identical.",
             dict(d=float(d), on=True))
        for rep in range(C["captures_per_distance"]):
            m = measure(dev, C, mode)
            rows.append(dict(kind="distance", distance_cm=d, rep=rep + 1, order=i + 1, band_power=m["p"], mean=m["mean"], rms=m["rms"]))
            print(".", end="", flush=True)
        print(f" {d} cm done")
    dev.cmd("STOP")

    # ---- analysis
    dist = [r for r in rows if r["kind"] == "distance"]
    first = [r for r in dist if r["order"] in (1, 2, 3)]
    rep10 = [r for r in dist if r["order"] == 4]

    def lvl_db(rs):
        return 10 * np.log10(max(float(np.mean([r["band_power"] for r in rs])) - p_floor, 1e-12))

    ds = sorted({r["distance_cm"] for r in first})
    L = {d: lvl_db([r for r in first if r["distance_cm"] == d]) for d in ds}
    spread = {d: float(np.ptp([10 * np.log10(max(r["band_power"] - p_floor, 1e-12)) for r in first if r["distance_cm"] == d])) for d in ds}
    x = np.log2(np.array([r["distance_cm"] for r in first], float))
    y = np.array([10 * np.log10(max(r["band_power"] - p_floor, 1e-12)) for r in first])
    slope, icpt = np.polyfit(x, y, 1)
    resid = y - (slope * x + icpt)
    n = len(x)
    slope_se = float(np.sqrt(np.sum(resid ** 2) / (n - 2) / np.sum((x - x.mean()) ** 2))) if n > 2 else float("nan")
    d_repeat = lvl_db(rep10) - L[10]
    exp = C["expected_db_per_doubling"]
    snr = {d: 10 * np.log10(np.mean([r["band_power"] for r in dist if r["distance_cm"] == d]) / p_floor) for d in sorted({r["distance_cm"] for r in dist})}
    min_snr = min(snr.values())
    snr_ok = min_snr >= C["min_snr_db"]
    max_spread = max(spread.values())
    spread_ok = max_spread <= C["max_capture_spread_db"]

    out = [f"\nNOISE-BAND LEVEL AGAINST DISTANCE (gain {C['gain']}, band {C['band_hz'][0]:.0f}-{C['band_hz'][1]:.0f} Hz)",
           "  distance_cm   in-band_rms_counts   level_re_10cm_dB   expected_dB   spread_of_3_dB"]
    p10 = np.mean([r["band_power"] for r in first if r["distance_cm"] == 10]) - p_floor
    for d in ds:
        pr = np.mean([r["band_power"] for r in first if r["distance_cm"] == d]) - p_floor
        out.append(f"  {d:11d}   {np.sqrt(max(pr, 0)):18.1f}   {L[d] - L[10]:16.2f}   {exp * np.log2(d / 10.0):11.2f}   {spread[d]:14.2f}")
    out.append(f"  repeat at 10 cm: {d_repeat:+.2f} dB against the first 10 cm set (limit +/-{C['repeat_tolerance_db']})")
    out.append(f"  fitted slope {slope:.2f} dB per doubling (+/-{slope_se:.2f} fit), expected {exp:.2f}, difference {slope - exp:+.2f} (limit +/-{C['slope_tolerance_db']})")
    if abs(d_repeat) > C["repeat_tolerance_db"] or not spread_ok:
        status = "INCONCLUSIVE (the 10 cm repeat or the captures at one position disagree: drift, volume or position)"
    elif abs(slope - exp) <= C["slope_tolerance_db"]:
        status = "PASS"
    else:
        status = "FAIL"
    out.append(f"  largest spread of 3 captures at one position: {max_spread:.2f} dB (limit {C['max_capture_spread_db']})")
    out.append(f"  level against distance: {status}")
    out.append(f"\nSIGNAL ABOVE SILENCE (in band): smallest ratio {min_snr:.1f} dB (need {C['min_snr_db']:.0f}): {'PASS' if snr_ok else 'FAIL'}")
    overall = status == "PASS" and snr_ok
    out.append("\nNOISE-BAND LEVEL RESULT: " + ("PASS (all criteria met)" if overall else "NOT PASSED: " + "; ".join(
        s for s in [("level: " + status) if status != "PASS" else "", "signal level" if not snr_ok else ""] if s)))
    if sim:
        out.append("(SIMULATED data: not a measurement)")
    text = "\n".join(out)
    print(text)

    base = os.path.join(args.outdir, f"acoustic_{run_id}")
    with open(base + "_log.csv", "w", newline="") as fh:
        cols = ["kind", "distance_cm", "rep", "order", "band_power", "mean", "rms"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in {c: r.get(c, "") for c in cols}.items()})
    meta.update(result=text, level_status=status, overall_pass=bool(overall), slope_db_per_doubling=float(slope), slope_fit_se=slope_se,
                repeat_10cm_db=float(d_repeat), silence_band_rms_counts=float(np.sqrt(p_floor)), min_snr_db=float(min_snr))
    json.dump(meta, open(base + "_meta.json", "w"), indent=1, default=float)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6, 4))
        dd = np.array(ds, float)
        ax.plot(dd, [L[d] - L[10] for d in ds], "o", color="#d1495b", label="measured (mean of 3)")
        dl = np.geomspace(9, 33, 50)
        ax.plot(dl, exp * np.log2(dl / 10.0), "-", color="#2060a8", label="theory: -6.02 dB per doubling")
        ax.plot([10], [d_repeat], "s", color="#444", label="repeat at 10 cm")
        ax.set_xscale("log")
        ax.set_xticks(ds)
        ax.set_xticklabels([str(d) for d in ds])
        ax.set_xlabel("distance (cm)")
        ax.set_ylabel("in-band level re 10 cm (dB)")
        ax.set_title(f"noise-band level against distance: {status.split(' ')[0]}")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        fig.suptitle(f"{run_id}   source {'SIMULATED' if sim else 'HARDWARE'}   gain {C['gain']}", fontsize=8)
        fig.tight_layout()
        fig.savefig(base + "_plot.png", dpi=130)
    except Exception as e:
        print("plot not written:", e)
    print(f"\nfiles: {base}_log.csv\n       {base}_meta.json\n       {base}_plot.png")


if __name__ == "__main__":
    main()
