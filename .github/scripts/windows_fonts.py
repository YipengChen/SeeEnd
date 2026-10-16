"""Expose installed Windows fonts to Qt's FreeType offscreen backend in CI."""

import os
from pathlib import Path
import shutil
import tempfile

source = Path(os.environ['WINDIR']) / 'Fonts'
target = Path(tempfile.mkdtemp(prefix='seeend-qt-fonts-', dir=os.environ['RUNNER_TEMP']))
count = 0
for font in source.iterdir():
    if font.suffix.lower() not in {'.ttf', '.otf', '.ttc'}:
        continue
    # QFreeTypeFontDatabase's directory filter omits .ttc, though FreeType can
    # load collections. Preserve their bytes with an accepted file extension.
    name = font.name + '.ttf' if font.suffix.lower() == '.ttc' else font.name
    shutil.copyfile(font, target / name)
    count += 1
if not count:
    raise RuntimeError('No installed Windows fonts are available')
with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as environment:
    environment.write(f'QT_QPA_FONTDIR={target}\n')
print(f'{count} system font files made available to Qt offscreen rendering: {target}', flush=True)
