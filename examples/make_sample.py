"""Create examples/sample_protocol.docx - a small demo protocol to try Protovid on."""

from pathlib import Path

import docx

d = docx.Document()
d.core_properties.title = "Water Filter Cartridge Replacement"
d.add_heading("Water Filter Cartridge Replacement", level=0)
d.add_heading("Purpose", level=1)
d.add_paragraph("This protocol describes how to safely replace the sediment filter cartridge "
                "on the unit every 6 months or when output flow drops below 2 litres per minute.")
d.add_heading("Materials", level=1)
for m in ["New sediment cartridge (5 micron)", "Filter housing wrench", "Nitrile gloves", "Bucket and towel"]:
    d.add_paragraph(m, style="List Bullet")
d.add_heading("Procedure", level=1)
for s in [
    "Switch off the unit and close the inlet valve.",
    "Warning: Do not open the housing while the system is pressurised.",
    "Press the pressure-release button until water stops flowing.",
    "Place the bucket under the housing and unscrew it with the wrench.",
    "Remove the old cartridge and rinse the housing with clean water.",
    "Insert the new cartridge and hand-tighten the housing.",
    "Open the inlet valve, switch the unit on and check for leaks for 5 minutes.",
]:
    d.add_paragraph(s, style="List Number")
out = Path(__file__).with_name("sample_protocol.docx")
d.save(out)
print(out)

# Stand-in "photos" so you can try --images without your own pictures.
# Replace these with real photos of your machines and components.
from PIL import Image, ImageDraw, ImageFont

img_dir = Path(__file__).with_name("images")
img_dir.mkdir(exist_ok=True)
try:
    font = ImageFont.truetype("DejaVuSans-Bold.ttf", 44)
except OSError:
    font = ImageFont.load_default(44)
for name, color in [
    ("filter_housing.png", (59, 130, 246)),
    ("sediment_cartridge.png", (234, 179, 8)),
    ("housing_wrench.png", (100, 116, 139)),
    ("inlet_valve.png", (220, 38, 38)),
    ("pressure_release_button.png", (22, 163, 74)),
]:
    im = Image.new("RGB", (1200, 900), (236, 240, 244))
    dr = ImageDraw.Draw(im)
    dr.rounded_rectangle([350, 150, 850, 650], 60, fill=color)
    dr.text((600, 760), Path(name).stem.replace("_", " "), font=font, fill=(30, 41, 59), anchor="mm")
    im.save(img_dir / name)
print(img_dir)
