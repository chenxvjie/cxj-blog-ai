import hashlib
import io
import json
import tarfile

import pytest

from deploy.artifact import unpack


def artifact(tmp_path, *, bad_digest=False, traversal=False, bad_tag=False):
    commit = 'a' * 40
    name = 'cxj-blog-ai:flow-' + commit
    image = io.BytesIO()
    with tarfile.open(fileobj=image, mode='w') as archive:
        data = json.dumps([{'RepoTags': ['other:latest' if bad_tag else name]}]).encode()
        member = tarfile.TarInfo('manifest.json')
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    image_data = image.getvalue()
    metadata = json.dumps({'commit': commit, 'image': name, 'sha256': '0'*64 if bad_digest else hashlib.sha256(image_data).hexdigest()}).encode()
    source = tmp_path / 'package.tgz'
    with tarfile.open(source, 'w:gz') as archive:
        for filename, data in [('image.tar', image_data), ('../release.json' if traversal else 'release.json', metadata)]:
            member = tarfile.TarInfo(filename)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return source, name


def test_valid_image_artifact(tmp_path):
    source, name = artifact(tmp_path)
    assert unpack(source, tmp_path / 'out') == name


@pytest.mark.parametrize('option', ['bad_digest', 'traversal', 'bad_tag'])
def test_rejects_invalid_artifact_before_docker_load(tmp_path, option):
    source, _ = artifact(tmp_path, **{option: True})
    with pytest.raises(ValueError):
        unpack(source, tmp_path / 'out')
