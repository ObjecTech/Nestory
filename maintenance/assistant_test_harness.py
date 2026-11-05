"""Maintenance-only Qwen harness. Never imported by the production application.

No Torch import, model load, device open or production DB read at import/prepare.
Functional chat owns an explicitly quiet fixture phase; it is NOT a periodic
vision concurrency qualification. This retained B0 fixture harness is independent
of the approved prototype chat-first policy. No unprofiled_chat fallback is used.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import socket
import stat
import sys
import threading
import time
import urllib.request
from types import SimpleNamespace
import uuid

from PIL import Image, ImageDraw

from baby_monitor.assistant_common import AssistantError
from baby_monitor.inference_scheduler import InferenceScheduler

ROOT = Path(__file__).resolve().parents[1]
MARKER = 'nestory-m2b-synthetic-only-v1'
FIXTURES = ('empty', 'gray', 'occluded')
CASES = [
    ('feeding_complete', '今天上午11点宝宝喝了150ml奶。', 'saved actual 150ml; exact time'),
    ('actual_consumed', '冲了150ml，但宝宝只喝了80ml。', 'never save 150; ask for missing time'),
    ('ambiguous', '宝宝刚才喝了一些奶。', 'confirmation; zero event before confirmation'),
    ('future', '明天下午准备给宝宝换尿布。', 'zero confirmed writes'),
    ('sleep', '下午1点30分到2点20分宝宝睡了一觉。', '3000 seconds; run after 14:20 local'),
    ('feeding_query', '今天宝宝喝了多少奶？', 'SQL facts, no care writes'),
    ('correction', '上一条喂奶记录写错了。', 'confirmation; do not guess a replacement'),
]


def write_private(path, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)


def no_symlinks(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlinked test paths are forbidden')
    return path


def prepare_run(parent):
    parent = no_symlinks(parent)
    # Neither checkout nor its production/default data is a valid test root.
    production = Path.home()/'baby-monitor-public'
    for protected in (ROOT, production):
        if parent == protected or protected in parent.parents:
            raise ValueError('Test root must be outside the source/production checkout')
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(parent.stat().st_mode) & 0o077:
        raise ValueError('Test parent must be private (0700); do not chmod a production directory')
    run = parent/(dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    run.mkdir(mode=0o700)
    for name in ('data', 'logs', 'fixtures'):
        (run/name).mkdir(mode=0o700)
    assets = {}
    for name in FIXTURES:
        image = Image.new('RGB', (640,360), '#888888')
        draw = ImageDraw.Draw(image)
        if name != 'gray':
            draw.rectangle((32,30,608,330), outline='#eeeeee', width=6)
            draw.line((32,250,608,250), fill='#dddddd', width=4)
        if name == 'occluded':
            draw.rectangle((0,0,640,290), fill='#151515')
        out = io.BytesIO(); image.save(out, 'JPEG', quality=85)
        raw = out.getvalue(); path = run/'fixtures'/(name+'.jpg')
        fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd,'wb') as stream: stream.write(raw)
        assets[name] = {'file': path.name, 'sha256': hashlib.sha256(raw).hexdigest(),
                        'source': 'generated geometric scene; no people; no external asset',
                        'expected': 'unknown/not_visible allowed; not a posture accuracy fixture'}
    write_private(run/'manifest.json', {'marker': MARKER, 'fixtures': assets,
        'baseline': 'eb1ab5af7447a83fcc808651c26e728de09e98a6',
        'real_model_tested': False, 'production_access': False})
    write_private(run/'cases.json', [{'id': k, 'input': text, 'oracle': oracle} for k,text,oracle in CASES])
    return run


def validate_run(run):
    run = no_symlinks(run)
    if stat.S_IMODE(run.stat().st_mode) != 0o700:
        raise ValueError('Run directory must have mode 0700')
    manifest = json.loads((run/'manifest.json').read_text())
    if manifest.get('marker') != MARKER:
        raise ValueError('Not a prepared isolated test run')
    for name in ('data','logs','fixtures'):
        path = no_symlinks(run/name)
        if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise ValueError('Invalid private test directory')
    for protected in (ROOT, Path.home()/'baby-monitor-public'):
        if run == protected or protected in run.parents:
            raise ValueError('Production/source paths are forbidden')
    for name in FIXTURES:
        path = no_symlinks(run/'fixtures'/(name+'.jpg'))
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['fixtures'][name]['sha256']:
            raise ValueError('Fixture changed after preparation')
    for path in (run/'data').rglob('*'):
        no_symlinks(path)
        if path.is_file() and path.stat().st_nlink != 1:
            raise ValueError('Hard-linked runtime data is forbidden')
    return run


class Journal:
    def __init__(self, run, backend):
        self.path = run/'logs'/('events-'+uuid.uuid4().hex[:8]+'.jsonl')
        fd = os.open(self.path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        self.stream = os.fdopen(fd, 'w', buffering=1)
        self.backend = backend; self.lock = threading.Lock()
        self.failed=False; self.on_failure=None

    def emit(self, kind, **data):
        value = dict(schema='m2b-v1', utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                     monotonic=time.monotonic(), pid=os.getpid(), thread_id=threading.get_ident(),
                     backend=self.backend, kind=kind, **data)
        with self.lock:
            try:
                self.stream.write(json.dumps(value, ensure_ascii=True, allow_nan=False)+'\n')
            except OSError:
                self.failed=True
                if self.on_failure: self.on_failure()

    def close(self):
        self.stream.close()


class FixtureGate:
    """No next fixture until a declared quiet period expires AND chat drains.

    This test-only sequencing gate never stretches a production observer period.
    It forbids mixed-load qualification while hard execution bounds are unknown.
    """
    def __init__(self, clock=time.monotonic):
        self.clock=clock; self.lock=threading.RLock(); self.phase='vision'
        self.not_before=0; self.active_chat=0; self.active_vision=0

    def begin_vision(self):
        with self.lock:
            if self.phase=='stopping' or self.active_chat or self.active_vision or self.clock()<self.not_before:
                raise RuntimeError('Fixture phase is not drained or its promised quiet period has not expired')
            self.phase='vision'

    def begin_functional(self):
        with self.lock:
            if self.phase!='vision' or self.active_chat or self.active_vision:
                raise RuntimeError('Cannot open functional phase')
            # The header was consumed BEFORE the completed fixture response; wait
            # 120s from NOW as well, so network/processing cannot shorten the promise.
            self.not_before=self.clock()+120
            self.phase='functional'

    @contextlib.contextmanager
    def job(self, kind):
        with self.lock:
            if kind=='chat':
                if self.phase!='functional' or self.active_vision or self.clock()+9>=self.not_before:
                    raise AssistantError('vision_capacity_reserved',503,retryable=True)
                self.active_chat+=1
            else:
                if self.phase!='vision' or self.active_chat:
                    raise AssistantError('vision_capacity_reserved',503,retryable=True)
                self.active_vision+=1
        try:
            yield
        finally:
            with self.lock:
                if kind=='chat': self.active_chat-=1
                else: self.active_vision-=1

    def stopping(self):
        with self.lock: self.phase='stopping'


@contextlib.contextmanager
def isolated_environment(run, model_port, web_port, weights=None):
    # Preserve only interpreter/library essentials, never production BMON_* / auth
    # / proxy / provider variables. Keep offline flags for the entire worker life.
    keep={key:value for key,value in os.environ.items() if key in
          ('HOME','PATH','LANG','LC_ALL','LD_LIBRARY_PATH','TMPDIR','SYSTEMROOT')}
    keep.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
        TZ='Asia/Shanghai', BMON_APP_DIR=str(ROOT), BMON_DATA_DIR=str(run/'data'),
        BMON_MEDIA_MOUNT=str(run/'data'/'media'), BMON_MEDIA_DIR=str(run/'data'/'media'),
        BMON_DEMO='false', BMON_HOST='127.0.0.1', BMON_PORT=str(web_port),
        BMON_MODEL_ENABLED='false', BMON_VLM_ENABLED='true', BMON_ASSISTANT_ENABLED='true',
        BMON_VOICE_ENABLED='false', BMON_PLAYBACK_BACKEND='silent',
        BMON_CLASSIFIED_PHOTOS_ENABLED='false', BMON_PERSON_TEST_MODE='false',
        BMON_LOCAL_PREVIEW_NO_LOGIN='false', BMON_VLM_SERVICE_URL=f'http://127.0.0.1:{model_port}',
        BMON_VLM_HOST='127.0.0.1', BMON_VLM_PORT=str(model_port),
        BMON_ASSISTANT_ALLOW_UNPROFILED_CHAT='false')
    if weights is not None: keep['BMON_VLM_MODEL_PATH']=str(weights)
    saved=dict(os.environ)
    previous_umask=os.umask(0o077)
    saved_opener=urllib.request._opener
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    os.environ.clear(); os.environ.update(keep)
    try: yield
    finally:
        os.environ.clear(); os.environ.update(saved)
        os.umask(previous_umask)
        urllib.request._opener=saved_opener


def port_free(port):
    with socket.socket() as sock:
        try: sock.bind(('127.0.0.1',port))
        except OSError: raise ValueError('Required loopback port is occupied') from None


def real_preflight(run, weights, maintenance_started, model_port, web_port):
    if not maintenance_started:
        raise ValueError('A new explicit human maintenance-start instruction is required; B0 cannot start real mode')
    if platform.system()!='Linux' or platform.machine() not in ('aarch64','arm64'):
        raise ValueError('Real mode requires the verified Jetson interpreter; no Mac model load')
    validate_run(run)
    if Path(run).absolute().parent != (Path.home()/'nestory-m2b').absolute():
        raise ValueError('Real runs must be under the dedicated nestory-m2b directory')
    if model_port!=18010 or web_port!=18881:
        raise ValueError('Real mode uses the reviewed candidate ports only')
    for port in (8010,8080,model_port,web_port): port_free(port)
    # Model loading is forbidden if the legacy worker or another harness is alive,
    # including loading processes that have not bound a port yet. Read cmdlines
    # only to classify them; never print/store credentials or full command lines.
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name)==os.getpid(): continue
        try: cmd=(proc/'cmdline').read_bytes().split(b'\0')
        except (OSError,PermissionError): continue
        scripts=[Path(os.fsdecode(arg)).name for arg in cmd if arg]
        if 'model_server.py' in scripts or 'run-assistant-qwen-test.py' in scripts:
            raise ValueError('Another model worker/harness exists; do not force-kill or load a second model')
    if not Path(weights).joinpath('model.safetensors').is_file():
        raise ValueError('Existing Qwen3-VL-2B weights are required')
    production=Path.home()/'baby-monitor-public'
    if Path(weights).resolve() != (production/'models'/'Qwen3-VL-2B-Instruct').resolve():
        raise ValueError('Only the existing 2B weight directory may be reused')
    if Path(sys.prefix).resolve() != (production/'.venv-models').resolve():
        raise ValueError('Use the existing provisioned .venv-models interpreter')
    config=json.loads((Path(weights)/'config.json').read_text())
    if config.get('model_type')!='qwen3_vl' or config.get('architectures')!=['Qwen3VLForConditionalGeneration']:
        raise ValueError('Unexpected model architecture; do not upgrade in this harness')


def load_worker(mock=False):
    # Import the unchanged worker HTTP handlers; Torch/Transformers only when
    # explicitly requested after preflight. Mock mode stubs imports, never loads.
    spec=importlib.util.spec_from_file_location('m2b_worker_'+uuid.uuid4().hex,ROOT/'model_server.py')
    module=importlib.util.module_from_spec(spec)
    if mock:
        # Restore only these two entries. Restoring the entire sys.modules dict
        # would unload newly imported NumPy/app modules and then reload them.
        missing=object()
        stubs={'torch':SimpleNamespace(), 'transformers':
               SimpleNamespace(AutoProcessor=object,Qwen3VLForConditionalGeneration=object)}
        previous={key:sys.modules.get(key,missing) for key in stubs}
        sys.modules.update(stubs)
        try:
            spec.loader.exec_module(module)
        finally:
            for key,value in previous.items():
                if value is missing: sys.modules.pop(key,None)
                else: sys.modules[key]=value
        dev_spec=importlib.util.spec_from_file_location('m2b_synthetic',ROOT/'scripts/run-assistant-dev.py')
        dev=importlib.util.module_from_spec(dev_spec); dev_spec.loader.exec_module(dev)
        class MockEngine:
            ready=True; error=None; load_seconds=0
            scheduler=InferenceScheduler()
            model='MOCK_ONLY_NOT_QWEN'
            def analyze(self, data, person_test_mode=False):
                with self.scheduler.slot('vision'):
                    Image.open(io.BytesIO(data)).verify()
                    from baby_monitor.vlm import normalize_observation
                    return dict(model=self.model,person_test_mode=person_test_mode,
                        observation_mode='adult_test' if person_test_mode else 'infant',
                        load_seconds=0,infer_seconds=0,observation=normalize_observation({
                            'baby_visible':False,'visibility':'not_visible','confidence':.95,
                            'visibility_confidence':.95},person_test_mode=person_test_mode))
            def chat(self, payload):
                with self.scheduler.slot('chat') as wait:
                    result=dev.SyntheticAssistantModel().chat(payload)
                    return dict(model=self.model,request_id=payload['request_id'],queue_wait_ms=wait,
                                infer_ms=0,input_tokens=0,output_tokens=0,result=result)
        module.ENGINE=MockEngine()
    return module


class Harness:
    def __init__(self, run, module, password, model_port=18010, web_port=18881, backend='mock'):
        from access_control import AccessStateStore
        from security import AuthConfig,make_password_hash
        from server import Handler,MonitorServer
        from baby_monitor.app import BabyMonitorApp
        from baby_monitor.camera import Camera
        from baby_monitor.config import Config
        if len(password)<14: raise ValueError('Test password must have at least 14 characters')
        self.run=validate_run(run); self.gate=FixtureGate(); self.module=module
        manifest=json.loads((self.run/'manifest.json').read_text())
        fixture_hashes={value['sha256'] for value in manifest['fixtures'].values()}
        self.journal=Journal(self.run,backend); self.started=False; self.closed=False
        self.bootstrap=False
        self.pipeline_lock=threading.Lock(); self.http_active=0
        self.app=BabyMonitorApp(Config.from_env())
        class FixtureCamera(Camera):
            def start(self): self.online=True
            def stop(self): self.online=False
        camera=FixtureCamera('generated-fixture-only',640,360,1)
        self.app.camera=camera; self.app.vlm.camera=camera; self.app.inference.camera=camera
        # No app.start(): no camera hardware, audio, background inference or
        # background VLM. A real observer is driven explicitly by fixture().
        camera.start()
        engine=module.ENGINE
        def journal_failed():
            self.gate.stopping(); engine.scheduler.stop_accepting()
        self.journal.on_failure=journal_failed
        raw_chat,raw_analyze=engine.chat,engine.analyze
        def chat(payload):
            with self.gate.job('chat'):
                result=raw_chat(payload)
                if self.journal.failed: raise AssistantError('model_failed',502,retryable=True)
                return result
        def analyze(data,person_test_mode=False):
            if hashlib.sha256(data).hexdigest() not in fixture_hashes:
                raise AssistantError('validation_error',422)
            with self.gate.job('vision'):
                result=raw_analyze(data,person_test_mode)
                if self.journal.failed: raise AssistantError('model_failed',502,retryable=True)
                return result
        engine.chat=chat; engine.analyze=analyze
        journal=self.journal
        class LoggedModelHandler(module.Handler):
            def _json(self,payload,status=200):
                if backend=='mock' and isinstance(payload,dict):
                    payload=dict(payload,test_backend='mock')
                    if 'model' in payload: payload['model']='MOCK_ONLY_NOT_QWEN'
                if self.command=='POST': journal.emit('model_response',route=self.path,status=int(status),output=payload)
                super()._json(payload,status)
        harness=self
        class TrackedWebServer(MonitorServer):
            def process_request(self,request,client_address):
                with harness.gate.lock: harness.http_active+=1
                try: super().process_request(request,client_address)
                except BaseException:
                    with harness.gate.lock: harness.http_active-=1
                    raise
            def process_request_thread(self,request,client_address):
                try: super().process_request_thread(request,client_address)
                finally:
                    with harness.gate.lock: harness.http_active-=1
        class TestWebHandler(Handler):
            def _test_write(self,method):
                path=self.path.split('?',1)[0]
                if path.startswith('/api/assistant/') or path in ('/login','/logout'):
                    return getattr(super(),method)()
                # Retain authentication/security for Assistant, but never expose
                # media uploads, audio controls or hardware/settings mutations.
                self._json(AssistantError('access_disabled',403).envelope('test-hardware-disabled'),403)
            def do_POST(self): return self._test_write('do_POST')
            def do_PATCH(self): return self._test_write('do_PATCH')
            def do_DELETE(self): return self._test_write('do_DELETE')
        self.model=None
        try:
            self.model=module.ModelHTTPServer(('127.0.0.1',model_port),LoggedModelHandler)
            origin=f'http://127.0.0.1:{web_port}'
            self.web=TrackedWebServer(('127.0.0.1',web_port),TestWebHandler,self.app,
                AccessStateStore(self.run/'data'/'access.json'),
                AuthConfig('assistant-test',make_password_hash(password),origin),auto_login=False)
        except BaseException:
            if self.model: self.model.server_close()
            self.app.stop(); self.journal.close()
            raise
        self.threads=[]
        self.journal.emit('prepared',real_model_loaded=False,external_api_version='1',
                          mode='exclusive_fixture_functional; periodic_mixed_load_blocked')

    def start(self):
        for server in (self.model,self.web):
            thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05},daemon=True)
            thread.start(); self.threads.append(thread)
        self.started=True
        self.journal.emit('listening',model_port=self.model.server_port,web_port=self.web.server_port)

    def capture_test_decoder(self):
        """Test-only raw output tap, including malformed JSON; never prompts/auth."""
        processor=self.module.ENGINE.processor
        original=processor.batch_decode
        def decode(*args,**kwargs):
            values=original(*args,**kwargs)
            self.journal.emit('raw_generation',
                running_kind=self.module.ENGINE.scheduler.metrics()['running_kind'],
                decoded=[value[:65536] for value in values],
                truncated=any(len(value)>65536 for value in values))
            return values
        processor.batch_decode=decode

    def fixture(self, name='empty', functional=False, adult=False):
        if not self.pipeline_lock.acquire(blocking=False):
            raise RuntimeError('Fixture pipeline already running')
        try: return self._fixture(name,functional,adult)
        finally: self.pipeline_lock.release()

    def _fixture(self, name, functional, adult):
        if name not in FIXTURES: raise ValueError('Only prepared geometric fixtures are accepted')
        # No queued turn may race a phase change. Pending/running business work is
        # drained as well as model jobs; gate.job checks again at actual admission.
        if self.app.assistant.queue.unfinished_tasks:
            raise RuntimeError('Assistant business queue must drain before a fixture')
        self.gate.begin_vision()
        self.bootstrap=functional
        self.app.set_observation_subject(adult)
        self.app.camera._accept_jpeg((self.run/'fixtures'/(name+'.jpg')).read_bytes())
        try:
            self.app.vlm._observe_frame(self.app.camera.latest())
        finally:
            self.bootstrap=False
        self.journal.emit('visual_pipeline',fixture=name,mode='adult_test' if adult else 'infant',
            online=self.app.vlm.online,result=self.app.vlm.last_result,
            next_normal_interval=self.app.vlm._next_interval(),
            test_observations=len(self.app.database.observations(limit=100)))
        if not self.app.vlm.online:
            raise RuntimeError('Fixture inference/pipeline failed; functional chat remains closed')
        if functional:
            self.gate.begin_functional()
            self.journal.emit('functional_phase',quiet_seconds=120,
                              warning='No periodic-concurrency acceptance; next fixture gated until lease and chat drain')

    def stop(self, timeout=120):
        if self.closed: return True
        self.gate.stopping()
        self.module.ENGINE.scheduler.stop_accepting()
        if self.started:
            self.web.close_streams(); self.web.shutdown()
        deadline=time.monotonic()+timeout
        while (self.app.assistant.queue.unfinished_tasks or self.module.ENGINE.scheduler.metrics()['running_kind']
               or self.gate.active_chat or self.gate.active_vision or self.pipeline_lock.locked() or self.http_active):
            if time.monotonic()>=deadline:
                self.journal.emit('drain_blocked',restore_production_allowed=False)
                return False
            time.sleep(.05)
        if self.started: self.model.shutdown()
        self.web.server_close(); self.model.server_close()
        for thread in self.threads: thread.join(2)
        self.app.stop()
        for path in (self.run/'data').rglob('*'):
            if path.is_file(): path.chmod(0o600)
        self.journal.emit('stopped',production_changed=False)
        self.journal.close(); self.closed=True
        return True


class ResourceSampler:
    """Test-process metrics only. The optional tegrastats child is read-only."""
    def __init__(self,harness,on_limit,real=False,min_available_gib=10):
        self.harness=harness; self.on_limit=on_limit; self.real=real
        self.minimum=min_available_gib*1024**3
        self.stop_event=threading.Event(); self.thread=None; self.child=None; self.output=None

    def start(self):
        if self.real:
            import shutil
            import subprocess
            executable=shutil.which('tegrastats')
            if executable:
                path=self.harness.run/'logs'/('tegrastats-'+uuid.uuid4().hex[:8]+'.log')
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                self.output=os.fdopen(fd,'wb')
                self.child=subprocess.Popen([executable,'--interval','1000'],stdout=self.output,
                    stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL)
            else: self.harness.journal.emit('metrics_unavailable',metric='tegrastats')
        def sample():
            while not self.stop_event.wait(1):
                memory={}
                if platform.system()=='Linux':
                    for line in Path('/proc/meminfo').read_text().splitlines():
                        key,_,value=line.partition(':')
                        if key in ('MemAvailable','MemTotal','SwapFree','SwapTotal'):
                            memory[key]=int(value.split()[0])*1024
                self.harness.journal.emit('sample',scheduler=self.harness.module.ENGINE.scheduler.metrics(),
                                          memory_bytes=memory,phase=self.harness.gate.phase)
                if self.real and memory.get('MemAvailable',self.minimum)<self.minimum:
                    self.harness.journal.emit('resource_stop',reason='available_memory_below_limit',
                                              limit_bytes=self.minimum,force_kill=False)
                    self.on_limit(); break
        self.thread=threading.Thread(target=sample,daemon=True); self.thread.start()

    def close(self):
        import signal
        import subprocess
        self.stop_event.set()
        if self.thread: self.thread.join(2)
        if self.child and self.child.poll() is None:
            self.child.send_signal(signal.SIGINT)
            try: self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # Never turn cleanup into an automatic kill policy.
                self.harness.journal.emit('sampler_cleanup_blocked',pid=self.child.pid,force_kill=False)
                return False
        if self.output: self.output.close()
        return True
