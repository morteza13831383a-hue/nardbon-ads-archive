"""Read bounded pages from the documented ad API, after the live feed has run."""
from collections import Counter
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
import re
import time

import requests


def dates(cutoff):
    end = datetime.fromisoformat(cutoff).date()
    start = date(2026, 4, 1)
    # Two disjoint days containing known ads test that the API really applies both bounds.
    probes = ['2026-09-15', '2026-06-15']
    return list(dict.fromkeys(probes + [(start + timedelta(days=n)).isoformat()
                                        for n in range((end - start).days + 1)]))


def retry_seconds(response):
    raw = response.headers.get('Retry-After', '')
    try:
        return max(1800, int(raw))
    except ValueError:
        try:
            return max(1800, int(parsedate_to_datetime(raw).timestamp() - time.time()))
        except (ValueError, TypeError, OverflowError):
            return 1800


def has_another(rows, meta, offset, limit):
    if meta.get('offsetClamped'):
        return 'plan_limit'
    nxt = offset + len(rows)
    max_offset = meta.get('maxOffset')
    reachable = meta.get('totalReachable')
    if (isinstance(max_offset, int) and nxt > max_offset) or (isinstance(reachable, int) and nxt >= reachable):
        if not isinstance(meta.get('total'), int) or nxt < meta['total'] or meta.get('totalApprox'):
            return 'plan_limit'
    if not rows or meta.get('hasMore') is False:
        return 'read'
    if isinstance(meta.get('total'), int) and not meta.get('totalApprox') and nxt >= meta['total']:
        return 'read'
    if len(rows) < limit and meta.get('hasMore') is not True:
        return 'read'
    return 'more'


def collect(ads, state, cutoff, budget, sync):
    now = int(time.time())
    history = state.setdefault('ad_history_v1', {})
    if (not sync.KEY or history.get('hold') or now < history.get('next_at', 0)
            or int(sync.API_INFO.get('X-RateLimit-Remaining') or 0) < budget + 20):
        return
    history['next_at'] = now + 45 * 60
    calendar = dates(cutoff)
    done_days = history.setdefault('days', {})
    done_advertisers = history.setdefault('advertisers', {})
    counts = Counter(sync.advertiser_slug(a) for a in ads.values() if a.get('geo') == 'IR')
    previous = (state.get('adv_sweep') or {}).get('done') or {}
    slugs = [s for s, n in counts.most_common() if re.fullmatch(r'[a-z0-9_-]{3,80}', s or '') and s not in previous]
    report = {'requests': 0, 'new': 0, 'dates_new': 0, 'advertisers_new': 0, 'pages': []}
    history['last'] = report
    for turn in range(budget):
        if sync.time_left() < 155:
            break
        daily = next((d for d in calendar if not history.get('date_unsupported') and
                      done_days.get(d, {}).get('status', 'more') == 'more'), None)
        slug = next((s for s in slugs if not history.get('advertiser_unsupported') and
                     done_advertisers.get(s, {}).get('status', 'more') == 'more'), None)
        # Validate the date route first, then keep both routes progressing.
        probes_left = any(d not in done_days for d in calendar[:2])
        by_day = bool(daily and (probes_left or not slug or turn % 2 == 0))
        if by_day and probes_left:
            daily = next(d for d in calendar[:2] if d not in done_days)
        target = daily if by_day else slug
        if not target:
            break
        cursors = done_days if by_day else done_advertisers
        cursor = cursors.get(target, {})
        offset = cursor.get('offset', 0)
        params = {'geo': 'IR', 'limit': 100, 'offset': offset}
        if by_day:
            tomorrow = (date.fromisoformat(daily) + timedelta(days=1)).isoformat()
            params.update({'from': daily, 'to': tomorrow})
        else:
            params['advertiser'] = slug
        time.sleep(5)
        report['requests'] += 1
        try:
            response = sync.SESSION.get(sync.API + '/ads', params=params, timeout=(5, 20), allow_redirects=False)
            if response.status_code == 429:
                history['next_at'] = now + retry_seconds(response)
                report['stop'] = 'HTTP 429'
                break
            if response.status_code in (401, 403) or response.is_redirect:
                history['hold'] = 'Access response ' + str(response.status_code)
                break
            if response.status_code in (400, 422) and by_day:
                history['date_unsupported'] = 'Filter rejected with HTTP ' + str(response.status_code)
                continue
            if response.status_code != 200:
                report['stop'] = 'HTTP ' + str(response.status_code)
                break
            if 'text/html' in response.headers.get('Content-Type','').lower():
                history['hold'] = 'Unexpected HTML instead of API data'
                break
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            report['stop'] = type(exc).__name__
            break
        rows = data.get('data') if isinstance(data, dict) else None
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            history['hold'] = 'Unexpected API response schema'
            break
        if by_day and ((not rows and target in calendar[:2] and not offset) or
                       any(str(r.get('firstSeenAt', ''))[:10] != daily for r in rows)):
            history['date_unsupported'] = 'Known day empty or firstSeenAt outside requested UTC day'
            continue
        if not by_day and any(sync.advertiser_slug({'advertiser_slug': r.get('advertiserSlug'),
                                                   'advertiser': r.get('advertiserName')}) != slug for r in rows):
            history['advertiser_unsupported'] = 'Returned advertiser differed from requested advertiser'
            break
        new = 0
        ids = [str(r.get('id') or '') for r in rows]
        if any(not re.fullmatch(r'c[a-z0-9]{24}', i) for i in ids):
            report['stop'] = 'Unexpected ad identifier'
            break
        repeated = bool(offset and rows and cursor.get('first_id') == ids[0])
        if not repeated:
            for r in rows:
                if r['id'] in ads or r.get('geo') != 'IR':
                    continue
                try:
                    seen = datetime.fromisoformat(r['firstSeenAt'].replace('Z', '+00:00'))
                    if seen.tzinfo is None or seen > datetime.fromisoformat(cutoff):
                        continue
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
                new += sync.merge(ads, r, 'history_date' if by_day else 'history_advertiser') == 'new'
        status = 'repeated' if repeated else has_another(rows, data.get('meta') or {}, offset, 100)
        cursors[target] = dict(offset=offset + len(rows), status=status, first_id=ids[0] if ids else None)
        report['pages'].append({'via': 'date' if by_day else 'advertiser', 'target': target,
                                'offset': offset, 'rows': len(rows), 'new': new, 'status': status})
        report['new'] += new
        report['dates_new' if by_day else 'advertisers_new'] += new
        if int(response.headers.get('X-RateLimit-Remaining') or 0) < 21:
            report['stop'] = 'Quota reserve'
            break
    sync.log('Historical ad API', {k: v for k, v in report.items() if k != 'pages'},
             history.get('date_unsupported'), history.get('hold'))
