"""One-off check: list the V600's WIA properties and grab a 75 dpi preview."""
import sys
import time
import win32com.client

FMT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"

dm = win32com.client.Dispatch("WIA.DeviceManager")
info = next(d for d in dm.DeviceInfos if "V600" in d.Properties("Name").Value)
dev = info.Connect()
item = dev.Items[1]

print("ITEM PROPERTIES")
for p in item.Properties:
    rng = ""
    try:
        if p.SubType == 1:  # range
            rng = f" range {p.SubTypeMin}..{p.SubTypeMax} step {p.SubTypeStep}"
        elif p.SubType in (2, 3):  # list / flag
            rng = " values " + ",".join(str(v) for v in p.SubTypeValues)
    except Exception:
        pass
    print(f"  {p.PropertyID:5} {p.Name}: {p.Value}{rng}")

def setp(pid, value):
    for p in item.Properties:
        if p.PropertyID == pid:
            p.Value = value
            return

setp(6146, 1)    # colour intent
setp(6147, 75)   # horizontal dpi
setp(6148, 75)   # vertical dpi

t = time.time()
img = item.Transfer(FMT_BMP)
out = sys.argv[1] if len(sys.argv) > 1 else "preview.bmp"
img.SaveFile(out)
print(f"\nSaved {out}: {img.Width}x{img.Height} px in {time.time() - t:.1f}s")
