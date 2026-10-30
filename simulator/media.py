from __future__ import annotations

import io
import math
import random
import struct
import wave
import zlib
from array import array
from dataclasses import dataclass

from faker import Faker

ROOT_FREQUENCIES = (196.0, 220.0, 246.94, 261.63, 293.66, 329.63, 349.23)
CHORD_SHAPES = ((1.0, 1.26, 1.498), (1.0, 1.189, 1.498), (1.0, 1.335, 1.498))
PROGRESSIONS = (
    (1.0, 1.335, 1.498, 1.0),
    (1.0, 0.749, 0.84, 1.122),
    (1.0, 1.189, 0.891, 1.335),
)

ORIGINAL_SAMPLE_RATE = 22_050
RENDITION_SAMPLE_RATE = 11_025
SAMPLE_WIDTH_BYTES = 2
WAV_HEADER_BYTES = 44
BEATS_PER_BAR = 4


def wav_bitrate_kbps(sample_rate: int) -> int:
    return round(sample_rate * SAMPLE_WIDTH_BYTES * 8 / 1000)


@dataclass(frozen=True, slots=True)
class AudioClip:
    samples: array
    seconds: int

    def wav(self, sample_rate: int = ORIGINAL_SAMPLE_RATE) -> bytes:
        step = ORIGINAL_SAMPLE_RATE // sample_rate
        frames = self.samples if step == 1 else self.samples[::step]
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(SAMPLE_WIDTH_BYTES)
            writer.setframerate(sample_rate)
            writer.writeframes(frames.tobytes())
        return buffer.getvalue()


def _beat(root: float, step: float, shape: tuple[float, ...], length: int) -> array:
    two_pi = 2.0 * math.pi
    frequencies = [
        two_pi * root * step * ratio / ORIGINAL_SAMPLE_RATE for ratio in shape
    ]
    return array(
        "h",
        (
            int(
                7_500
                * math.exp(-3.0 * n / length)
                * sum(math.sin(f * n) for f in frequencies)
                / len(shape)
            )
            for n in range(length)
        ),
    )


def synth_clip(rng: random.Random, seconds: int) -> AudioClip:
    root = rng.choice(ROOT_FREQUENCIES)
    shape = rng.choice(CHORD_SHAPES)
    progression = rng.choice(PROGRESSIONS)
    beat_length = int(ORIGINAL_SAMPLE_RATE * 60 / rng.randint(78, 132))
    beats = [_beat(root, step, shape, beat_length) for step in progression]
    total = seconds * ORIGINAL_SAMPLE_RATE
    samples = array("h")
    index = 0
    while len(samples) < total:
        samples.extend(beats[(index // BEATS_PER_BAR) % len(beats)])
        index += 1
    del samples[total:]
    return AudioClip(samples, seconds)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def render_cover_png(rng: random.Random, size: int = 256) -> bytes:
    start = [rng.randint(0, 255) for _ in range(3)]
    end = [rng.randint(0, 255) for _ in range(3)]
    stripe = rng.randint(12, 48)
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            mix = (x + y) / (2 * size)
            accent = 40 if ((x + 2 * y) // stripe) % 2 else 0
            rows.extend(
                min(255, int(s + (e - s) * mix) + accent)
                for s, e in zip(start, end, strict=True)
            )
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + _png_chunk(b"IEND", b"")
    )


def write_lyrics(fake: Faker, rng: random.Random) -> str:
    chorus = [fake.sentence(nb_words=rng.randint(4, 7)).rstrip(".") for _ in range(2)]
    parts: list[str] = []
    for verse in range(rng.randint(2, 3)):
        lines = [
            fake.sentence(nb_words=rng.randint(5, 9)).rstrip(".") for _ in range(4)
        ]
        parts.append(f"[Verse {verse + 1}]\n" + "\n".join(lines))
        parts.append("[Chorus]\n" + "\n".join(chorus * 2))
    return "\n\n".join(parts) + "\n"
