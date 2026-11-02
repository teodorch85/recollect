"""The order prints were taken in, from what the lab printed on their backs.

A minilab prints a machine line on each back: the order (roll) number and the frame number on the
film, plus exposure codes, e.g.
    0123 NNN 12        12 N N +1 0045        <12> 0045        N12 +1 -1 0045
or, for prints of digital photos, the camera's file name:
    IMG_0456.JPG       DSC01234.JPG          P1010023.JPG
Frame 12 of roll 0045 was taken after frame 11 of the same roll, so within a batch the photos are
put in order roll by roll, frame by frame. Photos whose back says nothing stay next to the photo
scanned just before them (prints are usually scanned in the order they came out of the envelope).
"""
import re

DIGITAL = re.compile(r"\b([A-Z]{1,5}[_-]?)(\d{3,5})\.JPE?G\b", re.IGNORECASE)
BRACKETED = re.compile(r"[<‹«(\[]\s*(\d{1,2})\s*[>›»)\]]")      # <12>
N_FRAME = re.compile(r"\bN\s?(\d{1,2})A?\b")                     # N12 (Fuji Frontier)
CODE = re.compile(r"^(N+|[+-]\d|0|[A-Z]?[+-]\d)$")               # exposure correction codes
NUMBER = re.compile(r"^(\d{1,6})A?$")
MAX_FRAME = 40  # a 36-exposure roll gives up to 38-39 frames


def parse_lab(text):
    """(roll, frame) from a back's lab lines: roll a string ('' if none), frame an int or None."""
    text = text or ""
    m = DIGITAL.search(text)
    if m:  # a camera file name: the counter orders the shots; the prefix tells cameras apart
        return m.group(1).upper().rstrip("_-"), int(m.group(2))
    for line in text.splitlines():
        tokens = re.findall(r"[<‹«(\[]?\s*[\w+-]+\s*[>›»)\]]?", line)
        tokens = [t.strip() for t in tokens if t.strip()]
        rolls = [t for t in tokens if re.fullmatch(r"\d{4,6}", t)]
        roll = rolls[-1] if rolls else ""
        frame = _frame(line, tokens, roll)
        if frame is not None:
            return roll.lstrip("0") or roll, frame
    return "", None


def frame_of(row):
    """(roll, frame) of a catalogue row: from its lab lines, else from the rest of its back (lab lines
    that were read before ReCollect knew their pattern are there)."""
    found = parse_lab(row.get("back_lab"))
    return found if found[1] is not None else parse_lab(row.get("back_text"))


def _frame(line, tokens, roll):
    m = BRACKETED.search(line) or N_FRAME.search(line)
    if m and 0 < int(m.group(1)) <= MAX_FRAME:
        return int(m.group(1))
    # a small number standing next to exposure codes: "12 N N +1" or "NNN 12" or "NNN 012"
    for i, t in enumerate(tokens):
        n = NUMBER.match(t)
        if not n or t == roll or len(n.group(1)) > 3 or not 0 < int(n.group(1)) <= MAX_FRAME:
            continue
        near = tokens[max(0, i - 1):i] + tokens[i + 1:i + 2]
        if any(CODE.match(x) for x in near):
            return int(n.group(1))
    return None


def order(rows):
    """{id: position (1, 2, …)} for one batch's photos, in the order they were taken as far as the backs
    tell, otherwise in the order they were scanned."""
    rows = sorted(rows, key=lambda r: r["id"])
    info = {r["id"]: frame_of(r) for r in rows}
    first_seen = {}  # rolls in the order their first print was scanned
    for r in rows:
        roll, frame = info[r["id"]]
        if frame is not None:
            first_seen.setdefault(roll, len(first_seen))
    keys, last = {}, (-1, -1)
    for n, r in enumerate(rows):
        roll, frame = info[r["id"]]
        if frame is not None:
            last = (first_seen[roll], frame)
        keys[r["id"]] = (*last, frame is None, n)  # unknown: right after the photo scanned before it
    return {pid: i + 1 for i, pid in enumerate(sorted(keys, key=keys.get))}
