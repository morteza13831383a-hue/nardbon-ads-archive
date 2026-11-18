#!/usr/bin/env python3
"""Export the committed channel catalog and upload small parts to the download host."""
import argparse
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

import sync


FIELD_NAMES={
    'u':'username','t':'title','m':'members','v':'avg_views','c':'category',
    'e':'sponsor_eligible','k':'keywords','s':'last_snapshot',
    'd':'description','dd':'description_date','pd':'posts_per_day',
    'lp':'last_post','sr':'source','ia':'iran_advertisers',
    'ic':'iran_creatives','cl':'classification','kd':'type',
    'co':'country','lg':'language','er':'engagement',
    'ts':'tgstat_seen','rg':'region','tl':'tglist_seen',
    'cb':'combot_seen','lz':'lyzem_seen','tm':'telemetr_seen',
    'tc':'tgchannelsme_seen',
}
ROOT=Path(__file__).resolve().parents[1]


def host_name(name):
    return name+'.txt' if name.endswith(('.csv','.jsonl.gz')) else name


def csv_cell(value):
    if value is None:return ''
    if isinstance(value,(dict,list)):value=json.dumps(value,ensure_ascii=False,separators=(',',':'))
    value=str(value)
    return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value


def export(source,out,max_part_bytes=2_000_000):
    data=json.loads(source.read_text(encoding='utf-8'))
    fields=[FIELD_NAMES.get(key,key)for key in data['fields']]
    out.mkdir(parents=True,exist_ok=True)
    for old in out.glob('channels-*'):
        old.unlink()
    parts=[];batch=[];size=0

    def write_part(rows,index):
        base=f'channels-{index:05d}'
        gz=out/(base+'.jsonl.gz')
        with gz.open('wb') as raw, gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,filename='') as handle:
            for row in rows:
                handle.write((json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8'))
        csvpath=out/(base+'.csv')
        with csvpath.open('w',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=fields)
            writer.writeheader()
            writer.writerows({key:csv_cell(row.get(key))for key in fields}for row in rows)
        for path in (gz,csvpath):
            parts.append({'name':path.name,'host_name':host_name(path.name),
                          'records':len(rows),'bytes':path.stat().st_size,
                          'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})

    for values in data['rows']:
        row=dict(zip(fields,values))
        encoded=json.dumps(row,ensure_ascii=False,separators=(',',':')).encode('utf-8')
        if batch and size+len(encoded)>max_part_bytes:
            write_part(batch,len(parts)//2+1);batch=[];size=0
        batch.append(row);size+=len(encoded)
    if batch:write_part(batch,len(parts)//2+1)
    manifest={'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),
              'source':'data/channels.json','source_snapshot_at':data.get('at'),
              'records':len(data['rows']),'fields':fields,'files':parts,
              'host_filename_note':'Files use .txt aliases on the download host; remove the final .txt to restore the original extension. Bytes and SHA-256 are unchanged.'}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest


def upload(out,manifest):
    remote_root=f'{sync.REMOTE_ROOT}/exports/channels'
    with sync.Uploader() as uploader:
        for item in manifest['files']:
            uploader.put(out/item['name'],f"{remote_root}/{item['host_name']}")
        uploader.put(out/'manifest.json',f'{remote_root}/manifest.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'data/channels.json')
    parser.add_argument('--out-dir',type=Path,default=ROOT/'outbox/channel-exports')
    parser.add_argument('--upload',action='store_true')
    args=parser.parse_args()
    manifest=export(args.source,args.out_dir)
    if args.upload:upload(args.out_dir,manifest)
    print('Channel export',manifest['records'],'rows',len(manifest['files']),'files',
          'uploaded' if args.upload else 'generated',flush=True)


if __name__=='__main__':main()
