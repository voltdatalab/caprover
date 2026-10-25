"""Pure contract tests; synthetic archives are not evidence of a built image."""
import contextlib
from email.message import Message
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


image = load('image-contract')
registry = load('ghcr-tag-absent')


class ImageContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='captain-contract-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {
            'usr/src/app/dist-frontend/index.html': b'synthetic UI',
            'usr/src/app/dist-frontend/static/app.js': b'synthetic asset',
            'usr/src/app/built/server.js': b'synthetic unchanged backend',
            **{name: b'original ' + name.encode() for name in image.ALLOWED},
        }
        self.changed = {**self.files, **{name: b'candidate ' + name.encode() for name in image.ALLOWED}}
        for directory, values in [('baseline', self.files), ('candidate', self.changed)]:
            for name in image.ALLOWED:
                p = self.root / directory / name.removeprefix('usr/src/app/built/')
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(values[name])
        self.config = {'Architecture': 'amd64', 'Os': 'linux',
                       'Config': {'Entrypoint': None, 'Cmd': ['node', './built/server.js'], 'Env': ['PORT=3000']}}
        self.write_inspect('old.json', self.config)
        self.write_inspect('new.json', self.config)
        self.archive('old.tar', self.files)
        self.archive('new.tar', self.changed)

    def write_inspect(self, name, value):
        (self.root / name).write_text(json.dumps([value]))

    def archive(self, name, files):
        with tarfile.open(self.root / name, 'w') as tf:
            for path, data in files.items():
                entry = tarfile.TarInfo(path)
                entry.size = len(data)
                entry.mode = 0o644
                tf.addfile(entry, io.BytesIO(data))

    def verify(self):
        args = ['image-contract.py', *map(str, [self.root / p for p in
                ['old.tar', 'new.tar', 'old.json', 'new.json', 'baseline', 'candidate', 'proof.json']])]
        with patch.object(sys, 'argv', args), contextlib.redirect_stdout(io.StringIO()):
            image.main()

    def test_accepts_only_two_intended_compiled_changes(self):
        self.verify()
        proof = json.loads((self.root / 'proof.json').read_text())
        self.assertEqual(set(proof['filesystemChangedPaths']), image.ALLOWED)
        self.assertEqual(proof['startedContainers'], 0)

    def test_refuses_changed_ui(self):
        self.archive('new.tar', {**self.changed, 'usr/src/app/dist-frontend/index.html': b'wrong UI'})
        with self.assertRaisesRegex(AssertionError, 'Unexpected filesystem delta'):
            self.verify()

    def test_refuses_unrelated_backend(self):
        self.archive('new.tar', {**self.changed, 'usr/src/app/built/server.js': b'wrong backend'})
        with self.assertRaisesRegex(AssertionError, 'Unexpected filesystem delta'):
            self.verify()

    def test_refuses_missing_dashboard(self):
        del self.changed['usr/src/app/dist-frontend/index.html']
        self.archive('new.tar', self.changed)
        with self.assertRaisesRegex(AssertionError, 'membership'):
            self.verify()

    def test_refuses_changed_command(self):
        self.write_inspect('new.json', {**self.config, 'Config': {'Cmd': ['wrong']}})
        with self.assertRaisesRegex(AssertionError, 'Runtime Config'):
            self.verify()

    def test_refuses_different_official_compiled_baseline(self):
        (self.root / 'baseline/docker/DockerApi.js').write_bytes(b'wrong baseline')
        with self.assertRaisesRegex(AssertionError, 'Official/backend base mismatch'):
            self.verify()


class RootFSContractTests(unittest.TestCase):
    """Exercise main(), reusing setup but not recounting historical tests."""
    root: Path
    files: dict
    changed: dict
    setUp = ImageContractTests.setUp
    archive = ImageContractTests.archive
    write_inspect = ImageContractTests.write_inspect
    verify = ImageContractTests.verify

    def member(self, name, kind=tarfile.DIRTYPE, **fields):
        entry = tarfile.TarInfo(name)
        entry.type = kind
        entry.mode = 0o755
        for key, value in fields.items():
            setattr(entry, key, value)
        return entry

    def rootfs(self, which, extras, archive_format=tarfile.PAX_FORMAT):
        values = self.files if which == 'old' else self.changed
        self.archive(which + '.tar', values)
        with tarfile.open(self.root / (which + '.tar'), 'a', format=archive_format) as tf:
            for entry in extras:
                tf.addfile(entry, io.BytesIO(b'x' * entry.size) if entry.isfile() else None)

    def control(self, extras):
        self.rootfs('old', extras)
        self.rootfs('new', extras)
        self.verify()

    def test_directory_membership_and_mode_main(self):
        for mutation in ['add', 'remove', 'mode']:
            with self.subTest(mutation=mutation):
                directory = self.member('empty/')
                self.control([directory])
                if mutation == 'add':
                    extras = [directory, self.member('another-empty/')]
                elif mutation == 'remove':
                    extras = []
                else:
                    extras = [self.member('empty/', mode=0o777)]
                self.rootfs('new', extras)
                with self.assertRaisesRegex(AssertionError, 'membership|Unexpected filesystem delta'):
                    self.verify()

    def test_inventory_directories_devices_and_temporal_companion(self):
        entries = [self.member('empty/'), self.member('dev/null', tarfile.CHRTYPE, devmajor=1, devminor=3)]
        self.control(entries)
        inv = image.inventory(self.root / 'old.tar')
        self.assertIn('empty', inv)
        self.assertEqual(inv['empty']['mode'], 0o755)
        self.assertEqual(inv['dev/null']['devmajor'], 1)
        entries[0].mtime = 12345
        entries[0].pax_headers = {'atime': '123', 'ctime': '456', 'mtime': '12345'}
        self.rootfs('new', entries)
        self.verify()


    def test_duplicate_aliases_main(self):
        cases = [
            [self.member('usr/src/app/built/server.js', tarfile.REGTYPE, size=len(self.files['usr/src/app/built/server.js']))],
            [self.member('empty/'), self.member('./empty/')],
            [self.member('empty/'), self.member('empty', tarfile.REGTYPE)],
            [self.member('.'), self.member('./')],
        ]
        for extras in cases:
            with self.subTest(names=[entry.name for entry in extras]):
                regular = extras[0].name == 'usr/src/app/built/server.js'
                self.control([] if regular else extras[:1])
                self.rootfs('new', [] if regular else extras)
                if regular:
                    with tarfile.open(self.root / 'new.tar', 'a') as tf:
                        entry = self.member(extras[0].name, tarfile.REGTYPE, size=len(self.files[extras[0].name]), mode=0o644)
                        tf.addfile(entry, io.BytesIO(self.files[entry.name]))
                with self.assertRaisesRegex(AssertionError, 'Duplicate canonical member'):
                    self.verify()

    def test_bad_names_and_whiteouts_main(self):
        names = ['/etc/escape', '../escape', 'opt/../escape', 'opt//escape',
                 'opt/./escape', 'opt/.wh.hidden', 'opt/.wh..wh..opq',
                 '././opt/escape', '', 'opt//']
        for name in names:
            with self.subTest(name=name):
                self.control([])
                # Same invalid input on both sides prevents mere set inequality from hiding a parser gap.
                extras = [self.member(name)]
                self.rootfs('old', extras)
                self.rootfs('new', extras)
                with self.assertRaisesRegex(AssertionError, 'Invalid member name|Whiteout'):
                    self.verify()

    def test_legitimate_links_and_docker_spelling_main(self):
        entries = [self.member('./'), self.member('./etc/'), self.member('etc/alternatives/'),
                   self.member('usr/bin/tool', tarfile.REGTYPE, size=3),
                   self.member('etc/alternatives/tool', tarfile.SYMTYPE, linkname='/usr/bin/tool'),
                   self.member('usr/bin/tool-relative', tarfile.SYMTYPE, linkname='../../etc/alternatives/tool'),
                   self.member('usr/bin/tool-hard', tarfile.LNKTYPE, linkname='./usr/bin/tool'),
                   self.member('run/fifo', tarfile.FIFOTYPE),
                   self.member('dev/block', tarfile.BLKTYPE, devmajor=8, devminor=0)]
        self.control(entries)
        inv = image.inventory(self.root / 'old.tar')
        self.assertEqual(inv['etc/alternatives/tool']['link'], '/usr/bin/tool')
        self.assertEqual(inv['usr/bin/tool-relative']['link'], '../../etc/alternatives/tool')
        self.assertEqual(inv['usr/bin/tool-hard']['link'], './usr/bin/tool')
        self.assertIn('.', inv)

    def test_hardlink_semantics_main(self):
        cases = [
            [self.member('hard', tarfile.LNKTYPE, linkname='missing')],
            [self.member('hard', tarfile.LNKTYPE, linkname='loop'), self.member('loop', tarfile.LNKTYPE, linkname='hard')],
            [self.member('directory/'), self.member('hard', tarfile.LNKTYPE, linkname='directory')],
            [self.member('hard', tarfile.LNKTYPE, linkname='../outside')],
        ]
        for entries in cases:
            with self.subTest(targets=[entry.linkname for entry in entries]):
                self.control([])
                self.rootfs('old', entries)
                self.rootfs('new', entries)
                with self.assertRaisesRegex(AssertionError, 'Invalid hardlink'):
                    self.verify()

    def test_pax_logical_name_main(self):
        self.control([self.member('placeholder', pax_headers={'path': './long/empty/'})])
        self.assertIn('long/empty', image.inventory(self.root / 'old.tar'))
        for logical in ['../bad', '/absolute', 'bad//part', 'bad//']:
            with self.subTest(logical=logical):
                entries = [self.member('placeholder', pax_headers={'path': logical})]
                self.rootfs('old', entries)
                self.rootfs('new', entries)
                with self.assertRaisesRegex(AssertionError, 'Invalid member name'):
                    self.verify()

    def test_archive_formats_logical_names_and_aliases(self):
        for archive_format in [tarfile.USTAR_FORMAT, tarfile.PAX_FORMAT, tarfile.GNU_FORMAT]:
            with self.subTest(archive_format=archive_format):
                # Long path uses USTAR prefix, PAX path or GNU longname respectively.
                name = 'prefix/' * 10 + 'd' * 50
                for which in ['old', 'new']:
                    self.rootfs(which, [self.member(name + '/')], archive_format)
                self.verify()
                self.assertIn(name, image.inventory(self.root / 'old.tar'))
                for which in ['old', 'new']:
                    self.rootfs(which, [self.member(name + '//')], archive_format)
                with self.assertRaisesRegex(AssertionError, 'Invalid member name'):
                    self.verify()
        self.control([self.member('dir/')])
        self.rootfs('new', [self.member('dir/'), self.member('placeholder', pax_headers={'path': './dir/'})])
        with self.assertRaisesRegex(AssertionError, 'Duplicate canonical member'):
            self.verify()

    def test_forward_hardlinks_absolute_and_relative_targets(self):
        entries = [self.member('first', tarfile.LNKTYPE, linkname='/chain'),
                   self.member('chain', tarfile.LNKTYPE, linkname='usr/bin/../bin/tool'),
                   self.member('usr/bin/tool', tarfile.REGTYPE, size=3),
                   self.member('symbolic', tarfile.SYMTYPE, linkname='../../missing'),
                   self.member('symbolic-hard', tarfile.LNKTYPE, linkname='symbolic')]
        self.control(entries)
        proof = json.loads((self.root / 'proof.json').read_text())
        self.assertTrue(proof['configEqual'])
        self.assertEqual(proof['base']['usr/src/app/dist-frontend/index.html'],
                         proof['candidate']['usr/src/app/dist-frontend/index.html'])
        for value in ['new target', '/new/target', '../new/target']:
            with self.subTest(link=value):
                self.control(entries)
                changed = [*entries[:-2], self.member('symbolic', tarfile.SYMTYPE, linkname=value), entries[-1]]
                self.rootfs('new', changed)
                with self.assertRaisesRegex(AssertionError, 'Unexpected filesystem delta'):
                    self.verify()

    def test_unsupported_type_and_non_directory_slash(self):
        for entry in [self.member('unknown', b'Z'), self.member('not-dir/', tarfile.REGTYPE)]:
            with self.subTest(kind=entry.type):
                self.control([])
                self.rootfs('old', [entry])
                self.rootfs('new', [entry])
                with self.assertRaisesRegex(AssertionError, 'Unsupported rootfs member type|Invalid member name'):
                    self.verify()

    def test_hardlinked_compiled_content_is_not_an_unchecked_alias(self):
        target = 'usr/src/app/built/docker/DockerApi.js'
        # An unchanged hardlink spelling must not conceal changed content at a
        # third filesystem path when its allowed compiled referent changes.
        alias = self.member('usr/src/app/built/unrelated-alias.js', tarfile.LNKTYPE, linkname=target)
        self.control([])
        self.rootfs('old', [alias])
        self.rootfs('new', [alias])
        with self.assertRaisesRegex(AssertionError, 'Unexpected filesystem delta'):
            self.verify()

    def test_empty_symlink_target_is_not_a_rootfs_link(self):
        self.control([self.member('link', tarfile.SYMTYPE, linkname='/valid')])
        for which in ['old', 'new']:
            self.rootfs(which, [self.member('link', tarfile.SYMTYPE, linkname='')])
        with self.assertRaisesRegex(AssertionError, 'Invalid symlink'):
            self.verify()

    def test_metadata_type_and_link_changes_main(self):
        cases = [('uid', 1), ('gid', 2), ('mode', 0o777), ('type', tarfile.FIFOTYPE),
                 ('devmajor', 3), ('devminor', 4), ('uname', 'other'), ('gname', 'other'),
                 ('pax_headers', {'SCHILY.xattr.user.example': 'different'})]
        for field, value in cases:
            with self.subTest(field=field):
                kind = tarfile.CHRTYPE if field in {'devmajor', 'devminor'} else tarfile.DIRTYPE
                self.control([self.member('empty', kind)])
                self.rootfs('new', [self.member('empty', kind, **{field: value})])
                with self.assertRaisesRegex(AssertionError, 'Unexpected filesystem delta'):
                    self.verify()
        self.control([self.member('link', tarfile.SYMTYPE, linkname='/target')])
        self.rootfs('new', [self.member('link', tarfile.SYMTYPE, linkname='../target')])
        with self.assertRaisesRegex(AssertionError, 'Unexpected filesystem delta'):
            self.verify()


class ParserNameBoundaryTests(unittest.TestCase):
    """Real tarfile + main(), all archive/proof/compiled I/O held in RAM."""
    server = 'usr/src/app/built/server.js'
    fixture_proofs = []

    def block(self, name, data=b'', kind=tarfile.REGTYPE):
        entry = tarfile.TarInfo(name)
        entry.type, entry.mode, entry.size = kind, 0o644, len(data)
        return entry.tobuf(tarfile.USTAR_FORMAT) + data + b'\0' * (-len(data) % 512)

    def extension(self, kind, value):
        if kind == 'gnu':
            return self.block('././@LongLink', value.encode() + b'\0', tarfile.GNUTYPE_LONGNAME)
        entry = tarfile.TarInfo('placeholder')
        entry.pax_headers = {'path': value}
        return entry.tobuf(tarfile.PAX_FORMAT)[:-512]

    def archive_bytes(self, changed, extensions=(), extras=()):
        files = {
            'usr/src/app/dist-frontend/index.html': b'synthetic UI',
            self.server: b'synthetic unchanged backend',
            **{name: (b'candidate ' if changed else b'original ') + name.encode()
               for name in image.ALLOWED},
        }
        blocks = []
        for name, data in files.items():
            prefix = b''.join(self.extension(kind, value) for kind, value in extensions) if name == self.server else b''
            blocks.append(prefix + self.block(name, data))
        blocks.extend(self.block(name, kind=tarfile.DIRTYPE) for name in extras)
        return b''.join(blocks) + b'\0' * 1024

    def boundary(self, extensions=(), extras=()):
        old = self.archive_bytes(False, extras=extras)
        new = self.archive_bytes(True, extensions, extras)
        # Fixture certification is outside main() and outside the refusal context.
        native_open = tarfile.open
        with native_open(fileobj=io.BytesIO(new), mode='r:') as tf:
            entries = list(tf)
        actual = entries[1]
        self.assertTrue(actual.isfile())
        self.assertEqual(actual.size, len(b'synthetic unchanged backend'))
        self.fixture_proofs.append({'extensions': list(extensions), 'entry_name': actual.name,
                                    'pax_path': actual.pax_headers.get('path'), 'extras': list(extras)})
        config = json.dumps([{'Architecture': 'amd64', 'Os': 'linux', 'Config': {'Cmd': ['node', './built/server.js']}}])
        outputs = {}

        def open_archive(path, mode):
            return native_open(fileobj=io.BytesIO({'old.tar': old, 'new.tar': new}[str(path)]), mode=mode)

        def read_bytes(path):
            label, relative = str(path).split('/', 1)
            return (b'original ' if label == 'baseline' else b'candidate ') + ('usr/src/app/built/' + relative).encode()

        def run():
            with patch.object(image.tarfile, 'open', open_archive), \
                 patch.object(Path, 'read_text', lambda path: config), \
                 patch.object(Path, 'read_bytes', read_bytes), \
                 patch.object(Path, 'write_text', lambda path, text: outputs.update(proof=json.loads(text))), \
                 patch.object(sys, 'argv', ['image-contract.py', 'old.tar', 'new.tar', 'old.json', 'new.json', 'baseline', 'candidate', 'proof.json']), \
                 contextlib.redirect_stdout(io.StringIO()):
                image.main()
            return outputs['proof']
        return actual, run

    def positive(self, extensions=(), extras=()):
        actual, run = self.boundary(extensions, extras)
        proof = run()
        self.assertEqual(proof['status'], 'PASS')
        self.assertEqual(set(proof['filesystemChangedPaths']), image.ALLOWED)
        return actual

    def test_unsafe_gnu_effective_name_cannot_hide_behind_pax_main(self):
        for unsafe in ['../escape', '/etc/escape']:
            with self.subTest(unsafe=unsafe):
                self.positive([('pax', self.server)])
                self.positive([('gnu', self.server)])
                actual, run = self.boundary([('gnu', unsafe), ('pax', self.server)])
                self.assertEqual(actual.name, unsafe)
                self.assertEqual(actual.pax_headers['path'], self.server)
                with self.assertRaisesRegex(AssertionError, 'Invalid member name|Ambiguous member name'):
                    run()

    def test_conflicting_extension_orders_and_safe_collisions_main(self):
        for order in ['gnu-first', 'pax-first']:
            for alternate in ['../escape', '/etc/escape', 'usr/src/app/dist-frontend/index.html', 'other-safe.js']:
                with self.subTest(order=order, alternate=alternate):
                    self.positive([('gnu', self.server), ('pax', self.server)])
                    extensions = [('gnu', alternate), ('pax', self.server)]
                    if order == 'pax-first':
                        extensions.reverse()
                    actual, run = self.boundary(extensions)
                    self.assertEqual(actual.name, alternate if order == 'gnu-first' else self.server)
                    self.assertEqual(actual.pax_headers['path'], self.server)
                    with self.assertRaisesRegex(AssertionError, 'Invalid member name|Ambiguous member name'):
                        run()

    def test_legitimate_formats_directory_slash_and_root_aliases_main(self):
        for extensions in [(), [('pax', self.server)], [('gnu', self.server)],
                           [('pax', self.server), ('gnu', self.server)],
                           [('gnu', self.server), ('pax', self.server)]]:
            for root in ['.', './']:
                with self.subTest(extensions=extensions, root=root):
                    actual = self.positive(extensions, [root, 'empty/'])
                    self.assertEqual(actual.name, self.server)


class RegistryContractTests(unittest.TestCase):
    def test_fresh_tag_404_only(self):
        env = {'IMAGE': 'ghcr.io/voltdatalab/caprover:1.15.5-no-global-prune-' + 'a' * 40,
               'GHCR_ACTOR': 'synthetic', 'GHCR_TOKEN': 'synthetic-not-a-secret'}
        for status in [200, 401, 403, 404, 429, 500]:
            with self.subTest(status=status):
                token_response = MagicMock()
                token_response.__enter__.return_value = io.StringIO('{"token":"synthetic"}')
                manifest = MagicMock()
                outcomes = [token_response, manifest if status == 200 else
                            urllib.error.HTTPError('https://ghcr.io/synthetic', status, 'synthetic', Message(), None)]
                with patch.dict(os.environ, env), patch.object(registry.urllib.request, 'urlopen', side_effect=outcomes), contextlib.redirect_stdout(io.StringIO()):
                    if status == 404:
                        registry.main()
                    else:
                        with self.assertRaises(RuntimeError):
                            registry.main()


class WorkflowAdmissionTests(unittest.TestCase):
    def test_exact_dispatch_ref_and_repository(self):
        text = (ROOT / '.github/workflows/publish-prune-fix-ghcr.yml').read_text()
        admission = text.split('        run: |\n', 1)[1].split('\n      - uses:', 1)[0]
        script = '\n'.join(line[10:] for line in admission.splitlines())
        expected = {'EVENT': 'workflow_dispatch', 'REPOSITORY': 'voltdatalab/caprover',
                    'REF': 'refs/heads/fix/nuclito-captain-1.15.5-no-global-prune', 'SOURCE_SHA': 'a' * 40}
        cases = [{}, {'EVENT': 'push'}, {'REF': 'refs/heads/test/no-post-update-prune'},
                 {'REF': 'refs/heads/master'}, {'REF': expected['REF'].upper()},
                 {'REPOSITORY': 'other/caprover'}, {'REPOSITORY': 'VoltDataLab/caprover'}, {'SOURCE_SHA': 'bad'}]
        with tempfile.TemporaryDirectory(prefix='captain-admission-') as temp:
            for case in cases:
                with self.subTest(case=case):
                    env = {**os.environ, **expected, **case, 'GITHUB_ENV': temp + '/env',
                           'PROOF': temp + '/proof', 'BASELINE': temp + '/baseline'}
                    result = subprocess.run(['bash', '-c', script], env=env, capture_output=True)
                    self.assertEqual(result.returncode == 0, not bool(case), result.stderr)
        self.assertEqual(text.count('workflow_dispatch:'), 1)
        self.assertNotIn('push:', text)
        self.assertNotIn('pull_request:', text)
        self.assertNotIn('environment:', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
