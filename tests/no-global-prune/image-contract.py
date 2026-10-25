"""CI-only offline image proof. Exports never start Captain or mount Docker in it."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ALLOWED = {'usr/src/app/built/docker/DockerApi.js',
           'usr/src/app/built/docker/DockerApi.js.map'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical_name(raw, directory):
    """Docker export names, not link targets: one optional ./ and directory /."""
    if directory and raw in {'.', './'}:
        return '.'
    assert raw and not raw.startswith('/') and '\x00' not in raw, 'Invalid member name'
    name = raw.removeprefix('./')
    if directory:
        name = name.removesuffix('/')
    parts = name.split('/')
    assert all(part not in {'', '.', '..'} for part in parts), 'Invalid member name'
    assert not any(part.startswith('.wh.') for part in parts), 'Whiteout in flat rootfs export'
    return name


def logical_name(tf, entry):
    """Validate parser authority, then reconcile preserved header spelling.

    Only directories admit canonical ./ and single trailing-/ equivalence (root
    . or ./ included). Regular names must agree literally. PAX/GNU may replace a
    truncated/placeholder physical USTAR name, but must agree with each other and
    entry.name; never choose a different winner than tarfile. Without extensions,
    retain the raw USTAR spelling check: tarfile may strip multiple directory /.
    Read only archive offsets, never member names as host paths.
    """
    effective = canonical_name(entry.name, entry.isdir())
    position = entry.offset
    saved = tf.fileobj.tell()
    spellings = []
    raw = entry.name
    try:
        while position < entry.offset_data:
            tf.fileobj.seek(position)
            header = tf.fileobj.read(512)
            info = tarfile.TarInfo.frombuf(header, tf.encoding, tf.errors)
            if info.type == tarfile.GNUTYPE_LONGNAME:
                spellings.append(tarfile.nts(tf.fileobj.read(info.size), tf.encoding, tf.errors))
            raw = tarfile.nts(header[:100], tf.encoding, tf.errors)
            if header[345:500].strip(b'\x00') and header[257:263] == b'ustar\x00':
                raw = tarfile.nts(header[345:500], tf.encoding, tf.errors) + '/' + raw
            position += 512
            if info.type in {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                              tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK}:
                position += (info.size + 511) // 512 * 512
        if 'path' in entry.pax_headers:
            spellings.append(entry.pax_headers['path'])
        for spelling in spellings or [raw]:
            canonical = canonical_name(spelling, entry.isdir())
            assert canonical == effective and (entry.isdir() or spelling == entry.name), 'Ambiguous member name'
        return entry.name
    finally:
        tf.fileobj.seek(saved)


def hardlink_target(raw):
    """Archive-root-relative hardlink lookup, allowing / and lexical ./.. safely."""
    assert raw and '\x00' not in raw, 'Invalid hardlink target'
    parts = []
    for part in raw.split('/'):
        if part in {'', '.'}:
            continue
        if part == '..':
            assert parts, 'Invalid hardlink target escapes rootfs'
            parts.pop()
        else:
            parts.append(part)
    assert parts, 'Invalid hardlink target'
    return '/'.join(parts)


def inventory(archive):
    """Inventory a complete flat docker export (never an OCI layer)."""
    result = {}
    supported = {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE,
                 tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE,
                 tarfile.BLKTYPE, tarfile.FIFOTYPE}
    with tarfile.open(archive, 'r:') as tf:
        for entry in tf:
            name = canonical_name(logical_name(tf, entry), entry.isdir())
            assert name not in result, f'Duplicate canonical member: {name}'
            assert entry.type in supported and entry.sparse is None, 'Unsupported rootfs member type'
            if entry.issym():
                assert entry.linkname and '\x00' not in entry.linkname, 'Invalid symlink target'
            value = {'type': entry.type.decode(), 'mode': entry.mode,
                     'uid': entry.uid, 'gid': entry.gid, 'link': entry.linkname,
                     'devmajor': entry.devmajor, 'devminor': entry.devminor,
                     'uname': entry.uname, 'gname': entry.gname, 'bytes': entry.size,
                     'pax': {key: value for key, value in entry.pax_headers.items()
                             if key not in {'path', 'linkpath', 'mtime', 'atime', 'ctime'}}}
            if entry.isfile():
                stream = tf.extractfile(entry)
                assert stream is not None
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
                value.update(sha256=digest.hexdigest())
            result[name] = value
    # Resolve only hardlinks, entirely within this inventory. Symlinks may be
    # absolute, relative, dangling or cyclic; those are legitimate rootfs data.
    resolved = {}
    for name, value in result.items():
        if value['type'] != tarfile.LNKTYPE.decode():
            continue
        current, seen = name, set()
        while current not in resolved:
            assert current in result and current not in seen, 'Invalid hardlink dangling or cyclic'
            seen.add(current)
            target = result[current]
            assert target['type'] != tarfile.DIRTYPE.decode(), 'Invalid hardlink to directory'
            if target['type'] != tarfile.LNKTYPE.decode():
                break
            current = hardlink_target(target['link'])
        final = resolved.get(current, current)
        resolved.update({path: final for path in seen})
        # A hardlink to an allowed compiled file also changes this path's
        # effective content. Keep that visible outside the two-file allowlist.
        value['resolved'] = result[final].copy()
    return result


def main():
    base_tar, candidate_tar, base_inspect, candidate_inspect, baseline_built, candidate_built, output = sys.argv[1:]
    old = inventory(base_tar)
    new = inventory(candidate_tar)
    assert set(old) == set(new), 'Filesystem membership changed'
    changed = sorted(name for name in old if old[name] != new[name])
    assert set(changed) == ALLOWED, f'Unexpected filesystem delta: {changed}'
    frontend = {name: value for name, value in old.items()
                if name.startswith('usr/src/app/dist-frontend/')}
    assert 'usr/src/app/dist-frontend/index.html' in frontend
    assert all(new[name] == value for name, value in frontend.items())
    for name in ALLOWED:
        relative = name.removeprefix('usr/src/app/built/')
        assert old[name]['sha256'] == sha((Path(baseline_built) / relative).read_bytes()), 'Official/backend base mismatch'
        assert new[name]['sha256'] == sha((Path(candidate_built) / relative).read_bytes()), 'Candidate/backend mismatch'
    old_info = json.loads(Path(base_inspect).read_text())[0]
    new_info = json.loads(Path(candidate_inspect).read_text())[0]
    assert old_info['Architecture'] == new_info['Architecture'] == 'amd64'
    assert old_info['Os'] == new_info['Os'] == 'linux'
    assert old_info['Config'] == new_info['Config'], 'Runtime Config (including Entrypoint/Cmd/Env/User) changed'
    result = {'status': 'PASS', 'startedContainers': 0,
              'filesystemChangedPaths': changed, 'uiFileCount': len(frontend),
              'configEqual': True, 'base': old, 'candidate': new}
    Path(output).write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key not in ('base', 'candidate')}))


if __name__ == '__main__':
    main()
