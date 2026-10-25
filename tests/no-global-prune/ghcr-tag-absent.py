"""Fail closed unless GHCR authoritatively returns 404 for the exact new tag."""
import base64
import json
import os
import re
import urllib.error
import urllib.request


def main():
    tag = os.environ['IMAGE'].removeprefix('ghcr.io/voltdatalab/caprover:')
    assert re.fullmatch(r'1\.15\.5-no-global-prune-[0-9a-f]{40}', tag)
    credentials = base64.b64encode(
        (os.environ['GHCR_ACTOR'] + ':' + os.environ['GHCR_TOKEN']).encode()
    ).decode()
    request = urllib.request.Request(
        'https://ghcr.io/token?service=ghcr.io&scope=repository:voltdatalab/caprover:pull,push',
        headers={'Authorization': 'Basic ' + credentials})
    with urllib.request.urlopen(request, timeout=30) as response:
        token = json.load(response)['token']
    request = urllib.request.Request(
        'https://ghcr.io/v2/voltdatalab/caprover/manifests/' + tag,
        headers={'Authorization': 'Bearer ' + token,
                 'Accept': 'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'})
    try:
        with urllib.request.urlopen(request, timeout=30):
            raise RuntimeError('Refusing to overwrite an existing commit tag')
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        if status != 404:
            raise RuntimeError('Registry did not prove tag absence') from None
    print('GHCR exact commit tag absent; publication may proceed')


if __name__ == '__main__':
    main()
