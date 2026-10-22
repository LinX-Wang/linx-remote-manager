import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import manager
import web_app
import download_bridge
from tools import build_release


class PublicDistribution(unittest.TestCase):
    def test_current_window_title_is_recognized_for_maximize_and_icon(self):
        self.assertTrue(web_app.is_manager_window_title('LinX Remote'))
        self.assertTrue(web_app.is_manager_window_title('LinX · 远程模型管理'))
        self.assertTrue(web_app.is_manager_window_title('LinX Remote [1]'))
        self.assertFalse(web_app.is_manager_window_title('LinX Remote - Google Chrome'))

    def test_hidden_chrome_placeholder_is_not_adopted_as_app_window(self):
        self.assertFalse(web_app.app_window_is_ready(160, 28, True))
        self.assertFalse(web_app.app_window_is_ready(1200, 800, False))
        self.assertTrue(web_app.app_window_is_ready(1200, 800, True))

    def test_official_package_validation_and_cache(self):
        payload = b'fictional download fixture'
        spec = dict(url='https://github.com/openai/codex/releases/download/rust-v0.163.0/codex-package-x86_64-unknown-linux-musl.tar.gz',
                    name='codex-package-0.163.0-x86_64-unknown-linux-musl.tar.gz',
                    sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state/'downloads').mkdir()
            cached = state/'downloads'/spec['name']
            cached.write_bytes(payload)
            with patch.object(download_bridge, 'openers', side_effect=AssertionError('Network forbidden')):
                self.assertEqual(download_bridge.local_download(spec, state, lambda *a, **kw: None), cached)
        with self.assertRaises(ValueError):
            download_bridge.validate(dict(spec, url=spec['url'].replace('/codex-package-', '/codex-')))

    def test_fresh_defaults_have_no_server(self):
        for key in ('alias', 'host', 'user', 'home', 'identity', 'cindy_host'):
            self.assertEqual(manager.DEFAULT[key], '')
        with self.assertRaisesRegex(ValueError, '请选择|请先'):
            manager.ssh_base(manager.DEFAULT)
        self.assertEqual(json.loads((ROOT/'settings.example.json').read_text(encoding='utf-8')), manager.DEFAULT)

    def test_first_manual_connection_detects_home(self):
        draft = dict(manager.DEFAULT, connection_source='manual', host='server.example', user='developer')
        with patch.object(manager, 'read_settings', return_value=dict(manager.DEFAULT)), \
             patch.object(manager, 'detect_remote_home', return_value={'home':'/home/developer/.xdt-server/v1/codex-home', 'user':'developer'}) as detect, \
             patch.object(manager, 'ssh_base', return_value=(['ssh'], 'developer@server.example')), \
             patch.object(manager, 'tunnel_status', return_value={}), \
             patch.object(manager, 'write_settings') as write:
            result = web_app.operation({'op':'settings', 'settings':draft})
        detect.assert_called_once()
        self.assertTrue(result['settings']['home'].startswith('/home/developer/'))
        write.assert_called_once_with(result['settings'])

    def test_failed_detection_preserves_connection(self):
        draft = dict(manager.DEFAULT, connection_source='manual', host='server.example', user='developer')
        with patch.object(manager, 'read_settings', return_value=dict(manager.DEFAULT)), \
             patch.object(manager, 'detect_remote_home', side_effect=ValueError('SSH unavailable')), \
             patch.object(manager, 'write_settings') as write:
            with self.assertRaises(ValueError):
                web_app.operation({'op':'settings', 'settings':draft})
        write.assert_not_called()

    def test_release_is_whitelisted_and_preserves_mac_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'public.zip'
            build_release.archive(output, build_release.public_files())
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertNotIn('LinX-Remote/settings.json', names)
                self.assertFalse(any(n.endswith(('.exe', '.bak', '.log')) for n in names))
                mode = archive.getinfo('LinX-Remote/启动管理器.command').external_attr >> 16
                self.assertTrue(mode & 0o111)
                self.assertNotIn(b'\r\n', archive.read('LinX-Remote/启动管理器.command'))
                self.assertIn('LinX-Remote/docs/USER_GUIDE.md', names)
                self.assertIn('LinX-Remote/server_monitor.py', names)
                self.assertEqual(archive.read('LinX-Remote/启动管理器.vbs'), (ROOT/'启动管理器.vbs').read_bytes())


if __name__ == '__main__':
    unittest.main()
