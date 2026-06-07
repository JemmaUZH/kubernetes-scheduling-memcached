#!/usr/bin/env python3
"""
Part 4 scheduler: co-schedules memcached with PARSEC batch jobs on a 4-core VM.
Run directly on the memcache-server VM.
"""

import sys
import time
import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

import docker
import psutil

from scheduler_logger import SchedulerLogger, Job as LogJob

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
log = logging.getLogger(__name__)

JOB_ENUM = {
    "freqmine":      LogJob.FREQMINE,
    "streamcluster": LogJob.STREAMCLUSTER,
    "barnes":        LogJob.BARNES,
    "canneal":       LogJob.CANNEAL,
    "vips":          LogJob.VIPS,
    "radix":         LogJob.RADIX,
    "blackscholes":  LogJob.BLACKSCHOLES,
}

# ── Thresholds: memcached uses 1 or 3 cores only ──────────────────────────────
CPU_UP_1_3   = 75.0   # 1 → 3 cores: approaching 1-core saturation
CPU_DOWN_3_1 = 60.0   # 3 → 1 core:  ~20% per core, load has dropped off
POLL_S       = 1
TOTAL_CORES  = 4

# ── Job definitions ───────────────────────────────────────────────────────────
EXPECTED_RUNTIMES = {
    "freqmine": 180, "streamcluster": 143, "canneal": 139,
    "barnes": 98, "blackscholes": 92, "vips": 48, "radix": 22,
}

JOB_CONFIG = {
    "freqmine":      {"image": "anakli/cca:parsec_freqmine",      "suite": "parsec",   "threads": 1},
    "streamcluster": {"image": "anakli/cca:parsec_streamcluster",  "suite": "parsec",   "threads": 1},
    "barnes":        {"image": "anakli/cca:splash2x_barnes",       "suite": "splash2x", "threads": 1},
    "canneal":       {"image": "anakli/cca:parsec_canneal",        "suite": "parsec",   "threads": 1},
    "vips":          {"image": "anakli/cca:parsec_vips",           "suite": "parsec",   "threads": 1},
    "radix":         {"image": "anakli/cca:splash2x_radix",        "suite": "splash2x", "threads": 1},
    "blackscholes":  {"image": "anakli/cca:parsec_blackscholes",   "suite": "parsec",   "threads": 2},
}

LAUNCH_ORDER = ["freqmine", "streamcluster", "barnes", "canneal", "vips", "radix", "blackscholes"]

# Slot-to-core mapping:
#   slot 0 → core 1  (paused when memcached uses 3 cores)
#   slot 1 → core 2  (paused when memcached uses 3 cores)
#   slot 2 → core 3  (always active)
BATCH_CORES = [1, 2, 3]


# ── Job state ─────────────────────────────────────────────────────────────────
@dataclass
class Job:
    name: str
    container_id: str
    core: int
    start_time: float
    paused: bool = False
    pause_time: Optional[float] = None
    total_paused: float = 0.0

    def elapsed(self) -> float:
        extra = (time.time() - self.pause_time) if self.paused and self.pause_time else 0.0
        return time.time() - self.start_time - self.total_paused - extra

    def remaining(self) -> float:
        return max(0.0, EXPECTED_RUNTIMES[self.name] - self.elapsed())


# ── Scheduler ─────────────────────────────────────────────────────────────────
class Scheduler:
    def __init__(self):
        self.docker = docker.from_env()
        self.logger = SchedulerLogger()
        self.memcached_pid = self._find_memcached_pid()
        self.memcached_cores = 1
        self.slots: list[Optional[Job]] = [None, None, None]
        self.queue: list[str] = list(LAUNCH_ORDER)

        self.logger.job_start(LogJob.MEMCACHED, [0], 3)
        self._set_memcached_cores(1, initial=True)
        for i in range(3):
            self._fill_slot(i)

    # ── Memcached helpers ──────────────────────────────────────────────────────
    def _find_memcached_pid(self) -> int:
        result = subprocess.run(["pgrep", "-x", "memcached"], capture_output=True, text=True)
        pids = result.stdout.strip().split()
        if not pids:
            raise RuntimeError("memcached process not found — is it running?")
        return int(pids[0])

    def _set_memcached_cores(self, n: int, initial: bool = False):
        cores = list(range(n))
        subprocess.run(
            ["sudo", "taskset", "-a", "-cp", ",".join(str(c) for c in cores), str(self.memcached_pid)],
            check=True,
        )
        self.memcached_cores = n
        if not initial:
            self.logger.update_cores(LogJob.MEMCACHED, cores)
        log.info(f"memcached → {n} core(s) {cores}")

    def _memcached_cpu(self) -> float:
        return psutil.Process(self.memcached_pid).cpu_percent(interval=1.0)

    # ── Slot helpers ───────────────────────────────────────────────────────────
    def _slot_active(self, slot: int) -> bool:
        return slot == 2 or self.memcached_cores == 1

    def _fill_slot(self, slot: int):
        if not self.queue or self.slots[slot] is not None:
            return
        name = self.queue.pop(0)
        core = BATCH_CORES[slot]
        cfg = JOB_CONFIG[name]
        cmd = f"./run -a run -S {cfg['suite']} -p {name} -i native -n {cfg['threads']}"
        container = self.docker.containers.run(
            cfg["image"], cmd,
            cpuset_cpus=str(core),
            detach=True, remove=True, name=name,
        )
        job = Job(name=name, container_id=container.id, core=core, start_time=time.time())
        self.slots[slot] = job
        self.logger.job_start(JOB_ENUM[name], [core], cfg["threads"])
        log.info(f"launched {name} on core {core} (slot {slot})")
        if not self._slot_active(slot):
            self._do_pause(slot)

    def _do_pause(self, slot: int):
        job = self.slots[slot]
        if job is None or job.paused:
            return
        try:
            self.docker.containers.get(job.container_id).pause()
        except (docker.errors.NotFound, docker.errors.APIError) as e:
            log.warning(f"{job.name} pause failed: {e}")
            return
        job.paused = True
        job.pause_time = time.time()
        self.logger.job_pause(JOB_ENUM[job.name])
        log.info(f"paused  {job.name} (slot {slot})")

    def _do_resume(self, slot: int):
        job = self.slots[slot]
        if job is None or not job.paused:
            return
        core = BATCH_CORES[slot]
        try:
            c = self.docker.containers.get(job.container_id)
            c.update(cpuset_cpus=str(core))
            c.unpause()
        except (docker.errors.NotFound, docker.errors.APIError) as e:
            log.warning(f"{job.name} resume failed: {e}")
            return
        job.total_paused += time.time() - job.pause_time
        job.paused = False
        job.pause_time = None
        job.core = core
        self.logger.job_unpause(JOB_ENUM[job.name])
        self.logger.update_cores(JOB_ENUM[job.name], [core])
        log.info(f"resumed {job.name} on core {core} (slot {slot})")

    # ── Core scaling ───────────────────────────────────────────────────────────
    def _maybe_scale(self, cpu: float):
        if self.memcached_cores == 1 and cpu > CPU_UP_1_3:
            target = 3
        elif self.memcached_cores == 3 and cpu < CPU_DOWN_3_1:
            target = 1
        else:
            return

        log.info(f"scaling {self.memcached_cores} → {target} cores (cpu={cpu:.1f}%)")
        self._set_memcached_cores(target)
        if target == 3:
            self._do_pause(0)
            self._do_pause(1)
        else:
            self._do_resume(0)
            self._do_resume(1)

    # ── Job completion ─────────────────────────────────────────────────────────
    def _check_finished(self):
        for slot in range(3):
            job = self.slots[slot]
            if job is None:
                continue
            try:
                status = self.docker.containers.get(job.container_id).status
                done = status not in ("running", "paused")
            except docker.errors.NotFound:
                done = True
            if not done:
                continue

            self.logger.job_end(JOB_ENUM[job.name])
            log.info(f"done    {job.name} (elapsed {job.elapsed():.0f}s)")
            self.slots[slot] = None

            if slot == 2:
                self._promote_all()
            else:
                self._fill_slot(slot)

            self._maybe_expand_last()

    def _maybe_expand_last(self):
        active = [(i, j) for i, j in enumerate(self.slots) if j is not None]
        if len(active) != 1 or self.queue:
            return
        _, job = active[0]
        batch_cores = list(range(self.memcached_cores, TOTAL_CORES))
        all_cores = ",".join(str(c) for c in batch_cores)
        try:
            self.docker.containers.get(job.container_id).update(cpuset_cpus=all_cores)
            self.logger.update_cores(JOB_ENUM[job.name], batch_cores)
            log.info(f"expanded {job.name} to all batch cores [{all_cores}]")
        except (docker.errors.NotFound, docker.errors.APIError):
            pass

    def _promote_all(self):
        """Core-3 job finished: shift slots 0 and 1 up by one, then fill slot 0."""
        for s in range(2, 0, -1):
            if self.slots[s - 1] is None:
                continue
            job = self.slots[s - 1]
            new_core = BATCH_CORES[s]
            try:
                c = self.docker.containers.get(job.container_id)
                c.update(cpuset_cpus=str(new_core))
                if self._slot_active(s) and job.paused:
                    c.unpause()
                    job.total_paused += time.time() - job.pause_time
                    job.paused = False
                    job.pause_time = None
                    self.logger.job_unpause(JOB_ENUM[job.name])
            except (docker.errors.NotFound, docker.errors.APIError):
                log.warning(f"{job.name} gone during promotion")
                self.slots[s - 1] = None
                continue
            job.core = new_core
            self.logger.update_cores(JOB_ENUM[job.name], [new_core])
            log.info(f"promoted {job.name} → core {new_core}")
            self.slots[s] = job
            self.slots[s - 1] = None

        self._fill_slot(0)

    # ── Main loop ──────────────────────────────────────────────────────────────
    def run(self):
        log.info("scheduler started")
        start = time.time()
        while any(s is not None for s in self.slots) or self.queue:
            cpu = self._memcached_cpu()
            log.info(f"cpu={cpu:.1f}% mcores={self.memcached_cores} slots={[s.name if s else None for s in self.slots]} q={self.queue}")
            self._maybe_scale(cpu)
            self._check_finished()
            time.sleep(POLL_S)

        self.logger.end()
        log.info(f"all done — makespan {time.time() - start:.0f}s")


if __name__ == "__main__":
    Scheduler().run()
