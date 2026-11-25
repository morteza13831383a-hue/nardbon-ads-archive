#!/usr/bin/env python3
"""Bounded HTML crawler. Python 3.10+, standard library only; no API calls."""
import json
import logging
import os
import random
import re
import time
from source_budget import reserve, report
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

LOG = logging.getLogger('crawler')
UA = 'IranAdsCollector/1.0'
VOID = set('area base br col embed hr img input link meta param source track wbr'.split())

class Node:
    def __init__(self, tag='', attrs=None):
        self.tag, self.attrs, self.children = tag, attrs or {}, []
    def nodes(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.nodes()
    def text(self):
        return ''.join(c.text() if isinstance(c, Node) else c for c in self.children)

class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break
    def handle_data(self, data):
        self.stack[-1].children.append(data)
    def links(self):
        return [n.attrs['href'] for n in self.root.nodes() if n.tag == 'a' and 'href' in n.attrs]
    def jsonld(self):
        def flatten(value):
            if isinstance(value, list):
                for x in value:
                    yield from flatten(x)
            elif isinstance(value, dict):
                yield value
                yield from flatten(value.get('@graph', []))
        for n in self.root.nodes():
            if n.tag == 'script' and n.attrs.get('type') == 'application/ld+json':
                try:
                    yield from flatten(json.loads(n.text()))
                except (ValueError, TypeError):
                    LOG.warning('Invalid JSON-LD skipped')


def canonical(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, ''))

def origin(url):
    p = urlsplit(url)
    return p.scheme.lower(), p.netloc.lower()

def match(pattern, text):
    return bool(re.search(pattern, text))

# Narrow exceptions the site owner explicitly approved in writing (support, Nov 2026:
# "there is no issue with you crawling our site"). Only the Persian channel listing and its
# pages, including its list filters (category, sort/order, member range, sponsored), so the
# listing can be read in slices. Every other robots.txt rule still applies.
OWNER_APPROVED = {
    'tgadsspy.com': [re.compile(r'^/channels\?lang=fa(?:&(?:page|category|niche|sort|order|minMembers|maxMembers|sponsored)=[A-Za-z0-9_.-]{1,40})*$')],
    # owner-approved by site support, 2026-11: exact ad detail paths on both sister hosts.
    'telegramadsarchive.com': [re.compile(r'^/ads/[A-Za-z0-9_-]+$')],
    'telegramadsanalytics.com': [re.compile(r'^/ads/[A-Za-z0-9_-]+$')],
    # Owner-approved API search, ending 2026-12-16 23:59 UTC in tglist_api_collect.py.
    'tglist.io': [re.compile(r'^/api/search_channels_v2\?page=[0-9]+&page_size=50&lang=fa&value=[^&]*&category_slug=&sort=$')],
}

# Site support additionally approved filtered ad listings on the G.MEDIA mirrors
# (2026-11). Keep this exception on /ads query pages only; other robots rules
# continue to apply. Query values are bounded to the site's filter alphabet.
_MIRROR_LISTING = re.compile(r'^/ads\?(?:[A-Za-z][A-Za-z0-9]*=[A-Za-z0-9_%.,:+-]{0,80})(?:&[A-Za-z][A-Za-z0-9]*=[A-Za-z0-9_%.,:+-]{0,80})*$')
for _mirror_host in ('telegramadsarchive.com', 'telegramadsanalytics.com',
                     'telegramads.it', 'tgads.biz', 'tgads.observer',
                     'telegramstats.net'):
    OWNER_APPROVED.setdefault(_mirror_host, []).append(_MIRROR_LISTING)
    # The owner also approved Persian keyword searches with the Iran filter.
    # A percent-encoded phrase can exceed the generic filter-value bound above.
    OWNER_APPROVED[_mirror_host].append(re.compile(
        r'^/ads\?geo=IR&q=[A-Za-z0-9_%+.\-]{1,512}&page=[0-9]+$'))
    OWNER_APPROVED[_mirror_host].append(re.compile(r'^/ads/[A-Za-z0-9_-]+$'))
    OWNER_APPROVED[_mirror_host].append(re.compile(r'^/channels\?lang=fa(?:&(?:page|minMembers|maxMembers|sort|order)=[A-Za-z0-9_.-]{1,40})*$'))

# Owner-approved Iran niche listing on the primary site; the shared host budget
# also covers its channel collector. Ad detail paths retain their existing rules.
OWNER_APPROVED['tgadsspy.com'].append(re.compile(r'^/ads\?geo=IR&niche=[a-z0-9-]{1,50}&page=[0-9]+$'))


class Robots:
    """robots rules with * and $, longest match and Allow tie priority."""
    def __init__(self, body):
        self.groups = []
        agents, rules, delay = [], [], 0
        seen_rule = False
        for raw in body.splitlines() + ['User-agent: __end__']:
            line = raw.split('#', 1)[0].strip()
            if ':' not in line:
                continue
            key, value = (x.strip() for x in line.split(':', 1))
            key = key.lower()
            if key == 'user-agent':
                if seen_rule:
                    self.groups.append((agents, rules, delay))
                    agents, rules, delay, seen_rule = [], [], 0, False
                agents.append(value.lower())
            elif agents and key in ('allow', 'disallow', 'crawl-delay'):
                seen_rule = True
                if key == 'crawl-delay':
                    try:
                        delay = max(0, float(value))
                    except ValueError:
                        pass
                elif value:
                    rules.append((value, key == 'allow'))
    def selected(self):
        groups = [(max((len(a) for a in aa if a != '*' and a in UA.lower()), default=0), aa, rr, dd)
                  for aa, rr, dd in self.groups]
        longest = max((g[0] for g in groups), default=0)
        return [g for g in groups if g[0] == longest and (longest or '*' in g[1])]
    @property
    def delay(self):
        return max((g[3] for g in self.selected()), default=0)
    def allowed(self, url):
        p = urlsplit(url)
        target = p.path + ('?' + p.query if p.query else '')
        exceptions = OWNER_APPROVED.get((p.hostname or '').lower(), [])
        if p.scheme == 'https' and any(rx.fullmatch(target) for rx in exceptions):
            return True
        hits = []
        for _, _, rules, _ in self.selected():
            for pattern, allow in rules:
                end = pattern.endswith('$')
                pat = pattern[:-1] if end else pattern
                regex = '^' + '.*'.join(re.escape(x) for x in pat.split('*')) + ('$' if end else '')
                if re.search(regex, target):
                    hits.append((len(pat.replace('*', '')), allow))
        return max(hits, default=(0, True))[1]

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Client:
    def __init__(self, base, delay=3, resilient=False):
        self.resilient = resilient
        self.base = base
        self.delay = max(1, delay)
        self.last = 0
        self.opener = build_opener(NoRedirect())
        self.robots = None
    def get(self, url, robots=False):
        for redirect in range(6):
            if origin(url) != origin(self.base):
                raise ValueError('Cross-origin URL/redirect rejected: ' + url)
            if '/api/' in urlsplit(url).path:
                raise ValueError('API paths are disabled')
            if not robots and (not self.robots or not self.robots.allowed(url)):
                raise ValueError('robots.txt disallows: ' + url)
            for attempt in range(3):
                reserve(self.base, self.delay)          # adaptive, shared by all collectors on this host
                self.last = time.monotonic()
                try:
                    headers = {'User-Agent': UA, 'Accept': 'text/html,text/plain'}
                    # Agreed with the site owner: their Cloudflare skips the bot challenge for
                    # requests carrying this header. Unset = normal public access.
                    token = os.environ.get('ARCHIVE_ACCESS_TOKEN', '').strip()
                    if token and origin(self.base) == origin('https://tgadsspy.com'):
                        headers['X-Nardbon-Archive'] = token
                    try:
                        response = self.opener.open(Request(url, headers=headers), timeout=30)
                    except HTTPError as e:
                        if e.code not in (301, 302, 303, 307, 308):
                            body = ''
                            if e.code == 403:
                                try:
                                    body = e.read(20000).decode('utf-8', 'replace')
                                except Exception:
                                    pass
                            from source_budget import retry_after_seconds
                            from source_budget import Challenge, is_challenge
                            challenged = (e.headers or {}).get('cf-mitigated', '').lower() == 'challenge' or is_challenge(body) or 'Just a moment' in body
                            report(self.base, 'challenge' if challenged else e.code,
                                   retry_after=retry_after_seconds(e.headers))
                            if self.resilient and challenged:
                                raise Challenge('Source challenge') from e
                        if e.code in (301,302,303,307,308):report(self.base,e.code)
                        raise
                    except (OSError, TimeoutError):
                        report(self.base, 0)               # network error or timeout: short pause
                        raise
                    with response as r:
                        content_type = r.headers.get('Content-Type', '')
                        if not robots and not any(x in content_type for x in ('text/html', 'application/xhtml+xml', 'application/xml', 'text/xml')):
                            raise ValueError('Not an HTML page: ' + content_type)
                        try:raw = r.read(20_000_001)
                        except (OSError,TimeoutError,HTTPException):
                            report(self.base,0);raise
                        if len(raw) > 20_000_000:
                            raise ValueError('Page exceeds 20 MB')
                        body=raw.decode(r.headers.get_content_charset() or 'utf-8', errors='replace')
                        if self.resilient:
                            from source_budget import is_challenge,Challenge
                            if is_challenge(body) or r.headers.get('cf-mitigated', '').lower() == 'challenge':
                                report(self.base,'challenge')
                                raise Challenge('Cloudflare challenge; waiting for shared pause')
                        report(self.base,response.status)
                        return body
                except HTTPError as e:
                    if e.code in (301, 302, 303, 307, 308):
                        url = canonical(urljoin(url, e.headers['Location']))
                        break
                    if robots and e.code in (404, 410):
                        return ''
                    if e.code == 429:
                        raise  # Pause the whole collection immediately; never retry a rate limit here.
                    if e.code in (500, 502, 503, 504) and attempt < 2:
                        retry = e.headers.get('Retry-After', '')
                        wait = int(retry) if retry.isdigit() else 5 * (attempt + 1)
                        # Do not occupy a cron worker for an arbitrarily long server cooldown.
                        if wait > 60:
                            raise RuntimeError('Server requests cooldown; retry next cron run') from e
                        time.sleep(max(1, wait))
                        continue
                    raise
            else:
                raise RuntimeError('Retries exhausted')
        raise RuntimeError('Too many redirects')
    def initialize(self):
        self.robots = Robots(self.get(urljoin(self.base, '/robots.txt'), robots=True))
        self.delay = max(self.delay, self.robots.delay)

