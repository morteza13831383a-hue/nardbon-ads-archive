#!/usr/bin/env python3
"""Read-only request-based yield for ad discovery from prioritized channel profiles."""
import argparse,json,sqlite3
from pathlib import Path
VIA='persian_channel_priority_profile'
def snapshot(ads_path,channels_path):
    a=sqlite3.connect('file:'+str(ads_path)+'?mode=ro',uri=True)
    c=sqlite3.connect('file:'+str(channels_path)+'?mode=ro',uri=True)
    def exists(db,table):return bool(db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone())
    ad_requests=iran=profiles=0;started=None
    if exists(a,'source_requests'):
        ad_requests,iran,started=a.execute("SELECT COUNT(*),COUNT(DISTINCT CASE WHEN q.status='iran'AND r.status BETWEEN 200 AND 299 THEN r.url END),MIN(r.at)FROM source_requests r LEFT JOIN queue q ON q.url=r.url WHERE r.via=?",(VIA,)).fetchone()
    if exists(c,'requests')and'via'in{r[1]for r in c.execute('PRAGMA table_info(requests)')}:
        profiles=c.execute('SELECT COUNT(*)FROM requests WHERE via=?',(VIA,)).fetchone()[0]
    total=profiles+ad_requests
    result={'strategy':VIA,'measurement_started_at':started,'profile_requests':profiles,'ad_requests':ad_requests,'verified_iran_ads':iran,'iran_per_100_ad_requests':round(100*iran/ad_requests,2)if ad_requests else None,'iran_per_100_profile_plus_ad_requests':round(100*iran/total,2)if total else None,'queue':dict(a.execute('SELECT status,COUNT(*)FROM queue WHERE via=?GROUP BY status',(VIA,))),'profile_queue':dict(c.execute('SELECT status,COUNT(*)FROM priority_profiles GROUP BY status'))if exists(c,'priority_profiles')else{},'cutoff':a.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0],'limitations':['Fresh HTTP request ledger only; excludes pre-existing completed queue rows.','Shared or previously known ads are deduplicated and retain their earlier strategy attribution.','Profile discovery can precede ad verification by a slice; short-window yield is provisional.']}
    a.close();c.close();return result
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--ads-checkpoint',type=Path,required=True);p.add_argument('--channels-checkpoint',type=Path,required=True);args=p.parse_args();print(json.dumps(snapshot(args.ads_checkpoint,args.channels_checkpoint),indent=2))
