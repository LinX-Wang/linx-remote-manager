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
HOSTS = {'hosts':[{'id':'demo-server', 'name':'演示工作空间', 'hostname':'server.example',
                   'user':'developer', 'port':22, 'managed_by_cindy':True}], 'warnings':[]}
PROFILES = [
    dict(id='demo-direct', name='Demo Relay', mode='direct', base_url='https://api.example/v1',
         model=MODELS[0], selected_models=MODELS, has_key=True, context_window=262144, context_windows={m:262144 for m in MODELS}),
    dict(id='demo-tunnel', name='本机 Sub2API', mode='tunnel', base_url='http://127.0.0.1:18765/v1',
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
        dict(mount='/data', focus=True, related_paths=[dict(kind='user_home',path='/data/developer')], size_kb=2048000000, used_kb=1210000000, available_kb=838000000, percent=59),
    ],
)
for row in MONITOR['processes'] + [p for g in MONITOR['gpus'] for p in g['processes']]:
    row.update(started_at=MONITOR['collected_at']-3661, elapsed_seconds=3661, start_ticks='12000')


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
        if url.startswith('https://linx.test/assets/') or url in ('https://linx.test/tools_ui.js','https://linx.test/tools_ui.css'):
            name=url[len('https://linx.test/') :]
            assert name in manager.PUBLIC_ASSET_FILES
            return request.fulfill(body=(ROOT/name).read_bytes(), content_type='font/woff2' if name.endswith('.woff2') else 'text/javascript' if name.endswith('.js') else 'text/css' if name.endswith('.css') else 'image/svg+xml')
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
            elif op == 'monitor_history':
                import time,math
                now=int(time.time())
                data=dict(samples=[dict(at=now-i*5,gpus=[dict(g,index=g['index'],utilization=max(0,min(100,g['utilization']+15*math.sin(i/4)+5*math.sin(i/9))),memory_used=g['memory_used']*(1+.04*math.sin(i/8)),temperature=g['temperature']+3*math.sin(i/10)) for g in MONITOR['gpus']]) for i in reversed(range(60)) if i not in range(31,36)])
            elif op == 'watch_status':
                data=dict(watches=[],events=[],enabled=False)
            elif op == 'watch_add':
                data=dict(watches=[dict(id='demo-watch',pid=int(payload['pid']),ticks=payload['ticks'],user=payload['user'],state='running')],events=[])
            elif op == 'watch_remove':
                data=dict(watches=[],events=[])
            elif op == 'connection_diagnostics':
                data=dict(checked_at=1792419000,rows=[dict(name='SSH / Python',status='ok',detail='只读检查通过'),dict(name='远端兼容桥',status='error',detail='演示故障：服务未响应')])
            elif op == 'files_list':
                data=dict(path='/home/developer',parent='/home',offset=0,more=False,entries=[dict(name='results',path='/home/developer/results',kind='directory',size=4096,modified_at=1792419000)]+[dict(name=name,path='/home/developer/'+name,kind='file',size=128,modified_at=1792419000) for name in ('README.md','slides.pptx','metrics.csv','train.py','config.yaml','preview.png','weights.safetensors','example.txt')])
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
        page.evaluate('document.fonts.ready')
        assert page.locator('#header-server').inner_text() == '演示工作空间'
        assert page.locator('#servermini').inner_text() == '演示工作空间'
        assert '本机 Sub2API' in page.locator('#content').inner_text()
        for mode in ('light', 'dark'):
            page.evaluate('(m)=>{appearanceMode=m;applyAppearance(m)}', mode)
            page.wait_for_timeout(400)
            page.screenshot(path=str(assets/('overview-'+mode+'.png')), full_page=True, animations='disabled')
        page.evaluate("appearanceMode='light';applyAppearance('light');S.page='connection';render()")
        page.evaluate("S.environment={tool:'Codex',state:'running',events:[{phase:'download',transfer:true,message:'演示：正在传输已校验的官方包',downloaded_bytes:96*1048576,total_bytes:160*1048576}]};renderEnvironment()")
        page.screenshot(path=str(assets/'connection-updates.png'), full_page=True, animations='disabled')
        page.evaluate('S.monitor=' + json.dumps(MONITOR, ensure_ascii=False) + ";S.page='monitor';render()")
        page.evaluate('pollTools()')
        page.wait_for_timeout(300)
        assert page.locator('#monitor-processes th').count() == 8
        assert page.locator('#monitor-processes tbody tr').first.locator('td').nth(4).inner_text() != '—'
        assert '1小时' in page.locator('#monitor-processes tbody tr').first.locator('td').nth(5).inner_text()
        assert page.locator('#monitor-progress').count() == 0
        page.evaluate('S.monitor.collected_at=Date.now()/1000')
        gpu_height=page.locator('.monitor-gpus').bounding_box()['height']
        page.locator('[data-gpu-trend]').first.click()
        page.locator('[data-chart-minutes="30"]').first.click()
        assert page.locator('#trend-chart svg').count() == 1
        assert page.locator('[data-trend-metric]').count() == 3
        assert page.locator('.trend-area').count()==2
        assert page.locator('.trend-line').count()==2
        assert page.locator('.trend-statistics').is_visible()
        plot=page.locator('.trend-interactive');box=plot.bounding_box()
        page.mouse.move(box['x']+box['width']*.65,box['y']+box['height']*.4)
        assert page.locator('.trend-tooltip').is_visible()
        assert '%' in page.locator('.trend-tooltip').inner_text()
        assert page.locator('.trend-cursor').get_attribute('visibility')=='visible'
        page.mouse.move(1,1)
        assert page.locator('.trend-tooltip').is_hidden()
        plot.focus();page.keyboard.press('ArrowLeft')
        assert page.locator('.trend-tooltip').is_visible()
        page.locator('[data-chart-minutes="30"]').focus()
        page.evaluate('document.activeElement.blur()')
        assert abs(page.locator('.monitor-gpus').bounding_box()['height']-gpu_height)<1
        assert page.locator('.monitor-gpus .trend-plot').count()==0
        for mode in ('light','dark'):
            page.evaluate('(m)=>applyAppearance(m)',mode)
            page.locator('.trend-dialog').screenshot(path=str(assets/('gpu-trends-'+mode+'.png')),animations='disabled')
        page.locator('[data-trend-gpu="1"]').click()
        assert 'GPU 1' in page.locator('.trend-header').inner_text()
        page.locator('[data-trend-metric="temperature"]').click()
        assert page.locator('.trend-plot').get_attribute('data-metric')=='temperature'
        page.keyboard.press('Escape')
        assert page.locator('#tools-trend-host').count()==0
        page.evaluate("applyAppearance('light')")
        page.locator('[data-watch-pid="240186"]').first.click()
        page.wait_for_function('Tools.watches.length === 1')
        assert page.locator('[data-watch-pid="240186"]').first.get_attribute('aria-pressed') == 'true'
        page.locator('[data-watch-pid="240186"]').first.click()
        page.wait_for_function('Tools.watches.length === 0')
        page.evaluate('render();clearToast()')
        page.screenshot(path=str(assets/'server-monitor.png'), full_page=True, animations='disabled')
        page.locator('.section-rail button').nth(1).click()
        page.wait_for_timeout(250)
        assert page.locator('#monitor-processes h2').is_visible()
        assert page.locator('#monitor-processes').evaluate('(e)=>e.getBoundingClientRect().top >= 0')
        page.locator('.section-rail button').first.click()
        page.wait_for_timeout(250)
        page.evaluate('window.scrollTo(0, 0)')
        assert page.evaluate('scrollY < 2')
        page.evaluate("S.page='providers';render();openWizard(S.profiles[0]);S.models=" + repr(MODELS) + ';S.stage=2;drawWizard()')
        page.locator('.modal').screenshot(path=str(assets/'model-selection.png'), animations='disabled')
        page.evaluate('closeWizard()')
        page.evaluate("navigate('connection')")
        page.locator('#tools-diagnose').click()
        page.wait_for_function('Tools.diagnostics && !Tools.diagnosing')
        assert page.locator('.diagnostic-state[data-state=error]').count() == 1
        page.evaluate('clearToast()')
        page.screenshot(path=str(assets/'connection-diagnostics.png'), full_page=True, animations='disabled')
        page.evaluate("navigate('files')")
        page.wait_for_function('Tools.files && !Tools.fileBusy')
        assert 'example.txt' in page.locator('.files-table').inner_text()
        for kind in ('folder','markdown','slides','sheet','code','config','image','model','text'):
            assert page.locator('[data-file-type="'+kind+'"]').count()>=1,kind
        assert page.evaluate("fileKind({name:'SLIDES.PPTX',kind:'file'}).kind")=='slides'
        assert page.evaluate("fileKind({name:'unknown.zzz',kind:'file'}).kind")=='generic'
        page.evaluate('clearToast()')
        page.screenshot(path=str(assets/'remote-files.png'),full_page=True,animations='disabled')
        page.evaluate('S.monitor=' + json.dumps(MONITOR, ensure_ascii=False) + ";S.page='monitor';render()")
        page.set_viewport_size({'width':390, 'height':844})
        assert page.locator('.section-rail').evaluate("e=>getComputedStyle(e).overflowX==='auto'")
        page.screenshot(path=str(assets/'mobile.png'), full_page=True, animations='disabled')
        for width in (320, 390, 768, 1024, 1440, 1920):
            page.set_viewport_size({'width':width, 'height':900})
            for section in ('providers', 'connection', 'tunnel', 'help', 'monitor', 'files'):
                page.evaluate('(p)=>{S.page=p;render()}', section)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'), (width, section)
                if width > 640 and page.locator('.section-rail').count():
                    assert page.evaluate("()=>{const nav=document.querySelector('.section-rail').getBoundingClientRect(),content=document.querySelector('#content'),edge=content.getBoundingClientRect().right-parseFloat(getComputedStyle(content).paddingRight);return nav.left-edge>=8}"), (width, section, 'navigation overlaps content')
            page.evaluate("S.page='monitor';render();openTrend(0)")
            assert page.locator('.trend-dialog').evaluate('e=>e.scrollWidth<=e.clientWidth+1'),width
            assert page.locator('.trend-dialog').bounding_box()['width']<=width,width
            page.evaluate('closeTrend()')
        # Verify a blank public install stays on local connection setup, without SSH.
        empty = True
        calls.clear()
        page.reload()
        page.wait_for_function("S.loaded && S.page==='connection'")
        page.wait_for_timeout(1000)
        assert 'list' not in calls and 'updates_check' not in calls
        assert not errors, errors
        browser.close()
    print('Ten fictional screenshots generated; trend dialog, file icons, 36 page layouts and isolated first-run behavior passed.')


if __name__ == '__main__':
    main()
