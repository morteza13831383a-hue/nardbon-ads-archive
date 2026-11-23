#!/usr/bin/env python3
"""Mirror committed channel source buckets to the download host, byte for byte."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import sync


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    'channel-html-import', 'combot-import', 'lyzem-import',
    'telemetr-import', 'tgchannelsme-import', 'tglist-import',
    'tgstat-import', 'telegramsearchengine-import',
)


def build_manifest(root=ROOT):
    files = []
    for source in SOURCES:
        for path in sorted((root / 'data' / source).glob('*.jsonl.gz')):
            relative = path.relative_to(root / 'data').as_posix()
            with path.open('rb') as handle:
                digest = hashlib.file_digest(handle, 'sha256').hexdigest()
            files.append({
                'source_path': relative,
                'host_path': relative + '.txt',
                'bytes': path.stat().st_size,
                'sha256': digest,
            })
    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'committed data/*-import/*.jsonl.gz buckets',
        'files': files,
        'host_filename_note': 'Remove the final .txt to restore the .jsonl.gz extension; bytes are unchanged.',
    }


def main():
    manifest = build_manifest()
    remote_root = f'{sync.REMOTE_ROOT}/exports/source-buckets'
    with sync.Uploader() as uploader:
        for item in manifest['files']:
            uploader.put(ROOT / 'data' / item['source_path'],
                         f"{remote_root}/{item['host_path']}")
        local_manifest = ROOT / 'outbox/source-buckets-manifest.json'
        local_manifest.parent.mkdir(parents=True, exist_ok=True)
        local_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        uploader.put(local_manifest, f'{remote_root}/manifest.json')
    print('Source buckets uploaded:', len(manifest['files']), flush=True)


if __name__ == '__main__':
    main()
