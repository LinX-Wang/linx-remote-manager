"""Reproducible screenshots with fictional data and zero external requests."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import manager

MODELS = ['gpt-5.4', 'gpt-5.4-mini', 'gpt-5.3-codex', 'deepseek-v3.2', 'deepseek-reasoner', 'custom-research-model']
SETTINGS = dict(manager.DEFAULT, alias='demo-server', user='developer',
                cindy_host='demo-server', home='/home/developer/.xdt-server/v1/codex-home')
HOSTS = {'hosts':[{'id':'demo-server', 'name':'婕旂ず宸ヤ綔绌洪棿', 'hostname':'server.example',
                   'user':'developer', 'port':22, 'managed_by_cindy':True}], 'warnings':[]}
PROFILES = [
    dict(id='demo-direct', name='Demo Relay', mode='direct', base_url='https://api.example/v1',
         model=MODELS[0], selected_models=MODELS, has_key=True, context_window=262144, context_windows={m:262144 for m in MODELS}),
    dict(id='demo-tunnel', name='鏈満 Sub2API', mode='tunnel', base_url='http://127.0.0.1:18765/v1',
         model=MODELS[0], selected_models=MODELS[:3], has_key=True, context_window=262144, context_windows={m:262144 for m in MODELS[:3]}),
]
UPDATES = dict(state='done', items=[
    dict(name='Codex', installed='codex-cli 0.159.2', available='0.163.0', update=True),
    dict(name='gawk', installed='GNU Awk 5.3.1', available='5.3.1', update=False),
])
MONITOR = dict(
    current_user='developer', cpu_cores=80, load=[3.21, 5.42, 7.11], collected_at=1792419000,
    memory=dict(total=262144, used=176000, available=86000),
    gpus=[
        dict(index=0, name='NVIDIA RTX 6000 Ada', temperature=58, utilization=74,
             memory_used=24576, memory_total=49152, processes=[
                 dict(user='developer', pid=240186), dict(user='worker-a', pid=240412),
                 dict(user='worker-b', pid=240598)]),
        dict(index=1, name='NVIDIA RTX 6000 Ada', temperature=55, utilization=39,
             memory_used=18432, memory_total=49152, processes=[
                 dict(user='developer', pid=240722), dict(user='worker-c', pid=240831)]),
    ],
    processes=[
        dict(user='developer', pid=240186, cpu=38.2, memory=4.1, command='python', args='video_inference_worker.py'),
        dict(user='worker-a', pid=240412, cpu=24.7, memory=2.8, command='python', args='inference_worker.py'),
        dict(user='worker-b', pid=240598, cpu=18.4, memory=2.2, command='python', args='batch_worker.py'),
        dict(user='root', pid=1842, cpu=3.1, memory=0.2, command='nvidia-smi', args='--query-gpu=utilization.gpu'),
    ],
    disks=[
        dict(mount='/', size_kb=204800000, used_kb=91200000, available_kb=113600000, percent=45),
        dict(mount='/disk2', size_kb=2048000000, used_kb=1210000000, available_kb=838000000, percent=59),
    ],
)


def main():
    from playwright.sync_api import sync_playwright
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', default=None)
    args = parser.parse_args()
    source = (ROOT/'manager_ui.html').read_text(encoding='utf-8')
    assets = ROOT/'docs/assets'
    assets.mkdir(parents=True, exist_ok=True)
    calls, errors = [], []
    empty = False

    def route(request):
        url = request.request.url
        if url == 'https://linx.test/':
            return request.fulfill(body=source, content_type='text/html')
        if url == 'https://linx.test/api':
            payload = request.request.post_data_json
            op = payload['op']
            calls.append(op)
            if op == 'bootstrap':
                data = dict(settings=manager.DEFAULT if empty else SETTINGS, tunnel={},
                            environment={'state':'idle'}, cindy_connections=HOSTS)
            elif op == 'list':
                if empty:
                    raise AssertionError('First run attempted SSH')
                data = dict(profiles=PROFILES, active='demo-direct', config_matches_active=True,
                            runtime_matches_active=True, runtime={'status':'ready'},
                            model_catalog={'status':'synced', 'count':len(MODELS),
                                           'menu_visible_count':len(MODELS), 'can_sync':True})
            elif op == 'cindy_connections':
                data = HOSTS
            elif op == 'monitor_status':
                data = MONITOR
            elif op in ('updates_check', 'updates_status'):
                if empty:
                    raise AssertionError('First run attempted update check')
                data = UPDATES
            elif op == 'ping':
                data = {'reopen':0}
            elif op in ('appearance', 'appearance_sync'):
                data = {}
            else:
                raise AssertionError('Unexpected operation: ' + op)
            return request.fulfill(json={'ok':True, 'data':data})
        # No fonts, image URLs, telemetry or other hosts may leave this browser.
        if url.endswith('/favicon.ico'):
            return request.fulfill(status=204)
        errors.append('Unexpected network URL')
        request.abort()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**({'channel':args.channel} if args.channel else {}))
        page = browser.new_page(viewport={'width':1440, 'height':1100}, device_scale_factor=1)
        page.route('**/*', route)
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto('https://linx.test/')
        page.wait_for_function('S.loaded && !S.busy')
        page.evaluate('clearToast()')
        for mode in ('light', 'dark'):
            page.evaluate('(m)=>{appearanceMode=m;applyAppearance(m)}', mode)
            page.wait_for_timeout(400)
            page.screenshot(path=str(assets/('overview-'+mode+'.png')), full_page=True)
        page.evaluate("appearanceMode='light';applyAppearance('light');S.page='connection';render()")
        page.evaluate("S.environment={tool:'Codex',state:'running',events:[{phase:'download',transfer:true,message:'婕旂ず锛氭鍦ㄤ紶杈撳凡鏍￠獙鐨勫畼鏂瑰寘',downloaded_bytes:96*1048576,total_bytes:160*1048576}]};renderEnvironment()")
        page.screenshot(path=str(assets/'connection-updates.png'), full_page=True)
        page.evaluate('S.monitor=' + json.dumps(MONITOR, ensure_ascii=False) + ";S.page='monitor';render()")
        page.wait_for_timeout(300)
        page.screenshot(path=str(assets/'server-monitor.png'), full_page=True)
        page.locator('.section-rail button').nth(1).click()
        page.wait_for_timeout(250)
        assert page.locator('#monitor-processes h2').is_visible()
        assert page.locator('#monitor-processes').evaluate('(e)=>e.getBoundingClientRect().top >= 0')
        page.locator('.section-rail button').first.click()
        page.wait_for_timeout(250)
        page.evaluate('window.scrollTo(0, 0)')
        assert page.evaluate('scrollY < 2')
        page.evaluate("S.page='providers';render();openWizard(S.profiles[0]);S.models=" + repr(MODELS) + ';S.stage=2;drawWizard()')
        page.locator('.modal').screenshot(path=str(assets/'model-selection.png'))
        page.evaluate('closeWizard()')
        page.evaluate('S.monitor=' + json.dumps(MONITOR, ensure_ascii=False) + ";S.page='monitor';render()")
        page.set_viewport_size({'width':390, 'height':844})
        assert page.locator('.section-rail').evaluate("e=>getComputedStyle(e).overflowX==='auto'")
        page.screenshot(path=str(assets/'mobile.png'), full_page=True)
        for width in (320, 390, 768, 1024, 1440, 1920):
            page.set_viewport_size({'width':width, 'height':900})
            for section in ('providers', 'connection', 'tunnel', 'help'):
                page.evaluate('(p)=>{S.page=p;render()}', section)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'), (width, section)
        # Verify a blank public install stays on local connection setup, without SSH.
        empty = True
        calls.clear()
        page.reload()
        page.wait_for_function("S.loaded && S.page==='connection'")
        page.wait_for_timeout(1000)
        assert 'list' not in calls and 'updates_check' not in calls
        assert not errors, errors
        browser.close()
    print('Six fictional screenshots generated; desktop and narrow-screen section navigation, 24 layouts and isolated first-run behavior passed.')


if __name__ == '__main__':
    main()
