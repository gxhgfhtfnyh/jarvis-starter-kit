# -*- coding: utf-8 -*-
"""LAN device finder for the presence detector (2026-10-22). Installs nothing, uses the camera for nothing.

    python presence_devices.py scan [--out FILE]  # ping sweep of the /24 + ARP table + names -> logs\\presence_devices.json
    python presence_devices.py seen        # cheap check: which devices are in the ARP table right now (no sweep)
    python presence_devices.py set <MAC>   # store his phone's MAC in logs\\presence_config.json (phone_mac)

Names tried per live host: reverse DNS, unicast mDNS PTR (port 5353), NetBIOS (nbtstat -A). Vendor: local table of
OUIs I am sure of, then a cached lookup of the 3-byte OUI only (api.macvendors.com; nothing else is sent). A MAC whose
first byte has the 0x02 bit set is a *private/randomized* address (iPhones with Private Wifi Address and modern
Android do this): the vendor is unknowable from it and the address may change per network.
"""
import json
import re
import socket
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LOGS = Path(r"J:\Jarvis2\logs")
DEVICES_FILE = LOGS / "presence_devices.json"
CONFIG_FILE = LOGS / "presence_config.json"
OUI_CACHE = LOGS / "presence_oui_cache.json"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

LOCAL_OUI = {"F0:72:EA": "Google/Nest (likely)", "DC:E5:5B": "Google (likely)", "7C:2E:BD": "Google (likely)"}


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NOWIN)
        return r.stdout.decode("cp437", "replace")
    except Exception:
        return ""


def local_subnet():
    """-> (my_ip, base 'a.b.c') from the interface that has a default gateway (first /24 found)."""
    out = _run(["ipconfig"])
    ips = re.findall(r"IPv4 Address[ .]*: ([\d.]+)", out)
    gws = re.findall(r"Default Gateway[ .]*: ([\d.]+)", out)
    if gws:
        base = ".".join(gws[0].split(".")[:3])
        for ip in ips:
            if ip.startswith(base + "."):
                return ip, base
    if ips:
        return ips[0], ".".join(ips[0].split(".")[:3])
    return None, None


def arp_table(iface_ip=None):
    """{ip: MAC 'AA:BB:..'} for dynamic entries (interface block of iface_ip if given)."""
    out = _run(["arp", "-a"])
    res, cur = {}, None
    for line in out.splitlines():
        m = re.match(r"Interface: ([\d.]+)", line)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"\s*([\d.]+)\s+([0-9a-f]{2}(?:-[0-9a-f]{2}){5})\s+dynamic", line, re.I)
        if m and (iface_ip is None or cur == iface_ip):
            res[m.group(1)] = m.group(2).upper().replace("-", ":")
    return res


def ping(ip):
    out = _run(["ping", "-n", "1", "-w", "400", ip], timeout=5)
    return "TTL=" in out


def sweep(base):
    with ThreadPoolExecutor(max_workers=64) as ex:
        return {f"{base}.{i}": ok for i, ok in zip(range(1, 255), ex.map(ping, (f"{base}.{i}" for i in range(1, 255))))}


def rdns(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return None


def mdns_name(ip):
    try:
        labels = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
        q = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)
        for part in labels.split("."):
            q += bytes([len(part)]) + part.encode()
        q += b"\x00" + struct.pack(">HH", 12, 0x8001)  # PTR, IN + unicast-response bit
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        s.sendto(q, (ip, 5353))
        data, _ = s.recvfrom(2048)
        s.close()
        m = re.findall(rb"[\x20-\x7e]{4,}", data[12:])
        names = [x.decode() for x in m if b".local" in x or b"iPhone" in x or b"iPad" in x]
        return names[0] if names else None
    except Exception:
        return None


def netbios_name(ip):
    out = _run(["nbtstat", "-A", ip], timeout=6)
    m = re.search(r"^\s*(\S+)\s+<00>\s+UNIQUE", out, re.M)
    return m.group(1) if m else None


def is_private_mac(mac):
    return bool(int(mac[:2], 16) & 0x02)


def vendor(mac, cache):
    if is_private_mac(mac):
        return "private/randomized address (phone-style; vendor unknowable)"
    oui = mac[:8]
    if oui in LOCAL_OUI:
        return LOCAL_OUI[oui]
    if oui in cache:
        return cache[oui]
    try:
        import urllib.request
        req = urllib.request.Request("https://api.macvendors.com/" + oui, headers={"User-Agent": "jarvis-presence"})
        v = urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "replace").strip()[:60]
    except Exception:
        v = "unknown (lookup failed)"
    if not v.startswith("unknown"):
        cache[oui] = v
    time.sleep(1.1)  # the free endpoint is rate limited
    return v


def scan(out_file=None):
    out_file = out_file or DEVICES_FILE
    my_ip, base = local_subnet()
    if not base:
        return {"error": "no local subnet found"}
    _run(["arp", "-d", "*"], timeout=10)  # drop stale cache entries (works only if allowed; harmless if not)
    alive = sweep(base)
    arp = arp_table(my_ip)
    hosts = sorted(set(arp) | {ip for ip, ok in alive.items() if ok}, key=lambda x: tuple(map(int, x.split("."))))
    hosts = [h for h in hosts if h in arp and not h.endswith(".255") and h != my_ip]
    try:
        cache = json.loads(OUI_CACHE.read_text(encoding="utf-8"))
    except Exception:
        cache = {}

    def probe(ip):
        return {"ip": ip, "mac": arp[ip], "answers_ping": alive.get(ip, False),
                "reverse_dns": rdns(ip), "mdns": mdns_name(ip),
                "netbios": netbios_name(ip) if alive.get(ip) else None}
    with ThreadPoolExecutor(max_workers=16) as ex:
        devs = list(ex.map(probe, hosts))
    for d in devs:
        d["private_mac"] = is_private_mac(d["mac"])
        d["vendor"] = vendor(d["mac"], cache)
        d["hostname"] = d["reverse_dns"] or d["mdns"] or d["netbios"]
    OUI_CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    out = {"scanned_at": time.time(), "my_ip": my_ip, "subnet": base + ".0/24", "devices": devs}
    Path(out_file).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def phone_seen(phone_mac, ping_ip=None):
    """Cheap presence probe for the configured phone: -> {'in_arp': bool, 'ping': bool|None, 'ip': str|None}."""
    my_ip, base = local_subnet()
    arp = arp_table(my_ip)
    ip = next((i for i, m in arp.items() if m == phone_mac.upper()), None)
    ip = ip or ping_ip
    pinged = None
    if ip:
        pinged = ping(ip)
        if pinged:
            arp = arp_table(my_ip)
    in_arp = any(m == phone_mac.upper() for m in arp.values())
    return {"in_arp": in_arp, "ping": pinged, "ip": ip}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        outf = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
        r = scan(outf)
        for d in r.get("devices", []):
            print(d["ip"], d["mac"], "ping" if d["answers_ping"] else "----", d["hostname"], "|", d["vendor"])
    elif cmd == "seen":
        my_ip, _ = local_subnet()
        for ip, mac in arp_table(my_ip).items():
            print(ip, mac)
    elif cmd == "set" and len(sys.argv) > 2:
        mac = sys.argv[2].upper().replace("-", ":")
        cfg = {}
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        cfg["phone_mac"] = mac
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print("phone_mac stored:", mac)
    else:
        print(__doc__)
