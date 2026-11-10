"""Keep the app's browser frame in sync without changing the user's browser."""
import json
import threading
import time
import urllib.request
from pathlib import Path
import websocket

_lock = threading.RLock()
_applied = None
_retry_after = 0
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def browser_endpoint(profile):
    try:
        lines = (Path(profile) / 'DevToolsActivePort').read_text().splitlines()
        port = int(lines[0])
        if not 0 < port < 65536:
            return None
        with _opener.open('http://127.0.0.1:%d/json/version' % port, timeout=0.6) as response:
            endpoint = json.load(response).get('webSocketDebuggerUrl', '')
        if not endpoint.startswith('ws://127.0.0.1:%d/' % port):
            return None
        return endpoint, port
    except (OSError, ValueError, IndexError):
        return None


def launch_arguments(state, dark):
    profile = Path(state) / 'ui-browser'
    profile.mkdir(parents=True, exist_ok=True)
    if not browser_endpoint(profile):
        prefs_path = profile / 'Default/Preferences'
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            prefs = json.loads(prefs_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            prefs = {}
        browser = prefs.setdefault('browser', {})
        browser['prefer_system_light_dark_scheme'] = False
        theme = browser.setdefault('theme', {})
        theme['color_scheme'] = theme['color_scheme2'] = 2 if dark else 1
        prefs_path.write_text(json.dumps(prefs), encoding='utf-8')
    return ['--user-data-dir=' + str(profile), '--remote-debugging-address=127.0.0.1',
            '--remote-debugging-port=0', '--no-default-browser-check',
            '--disable-session-crashed-bubble', '--hide-crash-restore-bubble']


class _CDP:
    def __init__(self, url):
        self.socket = websocket.create_connection(url, timeout=3, suppress_origin=True,
                                                  http_no_proxy=['127.0.0.1', 'localhost'])
        self.sequence = 0

    def call(self, method, **params):
        self.sequence += 1
        self.socket.send(json.dumps(dict(id=self.sequence, method=method, params=params)))
        while True:
            response = json.loads(self.socket.recv())
            if response.get('id') == self.sequence:
                if 'error' in response:
                    raise RuntimeError(response['error'].get('message', 'Browser command failed'))
                return response.get('result', {})

    def close(self):
        self.socket.close()


def sync_theme(state, dark):
    """Change only the isolated app profile, using a hidden settings target."""
    global _applied, _retry_after
    with _lock:
        if time.monotonic() < _retry_after:
            return False
        endpoint = browser_endpoint(Path(state) / 'ui-browser')
        if not endpoint:
            return False
        key = (endpoint[0], bool(dark))
        if key == _applied:
            return True
        browser = page = None
        target = None
        try:
            browser = _CDP(endpoint[0])
            # A hidden target never opens a settings window or steals focus.
            target = browser.call('Target.createTarget', url='chrome://settings/appearance', hidden=True)['targetId']
            page = _CDP('ws://127.0.0.1:%d/devtools/page/%s' % (endpoint[1], target))
            for _ in range(25):
                result = page.call('Runtime.evaluate', expression='typeof chrome.settingsPrivate', returnByValue=True)
                if result.get('result', {}).get('value') == 'object':
                    break
                time.sleep(.08)
            expression = '''(async()=>{
              const api=chrome.settingsPrivate;
              const prefs=await new Promise(resolve=>api.getAllPrefs(resolve));
              const keys=new Set(prefs.map(p=>p.key));
              const scheme=['browser.theme.color_scheme2','browser.theme.color_scheme'].find(k=>keys.has(k));
              if(!scheme)throw Error('Browser theme preference unavailable');
              const set=(k,v)=>new Promise((resolve,reject)=>api.setPref(k,v,'',()=>{
                if(chrome.runtime.lastError)reject(Error(chrome.runtime.lastError.message));else resolve();
              }));
              if(keys.has('browser.prefer_system_light_dark_scheme'))await set('browser.prefer_system_light_dark_scheme',false);
              await set(scheme,SCHEME);
              const actual=await new Promise(resolve=>api.getPref(scheme,resolve));
              return actual.value===SCHEME;
            })()'''.replace('SCHEME', '2' if dark else '1')
            result = page.call('Runtime.evaluate', expression=expression, awaitPromise=True, returnByValue=True)
            if result.get('exceptionDetails') or result.get('result', {}).get('value') is not True:
                raise RuntimeError('Browser theme verification failed')
            _applied = key
            return True
        except (OSError, ValueError, RuntimeError, websocket.WebSocketException):
            _retry_after = time.monotonic() + 10
            return False
        finally:
            if page:
                page.close()
            if browser:
                try:
                    if target:
                        browser.call('Target.closeTarget', targetId=target)
                except (OSError, RuntimeError, websocket.WebSocketException):
                    pass
                browser.close()
