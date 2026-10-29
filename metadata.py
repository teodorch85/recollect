"""Write what we know about a print into its JPEG, in the tags Immich (and most photo apps) read.

    description   XMP dc:description (back text, searchable in Immich), copied to EXIF ImageDescription
                  only when plain ASCII: that tag has no charset, so Greek there could come out garbled
    tags          XMP digiKam:TagsList (Immich's first choice, '/' = hierarchy)
    keywords      the same as a flat list: IPTC Keywords + XMP dc:subject (Windows Explorer, other apps)
    date          EXIF DateTimeOriginal, only when the year is known (else Immich would show the scan date)

Only the metadata is rewritten; the image data is not re-encoded.
"""
import pyexiv2

pyexiv2.set_log_level(4)  # exiv2 warnings about unknown tags are not errors

EXIF_DATE = ("Exif.Photo.DateTimeOriginal", "Exif.Photo.DateTimeDigitized")
# Windows-only duplicates written by earlier versions; Explorer reads the standard tags below too
OLD_TAGS = ("Exif.Image.XPKeywords", "Exif.Image.XPComment", "Exif.Image.XPTitle", "Exif.Image.XPSubject",
            "Exif.Photo.UserComment")


def write(path, description, tags, keywords, date=None, replaces=None):
    """date: 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' (missing parts become 1 January), or None for undated.
    replaces: the description we wrote last time. An empty description removes the one in the file
    only if it is still that one; a description typed in another app is left alone."""
    if path.suffix.lower() not in (".jpg", ".jpeg"):
        return  # TIFFs keep their info in the database only
    tags, leaves = ([*dict.fromkeys(t.strip() for t in ts if t and t.strip())] for ts in (tags, keywords))
    with pyexiv2.ImageData(path.read_bytes()) as img:  # in memory: exiv2 can't open Greek file paths
        exif = {k: None for k in OLD_TAGS}  # None deletes
        current = (img.read_xmp().get("Xmp.dc.description", {}).get('lang="x-default"')
                   or img.read_exif().get("Exif.Image.ImageDescription"))
        ours = bool(description) or (replaces and current == replaces)
        if ours:
            exif["Exif.Image.ImageDescription"] = description if description and description.isascii() else None
        if date:
            y, m, d = (date.split("-") + ["01", "01"])[:3]
            stamp = f"{y}:{int(m):02d}:{int(d):02d} 12:00:00"
        exif.update({k: stamp if date else None for k in EXIF_DATE})
        img.modify_exif(exif)
        img.modify_iptc({"Iptc.Envelope.CharacterSet": "\x1b%G",  # = UTF-8, so Greek keywords survive
                         "Iptc.Application2.Keywords": leaves or None})
        xmp = {"Xmp.dc.description": {'lang="x-default"': description} if description else None} if ours else {}
        img.modify_xmp({**xmp, "Xmp.dc.subject": leaves or None,
                        "Xmp.digiKam.TagsList": tags or None,
                        "Xmp.photoshop.DateCreated": date or None})
        data = img.get_bytes()
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)  # never leave a half-written photo
    finally:
        tmp.unlink(missing_ok=True)  # replace failed (photo open in a viewer): no stray file next to it


def read(path):
    """(exif, iptc, xmp) dicts, for checking."""
    with pyexiv2.ImageData(path.read_bytes()) as img:
        return img.read_exif(), img.read_iptc(), img.read_xmp()
