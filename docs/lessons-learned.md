# Lessons learned

## 1. The ESP32 ADC returned sample pairs swapped

Amplitudes measured on a tone did not match expectations. The consecutive differences of a clean tone were not steady: they alternated, about -d and +3d (for example about -10 and +34 counts at 1 kHz). That is the signature of each pair of samples swapped. Swapping each pair back made the waveform smooth, and the amplitude then matched the input. A mirror tone appears when the swap is not undone, with amplitude proportional to |tan(2 pi f / fs)|. The test script now checks the order on every run and aborts if it is not the expected one.

## 2. Measurements depend on how the probe touches the board

The injected signal was applied with jumper wires held by hand against bare pads. The amplitude of the same tone changed from run to run (about 246 counts on 22 Nov; about 203 and 209 on 23 Nov), and gain 1 runs, with only about 20 counts of signal, disagreed with each other by 1 dB or more. A repeatable bench setup matters more than the analysis code.

## 3. Check the whole path before trusting a number

- The two runs that drifted were still stable inside each run (0.01 to 0.04 dB spread), so repeatability inside a run did not prove the setup was the same as before.
- Comparing amplitudes across frequencies, not only relative levels, exposed that the drift was a nearly constant factor above 5 kHz, which points upstream of the filter.
- Every report keeps the runs that went wrong.

## 4. Serial link

Transfers of 32 KB captures lost bytes at 460800 baud (113 bytes missing) and 921600 baud (205 missing). 115200 baud worked. A chunked transfer with per-chunk checksums and retries was added; at 115200 it ran with 0 retries. The cause of the loss at higher rates was not found. Opening the serial port can reset the ESP32, and an interrupted run could leave the board at the fast rate, so the script restores 115200 on exit and can find a board left at a fast rate.

## 5. Other board and tooling issues found

- Flash mode: the board needed Flash Mode DIO and Flash Frequency 40 MHz in the Arduino IDE (a fix found during bring-up).
- GPIO19 (gain select A1) had a solder bridge. TODO: add what it caused and how it was found.
- Bias network: with the capsule plugged in, the bias node read about 2.6 V against 2.64 to 2.69 V calculated (two-digit multimeter reading), and with the capsule unplugged TP1 read 3.3 V. A leak was suspected; the root cause is not proven. TODO: add the flux-residue finding.

## 6. Check the calculation's inputs against the design files

The first comparison of measurement against calculation reported agreement within 0.36 dB. The calculation had been built from component values that were not the ones in the schematic and bill of materials. When the values were read from the netlist and the BOM (12 kohm and 5.1 kohm resistors, 470 pF and 3.3 nF capacitors), the same measurements were within 0.35 dB up to 25 kHz and 0.78 dB at 55 kHz, still inside the tolerance band but not as tight. The summaries and plots were regenerated from the unchanged raw logs, and the repository history shows both versions. A calculation is only a check if its inputs come from the design files, not from memory.

## 7. Scope

A claim is only as strong as its comparison with a calculation. Several early numbers were dropped because they were not compared with anything, or were measured in a state that could not be reproduced.
