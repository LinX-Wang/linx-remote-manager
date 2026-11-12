import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import manager
import remote_tools
import web_app

class TransferHttp(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patches=[patch.object(web_app,'existing',return_value=None),patch.object(web_app,'instance_path',return_value=self.root/'instance'),patch.object(manager,'STATE',self.root),patch.object(web_app,'maintain_window_identity',return_value=None)]
        for p in self.patches:p.start()
        self.server=web_app.prepare_server(False)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        info=json.loads((self.root/'instance').read_text());self.token=info['url'].split('#')[1]
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.transfers=remote_tools.Transfers();self.swap=patch.object(remote_tools,'transfers',self.transfers);self.swap.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.swap.stop()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def post(self,path,data=b'',headers=None):
        request=urllib.request.Request(self.base+path,data=data,headers=dict({'X-Kit-Token':self.token},**(headers or {})))
        return urllib.request.urlopen(request,timeout=5)
    def test_transfer_routes_require_auth_origin_and_valid_job(self):
        for headers in ({'X-Kit-Token':'wrong'},{'Origin':'https://untrusted.example'}):
            with self.assertRaises(urllib.error.HTTPError) as result:self.post('/transfer/download',headers=headers)
            self.assertEqual(result.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as result:self.post('/transfer/download',headers={'X-Transfer-Id':'missing'})
        self.assertEqual(result.exception.code,400)
    def test_download_stream_is_exact_and_repeatable_with_utf8_filename(self):
        content=bytes(range(256))*4096;path=self.root/'payload';path.write_bytes(content)
        job=dict(id='test-id',direction='download',name='结果.bin',state='done',total=len(content),local=str(path),updated=__import__('time').time())
        self.transfers.jobs[job['id']]=job
        for _ in range(2):
            with self.post('/transfer/download',headers={'X-Transfer-Id':job['id']}) as response:
                self.assertEqual(response.read(),content)
                self.assertIn("filename*=UTF-8''",response.headers['Content-Disposition'])
            for _ in range(100):
                if job['state']=='done':break
                __import__('time').sleep(.01)
        self.assertEqual(job['state'],'done')
        self.transfers.discard(job['id']);self.assertFalse(path.exists())
    def test_raw_upload_not_json_or_base64(self):
        content=bytes(range(256))*5000;received=[]
        def upload(key,source,size):
            received.append((key,size,hashlib.sha256(source.read(size)).hexdigest()))
            return {'state':'done'}
        with patch.object(self.transfers,'upload',side_effect=upload):
            with self.post('/transfer/upload',data=content,headers={'X-Transfer-Id':'upload-id','Content-Type':'application/octet-stream'}) as response:self.assertTrue(json.load(response)['ok'])
        self.assertEqual(received,[('upload-id',len(content),hashlib.sha256(content).hexdigest())])

if __name__=='__main__':unittest.main()
