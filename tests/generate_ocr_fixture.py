from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

root = Path(__file__).resolve().parent
font_path = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if not font_path.is_file():
    font_path = Path("/home/ilya/.hermes/cache/scratch/NotoSansCJK-Regular.ttc")
font = ImageFont.truetype(str(font_path), 82)
image = Image.new("RGB", (1700, 500), "white")
draw = ImageDraw.Draw(image)
draw.text((48, 60), "你好 | ni hao | hello", font=font, fill="black")
draw.text((48, 275), "学校 | xuexiao | school", font=font, fill="black")
image.save(root / "fixtures" / "vocabulary-2-rows.png")
print(root / "fixtures" / "vocabulary-2-rows.png")
