"""Where each photo is filed and what it's called, from a template in the style of Immich's storage
template (https://docs.immich.app/administration/storage-template/):

    {{y}}/{{album}}/{{filename}}            -> 1994/Crete/1994-07_crete_00012.jpg

Variables: date taken y yy M MM MMM MMMM d dd (empty when not known: a print may have only a year),
album (= the batch name, without the year), id (the photo number), filename (ReCollect's own name:
date_batch_id), date (1994-07, 1994 or 'undated'), slug (the batch name in file-name form), paper, size.
{{#if var}} … {{else}} … {{/if}} as in Immich; {{{var}}} is the same as {{var}}.
'/' separates folders. The extension is added, and so is the photo number when the name lacks it
(it keeps names unique and lets ReCollect find a file again).
"""
import calendar
import re

UNKNOWN = "Unknown year"  # same as organizer.UNKNOWN
DEFAULT = "{{#if y}}{{y}}{{else}}Unknown year{{/if}}/{{album}}/{{filename}}"
PRESETS = [
    DEFAULT,
    "{{#if y}}{{y}}{{else}}Unknown year{{/if}}/{{filename}}",
    "{{#if y}}{{y}}{{else}}Unknown year{{/if}}/{{#if MM}}{{MM}}{{else}}Unknown month{{/if}}/{{filename}}",
    "{{#if y}}{{y}}{{else}}Unknown year{{/if}}/{{#if MM}}{{y}}-{{MM}}{{else}}{{y}}{{/if}}/{{filename}}",
    "{{#if y}}{{y}}{{else}}Unknown year{{/if}}/{{#if MMMM}}{{MMMM}}{{else}}Unknown month{{/if}}/{{filename}}",
    "{{album}}/{{filename}}",
    "{{album}}/{{date}}_{{id}}",
]
VARIABLES = ("y", "yy", "M", "MM", "MMM", "MMMM", "d", "dd", "album", "id", "filename", "date", "slug", "paper",
             "size")
TOKEN = re.compile(r"\{\{\{?\s*([^{}]*?)\s*\}?\}\}")
BAD = re.compile(r'[<>:"\\|?*\x00-\x1f]')  # not allowed in Windows names ('/' separates folders)


class TemplateError(ValueError):
    pass


def parse(template):
    """Nested parts: str | ("var", name) | ("if", name, then_parts, else_parts). Raises TemplateError."""
    stack = [{"kind": "root", "then": [], "else": None}]

    def into():
        top = stack[-1]
        return top["else"] if top["else"] is not None else top["then"]

    def text(t):
        if "{" in t or "}" in t:
            raise TemplateError(f"A brace that isn't part of a {{{{variable}}}}: “{t.strip()}”.")
        into().append(t)

    pos = 0
    for m in TOKEN.finditer(template):
        text(template[pos:m.start()])
        pos = m.end()
        tag = m.group(1)
        if tag.startswith("#if"):
            name = tag[3:].strip()
            if name not in VARIABLES:
                raise TemplateError(f"Unknown variable in {{{{#if {name}}}}}.")
            stack.append({"kind": "if", "var": name, "then": [], "else": None})
        elif tag == "else":
            if stack[-1]["kind"] != "if" or stack[-1]["else"] is not None:
                raise TemplateError("{{else}} without a matching {{#if …}}.")
            stack[-1]["else"] = []
        elif tag == "/if":
            if stack[-1]["kind"] != "if":
                raise TemplateError("{{/if}} without a matching {{#if …}}.")
            block = stack.pop()
            into().append(("if", block["var"], block["then"], block["else"] or []))
        elif tag in VARIABLES:
            into().append(("var", tag))
        else:
            raise TemplateError(f"Unknown variable {{{{{tag}}}}}. Available: {', '.join(VARIABLES)}.")
    text(template[pos:])
    if len(stack) > 1:
        raise TemplateError("A {{#if …}} is missing its {{/if}}.")
    if not template.strip(" /"):
        raise TemplateError("The template is empty.")
    return stack[0]["then"]


def _render(parts, values):
    out = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        elif p[0] == "var":
            out.append(BAD.sub("", str(values.get(p[1]) or "")).replace("/", "-"))
        else:
            out.append(_render(p[2] if values.get(p[1]) else p[3], values))
    return "".join(out)


def values_for(year, month, day, album, pid, slug, paper="", size=""):
    """The variables for one photo; month/day may be None."""
    y = str(year) if year else ""
    m = int(month) if month else None
    d = int(day) if day else None
    date = f"{y}-{m:02d}" if y and m else (y or "undated")
    return {"y": y, "yy": y[-2:], "M": str(m) if m else "", "MM": f"{m:02d}" if m else "",
            "MMM": calendar.month_abbr[m] if m else "", "MMMM": calendar.month_name[m] if m else "",
            "d": str(d) if d else "", "dd": f"{d:02d}" if d else "", "album": album, "id": pid,
            "slug": slug, "date": date, "filename": "_".join(p for p in (date, slug, pid) if p),
            "paper": paper or "", "size": size or ""}


def render(parts, values):
    """(folder parts, file name without extension) for one photo."""
    text = _render(parts, values)
    segments = [re.sub(r"[-_ ]{2,}", lambda m: m.group(0)[0], s).strip(" -_.") for s in text.split("/")]
    segments = [s for s in segments if s and s not in (".", "..")]
    name = segments.pop() if segments else ""
    pid = values["id"]
    if not re.search(rf"(^|_){pid}(_|$)", name):
        name = f"{name}_{pid}" if name else pid
    folders = [s[:80] for s in segments]
    if folders and folders[0].startswith("_"):  # _inbox and _backs are ReCollect's own
        folders[0] = folders[0].lstrip("_") or "Photos"
    if not folders:  # never loose in the library's top folder, next to the catalogue
        folders = [UNKNOWN if not values["y"] else "Photos"]
    return folders, name[:120]


def example(template):
    """What a typical photo would be called, or the problem with the template."""
    try:
        parts = parse(template)
    except TemplateError as e:
        return None, str(e)
    folders, name = render(parts, values_for(1994, 7, None, "Crete", "00012", "crete", "Kodak", "10x15"))
    undated, uname = render(parts, values_for(None, None, None, "Crete", "00013", "crete"))
    return "/".join(folders + [name + ".jpg"]) + "   ·   without a year: " + "/".join(undated + [uname + ".jpg"]), None
