"""Background jobs (downloads / installs) with live state, history and cancel support."""
import json
import os
import queue
import subprocess
import threading
import time

from PySide6.QtCore import QObject, Signal

NOWIN = 0x08000000  # CREATE_NO_WINDOW
ACTIVE = ("queued", "running")


class Job(QObject):
    changed = Signal()

    def __init__(self, name, kind, key="", fn=None, hidden=False, domain=None):
        super().__init__()
        self.name, self.kind, self.key, self.domain = name, kind, key, domain
        self.fn, self.hidden = fn, hidden
        self.state = "queued"  # queued running done failed cancelled
        self.progress = -1.0   # -1 means indeterminate
        self.message = "Queued"
        self.done_msg = "Completed"
        self.fail_msg = "Failed"
        self.path = ""
        self.size = 0
        self.finished_at = 0.0
        self.proc = None
        self.cancel_event = threading.Event()
        self.log = lambda msg: None

    @property
    def active(self):
        return self.state in ACTIVE

    @property
    def cancelled(self):
        return self.cancel_event.is_set()

    def update(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.changed.emit()

    def request_cancel(self):
        if not self.active:
            return
        self.cancel_event.set()
        p = self.proc
        if p and p.poll() is None:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True, creationflags=NOWIN)
        if self.state == "queued":
            self.update(state="cancelled", message="Cancelled", progress=-1, finished_at=time.time())


class JobManager(QObject):
    added = Signal(object)
    logged = Signal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.jobs = []
        self.q = queue.Queue()
        threading.Thread(target=self._serial, daemon=True).start()

    def submit(self, job, parallel=False):
        job.log = self.logged.emit
        if not job.hidden:
            self.jobs.insert(0, job)
            self.added.emit(job)
        if parallel:
            threading.Thread(target=self._run, args=(job,), daemon=True).start()
        else:
            self.q.put(job)

    def cancel(self, job):
        job.request_cancel()
        self.save()

    def _serial(self):
        while True:
            self._run(self.q.get())

    def _run(self, job):
        if job.state == "cancelled":
            return
        job.update(state="running", message="Starting…")
        try:
            ok = bool(job.fn(job))
            if job.cancelled:
                job.update(state="cancelled", message="Cancelled", progress=-1)
            elif ok:
                job.update(state="done", message=job.done_msg, progress=1.0)
            else:
                job.update(state="failed", message=job.fail_msg, progress=-1)
        except Exception as e:
            if job.cancelled:
                job.update(state="cancelled", message="Cancelled", progress=-1)
            else:
                job.update(state="failed", message=f"Failed · {e}", progress=-1)
        job.update(finished_at=time.time())
        self.save()

    def save(self):
        recs = [{"name": j.name, "kind": j.kind, "key": j.key, "domain": j.domain,
                 "state": j.state, "message": j.message, "path": j.path, "size": j.size,
                 "finished_at": j.finished_at}
                for j in self.jobs if not j.active][:100]
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(recs, f, indent=1)
        except OSError:
            pass

    def load(self):
        """Restore finished jobs from disk (oldest first)."""
        try:
            with open(self.path, encoding="utf-8") as f:
                recs = json.load(f)
        except (OSError, ValueError):
            return []
        jobs = []
        for r in recs:
            try:
                j = Job(r["name"], r["kind"], r.get("key", ""), domain=r.get("domain"))
                j.state, j.message = r["state"], r["message"]
                j.path, j.size, j.finished_at = r.get("path", ""), r.get("size", 0), r.get("finished_at", 0)
                j.progress = 1.0 if j.state == "done" else -1
                jobs.append(j)
            except (KeyError, TypeError):
                continue
        self.jobs = list(jobs)
        return list(reversed(jobs))
