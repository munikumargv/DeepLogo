"""Microphone front-end for real hardware (optional: needs ``vosk`` + ``sounddevice``).

Privacy properties enforced here:
* audio lives only in a fixed-size in-memory ring buffer that is constantly
  overwritten - it is never written to disk or sent anywhere;
* the recogniser runs with a *closed grammar* made of the configured terms
  plus "[unk]", so it cannot transcribe ordinary conversation - anything that
  is not a listed term comes out as "[unk]" and is dropped.
"""
from __future__ import annotations

import json
import queue
import time

from .device import EdgeDevice
from .keywords import normalize

SAMPLE_RATE = 16000
FRAME = 4000  # 250 ms


class RingBuffer:
    def __init__(self, seconds: float = 2.0):
        self.buf = bytearray(int(seconds * SAMPLE_RATE) * 2)
        self.pos = 0

    def write(self, data: bytes) -> None:
        for chunk in (data[i:i + len(self.buf)] for i in range(0, len(data), len(self.buf))):
            end = self.pos + len(chunk)
            if end <= len(self.buf):
                self.buf[self.pos:end] = chunk
            else:
                k = len(self.buf) - self.pos
                self.buf[self.pos:] = chunk[:k]
                self.buf[:end - len(self.buf)] = chunk[k:]
            self.pos = end % len(self.buf)

    def wipe(self) -> None:
        self.buf[:] = bytes(len(self.buf))


def run_microphone(device: EdgeDevice, model_path: str) -> None:  # pragma: no cover - hardware
    import sounddevice as sd
    from vosk import KaldiRecognizer, Model

    grammar = sorted({normalize(t.text) for t in device.spotter.terms}) + ["[unk]"]
    rec = KaldiRecognizer(Model(model_path), SAMPLE_RATE, json.dumps(grammar, ensure_ascii=False))
    ring = RingBuffer()
    frames: queue.Queue[bytes] = queue.Queue(maxsize=16)

    def on_audio(indata, _frames, _time, _status):
        try:
            frames.put_nowait(bytes(indata))
        except queue.Full:
            pass  # drop rather than buffer more audio

    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=FRAME, dtype="int16",
                           channels=1, callback=on_audio):
        try:
            while True:
                data = frames.get()
                ring.write(data)
                if rec.AcceptWaveform(data):
                    text = json.loads(rec.Result()).get("text", "").replace("[unk]", " ").strip()
                    if text:
                        device.on_utterance(text, time.time())
                device.tick(time.time())
                del data
        finally:
            ring.wipe()
