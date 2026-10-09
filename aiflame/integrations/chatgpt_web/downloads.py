"""Package the browser extension's public assets."""

import io
import zipfile
from aiflame.core.paths import PACKAGE


def extension_archive():
    buffer = io.BytesIO()
    directory = PACKAGE / "integrations/chatgpt_web/extension"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for asset in sorted(directory.iterdir()):
            if asset.is_file() and asset.suffix in (".json", ".js", ".html", ".md", ".png"):
                archive.write(asset, "SeeEnd-Web-extension/" + asset.name)
    return buffer.getvalue()
