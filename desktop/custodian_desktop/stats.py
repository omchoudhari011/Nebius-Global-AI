"""The PC's vital signs, for "system status", the HUD and the watchdog: processor, memory,
graphics card, battery, drives, network and the busiest apps.

One Sampler keeps what it needs between readings (network byte counters, each process's CPU
time, the last GPU reading) so a reading is cheap: the HUD takes one every two seconds.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

GPU_EVERY = 5.0  # seconds between nvidia-smi calls
SKIP_PROCESSES = {"system idle process", "system", "idle", "registry", "memory compression", "secure system"}


def _gpu() -> dict | None:
    """Utilisation, memory and temperature of an NVIDIA card, or None (no card or no driver tools)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no console flashing up under pythonw
        ).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    try:
        name, util, used, total, temp = [p.strip() for p in out[0].split(",")]
        return {"name": name, "percent": float(util), "memory_used_gb": round(float(used) / 1024, 1),
                "memory_total_gb": round(float(total) / 1024, 1), "temperature_c": float(temp)}
    except ValueError:
        return None


class Sampler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._net: tuple[float, int, int] | None = None
        self._procs: dict[int, float] = {}  # pid -> CPU seconds at the last reading
        self._apps_at = 0.0
        self._gpu: tuple[float, dict | None] = (0.0, None)
        self.cores = os.cpu_count() or 1

    def _network(self, psutil) -> dict:  # noqa: ANN001
        counters = psutil.net_io_counters()
        now = time.monotonic()
        down = up = 0.0
        if self._net is not None:
            seconds = max(now - self._net[0], 1e-3)
            down = max(0, counters.bytes_recv - self._net[1]) / seconds
            up = max(0, counters.bytes_sent - self._net[2]) / seconds
        self._net = (now, counters.bytes_recv, counters.bytes_sent)
        online = any(stats.isup for name, stats in psutil.net_if_stats().items()
                     if "loopback" not in name.lower() and "vethernet" not in name.lower())
        return {"online": online, "down_kbps": round(down * 8 / 1000, 1), "up_kbps": round(up * 8 / 1000, 1)}

    def _top_apps(self, psutil, limit: int = 3) -> list[dict]:  # noqa: ANN001
        """Apps by CPU since the last reading (the first reading primes the counters). One pass
        over the processes, each read once: CPU is the change in its CPU time since last time."""
        now = time.monotonic()
        elapsed = now - self._apps_at if self._apps_at else 0.0
        by_name: dict[str, dict] = {}
        times: dict[int, float] = {}
        for proc in psutil.process_iter(["name", "cpu_times", "memory_info"]):
            info = proc.info
            if info.get("cpu_times") is None:
                continue
            spent = info["cpu_times"].user + info["cpu_times"].system
            times[proc.pid] = spent
            name = (info.get("name") or "?").removesuffix(".exe")
            if name.lower() in SKIP_PROCESSES:
                continue
            before = self._procs.get(proc.pid)
            cpu = max(0.0, spent - before) / elapsed / self.cores * 100 if before is not None and elapsed > 0 else 0.0
            entry = by_name.setdefault(name, {"app": name, "cpu_percent": 0.0, "memory_mb": 0})
            entry["cpu_percent"] += cpu
            entry["memory_mb"] += round((info["memory_info"].rss if info.get("memory_info") else 0) / 1e6)
        self._procs, self._apps_at = times, now
        top = sorted(by_name.values(), key=lambda a: -a["cpu_percent"])[:limit]
        for app in top:
            app["cpu_percent"] = round(app["cpu_percent"], 1)
        return top

    def fresh(self, seconds: float) -> bool:
        """Whether the per-app counters were read within `seconds` (a reading now measures since then)."""
        return bool(self._apps_at) and time.monotonic() - self._apps_at < seconds

    def sample(self, *, cpu_interval: float | None = None, with_apps: bool = True) -> dict:
        import psutil

        with self._lock:
            cpu = psutil.cpu_percent(interval=cpu_interval)
            memory = psutil.virtual_memory()
            battery = psutil.sensors_battery()
            disks = []
            for part in psutil.disk_partitions(all=False):
                try:
                    use = psutil.disk_usage(part.mountpoint)
                except OSError:
                    continue
                disks.append({"drive": part.device, "free_gb": round(use.free / 1e9, 1), "total_gb": round(use.total / 1e9, 1),
                              "used_percent": use.percent})
            if time.monotonic() - self._gpu[0] > GPU_EVERY:  # nvidia-smi takes up to a second: refresh it on the side
                self._gpu = (time.monotonic(), self._gpu[1])

                def refresh() -> None:
                    self._gpu = (time.monotonic(), _gpu())

                threading.Thread(target=refresh, daemon=True, name="gpu-reading").start()
            return {
                "cpu_percent": cpu,
                "memory": {"used_percent": memory.percent, "used_gb": round(memory.used / 1e9, 1),
                           "total_gb": round(memory.total / 1e9, 1)},
                "gpu": self._gpu[1],
                "battery": {"percent": round(battery.percent), "charging": bool(battery.power_plugged),
                            "minutes_left": (round(battery.secsleft / 60) if battery.secsleft not in
                                             (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) else None)}
                if battery else None,
                "disks": disks,
                "network": self._network(psutil),
                "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
                "top_apps": self._top_apps(psutil) if with_apps else [],
            }


SAMPLER = Sampler()
