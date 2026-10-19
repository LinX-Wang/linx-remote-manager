import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import manager
import server_monitor
import web_app


class ServerMonitor(unittest.TestCase):
    def test_snapshot_parses_resources_and_hides_common_credentials(self):
        raw = '''__GPU__
0, NVIDIA Test, 44, 2048, 8192, 61
__PMON__
__MEM__
Mem: 16000 8000 1000 200 3000 7000
__LOAD__
1.0 0.8 0.6 2/100 42
__DISK__
Filesystem 1024-blocks Used Available Capacity Mounted on
/dev/test 100000 25000 75000 25% /
__PS__
ws 123 12.5 1.2 python python app.py --api-key abc123 -H \"Authorization: Bearer token987\" https://user:pass@example.invalid/api?token=secret456
'''
        result = server_monitor.parse_snapshot(raw)
        self.assertEqual(result['gpus'][0]['name'], 'NVIDIA Test')
        self.assertEqual(result['memory']['total'], 16000)
        self.assertEqual(result['disks'][0]['mount'], '/')
        args = result['processes'][0]['args']
        for secret in ('abc123', 'token987', 'user:pass', 'secret456'):
            self.assertNotIn(secret, args)
        self.assertGreaterEqual(args.count('[已隐藏]'), 3)

    def test_monitor_api_uses_current_settings_and_collector(self):
        settings = dict(manager.DEFAULT, alias='fixture')
        payload = {'gpus': [], 'processes': [], 'current_user': 'ws'}
        with patch.object(manager, 'read_settings', return_value=settings), \
             patch.object(server_monitor.monitor, 'collect', return_value=payload) as collect:
            result = web_app.operation({'op': 'monitor_status', 'force': True})
        self.assertEqual(result, payload)
        collect.assert_called_once_with(settings, force=True)

    def test_manual_password_auth_is_runtime_only_and_survives_detected_username(self):
        manager.SSH_PASSWORDS.clear()
        draft = dict(manager.DEFAULT, connection_source='manual', host='server.example', user='', auth_method='password')
        with patch.object(manager, 'read_settings', return_value=dict(manager.DEFAULT)), \
             patch.object(manager, 'detect_remote_home', return_value={'home':'/home/ws/.xdt-server/v1/codex-home', 'user':'ws'}), \
             patch.object(manager, 'ssh_base', return_value=(['ssh'], 'ws@server.example')), \
             patch.object(manager, 'tunnel_status', return_value={}), \
             patch.object(manager, 'write_settings') as write:
            result = web_app.operation({'op':'settings', 'settings':draft, 'ssh_password':'runtime-secret'})
        self.assertEqual(result['settings']['user'], 'ws')
        self.assertNotIn('runtime-secret', json.dumps(result['settings']))
        self.assertNotIn('runtime-secret', json.dumps(write.call_args.args[0]))
        env, askpass = manager.ssh_auth_environment(result['settings'])
        try:
            self.assertEqual(env['LINX_REMOTE_SSH_PASSWORD'], 'runtime-secret')
            self.assertNotIn('runtime-secret', askpass.read_text(encoding='utf-8'))
        finally:
            askpass.unlink(missing_ok=True)
            manager.SSH_PASSWORDS.clear()


if __name__ == '__main__':
    unittest.main()
