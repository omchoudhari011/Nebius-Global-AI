"""Jarvis keeping an eye on the PC: it speaks up, once, when something needs you.

- The battery, unplugged, at 20% and again at 10% (said again only after it's been charged).
- The processor above 90% for two minutes running - with the app using most of it.
- Memory above 92% for two minutes.
- A drive with under 5% free (checked at most hourly).

Each kind of warning is said at most every half hour, and never in Custodian's quiet hours (the
app checks those before saying it). check() only decides; it never speaks or reads the PC.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CPU_HIGH, MEMORY_HIGH = 90.0, 92.0
SUSTAINED = 120.0  # seconds above the line before it counts
REPEAT_AFTER = 30 * 60
BATTERY_STEPS = (20, 10)
DISK_FREE_SHARE = 0.05


@dataclass
class Watchdog:
    high_since: dict[str, float] = field(default_factory=dict)
    said_at: dict[str, float] = field(default_factory=dict)
    battery_told: set[int] = field(default_factory=set)
    disks_checked_at: float = -1e9

    def _due(self, kind: str, now: float) -> bool:
        return now - self.said_at.get(kind, -1e9) >= REPEAT_AFTER

    def _sustained(self, kind: str, high: bool, now: float) -> bool:
        if not high:
            self.high_since.pop(kind, None)
            return False
        started = self.high_since.setdefault(kind, now)
        return now - started >= SUSTAINED

    def check(self, reading: dict, now: float, address: str = "sir") -> list[tuple[str, str]]:
        """(kind, what to say) for each warning due now."""
        alerts: list[tuple[str, str]] = []

        def say(kind: str, text: str) -> None:
            if self._due(kind, now):
                self.said_at[kind] = now
                alerts.append((kind, text))

        battery = reading.get("battery")
        if battery:
            if battery.get("charging"):
                self.battery_told = {s for s in self.battery_told if battery["percent"] <= s}  # charged past it: tell again next time
            else:
                step = next((s for s in sorted(BATTERY_STEPS) if battery["percent"] <= s), None)
                if step is not None and step not in self.battery_told:
                    self.battery_told |= {s for s in BATTERY_STEPS if s >= step}
                    left = f", about {battery['minutes_left']} minutes left" if battery.get("minutes_left") else ""
                    self.said_at.pop("battery", None)
                    say("battery", f"{address.capitalize() if address else 'Sir'}, the battery is at {battery['percent']}%{left}. "
                                   "You may want to plug in.")
        cpu = reading.get("cpu_percent") or 0
        if self._sustained("cpu", cpu >= CPU_HIGH, now):
            top = (reading.get("top_apps") or [{}])[0]
            culprit = f" {top['app']} is using the most." if top.get("app") and top.get("cpu_percent", 0) >= 20 else ""
            say("cpu", f"The processor has been above 90% for a couple of minutes, {address}.{culprit}")
        memory = (reading.get("memory") or {}).get("used_percent") or 0
        if self._sustained("memory", memory >= MEMORY_HIGH, now):
            say("memory", f"Memory is nearly full, {address}: {round(memory)}% in use. Closing an app or two would help.")
        if now - self.disks_checked_at >= 3600:
            self.disks_checked_at = now
            for disk in reading.get("disks") or []:
                if disk.get("total_gb") and disk["free_gb"] / disk["total_gb"] < DISK_FREE_SHARE:
                    say(f"disk {disk['drive']}", f"Drive {disk['drive'].rstrip(chr(92))} is almost full, {address}: "
                                                 f"{disk['free_gb']:g} GB left.")
        return alerts
