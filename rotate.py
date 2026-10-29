"""Turn scanned prints the right way up: Claude looks at a small copy of each photo and says how far
to turn it. Needs an Anthropic API key (Settings ▸ Claude), kept in Windows Credential Manager.

Cost: one small image per photo (about 1,000 tokens in, a few hundred out), roughly 1 cent a photo.
"""
import base64
import json
from concurrent.futures import ThreadPoolExecutor

import cv2

MODEL = "claude-opus-5-5"
LOOK_PX = 768  # longest side of the copy Claude sees: plenty to tell up from down, few tokens
KEY_TARGET = "ReCollect/AnthropicAPIKey"  # name of the entry in Windows Credential Manager
TURNS = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}

PROMPT = """This is a scan of a printed family photo. It may be lying sideways or upside down on the scanner.
How many degrees must it be turned clockwise so that it is upright, the way the photographer held the camera?
Judge by people (heads above feet, faces upright), the sky and horizon, buildings, trees, furniture and any
writing. A border or a camera date printed on the photo is not a reliable cue on its own.
confidence: "high" if the cues are clear, "medium" if likely, "low" if there's no real cue (then answer 0)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "turn_clockwise": {"type": "integer", "enum": [0, 90, 180, 270]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string", "description": "a few words, e.g. 'faces upside down'"},
    },
    "required": ["turn_clockwise", "confidence", "reason"],
    "additionalProperties": False,
}


class RotateError(RuntimeError):
    pass


# ---------- the API key, in Windows Credential Manager (never in settings.json) ----------
def get_key():
    import win32cred
    try:
        cred = win32cred.CredRead(KEY_TARGET, win32cred.CRED_TYPE_GENERIC)
    except Exception:  # pywintypes.error: no such entry
        return ""
    return cred["CredentialBlob"].decode("utf-16-le")


def set_key(key):
    import win32cred
    if not key:
        try:
            win32cred.CredDelete(KEY_TARGET, win32cred.CRED_TYPE_GENERIC)
        except Exception:
            pass
        return
    win32cred.CredWrite({"Type": win32cred.CRED_TYPE_GENERIC, "TargetName": KEY_TARGET, "UserName": "ReCollect",
                         "CredentialBlob": key, "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE}, 0)


# ---------- asking Claude ----------
def _client(key):
    import anthropic
    return anthropic.Anthropic(api_key=key, max_retries=3, timeout=60.0)


def _small_jpeg(bgr):
    h, w = bgr.shape[:2]
    f = LOOK_PX / max(h, w)
    if f < 1:
        bgr = cv2.resize(bgr, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.standard_b64encode(buf.tobytes()).decode("ascii")


def ask(client, bgr):
    """{"turn_clockwise", "confidence", "reason"} for one photo. Raises RotateError."""
    import anthropic
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # if a safety check declines the photo, another model answers instead
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _small_jpeg(bgr)}},
                {"type": "text", "text": PROMPT},
            ]}],
        )
    except anthropic.AuthenticationError:
        raise RotateError("The Anthropic API key was refused. Check it in Settings ▸ Claude.") from None
    except anthropic.PermissionDeniedError:
        raise RotateError("The Anthropic API key isn't allowed to use Claude (check billing in the Console).") from None
    except anthropic.RateLimitError:
        raise RotateError("Claude is busy (rate limit). The photo was saved without turning.") from None
    except anthropic.APIStatusError as e:
        raise RotateError(f"Claude reported a problem ({e.status_code}).") from None
    except anthropic.APIConnectionError:
        raise RotateError("Couldn't reach Claude: is the PC online?") from None
    if response.stop_reason != "end_turn":  # refusal or cut off: no answer to trust
        raise RotateError(f"Claude gave no answer ({response.stop_reason}).")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def orient_all(key, photos):
    """Answers for several photos at once (one request each, in parallel); an answer is None where
    that photo failed. Returns (answers, first problem or None)."""
    client = _client(key)
    problems = []

    def one(bgr):
        try:
            return ask(client, bgr)
        except RotateError as e:
            problems.append(str(e))
            return None
        except Exception as e:  # never lose a scan over a rotation hint
            problems.append(f"{type(e).__name__}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = list(pool.map(one, photos))
    return answers, (problems[0] if problems else None)


def turn(bgr, degrees):
    return cv2.rotate(bgr, TURNS[degrees]) if degrees in TURNS else bgr


def test_key(key):
    """A tiny request, to check the key works. Returns None if fine, else the problem."""
    import numpy as np
    try:
        ask(_client(key), np.full((64, 64, 3), 200, np.uint8))
    except RotateError as e:
        return str(e)
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return None
