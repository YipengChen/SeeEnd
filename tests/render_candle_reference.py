"""Render the complete reference-length LED simulation: python -m tests.render_candle_reference.

Uses Pillow for this development artifact only; the firmware has no Pillow dependency.
"""
from pathlib import Path
from PIL import Image, ImageDraw
from aiflame.devices.firmware.candle import Renderer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = (PROJECT_ROOT / 'docs' if (PROJECT_ROOT / 'docs').is_dir()
             else PROJECT_ROOT.parent / 'docs')


def render(output=DOCS_ROOT / 'reference-flame-full.gif'):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    renderer = Renderer()
    renderer.render(0, 'done', 25)
    frames = []
    palette = [16, 13, 9, 25, 22, 18]
    for value in range(1, 17):
        palette.extend((int(255 * (value / 16) ** .5),
                        int(185 * (value / 16) ** .7), int(65 * value / 16)))
    palette.extend((240, 240, 240))
    palette.extend([0] * (768 - len(palette)))
    for t in range(0, 76240, 40):
        pixels = renderer.render(t, 'done', 25)
        frame = Image.new('P', (180, 350), 0)
        frame.putpalette(palette)
        draw = ImageDraw.Draw(frame)
        for index, value in enumerate(pixels):
            x, y = index % 9 * 20 + 10, index // 9 * 20 + 10
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=value + 1)
        draw.text((8, 329), f'{t / 1000:05.2f}s  /  76.24s', fill=18)
        frames.append(frame)
    frames[0].save(output, save_all=True, append_images=frames[1:], duration=40,
                   loop=0, optimize=False)
    # Include all three complete strong-wind intervals, not only the opening.
    times = (12.8, 13.3, 13.78, 14.3, 14.5, 15.1, 15.4, 16.2,
             38.7, 39.2, 39.65, 40.2, 40.4, 40.95, 41.2, 42.1,
             69.7, 70.25, 70.75, 71.25, 71.45, 72.1, 72.45, 73.5)
    sheet = Image.new('RGB', (8 * 180, 3 * 350))
    for index, seconds in enumerate(times):
        sheet.paste(frames[round(seconds * 25)].convert('RGB'),
                    (index % 8 * 180, index // 8 * 350))
    sheet.save(output.with_suffix('.png'))
    print(f'{len(frames)} frames, 76.24 seconds: {output}')


if __name__ == '__main__':
    render()
