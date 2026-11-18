#!/usr/bin/env python3
"""Publish collected HTML archive through the existing upload chain; no source API."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

import sync


def host_export_name(name):
    """The download receiver accepts text paths; preserve source bytes and names."""
    if name.endswith(('.csv', '.jsonl.gz')):
        return name + '.txt'
    return name


def host_export_manifest(source):
    """Document the on-host filename aliases without changing source checksums."""
    manifest=json.loads(source.read_text(encoding='utf-8'))
    for entry in manifest.get('files',[]):
        entry['host_name']=host_export_name(entry['name'])
    manifest['host_filename_note']='Download host uses .txt aliases; remove the final .txt to restore the original filename. File bytes and SHA-256 are unchanged.'
    return manifest


def merge_record(ads, record):
    aid=record['id']
    raw={
        'id':aid,'title':record['title'],'text':record['text'],'niche':record.get('niche'),
        'geo':'IR','lang':record.get('lang'),'advertiserName':record.get('advertiser'),
        'ctaButtonText':record.get('cta_text'),'ctaUrl':record.get('cta_url'),
        'firstSeenAt':record.get('first_seen'),'lastSeenAt':record.get('last_seen'),
        'mediaUrl':record.get('media_src'),
    }
    if aid not in ads:
        sync.merge(ads,raw,'html-archive')
    else:
        current=ads[aid]
        # Never replace fresher API observations with an older historical crawl.
        for key in ('title','text','niche','geo','lang','advertiser','cta_text','cta_url','first_seen'):
            if not current.get(key) and record.get(key):
                current[key]=record[key]
        if record.get('last_seen') and record['last_seen'] > (current.get('last_seen') or ''):
            current['last_seen']=record['last_seen']
        if not current.get('media_src') and record.get('media_src'):
            current['media_src']=record['media_src']
            current['media_status']='pending'
    ads[aid]['html_archive']={
        'source_url':record['source_url'],'collected_at':record['collected_at'],
        'cutoff':record['archive_cutoff'],'html_sha256':record.get('html_sha256'),
        'record_detail':record.get('record_detail','detail'),
        'listing_source_url':record.get('listing_source_url'),
    }


def upload_panel(ads,state,run):
    """Reuse the transport while publishing only the ad panel, never channel files/status."""
    sync.OUT_MEDIA.mkdir(parents=True,exist_ok=True)
    tmp=sync.OUTBOX/'_gen'
    tmp.mkdir(parents=True,exist_ok=True)
    sent_media=sent_files=0
    with sync.Uploader() as up:
        for path in sorted(sync.OUT_MEDIA.glob('*.webp')):
            if sync.time_left()<60:
                break
            up.put(path,f'{sync.REMOTE_ROOT}/media/{path.name}')
            if path.stem in ads:
                ads[path.stem].update(media_file=f'media/{path.name}',media_status='ok')
            path.unlink()
            sent_media+=1
        pending=[ad for ad in ads.values() if not ad.get('sent_at')]
        for ad in pending:
            ad['sent_at']=sync.now_iso()
        try:
            shards=sync.write_shards(ads)
            files=[(path,f'{sync.REMOTE_ROOT}/data/{month}.json') for month,path in shards.items()]
            files += [(path,f'{sync.REMOTE_ROOT}/{path.relative_to(sync.PANEL).as_posix()}') for path in sorted(sync.PANEL.rglob('*')) if path.is_file()]
            for path,remote in files:
                digest=sync.sha(path)
                if state['uploaded'].get(remote)!=digest:
                    up.put(path,remote)
                    state['uploaded'][remote]=digest
                    sent_files+=1
        except Exception:
            for ad in pending:
                ad['sent_at']=None
            raise
        run.update(sent_media=sent_media,sent_files=sent_files,ok=True)
        state['last_upload_ok']=sync.now_iso()
        state['runs'].append(run)
        status,manifest=sync.build_status(ads,state,shards)
        state['runs'].pop()
        for name,obj in (('manifest.json',manifest),('status.json',status)):
            path=tmp/f'archive-{name}'
            path.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
            up.put(path,f'{sync.REMOTE_ROOT}/{name}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir',type=Path,default=sync.ROOT/'archive-work')
    parser.add_argument('--upload',action='store_true',help='Also send data and queued media to the configured host')
    parser.add_argument('--panel-only',action='store_true',help='Keep detailed CSV/gzip exports local until receiver permits their types')
    args=parser.parse_args()
    dbpath=args.work_dir/'checkpoint.sqlite3'
    if not dbpath.exists():
        parser.error('Checkpoint missing; run archive_html.py first')
    ads=sync.load_ads()
    with sqlite3.connect(dbpath) as db:
        for (payload,) in db.execute('SELECT payload FROM records ORDER BY id'):
            merge_record(ads,json.loads(payload))
    state=sync.load_state()
    run={'at':sync.now_iso(),'ok':False,'error':None,'source':'html-archive'}
    stats={'media':0}
    # No run_incremental, run_backfill, channels.step or t.me requests here.
    sync.write_shards(ads)
    if not args.upload:
        print(f'Merged {len(ads)} total panel ads locally; no upload performed',flush=True)
        return
    try:
        sync.fetch_media(ads,stats)
        upload_panel(ads,state,run)
        if args.panel_only:
            print('Panel files uploaded; detailed CSV/gzip exports retained in repository/artifacts until receiver types are enabled',flush=True)
            return
        # Extra detailed exports are additive; existing panel paths stay the same.
        receipt=args.work_dir/'export-receipts.json'
        uploaded=json.loads(receipt.read_text()) if receipt.exists() else {}
        files=sorted((args.work_dir/'exports').glob('ads-*'))
        manifest=args.work_dir/'exports'/'manifest.json'
        host_manifest=args.work_dir/'host-export-manifest.json'
        host_manifest.write_text(json.dumps(host_export_manifest(manifest),ensure_ascii=False,indent=2),encoding='utf-8')
        files.append(host_manifest)  # manifest is published last
        with sync.Uploader() as uploader:
            for path in files:
                digest=hashlib.sha256(path.read_bytes()).hexdigest()
                name='manifest.json' if path==host_manifest else host_export_name(path.name)
                remote=f'{sync.REMOTE_ROOT}/exports/archive-html/{name}'
                if uploaded.get(remote)==digest:
                    continue
                uploader.put(path,remote)
                uploaded[remote]=digest
                temp=receipt.with_suffix('.tmp')
                temp.write_text(json.dumps(uploaded,sort_keys=True),encoding='utf-8')
                temp.replace(receipt)
        state['html_archive_exported_at']=sync.now_iso()
        print('Panel files and detailed archive exports uploaded',flush=True)
    finally:
        sync.write_shards(ads)
        sync.save_state(state)

if __name__=='__main__':
    main()
