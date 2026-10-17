import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import connection_detection as d
import manager
import web_app


class RemoteDetection(unittest.TestCase):
    def test_missing_install_uses_actual_account_home_and_finds_free_port(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(d,'port_available',side_effect=lambda p:p==18766):
            result=d.inspect_remote({'remote_port':18765},tmp,'root')
            self.assertEqual(result['user'],'root')
            self.assertEqual(result['directory']['status'],'missing')
            self.assertEqual(result['directory']['value'],str(Path(tmp).resolve()/'.xdt-server/v1/codex-home'))
            self.assertEqual(result['tunnel']['value'],18766)
            self.assertFalse((Path(tmp)/'.xdt-server').exists(),'Discovery must not install anything')

    def test_custom_install_and_existing_provider_port_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'custom';(home/'bin').mkdir(parents=True)
            (home/'bin/codex').write_text('fixture')
            state=home/'portable-providers';state.mkdir()
            (state/'profiles.json').write_text(json.dumps({'profiles':[dict(mode='tunnel',base_url='http://127.0.0.1:19555/v1',key='fictional-secret')]}))
            with patch.object(d.os,'access',return_value=True),patch.object(d,'port_available',return_value=False):
                result=d.inspect_remote(dict(home=str(home),remote_port=18765),tmp,'developer')
            self.assertEqual(result['directory']['status'],'verified')
            self.assertEqual(result['directory']['value'],str(home.resolve()))
            self.assertEqual(result['tunnel']['value'],19555)
            self.assertEqual(result['tunnel']['status'],'in_use')
            self.assertNotIn('fictional-secret',json.dumps(result))

    def test_ambiguous_install_and_owned_tunnel(self):
        with tempfile.TemporaryDirectory() as tmp:
            for version in ('v2','v3'):
                path=Path(tmp)/'.xdt-server'/version/'codex-home/bin';path.mkdir(parents=True)
                (path/'codex').write_text('fixture')
            with patch.object(d.os,'access',return_value=True),patch.object(d,'port_available',return_value=False):
                result=d.inspect_remote(dict(remote_port=18765,owned_tunnel=True),tmp,'developer')
            self.assertEqual(result['directory']['status'],'choose')
            self.assertEqual(len(result['directory']['candidates']),2)
            self.assertEqual(result['tunnel']['status'],'managed')


class LocalDetection(unittest.TestCase):
    def test_docker_mapping_parsing_ignores_unrelated_services(self):
        result=SimpleNamespace(returncode=0,stdout='sub2api\tregistry.example/sub2api:latest\t0.0.0.0:8181->8080/tcp, [::]:8181->8080/tcp\nredis\tredis:alpine\t0.0.0.0:6379->6379/tcp\n')
        with patch.object(d.subprocess,'run',return_value=result):
            ports,note=d.docker_candidates()
        self.assertEqual(ports,{8181})

    def test_healthy_unrelated_service_is_not_sub2api(self):
        def response(port,path):return b'{"status":"ok"}' if path=='/health' else b'<title>Another service</title>'
        with patch.object(d,'docker_candidates',return_value=(set(),'')),patch.object(d,'local_response',side_effect=response):
            result=d.inspect_local()
        self.assertEqual(result['status'],'not_found')

    def test_unique_mapping_selected_and_ambiguous_instances_need_choice(self):
        def response(port,path):
            if port not in (8181,8282):raise OSError('not listening')
            return b'{"status":"ok"}'
        with patch.object(d,'local_response',side_effect=response):
            with patch.object(d,'docker_candidates',return_value=({8181},'')):
                result=d.inspect_local(8080)
                self.assertEqual((result['status'],result['value']),('verified',8181))
            with patch.object(d,'docker_candidates',return_value=({8181,8282},'')):
                result=d.inspect_local(8080)
                self.assertEqual(result['status'],'choose')
                result=d.inspect_local(8282)
                self.assertEqual((result['status'],result['value']),('verified',8282))


class ConnectionWorkflow(unittest.TestCase):
    def test_live_worker_ports_are_not_silently_changed(self):
        settings=dict(manager.DEFAULT,connection_source='manual',host='server.example',user='developer',home='/home/developer/custom')
        remote=dict(user='developer',directory=dict(status='verified',value=settings['home'],candidates=[settings['home']]),tunnel=dict(status='available',value=18766,registered_ports=[]))
        core=SimpleNamespace(HIDDEN={},ssh_base=lambda s:(['ssh'],'example'),tunnel_status=lambda s:{'fresh':True})
        local=dict(status='verified',value=8181,candidates=[dict(port=8181,source='fixture')],note='fixture')
        with patch.object(d.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(remote))),patch.object(d,'inspect_local',return_value=local):
            result=d.detect(settings,settings,core)
        self.assertEqual(result['settings']['remote_port'],settings['remote_port'])
        self.assertEqual(result['settings']['local_port'],settings['local_port'])
        self.assertEqual(result['local']['status'],'restart_required')
        self.assertEqual(result['tunnel']['status'],'managed')

    def test_detect_button_never_writes_and_connect_applies_detected_values(self):
        draft=dict(manager.DEFAULT,connection_source='manual',host='server.example',user='developer')
        detected=dict(settings=dict(draft,home='/home/developer/.xdt-server/v1/codex-home',local_port=8181,remote_port=18766),
                      directory={'status':'verified'},tunnel={'status':'available'},local={'status':'verified'})
        with patch.object(manager,'read_settings',return_value=draft),patch.object(manager,'detect_connection',return_value=detected),patch.object(manager,'write_settings') as write,patch.object(manager,'tunnel_status',return_value={}),patch.object(manager,'ssh_base',return_value=(['ssh'],'server')):
            self.assertEqual(web_app.operation(dict(op='connection_detect',settings=draft)),detected)
            write.assert_not_called()
            result=web_app.operation(dict(op='settings',settings=draft,auto_detect=True))
            self.assertEqual(result['settings']['local_port'],8181)
            self.assertEqual(result['settings']['remote_port'],18766)
            write.assert_called_once_with(detected['settings'])
            write.reset_mock();detected['directory']['status']='choose'
            result=web_app.operation(dict(op='settings',settings=draft,auto_detect=True))
            self.assertTrue(result['needs_selection']);write.assert_not_called()

    def test_detection_failure_never_overwrites_settings(self):
        draft=dict(manager.DEFAULT,connection_source='manual',host='server.example',user='developer')
        with patch.object(manager,'read_settings',return_value=draft),patch.object(manager,'detect_connection',side_effect=ValueError('SSH unavailable')),patch.object(manager,'write_settings') as write:
            with self.assertRaises(ValueError):web_app.operation(dict(op='settings',settings=draft,auto_detect=True))
            write.assert_not_called()

if __name__=='__main__':unittest.main()
