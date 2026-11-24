#!/usr/bin/env python3
"""Writes hil_config.json: calculated response and Monte Carlo tolerance band for the anti-alias filter.

The calculation is analytic, from the schematic and bill of materials in hardware/kicad (RA1-RA4, CA1-CA4, R_ISO1, C_ADC1):
two unity-gain Sallen-Key low-pass stages followed by the ADC input RC
  stage 1: RA1=RA2=12k, C_feedback CA2=470p, C_ground CA1=470p        (f0 28.2 kHz, Q 0.50)
  stage 2: RA3=RA4=5.1k, C_feedback CA4=3.3n, C_ground CA3=470p       (f0 25.1 kHz, Q 1.33)
  ADC input: R_ISO1=10 ohm into C_ADC1=100n to ground (pole at 159 kHz)
H(s) = 1 / (1 - w^2 R1 R2 Cfb Cg + j w Cg (R1+R2)) for each Sallen-Key stage.
Not in the calculation: op-amp finite gain-bandwidth, the preamp's own bandwidth, the ADC sample-and-hold, parasitics.
The tolerance band is an ANALYTIC Monte Carlo (this file), NOT an ngspice run: every R uniform +/-1 %, every C uniform +/-5 %,
independent per component, 20000 runs, band = min..max of the response relative to the 1 kHz response of the same run.
If you have ngspice Monte Carlo output, edit calc_db / band_db / sim_db in hil_config.json by hand (the script only reads the file).
"""
import json, sys
import numpy as np

FREQS = [1000, 2000, 5000, 10000, 15000, 20000, 25000, 30000, 38000, 55000]
STAGES = [dict(R=(12000.0, 12000.0), Cfb=470e-12, Cg=470e-12), dict(R=(5100.0, 5100.0), Cfb=3.3e-9, Cg=470e-12)]
ADC_RC = dict(R=10.0, C=100e-9)           # R_ISO1, C_ADC1 at GPIO34
N_MC, SEED, R_TOL, C_TOL = 20000, 12345, 0.01, 0.05


def response_db(f, p):
    """p: dict of arrays (one entry per Monte Carlo run) for R1a R2a Cfba Cga R1b R2b Cfbb Cgb Radc Cadc; returns (runs, nf) in dB."""
    w = 2 * np.pi * np.asarray(f, dtype=float)[None, :]
    out = 0.0
    out = out + 20.0 * np.log10(1.0 / np.abs(1.0 + 1j * w * p["Radc"][:, None] * p["Cadc"][:, None]))
    for st in ("a", "b"):
        r1, r2, cfb, cg = (p[k + st][:, None] for k in ("R1", "R2", "Cfb", "Cg"))
        den = (1.0 - w * w * r1 * r2 * cfb * cg) + 1j * w * cg * (r1 + r2)
        out = out + 20.0 * np.log10(1.0 / np.abs(den))
    return out


def nominal():
    a, b = STAGES
    return {"R1a": np.array([a["R"][0]]), "R2a": np.array([a["R"][1]]), "Cfba": np.array([a["Cfb"]]), "Cga": np.array([a["Cg"]]),
            "R1b": np.array([b["R"][0]]), "R2b": np.array([b["R"][1]]), "Cfbb": np.array([b["Cfb"]]), "Cgb": np.array([b["Cg"]]),
            "Radc": np.array([ADC_RC["R"]]), "Cadc": np.array([ADC_RC["C"]])}


def main(out="hil_config.json"):
    rng = np.random.default_rng(SEED)
    nom = nominal()
    mc = {}
    for k, v in nom.items():
        tol = R_TOL if k.startswith("R") else C_TOL
        mc[k] = v[0] * (1.0 + rng.uniform(-tol, tol, N_MC))
    f = np.array(FREQS, dtype=float)
    ref = FREQS.index(1000)
    cal = response_db(f, nom)[0]
    cal = cal - cal[ref]
    m = response_db(f, mc)
    m = m - m[:, [ref]]
    fine = np.arange(20000, 40000, 5.0)
    cf = response_db(fine, nom)[0] - response_db([1000.0], nom)[0, 0]
    f3 = float(fine[np.argmax(cf <= -3.0)])
    cfg = {
        "description": "Calculated (schematic values, analytic) and analytic Monte Carlo band of the anti-alias filter, dB relative to the 1 kHz response. NOT ngspice.",
        "reference_hz": 1000,
        "freqs_hz": FREQS,
        "calc_db": {str(int(x)): round(float(v), 3) for x, v in zip(FREQS, cal)},
        "band_db": {str(int(x)): [round(float(lo), 3), round(float(hi), 3)] for x, lo, hi in zip(FREQS, m.min(axis=0), m.max(axis=0))},
        "calc_minus3db_hz": f3,
        "band_method": f"analytic Monte Carlo, {N_MC} runs, seed {SEED}, R +/-{R_TOL*100:g}% uniform, C +/-{C_TOL*100:g}% uniform, independent per component",
        "sim_db": {"20000": -0.66, "25000": -3.3, "100000": -48.0},
        "sim_note": "ngspice values the user has on file (only these three points); no ngspice Monte Carlo is on file",
        "earlier_manual_db": {
            "1": {"2000": -0.44, "5000": -0.80, "10000": -0.93, "15000": -1.03, "20000": -1.42, "25000": -4.16, "30000": -8.44},
            "2": {"5000": -0.54, "10000": -0.74, "15000": -0.87, "20000": -1.29, "25000": -3.75, "30000": -7.90, "38000": -15.20, "55000": -27.41}
        },
        "earlier_manual_note": "square-wave sweeps from the 2026-11-22 manual runs (v7q gain 2, v7h gain 1), relative to the 1 kHz reading"
    }
    with open(out, "w") as fh:
        json.dump(cfg, fh, indent=2)
    for x in FREQS:
        k = str(x)
        print(f"{x:6d} Hz  calc {cfg['calc_db'][k]:8.3f}  band {cfg['band_db'][k][0]:8.3f} .. {cfg['band_db'][k][1]:8.3f}")
    print("calc -3 dB point:", f3, "Hz ->", out)


if __name__ == "__main__":
    main(*(sys.argv[1:2]))
