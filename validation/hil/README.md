# HIL sweep of the acoustic front end

Automates the bench filter-response check: the script sets a tone, captures raw ADC samples, undoes the ESP32
sample-pair swap, measures the amplitude, normalises to 1 kHz and compares every point with the calculation and the tolerance band.

## Files

| File | What it is |
|---|---|
| `acoustic_hil/acoustic_hil.ino` | firmware with the serial command protocol |
| `hil_sweep.py` | the test script |
| `make_config.py` | writes `hil_config.json` (calculated response and tolerance band) |
| `reanalyze.py` | re-evaluates saved runs against the current `hil_config.json` |
| `hil_config.json` | the tables the script reads (nothing is hard-coded in the script) |
| `mock_device.py` | SIMULATED board for testing the script without hardware. Its numbers are never measurements |

## One-time setup (Mac)

```
cd <this folder>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Upload `acoustic_hil/acoustic_hil.ino` from the Arduino IDE (Flash Mode = DIO, Flash Frequency = 40 MHz, as before), then **close the Serial Monitor**.
Wiring is the same as the manual sweeps: GPIO25 -> 100k -> junction; junction -> TP2; junction -> 100 ohm -> TP3; capsule unplugged.
The jumpers must touch TP2 and TP3 for the whole run. Do not touch the board until the script prints that it has connected (opening the port can reset the ESP32); after that there is a `--countdown` (default 20 s) to place the jumpers. Run one gain at a time. If the 1 kHz reference moves by more than 0.3 dB between sweeps the script warns that a contact wobbled.
Find the port with `ls /dev/cu.*` (something like `/dev/cu.usbserial-0001` or `/dev/cu.SLAB_USBtoUART`).

## Run

```
python3 hil_sweep.py --port /dev/cu.usbserial-0001 --gains 2 --reps 3 --countdown 15 --baud 115200 --n-samples 8192 --settle-ms 300
```

These options are the ones that worked on the author's board and USB-serial link (see docs/known-issues.md).

Defaults: gains 1 and 2, 3 sweeps, frequencies from the config (1, 2, 5, 10, 15, 20, 25, 30, 38, 55 kHz, 1 kHz is the reference),
460800 baud (the board starts at 115200 and the script switches it). On the author's link bulk transfers lost bytes at 460800 and 921600 and were reliable only at 115200, so use `--baud 115200`. Captures are read back in 1 KB chunks, each checked and re-requested if it arrives short; the retry count is printed.
Opening the port usually resets the ESP32; the script waits for it to boot. If a run is interrupted (Ctrl-C) the script puts the board back to 115200 on the way out, and if it was killed hard the next run detects the board at the fast rate. If it still cannot connect, press the EN button on the board and run again.

| Option | Default | Meaning |
|---|---|---|
| `--gains 1 2 3` | `1 2` | gain settings to sweep. Gain 3 is reported as KNOWN-ROLLOFF instead of FAIL (extra pole near 16 kHz) |
| `--reps` | 3 | sweeps per gain; the spread (max - min over reps) is reported |
| `--limit-db` | 0.5 | PASS also if within this many dB of the calculated value |
| `--limited-counts` | 5 | LIMITED if the tone is below this many counts (ADC resolution) |
| `--amp A B ..` | 512 (gains 1, 2), 128 (gain 3) | tone amplitude code per gain, 1..512 (512 = full square wave) |
| `--settle-ms` | 500 | wait after each frequency change |
| `--countdown` | 20 | seconds to place the jumpers after the board connects (excluded from the reported sweep time) |
| `--expect-swap-mode` | 1 | swap mode the order check must find; anything else aborts |
| `--manual-minutes` | none | stopwatch time of the same sweep by hand, if you have one (recorded, labelled user-supplied) |

## What it does

1. Boots the board, checks the firmware version, switches baud rate.
2. **Order check** on the first buffer: 1 kHz DAC sine at gain 2. Looks at the consecutive differences near a rising zero crossing
   (swapped pairs give about -d / +3d instead of a steady d) and at the waveform roughness of the delivered, swap(0,1) and swap(1,2) orderings.
   No clean signal, or a swap mode other than the expected one: the script stops with a message.
3. For each gain, each rep, each frequency: `SWEEP f amp`, wait, `CAPTURE 16384`, swap back, amplitude with a Hann window,
   `A = sqrt(4E / (N sum w^2))` (= `(2/N) sqrt(8/3 E)` for Hann), E = power summed over the peak bin +/- 3 with the noise power in those bins removed.
   Each rep is normalised to its own 1 kHz amplitude.
4. Status per point (mean over reps):
   - `REF` the 1 kHz reference
   - `PASS` inside the Monte Carlo band, or within `--limit-db` of the calculated value
   - `FAIL` otherwise
   - `LIMITED` the tone was under `--limited-counts` counts in at least one rep (not a valid comparison)
   - `CLIPPED` raw samples reached the clip thresholds (not valid)
   - `KNOWN-ROLLOFF` a miss at a gain listed in `--known-rolloff-gains` (default 3)
5. Writes to `hil_results/`: `..._log.csv` (every capture), `..._summary.csv`, `..._plot.png`, `..._meta.json` (timing, arguments).
   Every row carries the timestamp, gain setting and firmware version.
6. Lists the points that differ from `earlier_manual_db` in the config by more than 0.3 dB.
7. Prints the timing: total from start to finished plot, plus the parts.

## Where the numbers in `hil_config.json` come from

`python3 make_config.py` regenerates it.
- `calc_db`: analytic, from the schematic and bill of materials in `hardware/kicad` (stage 1: RA1 = RA2 = 12k, CA2 470p feedback, CA1 470p ground; stage 2: RA3 = RA4 = 5.1k, CA4 3.3n feedback, CA3 470p ground; then R_ISO1 10 ohm into C_ADC1 100n). -3 dB at 25.49 kHz. Not included: op-amp gain-bandwidth, the preamp's own bandwidth, the ADC sample-and-hold, parasitics.
- `band_db`: an **analytic** Monte Carlo (20,000 runs, fixed seed, every R uniform +/-1 %, every C uniform +/-5 %), **not an ngspice run**.
    If you have ngspice Monte Carlo output, paste it into `band_db` (and `calc_db`); the script only reads the file.
- `sim_db`: the three ngspice points on file (20 kHz -0.66, 25 kHz -3.3, 100 kHz -48.0). Shown in the plot and CSV.
- `earlier_manual_db`: the square-wave manual runs of 2026-11-22 (gain 2: v7q, gain 1: v7h).

## Re-evaluating saved runs

If the calculation changes (edit `make_config.py`, run it), `python3 reanalyze.py results/` rewrites every summary, plot and the calc/band columns of every log from the saved per-capture measurements. The measured amplitudes are never modified. The runs in `results/` were re-evaluated this way on 23 Nov 2026 after the calculation was corrected to the schematic values.

## Testing without hardware

`python3 hil_sweep.py --port mock` runs everything against `mock_device.py`. All output is prefixed `MOCK`, the PNG carries a SIMULATED watermark,
and the CSV has `source=MOCK`. The mock contains an invented passband tilt so the PASS/FAIL logic is exercised. Never quote it.

## What it can and cannot check

Checks: the sample order (pair swap) at the start; the filter response of the whole chain (injection network, preamp, filter, ADC) from 1 to 55 kHz against the calculation and tolerance band, with repeat spread.
Cannot check: the filter alone (the preamp, the injection network and the ADC are in every point), anything above 40 kHz without aliasing (55 kHz is measured as its 25 kHz alias),
noise or distortion, the capsule, or whether the order check still holds mid-run (it runs once, at the start).
