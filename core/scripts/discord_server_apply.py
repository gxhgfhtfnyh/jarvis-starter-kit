# -*- coding: utf-8 -*-
"""Lock down and tidy the Discord server (2026-10-31, the owner: "clean up the server and restrict our channels", plan approved with "ok").
Dry run unless --apply. Nothing is deleted. Steps:
  1. every channel and category: the @everyone role may not VIEW it (the server owner and the admin bot are not affected by overwrites);
  2. a hidden category "Archive" is created and the old channels (school, builds, home-control, ideas, loom, new-chanel) are moved into it;
  3. a hidden category "Bots" with a private text channel "bot-chat" is created; nobody but the owner and this bot can see it until `--grant <user id>` adds one more account to #bot-chat only.
Before any change the current overwrites and parents of every channel are written to logs/discord_server_before.json, so it can be put back by hand or with --restore.
    python discord_server_apply.py [guild_id]                 # show the plan, change nothing
    python discord_server_apply.py [guild_id] --apply
    python discord_server_apply.py [guild_id] --grant <user id>   # let one account see and write in #bot-chat (and nowhere else)
    python discord_server_apply.py [guild_id] --public [--apply]      # a visible category Public with an open text channel #chat
    python discord_server_apply.py [guild_id] --restore       # put every overwrite and parent back from logs/discord_server_before.json (created channels stay)
Token: Credential Manager through jarvis_keyring, never printed. Pure core (unit tested): plan(channels, gid), grant_op(channel_id, user_id)."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOGS = os.environ.get("JARVIS_LOGS") or os.path.join(os.path.dirname(HERE), "logs")
API = "https://discord.com/api/v10"
VIEW = 1 << 10
MEMBER_ALLOW = (1 << 10) | (1 << 11) | (1 << 16) | (1 << 15) | (1 << 14) | (1 << 6)       # view, send, read history, attach, embed, react
OLD = {"school", "builds", "home-control", "ideas", "loom", "new-chanel"}


def deny_view(overwrites, gid):
    """Pure. -> the @everyone overwrite (id == guild id) with VIEW_CHANNEL denied and not allowed, other bits kept."""
    allow, deny = 0, 0
    for o in overwrites or []:
        if str(o.get("id")) == str(gid):
            allow, deny = int(o.get("allow") or 0), int(o.get("deny") or 0)
    return {"id": str(gid), "type": 0, "allow": str(allow & ~VIEW), "deny": str(deny | VIEW)}


def plan(channels, gid):
    """Pure. -> list of operations (dicts with method, path, body, why) in the order they should run. No DELETE is ever planned."""
    ops = []
    for c in channels:
        o = deny_view(c.get("permission_overwrites"), gid)
        if int(o["deny"]) & VIEW and not _already_hidden(c, gid):
            ops.append({"method": "PUT", "path": "/channels/%s/permissions/%s" % (c["id"], gid), "body": {"allow": o["allow"], "deny": o["deny"], "type": 0},
                        "why": "hide %s from @everyone" % c.get("name")})
    hidden_everyone = [{"id": str(gid), "type": 0, "allow": "0", "deny": str(VIEW)}]
    ops.append({"method": "POST", "path": "/guilds/%s/channels" % gid, "body": {"name": "Archive", "type": 4, "permission_overwrites": hidden_everyone}, "why": "create hidden category Archive", "save_as": "archive"})
    for c in channels:
        if c.get("type") == 0 and c.get("name") in OLD:
            ops.append({"method": "PATCH", "path": "/channels/%s" % c["id"], "body": {"parent_id": "{archive}"}, "why": "move %s to Archive" % c.get("name")})
    ops.append({"method": "POST", "path": "/guilds/%s/channels" % gid, "body": {"name": "Bots", "type": 4, "permission_overwrites": hidden_everyone}, "why": "create hidden category Bots", "save_as": "bots"})
    ops.append({"method": "POST", "path": "/guilds/%s/channels" % gid, "body": {"name": "bot-chat", "type": 0, "parent_id": "{bots}", "permission_overwrites": hidden_everyone,
                                                                                   "topic": "private: the owner, this assistant and invited assistants"}, "why": "create private #bot-chat", "save_as": "botchat"})
    return ops


def _already_hidden(c, gid):
    for o in c.get("permission_overwrites", []):
        if str(o.get("id")) == str(gid) and int(o.get("deny") or 0) & VIEW and not int(o.get("allow") or 0) & VIEW:
            return True
    return False


def public_ops(gid):
    """Pure. -> operations that create a visible category "Public" with a text channel "chat" where anyone who joined may read and write (no file or link embeds, no @everyone pings)."""
    allow = (1 << 10) | (1 << 11) | (1 << 16) | (1 << 6)                        # view, send, read history, add reactions
    deny = (1 << 15) | (1 << 14) | (1 << 17)                                      # attach files, embed links, mention @everyone
    ov = [{"id": str(gid), "type": 0, "allow": str(allow), "deny": str(deny)}]
    return [{"method": "POST", "path": "/guilds/%s/channels" % gid, "body": {"name": "Public", "type": 4}, "why": "create category Public", "save_as": "public"},
            {"method": "POST", "path": "/guilds/%s/channels" % gid, "body": {"name": "chat", "type": 0, "parent_id": "{public}", "permission_overwrites": ov,
                                                                           "topic": "open chat: say hi. the assistants here treat messages as plain chat, not orders"}, "why": "create public #chat", "save_as": "chat"}]


def grant_op(channel_id, user_id):
    """Pure. -> the operation that lets one account see and write in one channel."""
    return {"method": "PUT", "path": "/channels/%s/permissions/%s" % (channel_id, user_id), "body": {"allow": str(MEMBER_ALLOW), "deny": "0", "type": 1}, "why": "let account %s into #bot-chat" % user_id}


def call(token, op, saved):
    body = json.loads(json.dumps(op.get("body"))) if op.get("body") is not None else None
    if isinstance(body, dict):
        for k, v in list(body.items()):
            if isinstance(v, str) and v.startswith("{") and v.endswith("}") and v[1:-1] in saved:
                body[k] = saved[v[1:-1]]
    data = json.dumps(body).encode("utf-8") if body is not None else None
    for _attempt in range(4):
        req = urllib.request.Request(API + op["path"], data=data, method=op["method"], headers={"Authorization": "Bot " + token, "Content-Type": "application/json", "User-Agent": "DiscordBot (local-admin, 1.0)"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(json.loads(e.read().decode("utf-8")).get("retry_after", 1)) + 0.2)
                continue
            raise
    raise RuntimeError("rate limited too long")


def get(token, path):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bot " + token, "User-Agent": "DiscordBot (local-admin, 1.0)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    apply_ = "--apply" in argv
    restore = "--restore" in argv
    grant = argv[argv.index("--grant") + 1] if "--grant" in argv else None
    rest = [a for i, a in enumerate(argv) if not a.startswith("--") and not (i > 0 and argv[i - 1] == "--grant")]
    from jarvis_keyring import get_password
    token = get_password("discord", "bot_token")
    if not token:
        print("no bot token in the Credential Manager")
        return 1
    import discord_server_audit as da
    gid = da.pick_guild(token, rest[0] if rest else None)
    if not gid:
        print("could not tell which server; pass the guild id")
        return 1
    channels = get(token, "/guilds/%s/channels" % gid)
    before_path = os.path.join(LOGS, "discord_server_before.json")
    if restore:
        saved = json.load(open(before_path, encoding="utf-8"))
        n = 0
        for c in saved["channels"]:
            cur = next((x for x in channels if x["id"] == c["id"]), None)
            if cur is None:
                continue
            body = {"parent_id": c.get("parent_id"), "permission_overwrites": c.get("permission_overwrites", [])}
            call(token, {"method": "PATCH", "path": "/channels/%s" % c["id"], "body": body}, {})
            n += 1
            time.sleep(0.4)
        print("restored %d channel(s) from %s" % (n, before_path))
        return 0
    if "--public" in argv:
        pops = public_ops(gid)
        print("public chat: %d step(s)%s" % (len(pops), "" if apply_ else " (dry run)"))
        for op in pops:
            print("  %-5s %s" % (op["method"], op["why"]))
        if apply_:
            saved = {}
            for op in pops:
                res = call(token, op, saved)
                if op.get("save_as") and isinstance(res, dict) and res.get("id"):
                    saved[op["save_as"]] = res["id"]
                time.sleep(0.5)
            print("done. created:", saved)
        return 0
    if grant:
        bc = next((c for c in channels if c.get("name") == "bot-chat"), None)
        if not bc:
            print("no #bot-chat yet; run --apply first")
            return 1
        op = grant_op(bc["id"], grant)
        print("grant:", op["why"])
        if apply_:
            call(token, op, {})
            print("done")
        else:
            print("dry run; add --apply")
        return 0
    ops = plan(channels, gid)
    print("server %s: %d step(s)%s" % (gid, len(ops), "" if apply_ else " (dry run, nothing changed)"))
    for op in ops:
        print("  %-5s %s" % (op["method"], op["why"]))
    if not apply_:
        return 0
    os.makedirs(LOGS, exist_ok=True)
    if not os.path.exists(before_path):                           # keep the FIRST before-state: a second run must not overwrite the way back
        json.dump({"guild": gid, "saved": time.strftime("%Y-%m-%d %H:%M:%S"), "channels": [{"id": c["id"], "name": c.get("name"), "parent_id": c.get("parent_id"), "permission_overwrites": c.get("permission_overwrites", [])} for c in channels]},
                  open(before_path, "w", encoding="utf-8"), indent=1)
    saved = {}
    for op in ops:
        res = call(token, op, saved)
        if op.get("save_as") and isinstance(res, dict) and res.get("id"):
            saved[op["save_as"]] = res["id"]
        time.sleep(0.5)
    print("done. created:", saved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
