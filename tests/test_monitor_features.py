import json
import unittest
from unittest.mock import patch
import monitor_features

class MonitorFeatures(unittest.TestCase):
    def run_background(self, settings, snapshots, cycles):
        clock=[0.0]
        class Stop:
            def is_set(self):return clock[0]>=cycles*5
            def wait(self,delay):clock[0]+=delay
        f=monitor_features.Features()
        with patch('manager.read_settings',side_effect=settings), \
             patch('server_monitor.monitor.collect',side_effect=snapshots) as collect, \
             patch('monitor_features.time.monotonic',side_effect=lambda:clock[0]), \
             patch('remote_tools.transfers.cleanup') as cleanup:
            f.loop(Stop())
        self.assertEqual(cleanup.call_count,1)
        return f,collect

    def test_background_samples_without_page_or_watches_and_follows_connection(self):
        a,b={'host':'demo'},{'host':'other'}
        f,collect=self.run_background([a,a,b], [
            dict(collected_at=100,gpus=[]),dict(collected_at=105,gpus=[]),
            dict(collected_at=110,gpus=[])],3)
        self.assertEqual(collect.call_count,3)
        self.assertEqual([x['at'] for x in f.history(a)['samples']],[100,105])
        self.assertEqual([x['at'] for x in f.history(b)['samples']],[110])
        self.assertEqual(f.status(a)['watches'],[])

    def test_background_skips_blank_setup_and_recovers_without_fake_samples(self):
        s={'host':'demo'}
        f,collect=self.run_background([{},s,s,s,s], [
            dict(collected_at=105,gpus=[]),
            dict(collected_at=105,gpus=[],stale=True,error='offline'),
            ValueError('temporarily unavailable'),
            dict(collected_at=120,gpus=[])],5)
        self.assertEqual(collect.call_count,4)
        self.assertEqual([x['at'] for x in f.history(s)['samples']],[105,120])

    def test_history_bounded_isolated_no_stale_no_duplicate(self):
        f=monitor_features.Features();settings={'host':'demo'}
        for i in range(500):f.record(settings,dict(collected_at=i*5,gpus=[dict(index=0,utilization=20)]))
        self.assertEqual(len(f.history(settings)['samples']),360)
        f.record(settings,dict(collected_at=3000,gpus=[],stale=True))
        self.assertEqual(f.history(settings)['samples'][-1]['at'],2495)
        f.record(settings,dict(collected_at=2495,gpus=[]))
        self.assertEqual(f.history(settings)['samples'][-1]['gpus'][0]['index'],0)
        self.assertEqual(f.history({'host':'other'})['samples'],[])

    def test_watch_disconnect_and_visibility_never_mean_exit(self):
        f=monitor_features.Features();s={'host':'demo'}
        good=dict(boot_id='boot',processes={'12':dict(status='running',ticks='100')})
        with patch('remote_tools.request',return_value=good):f.add(s,12,'100')
        with patch('remote_tools.request',side_effect=ValueError('offline')):f.tick(s)
        self.assertEqual(f.status(s)['events'],[])
        self.assertEqual(f.status(s)['watches'][0]['state'],'disconnected')
        with patch('remote_tools.request',return_value=dict(boot_id='boot',processes={'12':dict(status='unknown')})):f.tick(s)
        self.assertEqual(f.status(s)['events'],[])
        missing=dict(boot_id='boot',processes={'12':dict(status='missing')})
        with patch('remote_tools.request',return_value=missing):f.tick(s);self.assertEqual(f.status(s)['events'],[]);f.tick(s);f.tick(s)
        self.assertEqual(len(f.status(s)['events']),1)
        self.assertEqual(f.status(s)['watches'][0]['state'],'exited')

    def test_pid_reuse_is_confirmed_and_reboot_distinguished(self):
        s={'host':'demo'}
        for boot,ticks,expected in [('boot','101','exited'),('newboot','100','restarted')]:
            f=monitor_features.Features()
            with patch('remote_tools.request',return_value=dict(boot_id='boot',processes={'12':dict(status='running',ticks='100')})):f.add(s,12,'100')
            with patch('remote_tools.request',return_value=dict(boot_id=boot,processes={'12':dict(status='running',ticks=ticks)})):f.tick(s);f.tick(s)
            self.assertEqual(f.status(s)['watches'][0]['state'],expected)
            self.assertEqual(len(f.status(s)['events']),1)
            self.assertEqual(f.status({'host':'other'})['events'],[])

    def test_wrong_identity_rejected(self):
        with patch('remote_tools.request',return_value=dict(boot_id='boot',processes={'12':dict(status='running',ticks='200')})):
            with self.assertRaises(ValueError):monitor_features.Features().add({},12,'100')
