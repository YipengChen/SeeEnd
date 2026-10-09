"""Prepare third-party notices and matching upstream source archives.

SeeEnd's own source is never copied into the public third-party materials.
"""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import sys
import subprocess
import tarfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / 'third_party_sources'
NOTICES = ROOT / 'packaging/generated_licenses'
# These dynamically linked libraries may be pulled in by Qt on the current
# Anaconda build. Preserve the distributor's recipe and patches as well.
CONDA_LIBRARIES = {'qtbase', 'qtsvg', 'pyside6', 'libiconv', 'libintl', 'libglib', 'graphite2', 'xz'}
CONDA_NOTICES = {'python', 'pyobjc-core', 'pyobjc-framework-cocoa',
                 'pyobjc-framework-fsevents', 'pyobjc-framework-coreservices',
                 'openssl', 'libffi', 'zlib', 'libzlib', 'xz', 'bzip2',
                 'pcre2', 'icu', 'libpng', 'freetype', 'harfbuzz', 'fontconfig',
                 'expat', 'libexpat', 'libjpeg-turbo', 'brotli', 'brotli-bin',
                 'libbrotlicommon', 'libbrotlidec', 'libbrotlienc', 'zstd', 'lz4-c',
                 'libtiff', 'libxcb', 'libdeflate', 'libmpdec', 'lcms2', 'libopenjpeg',
                 'sqlite', 'xorg-libxau', 'libcxx', 'libwebp-base', 'xorg-libxdmcp',
                 'ncurses', 'libavif', 'lerc'}
PYTHON_PACKAGES = {'PySide6', 'shiboken6',
                   'pyobjc-core', 'pyobjc-framework-Cocoa', 'pyobjc-framework-UserNotifications',
                   'PyInstaller', 'packaging'}


def fetch(url, path, expected, download):
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
        return
    if not download:
        raise RuntimeError(f'Missing verified third-party source: {path.name}; run packaging/prepare_licenses.py --download')
    print('Fetching', path.name, flush=True)
    candidates = [url]
    if url.startswith('https://ftp.gnu.org/pub/gnu/'):
        candidates.insert(0, url.replace('https://ftp.gnu.org/pub/gnu/', 'https://mirrors.kernel.org/gnu/'))
    for candidate in candidates:
        result = subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--max-time', '180',
                                 '--silent', '--show-error', candidate, '-o', str(path)])
        if result.returncode == 0:
            break
    else:
        raise RuntimeError('Source download failed: ' + path.name)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        path.unlink()
        raise RuntimeError('Source SHA256 mismatch: ' + path.name)


def main(download=False):
    SOURCES.mkdir(exist_ok=True)
    if NOTICES.exists():
        shutil.rmtree(NOTICES)
    NOTICES.mkdir()
    shutil.copy2(ROOT / 'docs/legal/LEGACY_GPL_3_0.txt', NOTICES / 'GPL-3.0.txt')
    records = []
    prefix = Path(sys.base_prefix)
    conda = prefix / 'conda-meta'
    for p in sorted(conda.glob('*.json')):
        d = json.loads(p.read_text())
        if d['name'] not in CONDA_LIBRARIES | CONDA_NOTICES:
            continue
        info = prefix / 'pkgs' / (d['name'] + '-' + d['version'] + '-' + d['build']) / 'info'
        if not info.is_dir():
            raise RuntimeError('Conda build recipe unavailable: ' + d['name'])
        name = d['name'] + '-' + d['version']
        dest = NOTICES / name
        dest.mkdir(exist_ok=True)
        if (info / 'licenses').exists():
            shutil.copytree(info / 'licenses', dest / 'licenses', dirs_exist_ok=True)
        record = {'name': d['name'], 'version': d['version'], 'license': d.get('license'),
                  'binary_origin': d.get('url'), 'build': d['build']}
        if d['name'] in CONDA_LIBRARIES:
            recipe = info / 'recipe'
            shutil.copytree(recipe, SOURCES / (name + '-recipe'), dirs_exist_ok=True)
            spec = yaml.safe_load((recipe / 'meta.yaml').read_text())
            sources = spec['source'] if isinstance(spec['source'], list) else [spec['source']]
            record['sources'] = []
            for source in sources:
                url = source['url']
                if isinstance(url, list):
                    url = url[0]
                archive = SOURCES / url.rsplit('/', 1)[-1]
                fetch(url, archive, source['sha256'], download)
                record['sources'].append({'url': url, 'archive': archive.name,
                                          'sha256': source['sha256']})
        records.append(record)
    if not any(r['name'] == 'pyside6' for r in records):
        # Official pip wheels use upstream Qt; conda builds above retain patches.
        if metadata.version('PySide6') != '6.11.0':
            raise RuntimeError('Register exact Qt source hashes for this PySide version before release')
        for source in json.loads((ROOT / 'packaging/qt_sources.json').read_text()):
            fetch(source['url'], SOURCES / source['archive'], source['sha256'], download)
            with tarfile.open(SOURCES / source['archive']) as archive:
                for member in archive.getmembers():
                    if member.isfile() and '/LICENSES/' in member.name:
                        target = NOTICES / source['name'] / 'LICENSES' / Path(member.name).name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(archive.extractfile(member).read())
            records.append(source)
    seen = set()
    for name in sorted(PYTHON_PACKAGES):
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        label = dist.metadata['Name'] + '-' + dist.version
        if label in seen:
            continue
        seen.add(label)
        dest = NOTICES / label
        dest.mkdir(exist_ok=True)
        for f in dist.files or []:
            if any(part.lower() in {'licenses', 'license', 'copying'} for part in Path(str(f)).parts) or Path(str(f)).name.lower().startswith(('license', 'copying')):
                src = Path(dist.locate_file(f))
                if src.is_file():
                    target = dest / str(f)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, target)
        (dest / 'METADATA.txt').write_text(dist.read_text('METADATA') or dist.read_text('PKG-INFO') or '')
        if name == 'pyobjc-framework-UserNotifications':
            index = SOURCES / (name + '-pypi.json')
            if download:
                subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                                'https://pypi.org/pypi/' + name + '/' + dist.version + '/json',
                                '-o', str(index)], check=True)
            data = json.loads(index.read_text())
            source = next(x for x in data['urls'] if x['packagetype'] == 'sdist')
            archive = SOURCES / source['filename']
            fetch(source['url'], archive, source['digests']['sha256'], download)
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    filename = Path(member.name).name
                    if member.isfile() and filename.lower().startswith(('license', 'copying')):
                        (dest / filename).write_bytes(tar.extractfile(member).read())
            records.append({'name': name + '-source', 'version': dist.version,
                            'url': source['url'], 'archive': source['filename'],
                            'sha256': source['digests']['sha256']})
        records.append({'name': dist.metadata['Name'], 'version': dist.version,
                        'license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                        'home_page': dist.metadata.get('Home-page')})
    (SOURCES / 'MANIFEST.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
    (NOTICES / 'MANIFEST.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
    shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', NOTICES / 'README.md')
    print('Third-party materials ready; original SeeEnd source excluded.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    main(parser.parse_args().download)
