"""已退役本机预览端口仅跳转当前入口，不提供旧媒体。"""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit
def create_server(port,target):
 if port in [8767,8768,8769] or target not in [8767,8768]:raise ValueError('兼容端口或目标不在允许范围')
 class Handler(BaseHTTPRequestHandler):
  def do_HEAD(self):self.do_GET()
  def do_GET(self):
   if urlsplit(self.path).path.startswith(('/media/','/original/')):self.send_error(410,'Retired preview media');return
   self.send_response(307);self.send_header('Location',f'http://127.0.0.1:{target}/');self.send_header('Cache-Control','no-store');self.send_header('Content-Length','0');self.end_headers()
  def log_message(self,*args):pass
 return ThreadingHTTPServer(('127.0.0.1',port),Handler)
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,required=True);p.add_argument('--target-port',type=int,choices=[8767,8768],required=True);a=p.parse_args();create_server(a.port,a.target_port).serve_forever()
