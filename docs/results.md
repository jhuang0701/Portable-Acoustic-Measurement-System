# Results: measured against calculation

Rule used throughout: a number is only reported if it was **measured and compared against a calculation or simulation**. Each result carries one status:

- **VALID**: measured, compared, and the obvious measurement artefacts were ruled out.
- **PROVISIONAL**: measured and compared, but an artefact is not ruled out or the result did not reproduce well.
- **NOT MEASURED**: no valid measurement exists.

All plots and CSVs are in [`validation/hil/results/`](../validation/hil/results/). Nothing was deleted: bad runs are listed too.

> **Correction (23 Nov).** The first version of this page compared the measurements with a calculation that used filter component values that are not in the schematic or the bill of materials. That calculation is replaced. Every number below uses the values in `hardware/kicad` (RA1 to RA4, CA1 to CA4, R_ISO1, C_ADC1), and the plots and summary files were regenerated from the unchanged raw logs with `validation/hil/reanalyze.py`. No measured value changed.

## Summary

- **Validated:** the ESP32 continuous ADC delivers samples with each pair swapped, and undoing that swap is required before any amplitude is measured (VALID, checked automatically at the start of every run).
- **Measured against calculation:** on 22 Nov the gain 2 filter response from 1 to 55 kHz is inside the Monte Carlo tolerance band at all 9 points in all 3 runs, within 0.35 dB of the calculation up to 25 kHz and within 0.78 dB at worst (55 kHz). The measured -3 dB point is about 25.0 kHz against 25.5 kHz calculated. Status PROVISIONAL, because the two runs on 23 Nov did not reproduce it (worst gap 1.9 dB).
- **Debugged with evidence:** the 23 Nov change is a nearly constant drop in tone amplitude above 5 kHz (about 2.7 dB and 1.75 dB below 22 Nov, with the 5 to 55 kHz shape within 0.45 dB of the 22 Nov shape), so it happened upstream of the filter; the cause is not found.
- **Still open:** why the injected level drifted, gain 1 and gain 3 behaviour (NOT MEASURED), and re-checking the older noise and dynamic-range numbers.

## Signal chain and what was calculated

WM-61B electret capsule, bias network, OPA2365 preamp with TMUX1104 gain select (nominal 1, 10.1, 101, 1001), two Sallen-Key low-pass sections, a 10 ohm and 100 nF network at the ADC pin, ESP32 ADC (GPIO34) at 80 kSPS.

Filter calculation from the schematic values: stage 1 R 12 kohm (RA1, RA2), C feedback 470 pF (CA2), C ground 470 pF (CA1), f0 28.2 kHz, Q 0.50; stage 2 R 5.1 kohm (RA3, RA4), C feedback 3.3 nF (CA4), C ground 470 pF (CA3), f0 25.1 kHz, Q 1.33; then R_ISO1 10 ohm into C_ADC1 100 nF. Calculated -3 dB point: 25.49 kHz. Not in the calculation: op-amp gain-bandwidth, the preamp's own bandwidth, the ADC sample-and-hold, parasitics.

The tolerance band is an **analytic** Monte Carlo (20,000 runs, resistors +/-1 %, capacitors +/-5 %, uniform, independent). It is not an ngspice run. See `validation/hil/make_config.py`.

## 1. Filter response, gain 2 (PROVISIONAL)

Method: a square wave from the ESP32 is injected through 100 kohm at the amplifier input with the capsule unplugged, and the filter output is captured by the ADC. The script undoes the pair swap, takes the amplitude of the fundamental with a Hann window (power summed over the peak bin +/-3, noise power removed), and normalises to the 1 kHz amplitude. 55 kHz is above the 40 kHz Nyquist limit and is measured as its 25 kHz alias.

### Table 1: gain 2, response relative to 1 kHz (dB), all five complete runs

| f (kHz) | calc | MC band | 22 Nov 22:33 | 22 Nov 22:51 | 22 Nov 22:53 | 23 Nov 15:28 | 23 Nov 15:36 |
|---|---|---|---|---|---|---|---|
| 1 kHz tone (counts) | | | 247 | 246 | 246 | 203 | 209 |
| 2 | -0.00 | -0.01 .. +0.01 | -0.01 | -0.01 | -0.01 | -0.56 | -0.30 |
| 5 | -0.02 | -0.10 .. +0.05 | -0.04 | -0.02 | -0.00 | -0.92 | -0.37 |
| 10 | -0.06 | -0.32 .. +0.21 | -0.16 | -0.10 | -0.03 | -1.15 | -0.45 |
| 15 | -0.09 | -0.56 .. +0.39 | -0.25 | -0.20 | -0.16 | -1.29 | -0.54 |
| 20 | -0.55 | -1.21 .. +0.12 | -0.75 | -0.72 | -0.74 | -1.89 | -1.14 |
| 25 | -2.67 | -3.60 .. -1.77 | -3.02 | -3.01 | -2.96 | -4.09 | -3.42 |
| 30 | -6.74 | -8.09 .. -5.40 | -7.18 | -7.18 | -7.06 | -8.25 | -7.51 |
| 38 | -13.99 | -15.57 .. -12.35 | -14.53 | -14.48 | -14.43 | -15.54 | -14.95 |
| 55 | -26.50 | -28.13 .. -24.80 | -27.07 | -27.28 | -27.14 | -28.41 | -27.82 |
| points inside band (of 9) | | | 9 | 9 | 9 | 1 | 6 |
| worst gap to calc (dB) | | | 0.57 | 0.78 | 0.64 | 1.91 | 1.32 |

### Table 2: tone amplitude relative to the 22 Nov 22:51 run (dB), smallest of the repeats; no calculation involved

| f (kHz) | 23 Nov 15:28 | 23 Nov 15:36 |
|---|---|---|
| 1 | -1.68 | -1.40 |
| 2 | -2.26 | -1.78 |
| 5 | -2.62 | -1.74 |
| 10 | -2.71 | -1.76 |
| 15 | -2.76 | -1.71 |
| 20 | -2.85 | -1.80 |
| 25 | -2.77 | -1.75 |
| 30 | -2.77 | -1.70 |
| 38 | -2.77 | -1.83 |
| 55 | -3.06 | -1.87 |

### Table 3: every hardware run

| run | gain | reps | 1 kHz tone (counts) | points inside band | note |
|---|---|---|---|---|---|
| 11-22 22:33 | 2 | 1 | 247 | 9/9 | first clean run |
| 11-22 22:51 | 2 | 3 | 246 | 9/9 | 3 repeats; spread inside the run 0.03 to 0.11 dB |
| 11-22 22:53 | 2 | 1 | 246 | 9/9 | longer capture (16384 samples), same result |
| 11-23 14:47 | 1 | 3 | 21 | 0/7 | low signal; spread up to 0.66 dB; no warning printed |
| 11-23 14:50 | 1 | 3 | 19 | 0/7 | low signal; spread 0.5 to 0.9 dB; 1 to 3 dB below calc |
| 11-23 15:28 | 2 | 3 | 203 | 1/9 | tone amplitude lower than on 22 Nov |
| 11-23 15:36 | 2 | 3 | 209 | 6/9 | tone amplitude lower than on 22 Nov |
| 11-23 15:37 | 1 | 3 | 22 | 1/7 | INVALID: script warned the 1 kHz reference moved 1.18 dB between sweeps |

![Gain 2, 22 Nov, 3 repeats](../validation/hil/results/hil_20261122_225122_plot.png)
![Gain 2, 23 Nov 15:28, 3 repeats](../validation/hil/results/hil_20261123_152822_plot.png)

What this does and does not show:

- The three runs of 22 Nov are inside the tolerance band at every point. Up to 25 kHz they are within 0.35 dB of the calculation; above that the measured response falls a little faster than calculated (0.44 dB at 30 kHz, 0.5 dB at 38 kHz, 0.78 dB at 55 kHz). The spread across repeats inside a run is 0.03 to 0.11 dB.
- The measured -3 dB point is about 25.0 kHz (interpolated between 20 and 25 kHz, crude), against 25.49 kHz calculated.
- Against the ngspice values on file (20 kHz -0.66 dB, 25 kHz -3.3 dB, assumed to be relative to 1 kHz), the 22 Nov runs read -0.72 to -0.75 dB and -2.96 to -3.02 dB.
- On 23 Nov the 1 kHz tone at gain 2 had fallen from about 246 to about 203 and 209 counts, and the response relative to 1 kHz drooped by up to 1.9 dB. Same board, script and command. A passive RC network around an op-amp cannot change by itself, so the change is in the injected signal, the contact, or something upstream.
- Table 2 supports "upstream of the filter": above 5 kHz the 23 Nov amplitudes are lower than the 22 Nov amplitudes by an almost constant factor (2.6 to 3.1 dB for the 15:28 run, 1.7 to 1.9 dB for the 15:36 run). That comparison uses no calculation. It was chosen after seeing the data, so it is a diagnosis, not a headline result.
- The jumper wires are held by hand against bare pads. The tone level moved from run to run, which is consistent with contact quality, but this was not proven.
- Supply and bias, measured with the multimeter on 23 Nov with the jumpers off: TP7 (analog supply) 3.3 V, TP3 (reference) 1.5 V. These do not explain the drift.

## 2. Sample order (VALID)

The ESP32 continuous ADC driver returns the samples of each pair swapped (0 and 1, 2 and 3, and so on). On a tone this appears as a zigzag in the consecutive differences, about -d, +3d instead of a steady d; for example about -10 and +34 counts at 1 kHz. Amplitude read from swapped data is wrong, with the mirror amplitude proportional to |tan(2 pi f / fs)|. The script checks the order on a clean 1 kHz tone at the start of every run and aborts if it is not the expected swap mode. It passed on every run that reached that step. True sample rate: 80,000 Hz.

Effect on the filter measurement, from the saved per-capture data of the three 22 Nov runs (gain 2, 2 to 25 kHz, against the calculation): using the samples as delivered, the worst gap is 8.5 to 8.6 dB (at 15 kHz); after the swap-back it is 0.28 to 0.37 dB.

## 3. Hardware-in-the-loop timing

A single-gain sweep (10 frequencies, 3 repeats, 30 captures of 8192 samples) took 61.8 s of captures, 2.06 s per capture; 70.6 s from start to finished plot without the 15 s countdown, 85.7 s with it. A manual sweep was never timed, so no manual-versus-automated ratio is claimed.

## 4. Gain 1 (NOT MEASURED)

Three runs on 23 Nov (14:47, 14:50, 15:37) disagree with each other (at 2 kHz: -0.31, -1.07 and -0.13 dB) and with the calculation. The tone is only 19 to 22 counts at gain 1, and the script flagged the last run (1 kHz reference moved 1.18 dB between sweeps). None of it is used.

## 5. Gain 3 (NOT MEASURED)

Not run with the HIL tool. An earlier manual run suggested an extra roll-off near 16 kHz at gain 3; it was taken in a different hardware state and is unverified.

## 6. Earlier manual measurements

Taken before the HIL tool existed. The hardware state at the time (a bias leak was present) is not guaranteed to match today's.

| Item | Measured | Calculated | Status |
|---|---|---|---|
| Gain step 1 to 2 and 2 to 3 at 931 Hz | x9.23 and x10.11 | x10.00 and x9.91 | VALID (ratios taken within one setup; worst step -0.69 dB); not re-run after the 23 Nov drift |
| Gain step 0 to 1 and totals | x10.50; totals 20.43, 39.73, 59.83 dB | x10.10; 20.09, 40.09, 60.01 dB | PROVISIONAL (setting 0 is only 0.4 counts; worst total 0.36 dB) |
| Preamp noise, gain 3, input referred | about 1.17 uV (range 1.03 to 1.34) | 0.88 uV | PROVISIONAL (ADC floor about 0.94 counts limits the measurement) |
| Dynamic range | at least 55.0 dB (54.2 to 55.7) | 70.4 dB ideal | PROVISIONAL, a lower bound limited by the source |
| Reference voltage | 1.5 V | 1.500 V | multimeter, two digits |
| Capsule noise in a room | about 50 dB SPL unweighted, +/-4 dB | none | room-limited, not a specification |
