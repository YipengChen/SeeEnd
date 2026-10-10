const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const packaging = path.resolve(__dirname, '../packaging');
function python(script) {
  return execFileSync(process.env.SEEEND_TEST_PYTHON || 'python', ['-B', '-c',
    'import sys\nsys.path.insert(0, ' + JSON.stringify(packaging) + ')\n' + script], {encoding:'utf8'}).trim();
}

test('extension staging is reproducible, keeps the development key and preserves historical packages', () => {
  assert.equal(python(`
import hashlib, json, tempfile, zipfile
from pathlib import Path
from extension_release import stage_extension
with tempfile.TemporaryDirectory() as work:
    root = Path(work)
    source, dist = root / 'source', root / 'dist'
    source.mkdir(); dist.mkdir()
    (source / 'manifest.json').write_text(json.dumps({'manifest_version':3,'version':'1.2.3','key':'development-key'}))
    (source / 'providers.js').write_text("const VERSION = '1.2.3';")
    (source / 'popup.html').write_text('<p>fixture</p>')
    (source / 'README.md').write_text('source instructions')
    (source / '.DS_Store').write_text('ignored')
    old = dist / 'SeeEnd-Web-extension'
    old.mkdir(); (old / 'manifest.json').write_text('old loaded extension')
    historical = root / 'docs/chrome-web-store/extension-upload.zip'
    historical.parent.mkdir(parents=True); historical.write_bytes(b'historical submission')
    identity = {'app_version':'1.0.0','source_sha256':'fixture'}
    directory, dev, store = stage_extension(source, dist, identity)
    original = [path.read_bytes() for path in (dev,store)]
    stage_extension(source, dist, identity)
    assert original == [path.read_bytes() for path in (dev,store)]
    assert (old / 'manifest.json').read_text() == 'old loaded extension'
    assert historical.read_bytes() == b'historical submission'
    assert (directory / 'README.md').read_text() == 'source instructions'
    with zipfile.ZipFile(dev) as archive:
        assert json.loads(archive.read(directory.name + '/manifest.json'))['key'] == 'development-key'
        assert all(not name.endswith('.DS_Store') for name in archive.namelist())
    with zipfile.ZipFile(store) as archive:
        assert 'manifest.json' in archive.namelist()
        assert 'key' not in json.loads(archive.read('manifest.json'))
        assert 'README.md' not in archive.namelist()
    receipt = json.loads((dist / 'SeeEnd-extension-1.2.3.json').read_text())
    assert receipt['packages_sha256'][store.name] == hashlib.sha256(store.read_bytes()).hexdigest()
    (source / 'providers.js').write_text("const VERSION = '1.2.2';")
    try:
        stage_extension(source, dist, identity)
    except RuntimeError:
        pass
    else:
        raise AssertionError('mismatched extension versions accepted')
print('ok')
`), 'ok');
});

test('third-party source selection excludes unrelated original code and refuses modified archives', () => {
  assert.equal(python(`
import hashlib, json, tempfile
from pathlib import Path
from release import third_party_files
with tempfile.TemporaryDirectory() as work:
    materials = Path(work)
    source = materials / 'vendor-source.tar.xz'
    source.write_bytes(b'upstream fixture')
    recipe = materials / 'vendor-1.0-recipe'
    recipe.mkdir(); (recipe / 'meta.yaml').write_text('vendor build recipe')
    (materials / 'original-seeend.py').write_text('must not be distributed')
    (materials / 'MANIFEST.json').write_text(json.dumps([{'name':'vendor','version':'1.0','sources':[
        {'archive':source.name,'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]}]))
    selected = [str(path.relative_to(materials)) for path in third_party_files(materials)]
    assert selected == ['MANIFEST.json','vendor-1.0-recipe/meta.yaml','vendor-source.tar.xz']
    source.write_bytes(b'modified source')
    try:
        third_party_files(materials)
    except RuntimeError:
        pass
    else:
        raise AssertionError('modified source accepted')
print('ok')
`), 'ok');
});

test('build receipts reject stale versions, changed app code and changed third-party manifests', () => {
  assert.equal(python(`
import json, sys, tempfile
from pathlib import Path
from release import RECEIPT, sha256_file, source_identity, validate_build, write_receipt
with tempfile.TemporaryDirectory() as work:
    root = Path(work)
    extension = root / 'aiflame/integrations/chatgpt_web/extension'
    extension.mkdir(parents=True)
    (extension / 'manifest.json').write_text(json.dumps({'version':'1.2.3'}))
    app = root / 'aiflame/app.py'; app.write_text('original application')
    project = root / 'pyproject.toml'; project.write_text('[project]\\nversion = "1.0.0"\\n')
    for name in ('third_party_sources', 'packaging/generated_licenses'):
        directory = root / name; directory.mkdir(parents=True)
        (directory / 'MANIFEST.json').write_text('[]')
    identity = source_identity(root)
    identity['third_party_sources_sha256'] = sha256_file(root / 'third_party_sources/MANIFEST.json')
    identity['third_party_licenses_sha256'] = sha256_file(root / 'packaging/generated_licenses/MANIFEST.json')
    dist = root / 'dist'; dist.mkdir()
    resources = dist / ('SeeEnd.app/Contents/Resources' if sys.platform == 'darwin' else 'SeeEnd/_internal')
    resources.mkdir(parents=True)
    write_receipt(dist / RECEIPT, identity); write_receipt(resources / RECEIPT, identity)
    assert validate_build(root, dist) == identity
    docs = root / 'docs'; docs.mkdir(); (docs / 'audit.md').write_text('unrelated documentation')
    assert source_identity(root)['source_sha256'] == identity['source_sha256']
    for path, replacement in [(project,'[project]\\nversion = "2.0.0"\\n'),
                              (app,'modified application'),
                              (root / 'third_party_sources/MANIFEST.json','[{}]')]:
        original = path.read_text(); path.write_text(replacement)
        try:
            validate_build(root, dist)
        except RuntimeError:
            pass
        else:
            raise AssertionError('stale build accepted: ' + str(path))
        path.write_text(original)
print('ok')
`), 'ok');
});
