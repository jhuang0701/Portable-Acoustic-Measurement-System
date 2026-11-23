"""SIMULATED board for testing hil_sweep.py without hardware. Everything it returns is synthetic: NOT a measurement.

It speaks the acoustic_hil.ino protocol, returns samples with the ESP32 pair swap, ADC noise of about 1 count, and a response
made of the calculated filter response plus a deliberately invented passband tilt (so that PASS/FAIL/LIMITED logic is exercised)
and, at gain 3, an invented single pole. Those invented shapes are test scaffolding and say nothing about the real board.
"""
import os
import numpy as np

FS = 80000.0
NMAX = 16384


def _sk_db(f, r, cfb, cg):
    w = 2 * np.pi * f
    den = (1 - w * w * r * r * cfb * cg) + 1j * w * cg * 2 * r
    return -20 * np.log10(np.abs(den))


def _calc_db(f):
    return _sk_db(f, 3920.0, 1.8e-9, 1.5e-9) + _sk_db(f, 2430.0, 6.8e-9, 1.0e-9)


class MockSerial:
    def __init__(self, tilt=True, noise=0.95, seed=1):
        self.fault = os.environ.get("MOCK_FAULT", "")       # test hooks: "noswap", "nosignal", "stuckfast", "dropbytes"
        self.board_baud = 921600 if self.fault == "stuckfast" else 115200
        self.reboot_pending = self.fault == "reboot"
        self.buf = bytearray(b"" if self.fault == "stuckfast" else b"ets Jun  8 2016 00:22:57\nrst:0x1 (POWERON_RESET)\nREADY acoustic_hil_1.1 (MOCK)\n")
        self.timeout = 1.0
        self.baudrate = 115200
        self.gain = 2
        self.src = None
        self.rng = np.random.default_rng(seed)
        self.tilt, self.noise = tilt, noise

    # ---- pyserial-like API
    def write(self, data):
        if self.baudrate != self.board_baud:
            return len(data)                                   # wrong baud rate: the board hears garbage
        for line in bytes(data).decode().split("\n"):
            if line.strip():
                self._handle(line.strip())
        return len(data)

    def readline(self):
        i = self.buf.find(b"\n")
        if i < 0:
            out = bytes(self.buf)
            self.buf.clear()
            return out
        out = bytes(self.buf[:i + 1])
        del self.buf[:i + 1]
        return out

    def read(self, n):
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def reset_input_buffer(self):
        self.buf.clear()

    def close(self):
        pass

    # ---- board behaviour
    def _say(self, s):
        self.buf += (s + "\n").encode()

    def _amp(self):
        if self.src is None:
            return 0.0, 1000.0
        kind, f, p = self.src
        if kind == "dac":
            return 155.0 * (1.0, 0.5, 0.25)[p], 0.93 * f
        base = {0: 0.21, 1: 21.3, 2: 200.0, 3: 2000.0}[self.gain]
        a = base * np.sin(np.pi * p / 1024.0) / np.sin(np.pi * 512 / 1024.0)
        rel = _calc_db(f) - _calc_db(1000.0)
        if self.tilt:
            t = lambda x: -0.9 * (1 - np.exp(-x / 3000.0))
            rel += t(f) - t(1000.0)
            if self.gain == 3:
                pole = lambda x: -10 * np.log10(1 + (x / 16500.0) ** 2)
                rel += pole(f) - pole(1000.0)
        return a * 10 ** (rel / 20.0), f

    def _handle(self, line):
        c = line.split()
        if c[0] == "PING":
            self._say("PONG")
        elif c[0] == "VER":
            self._say("VER acoustic_hil_1.1 fs=80000 nmax=16384")
        elif c[0] == "BAUD":
            self._say(f"OK BAUD {c[1]}")
            self.board_baud = int(c[1])
        elif c[0] == "GAIN" and self.reboot_pending:
            self.reboot_pending = False                    # the board resets by itself: back to 115200, state lost, no reply
            self.board_baud = 115200
            self.gain, self.src = 2, None
            self.buf += b"\xfe\xfe boot garbage\n"
        elif c[0] == "GAIN":
            self.gain = int(c[1]); self._say(f"OK GAIN {c[1]}")
        elif c[0] == "SWEEP":
            self.src = ("sq", float(c[1]), int(c[2])); self._say(f"OK SWEEP {c[1]} {c[2]}")
        elif c[0] == "DAC":
            self.src = ("dac", float(c[1]), int(c[2])); self._say(f"OK DAC {c[1]} {c[2]}")
        elif c[0] == "STOP":
            self.src = None; self._say("OK STOP")
        elif c[0] == "CAPTURE":
            n = int(c[1])
            a, f = self._amp()
            if self.fault == "nosignal":
                a = 0.0
            i = np.arange(n)
            x = 1713 + a * np.cos(2 * np.pi * f * i / FS + 0.3) + self.rng.normal(0, self.noise * (1 + 0.8 * (self.gain == 3)), n)
            x = np.clip(np.round(x), 0, 4095).astype(np.uint16)
            out = x.copy()
            if self.fault != "noswap":
                out[0::2], out[1::2] = x[1::2], x[0::2]        # the ESP32 delivers pairs swapped
            self.last = out.astype("<u2").tobytes()
            self._say(f"DATA {n} {int(out.min())} {int(out.max())} {int(out.astype(np.uint64).sum() & 0xFFFF)}")
        elif c[0] == "GET":
            k = int(c[1])
            chunk = self.last[k * 1024:(k + 1) * 1024]
            self._say(f"CHUNK {k} {len(chunk)} {sum(chunk) & 0xFFFF}")
            if self.fault == "dropbytes" and self.rng.random() < 0.3:
                chunk = chunk[:-int(self.rng.integers(1, 200))]      # simulate bytes lost on the link
            self.buf += chunk
        else:
            self._say("ERR unknown command")
