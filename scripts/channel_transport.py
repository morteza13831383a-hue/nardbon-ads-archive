"""Connection-reusing curl transport underneath html_core's request policy."""
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.response import addinfourl

from curl_cffi.requests import Session, RequestsError


class CurlOpener:
    def __init__(self):
        self.session = Session(trust_env=False, discard_cookies=True)

    def close(self):
        self.session.close()

    def open(self, request, timeout):
        url = request.full_url
        try:
            with self.session.stream("GET", url, headers=dict(request.header_items()),
                                     timeout=timeout, allow_redirects=False) as response:
                code = response.status_code
                headers = Message()
                for key, value in response.headers.multi_items():
                    headers[key] = value
                limit = 20_000 if code >= 300 else 20_000_001
                body = BytesIO()
                for chunk in response.iter_content():
                    body.write(chunk[:limit - body.tell()])
                    if body.tell() >= limit:
                        break
                body.seek(0)
                if code >= 300:
                    raise HTTPError(url, code, response.reason, headers, body)
                return addinfourl(body, headers, url, code)
        except RequestsError as error:
            raise URLError(type(error).__name__) from error
