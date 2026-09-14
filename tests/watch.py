"""Spectator access boundaries and real desktop/MCP/HTTP acceptance."""
import argparse
import concurrent.futures
import hashlib
import http.client
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/linux-computer-use/scripts'))
from cul.engine import Engine
from cul.watch import Viewer, Spectator, registry_directory, request, sockets


def http(server, route, body=None, authorized=True, **headers):
    if authorized:
        headers['Authorization'] = 'Bearer ' + server.token
    if body is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(server.origin + route, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if 'application/json' in response.headers['Content-Type'] else raw
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class Boundaries(unittest.TestCase):
    def test_stale_sockets_do_not_hide_a_live_session(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, CUL_WATCH_DIR=temp):
            for index in range(70):
                path = pathlib.Path(temp) / f'{index:016x}.sock'
                with socket.socket(socket.AF_UNIX) as stale:
                    stale.bind(str(path))
                path.chmod(0o600)
            engine = Engine()
            engine.session_id = 'f' * 16
            spectator = Spectator(engine, lambda:None)
            viewer = Viewer(pathlib.Path(temp))
            threading.Thread(target=viewer.serve_forever, daemon=True).start()
            try:
                code, result = http(viewer, '/api/sessions')
                self.assertEqual(code, 200)
                self.assertEqual([item['session_id'] for item in result['sessions']], [engine.session_id])
                self.assertEqual(http(viewer, '/api/frame?session='+engine.session_id)[1]['state'], 'waiting')
            finally:
                viewer.shutdown(); viewer.server_close(); spectator.close(); engine.close()

    def test_registry_refuses_shared_or_symlink_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / 'shared'
            path.mkdir(mode=0o755)
            with patch.dict(os.environ, CUL_WATCH_DIR=str(path)):
                with self.assertRaises(PermissionError): registry_directory()
            path.chmod(0o700)
            link = pathlib.Path(temp) / 'alias'
            link.symlink_to(path, target_is_directory=True)
            with patch.dict(os.environ, CUL_WATCH_DIR=str(link)):
                with self.assertRaises(PermissionError): registry_directory()

    def test_http_authorization_origin_host_and_stop_boundary(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, CUL_WATCH_DIR=temp):
            engine = Engine()
            stopped = threading.Event()
            spectator = Spectator(engine, stopped.set)
            viewer = Viewer(pathlib.Path(temp))
            worker = threading.Thread(target=viewer.serve_forever, daemon=True)
            worker.start()
            try:
                self.assertEqual(http(viewer, '/api/sessions', authorized=False)[0], 403)
                self.assertEqual(http(viewer, '/api/sessions', Origin='https://untrusted.example')[0], 403)
                self.assertEqual(http(viewer, '/api/sessions', Host='untrusted.example')[0], 403)
                code, data = http(viewer, '/api/sessions')
                self.assertEqual((code, len(data['sessions'])), (200, 1))
                self.assertEqual(data['sessions'][0]['session_id'], engine.session_id)
                self.assertEqual(http(viewer, '/api/frame?session=../../other')[0], 400)
                self.assertEqual(http(viewer, '/api/stop', {'session': engine.session_id}, authorized=False)[0], 403)
                self.assertFalse(stopped.is_set())
                self.assertEqual(http(viewer, '/api/stop', {'session': engine.session_id}, Origin='https://untrusted.example')[0], 403)
                self.assertFalse(stopped.is_set())
                self.assertIn('error', request(spectator.path, 'click', x=1, y=2))
                self.assertFalse(stopped.is_set())
                self.assertEqual(http(viewer, '/api/stop', {'session': engine.session_id})[1], {'stopping': True})
                self.assertTrue(stopped.wait(1))
            finally:
                viewer.shutdown(); viewer.server_close(); spectator.close(); engine.close()
            self.assertFalse(sockets(pathlib.Path(temp)))


def acceptance(args):
    from e2e import Client, wait_state
    out = args.output.absolute()
    out.mkdir(parents=True, exist_ok=True)
    # Keep AF_UNIX paths short on CI workspaces too.
    registry = pathlib.Path(tempfile.mkdtemp(prefix='cul-watch-e2e-'))
    os.environ['CUL_WATCH_DIR'] = str(registry)
    state_file = out / 'fixture.json'
    state_file.unlink(missing_ok=True)
    command = [str(args.launcher.absolute()), 'mcp'] if args.launcher else None
    if args.container:
        os.environ['CUL_WORKDIR'] = str(ROOT)
        fixture = '/work/tests/fixture.py'
        remote_state = '/work/' + str(state_file.relative_to(ROOT))
    else:
        fixture, remote_state = str(ROOT / 'tests/fixture.py'), str(state_file)
    client = Client(out, command=command)
    viewer = Viewer(registry)
    viewer_thread = threading.Thread(target=viewer.serve_forever, daemon=True)
    viewer_thread.start()
    report = {'status':'failed','compositor':args.compositor,'container_launcher':args.container,'runtime_sha256':client.runtime_sha256,'checks':[]}
    try:
        client.request('initialize', {'protocolVersion':'2025-06-18'})
        session = client.tool('start_session', {'mode':'isolated', 'compositor':args.compositor})
        sid = session['session_id']
        assert session['watch']['available']
        report['checks'].append('MCP session advertises spectator availability')
        listed = http(viewer, '/api/sessions')[1]['sessions']
        assert any(item['session_id']==sid for item in listed)
        app = client.tool('launch_app', {'argv':['python3',fixture,remote_state,'--animate']})
        wait_state(state_file, lambda s: s['ready'])
        state = client.tool('get_state', {'pid':app['pid']})
        entry = next(n for n in state['accessibility']['nodes'] if n['name']=='Unicode test entry')
        client.tool('focus_element', {'token':entry['token']})
        before = http(viewer, '/api/frame?session='+sid)[1]
        assert before.get('image'), before
        phrase = 'Watched live — café 日本語 🐧'
        client.tool('type_text', {'text':phrase})
        wait_state(state_file, lambda s:s['text']==phrase)
        time.sleep(.12)
        after = http(viewer, '/api/frame?session='+sid)[1]
        assert after['image']['sha256'] != before['image']['sha256']
        import base64
        (out/'watched.jpg').write_bytes(base64.b64decode(after['image']['data']))
        report['checks'].append('Browser HTTP frame changed after real GTK Unicode input')
        frame = client.tool('screenshot', {'max_width':640})['image']
        for _ in range(20):
            response = http(viewer, '/api/frame?session='+sid)[1]
            assert response.get('image'), response
        client.tool('move', {'x':2,'y':2,'frame_id':frame['frame_id']})
        report['checks'].append('Twenty spectator reads do not evict or rescale the agent frame ID')
        # A long motion holds the action lock. Independent spectator capture must
        # return fresh images during it, rather than waiting for the action to end.
        drag = {'start_x':100,'start_y':100,'end_x':350,'end_y':250,'duration_ms':1800}
        prior_drag = http(viewer,'/api/frame?session='+sid)[1]['image']['sha256']
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(client.tool,'drag',drag)
            time.sleep(.25)
            started = time.monotonic()
            during = http(viewer,'/api/frame?session='+sid)[1]
            latency = time.monotonic()-started
            assert during.get('image') and not future.done(), (during.get('error'),latency)
            assert during['image']['sha256'] != prior_drag, 'Live counter froze during drag'
            future.result(timeout=10)
        report['during_drag_frame_ms']=round(latency*1000,2)
        report['checks'].append('Animated GTK counter visibly changes while a real drag remains in progress')
        # Closing a viewer must not close or change the agent session.
        viewer.shutdown(); viewer.server_close()
        client.tool('move_relative', {'dx':3,'dy':2})
        viewer=Viewer(registry)
        threading.Thread(target=viewer.serve_forever,daemon=True).start()
        assert http(viewer,'/api/frame?session='+sid)[1].get('image')
        report['checks'].append('Viewer closes and reopens while the same agent session keeps working')
        if args.compositor == 'xvfb' and not args.container:
            # Freeze only the owned preview helper, keeping Xvfb and agent input
            # responsive. Agent-issued stop must not wait on blocked Xlib capture.
            import signal
            children = sorted({pid for file in pathlib.Path(f'/proc/{client.process.pid}/task').glob('*/children') for pid in file.read_text().split()})
            helpers = [int(pid) for pid in children if b'capture_worker' in pathlib.Path('/proc',pid,'cmdline').read_bytes()]
            assert len(helpers)==1, helpers
            os.kill(helpers[0], signal.SIGKILL)
            time.sleep(.12)
            failed = http(viewer,'/api/frame?session='+sid)[1]
            assert failed.get('error'), failed
            assert http(viewer,'/api/frame?session='+sid)[1].get('image')
            report['checks'].append('A crashed capture helper is replaced and live viewing recovers')
            children = sorted({pid for file in pathlib.Path(f'/proc/{client.process.pid}/task').glob('*/children') for pid in file.read_text().split()})
            helpers = [int(pid) for pid in children if b'capture_worker' in pathlib.Path('/proc',pid,'cmdline').read_bytes()]
            assert len(helpers)==1, helpers
            os.kill(helpers[0], signal.SIGSTOP)
            time.sleep(.12)
            with concurrent.futures.ThreadPoolExecutor() as pool:
                waiting = pool.submit(http,viewer,'/api/frame?session='+sid)
                time.sleep(.2)
                started=time.monotonic()
                assert client.tool('stop')['stopped']
                assert time.monotonic()-started < 3
                waiting.result(timeout=3)
            report['checks'].append('Agent stop terminates a frozen preview helper before X11 teardown')
            assert http(viewer,'/api/stop',{'session':sid})[1]['stopping']
            client.process.wait(timeout=10)
        else:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(client.tool,'drag',{'start_x':100,'start_y':100,'end_x':450,'end_y':300,'duration_ms':5000})
                time.sleep(.25)
                started=time.monotonic()
                code,response=http(viewer,'/api/stop',{'session':sid})
                assert code==200 and response.get('stopping'),response
                try: future.result(timeout=10)
                except AssertionError: pass  # cancelled action is reported as a tool error
                client.process.wait(timeout=10)
                report['stop_ms']=round((time.monotonic()-started)*1000,2)
            report['checks'].append('Viewer stop cancels an active drag')
        assert client.process.returncode==0, client.process.returncode
        assert not http(viewer,'/api/sessions')[1]['sessions']
        report['checks'].append('Viewer stop exits MCP and removes spectator discovery')
        report['status']='passed'
    except Exception as error:
        report['error']=str(error)
        raise
    finally:
        viewer.shutdown();viewer.server_close();client.close()
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
        for child in registry.glob('c-*'):
            try: child.rmdir()
            except OSError: pass
        try: registry.rmdir()
        except OSError: pass


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--e2e',action='store_true')
    parser.add_argument('--compositor',default='sway',choices=['kwin','sway','xvfb','hyprland'])
    parser.add_argument('--output',type=pathlib.Path,default=ROOT/'artifacts/watch-e2e')
    parser.add_argument('--launcher',type=pathlib.Path)
    parser.add_argument('--container',action='store_true')
    args=parser.parse_args()
    if args.e2e: acceptance(args)
    else: unittest.main(argv=[sys.argv[0]],verbosity=2)
