#!/usr/bin/env bash
set -Eeuo pipefail
commit=$(git rev-parse HEAD)
[[ "$commit" =~ ^[0-9a-f]{40}$ ]]
image="cxj-blog-ai:flow-$commit"
docker build --target verify --build-arg VCS_REF="$commit" .
docker build --target runtime --build-arg VCS_REF="$commit" -t "$image" .
mkdir -p release
docker save "$image" -o release/image.tar
python3 - "$commit" "$image" <<'PY'
import hashlib,json,pathlib,sys
artifact=pathlib.Path('release/image.tar')
with artifact.open('rb') as f:
    checksum=hashlib.sha256()
    for block in iter(lambda:f.read(1024*1024),b''):
        checksum.update(block)
    digest=checksum.hexdigest()
pathlib.Path('release/release.json').write_text(json.dumps({'commit':sys.argv[1],'image':sys.argv[2],'sha256':digest}),encoding='utf-8')
PY
