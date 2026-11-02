"""Own activation and automatic readiness independently of browser sessions."""
import hashlib
import json
import os
from pathlib import Path
import threading


def migration_token(data):
    marker = Path(data) / 'migration-pending.json'
    return hashlib.sha256(marker.read_bytes()).hexdigest() if marker.is_file() else None


class BusinessControl:
    def __init__(self, bridge, ai, sender, *, data, monitor, ai_auto, send_auto):
        self.bridge, self.ai, self.sender = bridge, ai, sender
        self.data = Path(data)
        self.monitor, self.ai_auto, self.send_auto = monitor, ai_auto, send_auto
        self.stop = threading.Event()
        self.active = False
        self.thread = threading.Thread(target=self.loop, name='automatic-readiness', daemon=True)
        self.thread.start()

    def publish(self):
        from bridge_paths import ROOT
        manifest = ROOT / 'portable-manifest.json'
        commit = json.loads(manifest.read_text(encoding='utf-8'))['commit'] if manifest.is_file() else 'source'
        path = self.data / 'production/business.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(pid=os.getpid(), commit=commit, active=self.active)), encoding='utf-8')
        os.replace(temp, path)

    def step(self):
        if not self.active:
            self.bridge.store.deployment_hold = (self.data / 'deployment-hold.json').is_file()
            if not self.bridge.store.deployment_hold:
                if self.monitor: self.bridge.start_monitor()
                if self.ai_auto: self.ai.start_automatic()
                if self.send_auto: self.sender.start_automatic()
                self.active = True
            self.publish()
        if self.active:
            self.bridge.compatibility.reconcile()

    def loop(self):
        while not self.stop.is_set():
            try:
                self.step()
            except Exception:
                # A transient inspection/storage failure must not kill automatic rechecks.
                # Existing readiness remains fail-closed until a complete pass succeeds.
                try:
                    self.bridge.store.compatibility_hold('probe_failed')
                    self.bridge.discovery.start()
                except Exception:
                    pass  # Storage/discovery can be closing or temporarily unavailable.
            self.stop.wait(5)

    def close(self):
        self.stop.set()
        self.thread.join()
