#!/usr/bin/env python3
"""Hardware-in-the-loop sweep of the acoustic front end (anti-alias filter response).

Talks to acoustic_hil.ino over USB serial, sets a tone, captures raw ADC samples, undoes the ESP32 sample-pair swap,
measures the amplitude (Hann window, power summed over the peak bin +/-3, noise removed), normalises to 1 kHz and compares
every point with the calculated response and the Monte Carlo tolerance band in hil_config.json.

    python3 hil_sweep.py --port /dev/cu.usbserial-0001

See README.md. Numbers from --port mock are SIMULATED and must never be used as measurements.
"""
import argparse, atexit, csv, datetime as dt, json, os, struct, sys, time
import numpy as np

FS = 80000.0          # ADC sample rate, Hz (measured: the true rate is exactly 80 kHz)
BANDS = 3             # peak bin +/- 3 bins


# ----------------------------------------------------------------------------------------------- DSP
def swap_back(raw, mode):
    """Undo the delivered pair swap. mode 1: swap (0,1)(2,3)...; mode 2: swap (1,2)(3,4)...; mode 0: unchanged."""
    x = np.asarray(raw, dtype=float)
    y = x.copy()
    if mode == 1:
        y[0::2], y[1::2] = x[1::2], x[0::2]
    elif mode == 2:
        y[1:-2:2], y[2:-1:2] = x[2:-1:2], x[1:-2:2]
    return y


def roughness(x):
    d2 = x[2:] - 2.0 * x[1:-1] + x[:-2]
    return float(np.sqrt(np.mean(d2 * d2)))


def tone_amplitude(x, f_nom, fs=FS):
    """Peak amplitude (counts) of the tone near f_nom (aliasing included).

    A = sqrt(4 E / (N sum w^2)), E = noise-corrected power summed over the peak bin +/-3 (Hann window). For a Hann window
    sum w^2 = 3N/8, which gives A = (2/N) sqrt(8/3 E), plus removal of the noise power in those 7 bins.
    Returns dict(amp, f_peak, mirror, snr_db)."""
    n = len(x)
    w = np.hanning(n)
    sw2 = float(np.sum(w * w))
    xs = (x - np.mean(x)) * w
    spec = np.fft.rfft(xs)
    p = spec.real ** 2 + spec.imag ** 2
    mag = np.sqrt(p)
    bw = fs / n
    a = f_nom % fs
    if a > fs / 2:
        a = fs - a
    hw = max(0.1 * f_nom, 400.0)
    klo = int(np.ceil(max(a - hw, 100.0) / bw))
    khi = int(np.floor(min(a + hw, fs / 2 - 100.0) / bw))
    kp = klo + int(np.argmax(mag[klo:khi + 1]))
    m0, mm, mpl = mag[kp], mag[kp - 1], mag[kp + 1]
    d = float(np.clip(2.0 * (mpl - mm) / (mm + 2.0 * m0 + mpl), -0.5, 0.5))
    fa = (kp + d) * bw
    k0 = np.floor(f_nom / fs + 0.5)
    diff = f_nom - k0 * fs
    f_peak = k0 * fs + fa if diff >= 0 else k0 * fs - fa
    ki = int(np.floor((fs / 2 - fa) / bw + 0.5))            # bin of the mirror tone
    ks = np.arange(klo, khi + 1)
    sel = ks[(np.abs(ks - kp) > 4) & (np.abs(ks - ki) > 4)]
    pn = float(np.mean(p[sel])) if len(sel) else 0.0       # mean noise power per bin
    snr = float(20 * np.log10(mag[kp] / np.mean(mag[sel]))) if len(sel) and np.mean(mag[sel]) > 0 else 0.0

    def band(kc):
        lo, hi = max(kc - BANDS, 1), min(kc + BANDS, n // 2 - 1)
        return float(np.sum(p[lo:hi + 1]))

    def to_amp(e):
        return float(np.sqrt(4.0 * e / (n * sw2))) if e > 0 else 0.0

    amp = to_amp(band(kp) - 7.0 * pn)
    mirror = to_amp(band(ki) - 7.0 * pn) if 5 <= ki < n // 2 - 5 else 0.0
    return dict(amp=amp, f_peak=float(f_peak), mirror=mirror, snr_db=snr)


def zigzag_check(raw, expect_mode):
    """Self-check of the sample order on a clean 1 kHz tone (gain 2, DAC sine). Returns a dict with ok, mode, message."""
    x = np.asarray(raw, dtype=float)
    n = len(x)
    res = dict(ok=False, mode=0, message="", diffs=[], amp=0.0, r0=0.0, r1=0.0, r2=0.0)
    if n % 2:
        res["message"] = "buffer length is odd, pair alignment cannot be trusted"
        return res
    m = float(np.mean(x))
    ic = -1
    for i in range(20, n - 20):
        if x[i - 2] < m and x[i + 2] > m:
            ic = i
            break
    if ic > 0:
        res["diffs"] = [int(x[k + 1] - x[k]) for k in range(ic - 3, ic + 7)]
    t = tone_amplitude(x, 1000.0)
    am, fa = t["amp"], t["f_peak"]
    dstep = am * 2.0 * np.pi * fa / FS
    r0, r1, r2 = roughness(x), roughness(swap_back(x, 1)), roughness(swap_back(x, 2))
    best = min(r1, r2)
    mode = (1 if r1 <= r2 else 2) if r0 > 3.0 * best else 0
    res.update(amp=am, r0=r0, r1=r1, r2=r2, mode=mode, dstep=dstep)
    if not (100.0 <= am <= 400.0 and min(r0, best) < 20.0):
        res["message"] = (f"NO CLEAN SIGNAL: tone amplitude {am:.1f} counts (expected 100 to 400), roughness {r0:.1f}/{r1:.1f}/{r2:.1f}. "
                          "Check the jumpers (GPIO25 -> 100k -> TP2, 100 ohm -> TP3) and the gain setting.")
        return res
    if mode != expect_mode:
        res["message"] = (f"SAMPLE ORDER NOT AS EXPECTED: detected swap mode {mode} (0 = none, 1 = pairs (0,1)(2,3), 2 = pairs (1,2)(3,4)), "
                          f"expected {expect_mode}. Differences {res['diffs']} vs predicted -{dstep:.1f}/+{3 * dstep:.1f}. Aborting; "
                          "pass --expect-swap-mode if the ADC driver behaviour really changed.")
        return res
    res["ok"] = True
    res["message"] = (f"sample order check passed: swap mode {mode}; roughness as delivered {r0:.2f}, swap(0,1) {r1:.2f}, swap(1,2) {r2:.2f}; "
                      f"diffs {res['diffs']} vs predicted -{dstep:.1f}/+{3 * dstep:.1f}")
    return res


# ----------------------------------------------------------------------------------------------- device
class Device:
    def __init__(self, ser, log=print):
        self.ser, self.log = ser, log
        self.fw = "?"
        self.retries = 0           # serial chunks or captures that had to be requested again
        self.restarts = 0          # times the board restarted by itself during the run
        self.state_gain = None     # last GAIN / source command, replayed after a restart
        self.state_src = None
        self.fast = False          # True while the board is switched to the fast baud rate
        self.fast_baud = 0

    def readline(self, timeout=2.0):
        self.ser.timeout = timeout
        return self.ser.readline().decode("ascii", "replace").strip()

    def wait_ready(self, seconds=8.0):
        t0 = time.time()
        while time.time() - t0 < seconds:
            line = self.readline(0.5)
            if line.startswith("READY"):
                return line
        # the board may already be running: ask
        self.ser.reset_input_buffer()
        self.ser.write(b"PING\n")
        if self.readline(1.0) == "PONG":
            return "READY (already running)"
        raise RuntimeError("no READY/PONG from the board: is acoustic_hil.ino uploaded and the Serial Monitor closed?")

    def _track(self, line):
        t = line.split()[0]
        if t == "GAIN":
            self.state_gain = line
        elif t in ("SWEEP", "DAC"):
            self.state_src = line
        elif t == "STOP":
            self.state_src = None

    def resync(self):
        """No valid reply: is the board alive at the current rate, or did it restart (back at 115200)? Returns 'alive', 'restarted' or None."""
        self.ser.reset_input_buffer()
        self.ser.write(b"PING\n")
        if self.readline(1.0) == "PONG":
            return "alive"
        if self.fast:
            self.ser.baudrate = 115200
            time.sleep(0.2)
            self.ser.reset_input_buffer()
            self.ser.write(b"PING\n")
            if self.readline(1.5) == "PONG":
                self.restarts += 1
                self.log("  WARNING: the board restarted by itself (it answered at 115200). Switching it back to the fast rate and continuing.")
                self.fast = False
                self.ser.write(f"BAUD {self.fast_baud}\n".encode("ascii"))
                self.readline(1.0)
                self.ser.baudrate = self.fast_baud
                self.fast = True
                time.sleep(0.25)
                self.ser.reset_input_buffer()
                self.ser.write(b"PING\n")
                if self.readline(1.0) == "PONG":
                    return "restarted"
        return None

    def replay(self):
        for line in (self.state_gain, self.state_src):
            if line:
                self.cmd(line, _retry=False)

    def cmd(self, line, expect="OK", timeout=1.0, _retry=True):
        self.ser.write((line + "\n").encode("ascii"))
        got = []
        for _ in range(6):
            r = self.readline(timeout)
            got.append(r)
            if r.startswith(expect):
                self._track(line)
                return r
            if r.startswith("ERR"):
                raise RuntimeError(f"board rejected '{line}': {r}")
        if _retry:
            state = self.resync()
            if state == "restarted":
                self.replay()
            if state:
                return self.cmd(line, expect, timeout, _retry=False)
        raise RuntimeError(f"no '{expect}' reply to '{line}'. Received instead: {got!r}")

    def connect(self, baud):
        self.log("  waiting for the board ...")
        try:
            self.log("  " + self.wait_ready(5.0))
        except RuntimeError:
            # an interrupted earlier run can leave the board at the fast baud rate (opening the port does not always reset it)
            if not baud or baud == 115200:
                raise
            found = None
            for rate in dict.fromkeys([baud, 921600, 460800, 230400]):
                self.ser.baudrate = rate
                time.sleep(0.2)
                self.ser.reset_input_buffer()
                self.ser.write(b"PING\n")
                if self.readline(1.0) == "PONG":
                    found = rate
                    break
            if not found:
                self.ser.baudrate = 115200
                raise RuntimeError("no answer from the board at 115200 or at the fast rates. Press the EN (reset) button on the board and run again, "
                                   "and check that acoustic_hil.ino is uploaded and the Serial Monitor is closed.")
            self.log(f"  board was still at {found} baud from an interrupted run: continuing")
            self.fast, self.fast_baud = True, found
        self.ser.reset_input_buffer()
        v = self.cmd("VER", "VER")
        self.fw = v.split()[1] if len(v.split()) > 1 else v
        if baud and baud != 115200 and not self.fast:
            self.cmd(f"BAUD {baud}", "OK BAUD")
            self.ser.baudrate = baud
            self.fast, self.fast_baud = True, baud
            time.sleep(0.25)
            self.ser.reset_input_buffer()
            self.ser.write(b"PING\n")
            if self.readline(1.0) != "PONG":
                raise RuntimeError(f"no answer after switching to {baud} baud: run again with --baud 115200")
        return self.fw

    def restore(self):
        """Stop the tone and put the board back to 115200 baud (runs at exit, also after Ctrl-C). Never raises."""
        try:
            self.ser.write(b"STOP\n")
            self.readline(0.5)
            if self.fast:
                self.ser.write(b"BAUD 115200\n")
                self.readline(0.5)
                time.sleep(0.1)
                self.ser.baudrate = 115200
                self.fast = False
        except Exception:
            pass

    CHUNK = 1024

    def get_chunk(self, k, want):
        """Fetch chunk k (want bytes). A chunk that arrives short or with a wrong byte sum is requested again."""
        for attempt in range(8):
            self.ser.reset_input_buffer()
            self.ser.write(f"GET {k}\n".encode("ascii"))
            hdr = ""
            for _ in range(3):
                hdr = self.readline(1.0)
                if hdr.startswith("CHUNK") or hdr.startswith("ERR"):
                    break
            if hdr.startswith("CHUNK"):
                _, kk, ln, sm = hdr.split()
                self.ser.timeout = 1.0
                data = self.ser.read(int(ln))
                if int(kk) == k and int(ln) == want and len(data) == want and sum(data) & 0xFFFF == int(sm):
                    return data
            self.retries += 1
            time.sleep(0.05)
        raise RuntimeError(f"chunk {k} could not be read after 8 tries: the serial link is losing data, run again with a lower --baud")

    def capture(self, n):
        """Capture n samples on the board and read them back; if the link fails, check whether the board restarted and try once more."""
        try:
            return self._capture(n)
        except RuntimeError:
            state = self.resync()
            if state == "restarted":
                self.replay()
            if state:
                return self._capture(n)
            raise

    def _capture(self, n):
        for attempt in range(3):
            self.ser.reset_input_buffer()
            self.ser.write(f"CAPTURE {n}\n".encode("ascii"))
            hdr = ""
            t_end = time.time() + 6.0
            while time.time() < t_end:
                hdr = self.readline(1.0)
                if hdr.startswith("DATA") or hdr.startswith("ERR"):
                    break
            if not hdr.startswith("DATA"):
                self.retries += 1
                raise RuntimeError("the board did not answer CAPTURE within 6 s (last line: %r)" % hdr)
            _, cnt, mn, mx, total = hdr.split()
            cnt = int(cnt)
            blob = bytearray()
            nbytes = 2 * cnt
            for k in range((nbytes + self.CHUNK - 1) // self.CHUNK):
                blob += self.get_chunk(k, min(self.CHUNK, nbytes - k * self.CHUNK))
            samples = np.frombuffer(bytes(blob), dtype="<u2")
            if int(samples.astype(np.uint64).sum() & 0xFFFF) == int(total):
                return samples.copy(), int(mn), int(mx)
            self.retries += 1
        raise RuntimeError("capture failed 3 times (checksum or header problems): check the USB cable, or run with a lower --baud")


# ----------------------------------------------------------------------------------------------- evaluation
def evaluate(gain, freqs, per_freq, cfg, args):
    """per_freq[f] = list of dicts (one per rep). Returns summary rows."""
    rows = []
    calc, band = cfg["calc_db"], cfg["band_db"]
    sim = cfg.get("sim_db", {})
    earlier = cfg.get("earlier_manual_db", {}).get(str(gain), {})
    known = gain in args.known_rolloff_gains
    for f in freqs:
        k = str(int(f))
        reps = per_freq[f]
        db = np.array([r["meas_db"] for r in reps])
        amps = np.array([r["amp_fix"] for r in reps])
        clipped = any(r["raw_max"] > args.clip_hi or r["raw_min"] < args.clip_lo for r in reps)
        mean = float(np.mean(db))
        c = calc.get(k)
        lo, hi = band.get(k, [None, None])
        diff = None if c is None else mean - c
        if f == args.ref_hz:
            status = "REF"
        elif clipped:
            status = "CLIPPED"
        elif float(np.min(amps)) < args.limited_counts:
            status = "LIMITED"
        elif c is None:
            status = "NO-CALC"
        else:
            inside = lo is not None and lo <= mean <= hi
            near = abs(mean - c) <= args.limit_db
            status = "PASS" if (inside or near) else "FAIL"
            if known and status == "FAIL":
                status = "KNOWN-ROLLOFF"
        e = earlier.get(k)
        rows.append(dict(gain=gain, freq=f, reps=len(reps), mean=mean, mn=float(np.min(db)), mx=float(np.max(db)),
                         spread=float(np.max(db) - np.min(db)), std=float(np.std(db, ddof=1)) if len(db) > 1 else 0.0,
                         calc=c, lo=lo, hi=hi, sim=sim.get(k), diff=diff, min_amp=float(np.min(amps)), status=status,
                         earlier=e, d_earlier=None if e is None else mean - e))
    return rows


def fmt(v, w=7, d=2):
    return " " * (w - 1) + "-" if v is None else f"{v:{w}.{d}f}"


def print_table(rows, gain, amp, args, log):
    log(f"\nGAIN SETTING {gain}  (tone amp code {amp}/512, {rows[0]['reps']} reps, PASS limit {args.limit_db:g} dB around calc, LIMITED below {args.limited_counts:g} counts)")
    log("  f_kHz    mean  spread     calc  band_lo  band_hi    diff  min_cnt  status")
    for r in rows:
        log(f"  {r['freq'] / 1000:5.1f} {fmt(r['mean'])} {fmt(r['spread'])} {fmt(r['calc'], 8)} {fmt(r['lo'], 8)} {fmt(r['hi'], 8)} {fmt(r['diff'])} {r['min_amp']:8.1f}  {r['status']}")
    counted = [r for r in rows if r["status"] in ("PASS", "FAIL")]
    n_pass = sum(r["status"] == "PASS" for r in counted)
    extra = {s: sum(r["status"] == s for r in rows) for s in ("LIMITED", "CLIPPED", "KNOWN-ROLLOFF", "NO-CALC")}
    extra = ", ".join(f"{v} {k}" for k, v in extra.items() if v)
    log(f"  -> {n_pass}/{len(counted)} PASS" + (f"; {extra}" if extra else ""))


def make_plot(all_rows, cfg, meta, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    gains = sorted(all_rows)
    fig, axes = plt.subplots(len(gains), 2, figsize=(13, 4.2 * len(gains)), squeeze=False)
    freqs = cfg["freqs_hz"]
    cal = [cfg["calc_db"][str(f)] for f in freqs]
    lo = [cfg["band_db"][str(f)][0] for f in freqs]
    hi = [cfg["band_db"][str(f)][1] for f in freqs]
    for gi, g in enumerate(gains):
        rows = all_rows[g]
        for ci, (xmax, ylim) in enumerate(((70000, (-32, 3)), (22000, (-3.0, 1.5)))):
            ax = axes[gi][ci]
            fine = np.array(freqs, dtype=float)
            ax.fill_between(fine / 1000, lo, hi, color="#cfe3f5", label="Monte Carlo band (R 1%, C 5%)", step=None)
            ax.plot(fine / 1000, cal, "-", color="#1f5fa8", lw=1.5, label="calculated (netlist)")
            sim = [(float(k) / 1000, v) for k, v in cfg.get("sim_db", {}).items() if float(k) <= xmax]
            if sim:
                ax.plot(*zip(*sim), "D", color="#7b3fa0", ms=6, label="ngspice (on file)")
            earlier = [(float(k) / 1000, v) for k, v in cfg.get("earlier_manual_db", {}).get(str(g), {}).items() if float(k) <= xmax]
            if earlier:
                ax.plot(*zip(*earlier), "x", color="#888888", ms=7, label="earlier manual run")
            ok = [r for r in rows if r["status"] not in ("LIMITED", "CLIPPED", "REF") and r["freq"] <= xmax]
            lim = [r for r in rows if r["status"] in ("LIMITED", "CLIPPED") and r["freq"] <= xmax]
            for sel, kw in ((ok, dict(fmt="o", color="#d1495b", label="measured mean, bars = min..max of reps")),
                            (lim, dict(fmt="o", color="#d1495b", mfc="white", label="LIMITED / CLIPPED (not valid)"))):
                if sel:
                    y = np.array([r["mean"] for r in sel])
                    ax.errorbar([r["freq"] / 1000 for r in sel], y, yerr=[y - [r["mn"] for r in sel], [r["mx"] for r in sel] - y],
                                capsize=3, ms=5, **kw)
            ax.set_xscale("log")
            ax.set_xlim(0.9, xmax / 1000)
            ticks = [t for t in (1, 2, 5, 10, 20, 50) if t <= xmax / 1000]
            ax.set_xticks(ticks)
            ax.set_xticklabels([str(t) for t in ticks])
            ax.minorticks_off()
            ax.set_ylim(*ylim)
            ax.set_xlabel("frequency (kHz)")
            ax.set_ylabel("response re 1 kHz (dB)")
            ax.set_title(f"gain setting {g}" + (" (known extra roll-off, not judged)" if g in meta["known_gains"] else "") + (" , zoom" if ci else ""), fontsize=10)
            ax.grid(True, which="both", alpha=0.3)
            if gi == 0 and ci == 0:
                ax.legend(fontsize=7, loc="lower left")
    fig.suptitle(f"Anti-alias filter response, measured vs calculated   {meta['stamp']}   firmware {meta['fw']}   source {meta['source']}", fontsize=11)
    if meta["source"] == "MOCK":
        fig.text(0.5, 0.5, "SIMULATED DATA\nNOT A MEASUREMENT", ha="center", va="center", fontsize=44, color="red", alpha=0.25, rotation=25)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ----------------------------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial port, e.g. /dev/cu.usbserial-0001, or 'mock' for the SIMULATED board")
    ap.add_argument("--baud", type=int, default=460800, help="working baud rate (the board starts at 115200 and is switched); 115200 disables the switch")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "hil_config.json"))
    ap.add_argument("--gains", type=int, nargs="+", default=[1, 2])
    ap.add_argument("--amp", type=int, nargs="+", default=None, help="tone amplitude code (1..512) per gain, default 512 for gains 1 and 2, 128 for gain 3")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--n-samples", type=int, default=16384)
    ap.add_argument("--settle-ms", type=int, default=500, help="wait after changing frequency before capturing")
    ap.add_argument("--limit-db", type=float, default=0.5, help="PASS if within this many dB of the calculated value (or inside the band)")
    ap.add_argument("--limited-counts", type=float, default=5.0, help="LIMITED if the tone is below this many counts (ADC resolution)")
    ap.add_argument("--clip-lo", type=int, default=150)
    ap.add_argument("--clip-hi", type=int, default=3700)
    ap.add_argument("--ref-hz", type=float, default=1000.0)
    ap.add_argument("--freqs", type=float, nargs="+", default=None, help="override the frequency list in the config")
    ap.add_argument("--expect-swap-mode", type=int, default=1)
    ap.add_argument("--known-rolloff-gains", type=int, nargs="*", default=[3], help="gains whose misses are reported as KNOWN-ROLLOFF, not FAIL")
    ap.add_argument("--countdown", type=int, default=20, help="seconds to wait after the board is connected, so you can place hand-held jumpers (0 = none)")
    ap.add_argument("--outdir", default="hil_results")
    ap.add_argument("--manual-minutes", type=float, default=None, help="stopwatch time of the same sweep done by hand, if you measured it (recorded as user-supplied)")
    args = ap.parse_args()

    t_start = time.perf_counter()
    cfg = json.load(open(args.config))
    freqs = sorted(args.freqs if args.freqs else cfg["freqs_hz"], key=lambda f: (f != args.ref_hz, f))   # reference first
    if args.ref_hz not in freqs:
        sys.exit("the reference frequency must be in the frequency list")
    amps = {g: (512 if g < 3 else 128) for g in args.gains}
    if args.amp:
        if len(args.amp) != len(args.gains):
            sys.exit("--amp needs one value per gain")
        amps = dict(zip(args.gains, args.amp))
    if args.n_samples % 2 or not 1024 <= args.n_samples <= 16384:
        sys.exit("--n-samples must be even, 1024..16384")
    mock = args.port == "mock"
    source = "MOCK" if mock else "HARDWARE"
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"{'MOCK_' if mock else ''}{stamp}"
    os.makedirs(args.outdir, exist_ok=True)
    log = print
    if mock:
        log("=" * 78 + "\n SIMULATED BOARD (mock_device.py). Nothing below is a measurement.\n" + "=" * 78)

    if mock:
        from mock_device import MockSerial
        ser = MockSerial()
    else:
        import serial
        ser = serial.Serial(args.port, 115200, timeout=1)
    dev = Device(ser, log)
    log(f"HIL sweep {run_id}, port {args.port}")
    atexit.register(dev.restore)
    fw = dev.connect(args.baud)
    log(f"  firmware {fw}")
    t_connected = time.perf_counter()

    t_cd = 0.0
    if args.countdown > 0:
        t0 = time.perf_counter()
        log(f"  place the jumpers on TP2 and TP3 now and hold them still until the run finishes; starting in {args.countdown} s")
        for sec in range(args.countdown, 0, -1):
            if sec % 5 == 0 or sec <= 3:
                log(f"    {sec} s")
            time.sleep(1)
        t_cd = time.perf_counter() - t0

    # ---- sample-order self-check on the first buffer (DAC sine, gain 2)
    log("  order check: gain 2, 1 kHz DAC tone, capturing the first buffer (about 3 s) ...")
    dev.cmd("GAIN 2")
    dev.cmd("DAC 1000 0")
    time.sleep(1.0)
    raw0, _, _ = dev.capture(args.n_samples)
    dev.cmd("STOP")
    zz = zigzag_check(raw0, args.expect_swap_mode)
    log("  " + zz["message"])
    if not zz["ok"]:
        log("ABORTED: " + zz["message"])
        sys.exit(2)
    mode = zz["mode"]
    t_zigzag = time.perf_counter()

    # ---- sweeps
    log_rows, all_rows, warnings = [], {}, []
    ref_by_gain = {}
    t_sweep0 = time.perf_counter()
    n_captures = 0
    for g in args.gains:
        dev.cmd(f"GAIN {g}")
        per_freq = {f: [] for f in freqs}
        log(f"\nGain setting {g}: {args.reps} sweeps x {len(freqs)} frequencies, tone amp code {amps[g]}")
        for rep in range(args.reps):
            ref_amp = None
            rep_rows = []
            for f in freqs:
                dev.cmd(f"SWEEP {int(f)} {amps[g]}")
                time.sleep(args.settle_ms / 1000.0)
                raw, mn, mx = dev.capture(args.n_samples)
                n_captures += 1
                print(".", end="", flush=True)
                fixed = swap_back(raw, mode)
                t = tone_amplitude(fixed, f)
                traw = tone_amplitude(raw.astype(float), f)
                if f == args.ref_hz:
                    ref_amp = t["amp"]
                    ref_by_gain.setdefault(g, []).append(ref_amp)
                    if ref_amp < 3.0:
                        dev.cmd("STOP")
                        log(f"ABORTED: reference tone only {ref_amp:.1f} counts at gain {g}: contact lost or no signal.")
                        sys.exit(3)
                rep_rows.append((f, t, traw, mn, mx))
            for f, t, traw, mn, mx in rep_rows:
                db = 20.0 * np.log10(max(t["amp"], 1e-9) / ref_amp)
                k = str(int(f))
                per_freq[f].append(dict(meas_db=db, amp_fix=t["amp"], raw_min=mn, raw_max=mx))
                lo, hi = cfg["band_db"].get(k, [None, None])
                log_rows.append(dict(timestamp=dt.datetime.now().isoformat(timespec="seconds"), run_id=run_id, source=source, firmware=fw,
                                     gain_setting=g, rep=rep + 1, freq_nom_hz=int(f), freq_peak_hz=round(t["f_peak"], 1),
                                     amp_fix_counts=round(t["amp"], 3), amp_raw_counts=round(traw["amp"], 3), mirror_fix_counts=round(t["mirror"], 3),
                                     snr_db=round(t["snr_db"], 1), raw_min=mn, raw_max=mx, meas_db_rel_ref=round(db, 3),
                                     calc_db=cfg["calc_db"].get(k), band_lo_db=lo, band_hi_db=hi))
            log(f" sweep {rep + 1}/{args.reps} done")
        dev.cmd("STOP")
        rr = ref_by_gain[g]
        drift = 20.0 * np.log10(max(rr) / max(min(rr), 1e-9))
        if drift > 0.3:
            msg = f"gain {g}: the 1 kHz reference amplitude moved by {drift:.2f} dB between sweeps ({', '.join(f'{v:.1f}' for v in rr)} counts): a contact probably wobbled, discard this run"
            warnings.append(msg)
            log("  WARNING: " + msg)
        all_rows[g] = evaluate(g, freqs, per_freq, cfg, args)
    t_sweep1 = time.perf_counter()
    dev.restore()
    ser.close()

    # ---- outputs
    for g in args.gains:
        print_table(all_rows[g], g, amps[g], args, log)
    log_path = os.path.join(args.outdir, f"hil_{run_id}_log.csv")
    with open(log_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(log_rows[0].keys()))
        w.writeheader()
        w.writerows(log_rows)
    sum_path = os.path.join(args.outdir, f"hil_{run_id}_summary.csv")
    with open(sum_path, "w", newline="") as fh:
        cols = ["timestamp", "run_id", "source", "firmware", "gain_setting", "freq_nom_hz", "reps", "mean_db", "min_db", "max_db", "spread_db", "std_db",
                "calc_db", "band_lo_db", "band_hi_db", "sim_db", "diff_vs_calc_db", "min_amp_counts", "status", "earlier_manual_db", "delta_vs_earlier_db"]
        w = csv.writer(fh)
        w.writerow(cols)
        now = dt.datetime.now().isoformat(timespec="seconds")
        for g in args.gains:
            for r in all_rows[g]:
                rnd = lambda v: "" if v is None else round(v, 3)
                w.writerow([now, run_id, source, fw, g, int(r["freq"]), r["reps"], rnd(r["mean"]), rnd(r["mn"]), rnd(r["mx"]), rnd(r["spread"]), rnd(r["std"]),
                            rnd(r["calc"]), rnd(r["lo"]), rnd(r["hi"]), rnd(r["sim"]), rnd(r["diff"]), round(r["min_amp"], 2), r["status"], rnd(r["earlier"]), rnd(r["d_earlier"])])
    png_path = os.path.join(args.outdir, f"hil_{run_id}_plot.png")
    make_plot(all_rows, cfg, dict(stamp=stamp, fw=fw, source=source, known_gains=args.known_rolloff_gains), png_path)
    t_end = time.perf_counter()

    # ---- disagreements with the earlier manual numbers
    log("\nPoints that differ from the earlier manual numbers by more than 0.3 dB:")
    any_d = False
    for g in args.gains:
        for r in all_rows[g]:
            if r["d_earlier"] is not None and abs(r["d_earlier"]) > 0.3 and r["status"] not in ("LIMITED", "CLIPPED"):
                any_d = True
                log(f"  gain {g}  {r['freq'] / 1000:5.1f} kHz: now {r['mean']:+.2f} dB, earlier {r['earlier']:+.2f} dB, delta {r['d_earlier']:+.2f} dB (spread over reps {r['spread']:.2f} dB)")
    if not any_d:
        log("  none")

    # ---- timing
    timing = dict(countdown_s=t_cd, total_excluding_countdown_s=t_end - t_start - t_cd, connect_s=t_connected - t_start, order_check_s=t_zigzag - t_connected, sweeps_s=t_sweep1 - t_sweep0,
                  outputs_s=t_end - t_sweep1, total_start_to_plot_s=t_end - t_start, captures=n_captures,
                  seconds_per_capture=(t_sweep1 - t_sweep0) / max(n_captures, 1), settle_ms=args.settle_ms, baud=args.baud, n_samples=args.n_samples,
                  gains=args.gains, reps=args.reps, freqs=len(freqs), source=source, serial_retries=dev.retries)
    log(f"\nSerial transfer retries (chunks or captures requested again): {dev.retries}; board restarts during the run: {dev.restarts}")
    if dev.restarts:
        warnings.append(f"the board restarted {dev.restarts} time(s) during the run; if this happened after the sample-order check, the check was not repeated")
    log(f"TIMING ({source}): total start to finished plot {timing['total_start_to_plot_s']:.1f} s, of which {t_cd:.0f} s was the countdown "
        f"-> {timing['total_excluding_countdown_s']:.1f} s without it "
        f"(connect {timing['connect_s']:.1f}, order check {timing['order_check_s']:.1f}, {n_captures} captures {timing['sweeps_s']:.1f} s = "
        f"{timing['seconds_per_capture']:.2f} s each, analysis/csv/plot {timing['outputs_s']:.1f})")
    if args.manual_minutes:
        timing["manual_minutes_user_supplied"] = args.manual_minutes
        log(f"  manual time you supplied: {args.manual_minutes:g} min -> ratio {args.manual_minutes * 60 / timing['total_start_to_plot_s']:.1f}x (only as good as your stopwatch number)")
    else:
        log("  no manual time supplied: the script does not estimate one.")
    if warnings:
        log("\nWARNINGS: " + "; ".join(warnings))
    json.dump(dict(run_id=run_id, firmware=fw, source=source, warnings=warnings, timing=timing, args=vars(args), order_check=dict(mode=mode, message=zz["message"])),
              open(os.path.join(args.outdir, f"hil_{run_id}_meta.json"), "w"), indent=2)
    log(f"\nfiles: {log_path}\n       {sum_path}\n       {png_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
