"""Jarvis: how Custodian talks when you speak to your PC.

Turns spoken to the desktop app (POST /chat with voice=true from the desktop) get the Jarvis
manner when the "persona" setting is "jarvis" (the default): calm, brief, polite, a dry wit, and
you addressed as the "address" setting ("sir" unless you change it). Typed chats in the app keep
Custodian's ordinary voice.

Only the manner changes, never the facts: the model gets a short note about it (note()), and the
instant replies of the fast path (app/fastpath.py) get the address added (dress()) - "Opened
Chrome." becomes "Opened Chrome, sir." Every reply still goes through the same accuracy checks.
"""

from __future__ import annotations

import re

from . import settings_store


def wanted(user_id: int, *, voice: bool, origin: str | None) -> str | None:
    """The address to use ("sir") when this turn should sound like Jarvis, else None."""
    if not voice or origin != "desktop":
        return None
    prefs = settings_store.get(user_id)
    if prefs["persona"] != "jarvis":
        return None
    return prefs["address"] or "sir"


def note(address: str) -> str:
    return (
        f"You are speaking out loud as Custodian, the voice of their PC. Address them as “{address}” now and then, "
        "the way a capable butler would - not in every sentence. Be calm, polite and brief, with a dry wit when it fits: "
        "one to three short sentences, no lists, headings, links or markdown, since this is read aloud. "
        "The manner never changes the facts: say only what the tools and memories show."
    )


_LAST_STOP = re.compile(r"([.!?])\s*$")


def dress(reply: str, address: str) -> str:
    """'Opened Chrome.' -> 'Opened Chrome, sir.'; left alone when it's long, already addresses
    them, or has more than one sentence (the address goes in the first short one)."""
    text = reply.strip()
    if not text or address.lower() in text.lower() or "\n" in text:
        return reply
    first, sep, rest = text.partition(". ")
    if sep:  # "All systems normal. Processor at 12%..." -> address the first sentence
        if len(first) > 90:
            return reply
        return f"{first}, {address}. {rest}"
    if len(text) > 140:
        return reply
    found = _LAST_STOP.search(text)
    if not found:
        return f"{text}, {address}."
    return f"{text[: found.start()]}, {address}{found.group(1)}"


def status_reply(result: dict, address: str | None = None) -> str:
    """A spoken system report from pc_system_info's result."""
    you = f", {address}" if address else ""
    parts, warnings = [], []
    cpu = result.get("cpu_percent")
    memory = (result.get("memory") or {}).get("used_percent")
    if cpu is not None:
        parts.append(f"processor at {round(cpu)}%")
        if cpu >= 90:
            warnings.append("the processor is running hot")
    if memory is not None:
        parts.append(f"memory at {round(memory)}%")
        if memory >= 90:
            warnings.append("memory is nearly full")
    gpu = result.get("gpu")
    if gpu and gpu.get("percent") is not None:
        parts.append(f"graphics at {round(gpu['percent'])}%")
    battery = result.get("battery")
    if battery:
        parts.append(f"battery {battery['percent']}%" + (" and charging" if battery.get("charging") else ""))
        if battery["percent"] <= 20 and not battery.get("charging"):
            warnings.append("the battery is low")
    for disk in result.get("disks") or []:
        if disk.get("total_gb") and disk["free_gb"] / disk["total_gb"] < 0.08:
            warnings.append(f"drive {disk['drive'].rstrip(chr(92))} has only {disk['free_gb']} GB free")
    system = disk_note = ""
    disks = result.get("disks") or []
    if disks:
        main = disks[0]
        disk_note = f" {main['free_gb']:g} GB free on {main['drive'].rstrip(chr(92))}."
    network = result.get("network")
    if network is not None:
        disk_note += " Online." if network.get("online") else " No network connection."
    top = result.get("top_apps") or []
    busiest = f" {top[0]['app']} is the busiest app." if top and top[0].get("cpu_percent", 0) >= 25 else ""
    head = f"All systems normal{you}." if not warnings else f"A few things need attention{you}: {', '.join(warnings)}."
    if parts:
        system = " " + ", ".join(parts)[:1].upper() + ", ".join(parts)[1:] + "."
    return (head + system + disk_note + busiest).strip()
