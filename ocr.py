"""Read text on photo backs with Windows' built-in OCR (local and free; Greek + English).

Works well on printed text (lab backprints, stamps); handwriting is hit-and-miss.
"""
import asyncio
import re

import cv2
import numpy as np
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

LANGS = ("el", "en-GB")
_engines = {}


def _engine(tag):
    if tag not in _engines:
        _engines[tag] = OcrEngine.try_create_from_language(Language(tag))
    return _engines[tag]


def _bitmap(bgr):
    bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    writer = DataWriter()
    writer.write_bytes(bgra.tobytes())
    bmp = SoftwareBitmap(BitmapPixelFormat.BGRA8, bgra.shape[1], bgra.shape[0], BitmapAlphaMode.PREMULTIPLIED)
    bmp.copy_from_buffer(writer.detach_buffer())
    return bmp


async def _recognize(bgr, tag):
    result = await _engine(tag).recognize_async(_bitmap(bgr))
    return "\n".join(line.text for line in result.lines)


def _score(text):
    """How much real text a reading contains: letters/digits in words of 3+ characters."""
    return sum(len(w) for w in re.findall(r"\w{3,}", text))


def _variants(bgr):
    """Versions of the back to try: plain (handwriting, stamps) and 'dot-matrix' for lab backprints,
    whose faint dotted letters OCR only reads once the dots are blurred into solid strokes."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    plain = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    big = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (0, 0), 2.0)
    paper = float(np.median(big))
    dots = np.where(big < paper - 15, 0, 255).astype(np.uint8)
    return [cv2.cvtColor(v, cv2.COLOR_GRAY2BGR) for v in (plain, dots)]


def _rotations(img):
    return [img, cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(img, cv2.ROTATE_180),
            cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)]


def read_line(bgr):
    """Plain English OCR of an image already cleaned up and the right way up (e.g. a date stamp)."""
    return " ".join(asyncio.run(_recognize(bgr, "en-GB")).split())


def read_text(bgr):
    """Best OCR reading of a back: every variant x rotation in English to find the right way up,
    then Greek on the winners; keeps the reading with the most real text."""
    async def run():
        texts = []
        for v in _variants(bgr):
            best_rot, best = None, ""
            for r in _rotations(v):
                t = await _recognize(r, "en-GB")
                if _score(t) > _score(best):
                    best_rot, best = r, t
            if best_rot is not None:
                greek = await _recognize(best_rot, "el")
                if re.search("[α-ωΑ-Ω]{3,}", greek) and _score(greek) >= _score(best) * 0.8:
                    best = greek
            if _score(best) >= 4:
                texts.append(best)
        return texts

    lines = []
    for text in asyncio.run(run()):
        for line in text.splitlines():
            line = line.replace("ø", "0").replace("Ø", "0").strip()  # lab printers slash their zeros
            if _score(line) and line not in lines:
                lines.append(line)
    return "\n".join(lines)
