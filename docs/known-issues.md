# Known issues, not measured, and ideas for revision 2

## Known issues

1. **Injected signal level drifted between 22 and 23 Nov.** At gain 2 the 1 kHz tone fell from about 246 to about 203 to 209 counts. Cause not found. Suspected: hand-held contact on bare pads. Supply (3.3 V) and reference (1.5 V) were normal.
2. **Gain 1 results are not usable.** The tone is only about 20 counts at gain 1 and three runs disagree with each other.
3. **Serial transfers lose bytes above 115200 baud.** Cause unknown. The script works at 115200.
4. **Occasional board resets** were seen around port opens and jumper handling. Cause not proven.
5. **Bias leak.** TP1 read 3.3 V with the capsule unplugged; the biased node read about 2.6 V with the capsule plugged in, against 2.64 to 2.69 V calculated. Root cause not proven.
6. **The Monte Carlo tolerance band is analytic, not ngspice.** Only three ngspice points are on file (20 kHz -0.66 dB, 25 kHz -3.3 dB, 100 kHz -48.0 dB).
7. **The first published comparison used the wrong filter values.** The calculation had been built from values that are not in the schematic or the bill of materials. It was corrected on 23 Nov to the schematic values and every summary and plot was regenerated from the unchanged raw logs. The calculation still leaves out op-amp gain-bandwidth, the preamp's own bandwidth and the ADC sample-and-hold, which may explain why the measured response falls slightly faster than calculated above 25 kHz (0.44 to 0.78 dB at 30 to 55 kHz). That is not tested.
8. **The pure 1 kHz tone level test is not stable in a room.** Four real runs (23 Nov) gave level against distance that free-field physics cannot produce, most likely from room reflections at a wavelength of about 34 cm (not proven). A noise-band version of the test passed. Details and every run are in docs/results.md section 7.

## Not measured

- Filter response above 55 kHz (42, 70, 100 kHz points), and a 22 kohm stopband run.
- Gain 3 with the automated tool, and the earlier roll-off near 16 kHz seen manually at gain 3.
- Distortion (THD).
- Capsule sensitivity and A-weighted noise floor (a room-noise estimate of about 50 dB SPL unweighted, +/-4 dB, is not a specification).
- A timed manual sweep for comparison with the automated sweep (about 71 s for 30 captures, automated).
- Absolute sound pressure level (no calibrated reference); the end-to-end test is relative level and frequency against a phone source.
- The PC visualisation app, and any calibrated room or speaker measurement.

## Ideas for revision 2 (not tested)

- Replace the bare test pads with header pins or test loops so probes can be clipped on and held.
- Add a defined calibration injection point.
- Gain 1 produces only about 20 counts for the injection used; choose an injection level suited to each gain.
