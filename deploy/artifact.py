"""Validate and unpack the two-file Flow image artifact; no arbitrary extraction."""
import hashlib
import json
import pathlib
import re
import shutil
import sys
import tarfile


def unpack(source, destination):
    root = pathlib.Path(destination)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tarfile.open(source, 'r:gz') as archive:
        files = {}
        for item in archive:
            path = pathlib.PurePosixPath(item.name)
            if item.isdir() and path.as_posix() == '.':
                continue
            if not item.isfile() or path.as_posix() not in ('image.tar', 'release.json') or path.as_posix() in files:
                raise ValueError('Artifact must contain only regular image.tar and release.json files')
            if item.size > (16384 if path.name == 'release.json' else 2 * 1024**3):
                raise ValueError('Artifact too large')
            files[path.as_posix()] = item
        if set(files) != {'image.tar', 'release.json'}:
            raise ValueError('Missing artifact files')
        for name, item in files.items():
            with archive.extractfile(item) as incoming, (root / name).open('xb') as output:
                shutil.copyfileobj(incoming, output)
    metadata = json.loads((root / 'release.json').read_text(encoding='utf-8'))
    commit = metadata['commit']
    if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Invalid commit')
    expected = 'cxj-blog-ai:flow-' + commit
    if metadata['image'] != expected:
        raise ValueError('Unexpected image name')
    with (root / 'image.tar').open('rb') as file:
        digest = hashlib.sha256()
        for block in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(block)
        if digest.hexdigest() != metadata['sha256']:
            raise ValueError('Image digest mismatch')
    with tarfile.open(root / 'image.tar') as image:
        member = image.getmember('manifest.json')
        if not member.isfile() or member.size > 65536:
            raise ValueError('Invalid Docker manifest')
        manifest = json.load(image.extractfile(member))
        if len(manifest) != 1 or manifest[0].get('RepoTags') != [expected]:
            raise ValueError('Docker image tag mismatch')
    return expected


if __name__ == '__main__':
    print(unpack(sys.argv[1], sys.argv[2]))
