import copy, hashlib, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import remote_manager as w

class ProviderRefresh(unittest.TestCase):
    def test_active_save_transaction_and_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);state=home/'portable-providers';state.mkdir();(home/'bin').mkdir()
            (home/'bin/codex').write_bytes(b'fixture')
            p=dict(id='example',name='Example',key='fictional-key',base_url='https://example.invalid/v1',
                   mode='direct',model='model-0',selected_models=['model-'+str(i) for i in range(6)])
            cfg=home/'config.toml';auth=home/'auth.json';dbfile=state/'profiles.json'
            cfg.write_text(w.render('''[mcp_servers.keep]
command="preserve"
''',p))
            auth.write_text(json.dumps({'OPENAI_API_KEY':p['key']}))
            dbfile.write_text(json.dumps(dict(profiles=[p],active=p['id'])))
            identity={'pid':1,'processIdentity':{'bootId':'fixture','startTicks':1}}
            snap=dict(running=True,threads=[],identity=identity)
            def prepare(home,state,p):
                content=json.dumps({'models':[w.generic_model_metadata(m,i) for i,m in enumerate(w.selected_models(p))]})
                digest=hashlib.sha256(content.encode()).hexdigest();target=state/'catalogs'/(digest+'.json')
                w.private(target,content)
                return dict(path=str(target),hash=digest,models=w.selected_models(p),version='fixture',
                            binary=w.catalog_binary_identity(home),compatibility_revision=w.TOOL_COMPAT_REVISION,tool_compatibility='native')
            def observed(home):
                target=w.configured_catalog(cfg.read_text())
                return [r['slug'] for r in json.loads(Path(target).read_text())['models']] if target else []
            def run(action,**args):return w.locked(dict(action=action,**args),home,state)
            def files():return [f.read_bytes() for f in (cfg,auth,dbfile)]
            with patch.object(w,'runtime_snapshot',side_effect=lambda h:copy.deepcopy(snap)), patch.object(w,'daemon_identity',return_value=identity), patch.object(w,'prepare_model_catalog',side_effect=prepare), patch.object(w,'runtime_model_ids',side_effect=observed), patch.object(w,'refresh_runtime',return_value=dict(running=True,verified=True,identity=identity)) as refresh:
                run('activate',id=p['id']);refresh.reset_mock()
                self.assertTrue(run('activate',id=p['id'])['unchanged']);refresh.assert_not_called()
                self.assertTrue(run('save',profile=dict(p,name='Renamed'))['auto_applied']);refresh.assert_not_called()
                edited=dict(p,selected_models=p['selected_models'][:-1],key='')
                result=run('save',profile=edited)
                self.assertTrue(result['auto_applied']);self.assertEqual(result['state']['model_catalog']['status'],'synced')
                self.assertEqual(observed(home),edited['selected_models']);self.assertEqual(refresh.call_count,1)
                self.assertEqual(refresh.call_args.kwargs['allowed_models'],edited['selected_models'])
                self.assertTrue(refresh.call_args.kwargs['preserve_models']);self.assertEqual(edited['key'],'')
                self.assertNotIn('fictional-key',json.dumps(result));self.assertIn('preserve',cfg.read_text())
                original=cfg.read_bytes(),auth.read_bytes();refresh.reset_mock()
                result=run('save',profile=dict(p,id='inactive'))
                self.assertFalse(result['auto_applied']);self.assertEqual(result['state']['active'],p['id'])
                self.assertEqual((cfg.read_bytes(),auth.read_bytes()),original);refresh.assert_not_called()
                before=files();snap['threads']=[dict(id='t',provider=w.PROVIDER_ID,status='active')]
                with self.assertRaisesRegex(ValueError,'正在生成'):run('save',profile=p)
                self.assertEqual(files(),before);snap['threads']=[]
                with patch.object(w,'runtime_model_ids',return_value=['wrong-model']):
                    with self.assertRaisesRegex(ValueError,'已回退'):run('save',profile=p)
                self.assertEqual(files(),before)

    def test_catalog_cache_checks_binary_models_and_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);state=home/'portable-providers';(home/'bin').mkdir();(home/'bin/codex').write_bytes(b'fixture')
            p=dict(model='model-a',selected_models=['model-a'])
            def make(home,p):return {'models':[w.generic_model_metadata(m,i) for i,m in enumerate(p['selected_models'])]}
            with patch.object(w,'make_model_catalog',side_effect=make),patch.object(w,'probe_model_catalog',return_value=['model-a']) as probe,patch.object(w,'codex_version',return_value='fixture'):
                first=w.prepare_model_catalog(home,state,p)
                self.assertEqual(w.prepare_model_catalog(home,state,p),first);self.assertEqual(probe.call_count,1)
                Path(first['path']).write_text('{}');w.prepare_model_catalog(home,state,p);self.assertEqual(probe.call_count,2)
                (home/'bin/codex').write_bytes(b'upgraded fixture');w.prepare_model_catalog(home,state,p);self.assertEqual(probe.call_count,3)
                w.prepare_model_catalog(home,state,dict(model='model-b',selected_models=['model-b']));self.assertEqual(probe.call_count,4)

    def test_removed_thread_model_falls_back_and_retained_model_keeps_effort(self):
        resumes=[]
        class Rpc:
            def __init__(self,home):pass
            def rpc(self,method,params):
                self_test.assertEqual(method,'thread/resume');resumes.append(params)
                return {'modelProvider':w.PROVIDER_ID}
            def close(self):pass
        self_test=self
        before=dict(running=True,identity={'pid':1},threads=[],saved_threads=[
            dict(id='keep',provider=w.PROVIDER_ID,model='model-b',effort='high'),
            dict(id='removed',provider=w.PROVIDER_ID,model='old-model',effort='xhigh')])
        from types import SimpleNamespace
        with patch.object(w,'idle_snapshot',return_value=before),patch.object(w,'Rpc',Rpc),patch.object(w,'daemon_identity',return_value={'pid':2}),patch.object(Path,'read_bytes',return_value=b'KEEP_ENV=1\0'),patch.object(w.subprocess,'run',return_value=SimpleNamespace(returncode=0)):
            w.refresh_runtime(Path('/fixture'),before,w.PROVIDER_ID,'model-a',{w.PROVIDER_ID},preserve_models=True,allowed_models=['model-a','model-b'])
        self.assertEqual(resumes[0]['model'],'model-b');self.assertEqual(resumes[0]['config'],{'model_reasoning_effort':'high'})
        self.assertEqual(resumes[1]['model'],'model-a');self.assertNotIn('config',resumes[1])

if __name__=='__main__':unittest.main()
