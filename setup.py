"""Include workspace documentation in source distributions."""

from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.sdist import sdist


class WorkspaceSdist(sdist):
    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        root = Path(__file__).resolve().parent
        # An extracted sdist already has docs beside README; only the workspace
        # keeps them outside the Python project directory.
        docs = root.parent / "docs"
        if (root / "docs").is_dir() or not docs.is_dir():
            return
        destination = Path(base_dir)
        for source in docs.rglob("*"):
            relative = source.relative_to(docs)
            if (not source.is_file() or source.suffix not in {".md", ".png", ".jpg", ".gif"}
                    or "reference-full-analysis" in relative.parts):
                continue
            target = destination / "docs" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if source.suffix == ".md":
                target.write_text(
                    source.read_text(encoding="utf-8").replace("](../code/", "](../"),
                    encoding="utf-8",
                )
        for name in ("README.md", "README.en.md"):
            readme = destination / name
            content = readme.read_text(encoding="utf-8").replace(
                "](../docs/", "](docs/"
            )
            # setuptools may hard-link release files; unlink before changing them.
            readme.unlink()
            readme.write_text(content, encoding="utf-8")


setup(cmdclass={"sdist": WorkspaceSdist})
