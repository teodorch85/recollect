"""Suggest a name for a batch: Claude looks at a few of its photos and the writing on their backs.
Uses the same Anthropic API key as auto-rotate (Settings ▸ Claude).
"""
import json

import cv2

import rotate

LOOK_PX = 512  # each photo is a small copy; a handful per batch is enough to see what it is
MAX_PHOTOS = 6

PROMPT = """These are printed family photos scanned as one batch (usually one box, album or envelope),
currently named "{current}". The writing found on their backs is:
{backs}

Suggest a short name for the batch: 2 to 5 words saying what the photos are of, e.g. "Wedding of Vasilis",
"Summer in Crete", "Maria's christening". Write it in the language of the current name and of the backs
(Greek if they are Greek). Leave out the year and month: they are kept separately.
Give names that appear in the writing exactly as written; don't invent names of people or places."""

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "reason": {"type": "string", "description": "a few words on what it's based on"},
    },
    "required": ["name", "reason"],
    "additionalProperties": False,
}


def _jpeg(bgr):
    h, w = bgr.shape[:2]
    f = LOOK_PX / max(h, w)
    if f < 1:
        bgr = cv2.resize(bgr, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA)
    import base64
    return base64.standard_b64encode(cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()).decode()


def suggest(key, current, photos, back_texts):
    """(name, reason) for a batch. photos: BGR images (a few); back_texts: the text read from backs."""
    import anthropic
    backs = "\n".join(f"- {t}" for t in back_texts if t) or "(none)"
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg(p)}}
               for p in photos[:MAX_PHOTOS]]
    content.append({"type": "text", "text": PROMPT.format(current=current, backs=backs)})
    try:
        response = rotate._client(key).beta.messages.create(
            model=rotate.MODEL,
            max_tokens=4000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError:
        raise rotate.RotateError("The Anthropic API key was refused. Check it in Settings ▸ Claude.") from None
    except anthropic.APIStatusError as e:
        raise rotate.RotateError(f"Claude reported a problem ({e.status_code}).") from None
    except anthropic.APIConnectionError:
        raise rotate.RotateError("Couldn't reach Claude: is the PC online?") from None
    if response.stop_reason != "end_turn":
        raise rotate.RotateError(f"Claude gave no answer ({response.stop_reason}).")
    answer = json.loads(next(b.text for b in response.content if b.type == "text"))
    return answer["name"].strip(), answer["reason"]
