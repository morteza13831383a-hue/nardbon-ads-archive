from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from channel_transport import CurlOpener


class Response(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/moved':
            self.send_response(302)
            self.send_header('Location','/ok')
        else:
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'page')

    def log_message(self,*args):
        pass


class Transport(unittest.TestCase):
    def test_opener_preserves_headers_and_defers_redirect_to_core(self):
        with ThreadingHTTPServer(('127.0.0.1',0),Response) as server:
            Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.shutdown)
            opener=CurlOpener()
            self.addCleanup(opener.close)
            url=f'http://127.0.0.1:{server.server_port}'
            with opener.open(Request(url+'/ok'),2) as reply:
                self.assertEqual(reply.headers.get_content_charset(),'utf-8')
                self.assertEqual(reply.read(),b'page')
                self.assertEqual(reply.status,200)
            with self.assertRaises(HTTPError) as failure:
                opener.open(Request(url+'/moved'),2)
            self.assertEqual(failure.exception.code,302)
