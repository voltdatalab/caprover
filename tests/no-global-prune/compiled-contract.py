"""Compare the full local backend build inventories, not just selected filenames."""
import hashlib
import json
from pathlib import Path
import sys


def inventory(root):
    root = Path(root)
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def main():
    old, new = map(inventory, sys.argv[1:3])
    assert set(old) == set(new), 'Compiled file membership changed'
    changed = sorted(name for name in old if old[name] != new[name])
    assert changed == ['docker/DockerApi.js', 'docker/DockerApi.js.map'], changed
    print(json.dumps({'status': 'PASS', 'changed': changed,
                      'base': old, 'candidate': new}, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
