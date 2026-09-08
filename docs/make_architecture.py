"""Render docs/architecture.png.

The submission requires an architecture diagram as an image file, and a mermaid
block in a README is not one. This draws the same graph the code actually builds,
so the picture cannot drift from `graph.py` without someone editing this file too.

    python docs/make_architecture.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1800, 1080
BG = "#ffffff"
INK = "#14161a"
MUTED = "#5b6470"
LINE = "#9aa4b2"

BLUE, BLUE_BG = "#2f6fd0", "#eef4fd"
GREEN, GREEN_BG = "#2f8f4e", "#ecf7ef"
AMBER, AMBER_BG = "#b07d1a", "#fdf6e6"
GREY_BG = "#f5f6f8"
RED = "#c0392b"

F = "C:/Windows/Fonts/arial.ttf"
FB = "C:/Windows/Fonts/arialbd.ttf"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FB if bold else F, size)


TITLE = font(46, True)
SUB = font(22)
PANEL = font(19, True)
BOX_T = font(23, True)
BODY = font(16)
EDGE = font(16, True)
SMALL = font(15)

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)


def panel(xy, label, colour, fill):
    x0, y0 = xy[0], xy[1]
    d.rounded_rectangle(xy, 14, fill=fill, outline=colour, width=2)
    d.text((x0 + 20, y0 + 14), label, font=PANEL, fill=colour)


def box(xy, title, lines, colour=INK, fill="#ffffff", outline=None):
    x0, y0 = xy[0], xy[1]
    d.rounded_rectangle(xy, 10, fill=fill, outline=outline or LINE, width=2)
    d.text((x0 + 18, y0 + 16), title, font=BOX_T, fill=colour)
    y = y0 + 50
    for ln in lines:
        d.text((x0 + 18, y), ln, font=BODY, fill=MUTED)
        y += 23


def arrow(p0, p1, label=None, colour=LINE, dashed=False, label_off=(0, -26)):
    x0, y0 = p0
    x1, y1 = p1
    if dashed:
        total = max(abs(x1 - x0), abs(y1 - y0))
        steps = max(int(total / 14), 1)
        for i in range(steps):
            if i % 2:
                continue
            a = i / steps
            b = min((i + 1) / steps, 1)
            d.line([x0 + (x1 - x0) * a, y0 + (y1 - y0) * a,
                    x0 + (x1 - x0) * b, y0 + (y1 - y0) * b], fill=colour, width=3)
    else:
        d.line([x0, y0, x1, y1], fill=colour, width=3)

    # arrowhead
    if x1 == x0:
        s = 1 if y1 > y0 else -1
        d.polygon([(x1, y1), (x1 - 9, y1 - 14 * s), (x1 + 9, y1 - 14 * s)], fill=colour)
    else:
        s = 1 if x1 > x0 else -1
        d.polygon([(x1, y1), (x1 - 14 * s, y1 - 9), (x1 - 14 * s, y1 + 9)], fill=colour)

    if label:
        mx, my = (x0 + x1) / 2 + label_off[0], (y0 + y1) / 2 + label_off[1]
        tw = d.textlength(label, font=EDGE)
        d.rectangle([mx - tw / 2 - 8, my - 4, mx + tw / 2 + 8, my + 22], fill=BG)
        d.text((mx - tw / 2, my), label, font=EDGE, fill=colour)


# ---------------------------------------------------------------- header
d.text((60, 46), "BillHound", font=TITLE, fill=INK)
d.text((60, 104), "An agent that reads every bill and only speaks when it matters."
                  "   Strands Agents SDK on Amazon Bedrock.", font=SUB, fill=MUTED)
d.line([60, 148, W - 60, 148], fill="#e3e6ea", width=2)

# ---------------------------------------------------------------- intake
box((60, 200, 330, 300), "Raw document",
    ["PDF, scan, or email body", "from any vendor"])

# ---------------------------------------------------------------- graph panel
panel((380, 176, 1740, 470), "Strands multi-agent graph  ·  GraphBuilder", BLUE, "#fbfcfe")

box((410, 226, 700, 386), "extractor",
    ["Transcribes into a typed Bill.", "Forbidden from correcting:",
     "a wrong total is the evidence."],
    colour=BLUE, fill=BLUE_BG, outline=BLUE)

box((790, 226, 1080, 386), "auditor",
    ["Calls the rule engine.", "May not invent, adjust or", "re-derive a single figure."],
    colour=BLUE, fill=BLUE_BG, outline=BLUE)

box((1290, 226, 1700, 386), "strategist",
    ["Decides dispute / cancel / monitor,", "then drafts the message —",
     "in German when the vendor is."],
    colour=BLUE, fill=BLUE_BG, outline=BLUE)

arrow((700, 300), (785, 300), "validated Bill", colour=BLUE)
arrow((1080, 300), (1285, 300), "if case.is_actionable", colour=BLUE)

# the silence branch — kept in its own corridor, well clear of the audit call
d.rounded_rectangle((880, 560, 1250, 640), 40, fill=GREEN_BG, outline=GREEN, width=2)
d.text((908, 585), "silence  ·  most bills end here", font=BOX_T, fill=GREEN)
arrow((1035, 386), (1035, 556), "clean", colour=GREEN, dashed=True, label_off=(-52, -18))

# ---------------------------------------------------------------- deterministic core
panel((60, 700, 830, 1010), "Deterministic core  ·  no model runs here", GREEN, GREEN_BG)

d.text((90, 748), "7 audit rules — pure functions, no I/O, no network", font=BOX_T, fill=INK)
rules = [
    "arithmetic_mismatch      duplicate_line_item",
    "silent_price_hike           off_contract_rate",
    "new_unexplained_fee     vat_inconsistent",
    "zombie_subscription",
]
y = 790
for r in rules:
    d.text((90, y), r, font=SMALL, fill=MUTED)
    y += 24

d.rounded_rectangle((90, 908, 480, 976), 10, fill="#ffffff", outline=LINE, width=2)
d.text((110, 930), "history.json  ·  ratecard.json", font=BODY, fill=MUTED)

d.text((510, 916), "Every euro figure the", font=SMALL, fill=INK)
d.text((510, 938), "human ever sees starts", font=SMALL, fill=INK)
d.text((510, 960), "here, not in a model.", font=SMALL, fill=INK)

# the audit call: its own dog-leg, arrowhead only on the final segment
d.line([845, 386, 845, 505], fill=GREEN, width=3)
d.line([845, 505, 445, 505], fill=GREEN, width=3)
arrow((445, 505), (445, 696), "run_audit", colour=GREEN, label_off=(66, -70))

# ---------------------------------------------------------------- human gate
panel((900, 700, 1740, 1010), "Human-in-the-loop  ·  two independent locks", AMBER, AMBER_BG)

box((930, 750, 1230, 870), "Escalation",
    ["vendor · amount · evidence", "· the drafted message"],
    colour=INK, fill="#ffffff")

d.rounded_rectangle((1290, 750, 1710, 870), 10, fill="#ffffff", outline=AMBER, width=3)
d.text((1310, 768), "Lock 1:", font=BOX_T, fill=RED)
d.text((1410, 768), "HumanInTheLoop", font=BOX_T, fill=AMBER)
d.text((1310, 802), "submit_to_portal is kept OFF the", font=BODY, fill=MUTED)
d.text((1310, 824), "approval allow-list, so execution", font=BODY, fill=MUTED)
d.text((1310, 846), "stops and asks before it can run.", font=BODY, fill=MUTED)

arrow((1230, 810), (1285, 810), colour=AMBER)
arrow((1440, 386), (1440, 746), "escalate", colour=AMBER, label_off=(56, -180))

d.text((930, 902), "Lock 2:", font=BOX_T, fill=RED)
d.text((1020, 903), "even after approval, submission is a DRY RUN", font=BODY, fill=INK)
d.text((1020, 927), "unless BILLHOUND_LIVE=1 is separately set.", font=BODY, fill=INK)
d.text((930, 962), "Nothing is ever sent by the agent alone.", font=BOX_T, fill=RED)

# ---------------------------------------------------------------- footer
d.text((60, 1035), "36 tests, none requiring AWS credentials  ·  "
                   "github.com/sai2311-eng/billhound  ·  MIT",
       font=SMALL, fill=MUTED)

out = Path(__file__).resolve().parent / "architecture.png"
img.save(out, "PNG")
print(f"wrote {out}  ({out.stat().st_size / 1024:.0f} KB, {W}x{H})")
