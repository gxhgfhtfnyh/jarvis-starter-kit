# -*- coding: utf-8 -*-
"""Find the owner's phone on the LAN by DIFFERENCE, with no questions to him (2026-10-22).
Idea: his phone is on the home wifi when he is home and not when he is out, so it is a device in the HOME snapshot
that is absent from the AWAY snapshot(s).

    python presence_find_phone.py snapshot away   # scan now, MERGE into logs\\presence_devices_away.json (union of scans)
    python presence_find_phone.py snapshot home   # scan now into logs\\presence_devices_home.json, then run diff
    python presence_find_phone.py diff            # rank candidates; one clear candidate -> presence_config.json
    python presence_find_phone.py confirm <MAC>   # after he confirms: phone_mac set, candidate cleared

Ranking (score): +3 private/randomized MAC (phone-style), +2 hostname/mDNS name looks like a phone (iphone, android,
galaxy, pixel, ...), +1 seen in the home snapshot via ping too, -3 vendor is an IoT/network brand (TP-Link, Google/Nest,
BroadLink, Quectel, ...). Only devices missing from EVERY away scan qualify. "Clear" = exactly one candidate with score
>= 3 and nobody else >= 3. Written as `phone_candidate` with status "unconfirmed"; presence scoring ignores it until
`confirm`/`set` stores `phone_mac`.
Caveat: wifi-sleeping phones can be missing from a scan; more away scans (merged) make "absent" more trustworthy.
"""
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import presence_devices as D  # noqa: E402

AWAY = D.LOGS / "presence_devices_away.json"
HOME = D.LOGS / "presence_devices_home.json"
PHONE_NAME = re.compile(r"iphone|ipad|android|galaxy|pixel|redmi|xiaomi|oneplus|oppo|vivo|huawei|phone|sm-[a-z]\d", re.I)
IOT_VENDOR = re.compile(r"tp-?link|google|nest|broadlink|quectel|innopia|d&m|espressif|tuya|hikvision", re.I)


def _load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def snapshot(kind):
    if kind == "away":
        prev = _load(AWAY)
        new = D.scan(AWAY)
        if prev and "devices" in new:
            by = {d["mac"]: d for d in prev.get("devices", [])}
            by.update({d["mac"]: d for d in new["devices"]})
            new["devices"] = sorted(by.values(), key=lambda d: tuple(map(int, d["ip"].split("."))))
            new["scans"] = int(prev.get("scans", 1)) + 1
        else:
            new["scans"] = 1
        AWAY.write_text(json.dumps(new, indent=2, ensure_ascii=False), encoding="utf-8")
        return new
    out = D.scan(HOME)
    return out


def rank():
    away, home = _load(AWAY), _load(HOME)
    if not away or not home:
        return None, "need both snapshots (away: %s, home: %s)" % (bool(away), bool(home))
    away_macs = {d["mac"] for d in away["devices"]}
    cands = []
    for d in home["devices"]:
        if d["mac"] in away_macs:
            continue
        s, why = 0, []
        if d.get("private_mac"):
            s += 3
            why.append("private MAC")
        name = " ".join(x for x in (d.get("hostname"), d.get("mdns"), d.get("netbios")) if x)
        if name and PHONE_NAME.search(name):
            s += 2
            why.append("phone-like name")
        if d.get("answers_ping"):
            s += 1
            why.append("answers ping")
        if IOT_VENDOR.search(d.get("vendor") or ""):
            s -= 3
            why.append("IoT/network vendor")
        cands.append({"mac": d["mac"], "ip": d["ip"], "hostname": d.get("hostname"), "vendor": d.get("vendor"),
                      "score": s, "why": why})
    cands.sort(key=lambda c: -c["score"])
    return cands, None


def diff():
    cands, err = rank()
    if err:
        return {"error": err}
    strong = [c for c in cands if c["score"] >= 3]
    res = {"candidates": cands, "clear": len(strong) == 1 and (len(cands) < 2 or cands[1]["score"] < 3)}
    if res["clear"]:
        cfg = _load(D.CONFIG_FILE) or {}
        if not cfg.get("phone_mac"):
            cfg["phone_candidate"] = {"mac": strong[0]["mac"], "ip": strong[0]["ip"], "status": "unconfirmed",
                                      "found_at": time.time(), "why": strong[0]["why"]}
            D.CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            res["wrote"] = "phone_candidate (unconfirmed)"
    return res


def confirm(mac):
    cfg = _load(D.CONFIG_FILE) or {}
    cfg["phone_mac"] = mac.upper().replace("-", ":")
    cfg.pop("phone_candidate", None)
    D.CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return "phone_mac stored: " + cfg["phone_mac"]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    a = sys.argv[1:]
    if a[:1] == ["snapshot"] and len(a) > 1 and a[1] in ("away", "home"):
        r = snapshot(a[1])
        print(f"{a[1]} snapshot: {len(r.get('devices', []))} devices")
        if a[1] == "home":
            print(json.dumps(diff(), indent=2, ensure_ascii=False))
    elif a[:1] == ["diff"]:
        print(json.dumps(diff(), indent=2, ensure_ascii=False))
    elif a[:1] == ["confirm"] and len(a) > 1:
        print(confirm(a[1]))
    else:
        print(__doc__)
