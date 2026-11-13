"""Environment-only TGStat session, endpoint boundary, and credential redaction."""
import json
import http.cookiejar
import os
import re
from http.cookiejar import Cookie, CookieJar
from urllib.parse import urlsplit, unquote

PROFILE_PATH=re.compile(r'^/(channel|chat)/@[A-Za-z][A-Za-z0-9_]{3,31}/?$')
ITEMS_PATH=re.compile(r'^/([a-z-]+)/items$')

class SecretFilter:
    def __init__(self): self._values=set()
    def add(self,value):
        if isinstance(value,str) and len(value)>=8:
            for variant in(value,unquote(value)):
                self._values.add(variant);self._values.add(json.dumps(variant,ensure_ascii=False)[1:-1])
    def clean(self,value):
        for secret in sorted(self._values,key=len,reverse=True):
            if secret:value=value.replace(secret,'[redacted]')
        return value


def endpoint_allowed(url,method):
    p=urlsplit(url)
    if p.scheme!='https' or p.netloc!='ir.tgstat.com' or p.query or p.fragment:return False
    return bool(method=='GET' and PROFILE_PATH.fullmatch(p.path) or method=='POST' and ITEMS_PATH.fullmatch(p.path))


class Session:
    def __init__(self):
        http.cookiejar.debug=False
        self.jar=CookieJar();self.filter=SecretFilter();self.csrf=None;self.csrf_param=None
    @classmethod
    def from_environment(cls):
        raw=os.environ.get('TGSTAT_COOKIE','').strip()
        if not raw:raise ValueError('TGSTAT_COOKIE is not configured in the environment')
        result=cls();result.filter.add(raw)
        try:
            rows=json.loads(raw)
            if not isinstance(rows,list)or not rows:raise ValueError()
            for row in rows:
                domain=row['domain'].lower();name=row['name'];value=row['value'];path=row.get('path','/')
                if domain.lstrip('.')!='tgstat.com' and not domain.lstrip('.').endswith('.tgstat.com'):raise ValueError()
                if not isinstance(name,str)or not re.fullmatch(r'[!#$%&\x27*+.^_`|~0-9A-Za-z-]+',name):raise ValueError()
                if not isinstance(value,str)or any(c in value for c in('\r','\n','\0'))or not path.startswith('/'):raise ValueError()
                host_only=bool(row.get('hostOnly',False))
                expiry=None if row.get('session',False) else row.get('expirationDate')
                if expiry is not None:expiry=int(expiry)
                result.filter.add(value);result.filter.add(name+'='+value)
                result.jar.set_cookie(Cookie(0,name,value,None,False,domain,not host_only,domain.startswith('.'),path,True,bool(row.get('secure',True)),expiry,expiry is None,None,None,{'HttpOnly':None}if row.get('httpOnly')else{},False))
        except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
            raise ValueError('TGSTAT_COOKIE must be a valid Cookie-Editor JSON export restricted to tgstat.com') from None
        return result
    def attach(self,request):
        http.cookiejar.debug=False
        if not endpoint_allowed(request.full_url,request.get_method()):raise ValueError('Session endpoint is not permitted')
        self.jar.add_cookie_header(request)
        header=request.get_header('Cookie')
        if not header:raise ValueError('No applicable unexpired TGStat session cookies')
        self.filter.add(header)
    def receive(self,response,request,doc):
        http.cookiejar.debug=False
        self.jar.extract_cookies(response,request)
        for cookie in self.jar:self.filter.add(cookie.value);self.filter.add(cookie.name+'='+cookie.value)
        for n in doc.root.nodes():
            if n.tag=='meta'and n.attrs.get('name')=='csrf-token':
                self.csrf=n.attrs.get('content');self.filter.add(self.csrf)
            if n.tag=='meta'and n.attrs.get('name')=='csrf-param':self.csrf_param=n.attrs.get('content')


def account_problem(doc,fragment=False):
    """Inspect site notices/navigation, not a channel's post text or biography."""
    notices=[]
    for n in doc.root.nodes():
        c=set(n.attrs.get('class','').split())
        if c.intersection({'alert-warning','alert-danger','error-summary'})and re.search(r'warn|limit|restrict|block|exceed|огранич|лимит|محدود|هشدار|مسدود',n.text(),re.I):return 'Account warning or limit detected'
        if n.tag in('title','h1')or c.intersection({'alert','alert-danger','alert-warning','notification','error-summary'}):notices.append(n.text())
        if n.tag=='a':
            path=urlsplit(n.attrs.get('data-src',n.attrs.get('href',''))).path
            if path in('/login','/signin','/sign-in'):return 'Session is logged out'
        if n.tag=='form'and any(word in (n.attrs.get('id','')+' '+n.attrs.get('action','')).lower()for word in('login','signin')):return 'Session is logged out'
    if fragment or not any(n.tag=='h1'for n in doc.root.nodes()):notices.append(doc.root.text())
    words=' '.join(notices).lower()
    if any(v in words for v in('sign in to access','please log in','login required','session expired','authorization required','войдите','авторизац','وارد حساب','ورود به حساب')):return 'Session is logged out'
    if any(v in words for v in('account is limited','account has been limited','account is restricted','account has been restricted','account warning','account is blocked','account has been blocked','limit exceeded','too many requests','rate limit','доступ ограничен','превышен лимит','حساب شما محدود','حساب شما مسدود','captcha')):return 'Account warning or limit detected'
    return None


def delivery_gh(repo,path,payload=None):
    """GitHub delivery child gets no TGStat secret in its environment."""
    import subprocess,tempfile
    from pathlib import Path
    command=['gh','api',f'repos/{repo}/{path}'];file=None
    try:
        if payload is not None:
            file=tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',suffix='.json',delete=False)
            json.dump(payload,file,ensure_ascii=False);file.close()
            command+=['--method','PATCH'if path.startswith('git/refs/')else'POST','--input',file.name]
        environment={k:v for k,v in os.environ.items()if k!='TGSTAT_COOKIE'}
        result=subprocess.run(command,capture_output=True,text=True,check=True,env=environment)
        return json.loads(result.stdout)
    finally:
        if file:Path(file.name).unlink(missing_ok=True)
