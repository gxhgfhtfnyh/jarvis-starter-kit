# -*- coding: utf-8 -*-
"""Read-only look at a Discord server the bot is in: channels, categories, roles, who can see what, and what the bot itself may change (2026-10-31, the owner: "can u
clean up the server and restrict our channels"). It changes NOTHING: only GET requests. Use it to write a plan he approves before any change.
    python discord_server_audit.py [guild_id]       # default: the guild that holds the channel we last heard from (logs/discord_incoming.jsonl), else the only guild
The bot token comes from the Credential Manager through jarvis_keyring (service "discord", user "bot_token") and is never printed or written.
Output: a short tree on stdout and the full JSON in logs/discord_server_audit.json.
Pure core (unit tested): summarise(channels, roles, guild_id, bot_perms), perms_of(value)."""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.environ.get("JARVIS_LOGS") or os.path.join(os.path.dirname(HERE), "logs")
API = "https://discord.com/api/v10"
VIEW_CHANNEL = 1 << 10
ADMIN = 1 << 3
MANAGE_CHANNELS = 1 << 4
MANAGE_ROLES = 1 << 28
TYPES = {0: "text", 2: "voice", 4: "category", 5: "announcement", 13: "stage", 15: "forum"}


def perms_of(value):
    """-> dict of the few permission bits that matter here, from a permissions integer (Discord sends it as a string)."""
    v = int(value or 0)
    return {"administrator": bool(v & ADMIN), "manage_channels": bool(v & MANAGE_CHANNELS), "manage_roles": bool(v & MANAGE_ROLES), "view": bool(v & VIEW_CHANNEL)}


def summarise(channels, roles, guild_id, bot_perms=0):
    """Pure. -> {"tree": [lines], "public": [names the @everyone role can see], "hidden": [names it cannot], "bot": perms_of(bot_perms), "counts": {...}}.
    A channel is hidden from @everyone when its @everyone overwrite denies VIEW_CHANNEL (overwrite id == the guild id)."""
    cats = {c["id"]: c for c in channels if c.get("type") == 4}
    kids = {}
    for c in channels:
        if c.get("type") != 4:
            kids.setdefault(c.get("parent_id"), []).append(c)

    def hidden(c):
        for o in c.get("permission_overwrites", []):
            if str(o.get("id")) == str(guild_id) and int(o.get("deny") or 0) & VIEW_CHANNEL:
                return True
        return False

    tree, public, hid = [], [], []
    for cid in [None] + sorted(cats, key=lambda k: cats[k].get("position", 0)):
        title = "(no category)" if cid is None else "# %s%s" % (cats[cid].get("name", "?"), "  [hidden from everyone]" if hidden(cats[cid]) else "")
        group = sorted(kids.get(cid, []), key=lambda c: c.get("position", 0))
        if cid is None and not group:
            continue
        tree.append(title)
        for c in group:
            h = hidden(c)
            tree.append("   %s %s%s" % (TYPES.get(c.get("type"), str(c.get("type"))), c.get("name", "?"), "  [hidden from everyone]" if h else ""))
            (hid if h else public).append(c.get("name", "?"))
    return {"tree": tree, "public": public, "hidden": hid, "bot": perms_of(bot_perms),
            "counts": {"channels": sum(1 for c in channels if c.get("type") != 4), "categories": len(cats), "roles": len(roles)}}


def get(path, token):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bot " + token, "User-Agent": "DiscordBot (local-audit, 1.0)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_guild(token, wanted=None):
    if wanted:
        return str(wanted)
    try:                                              # the channel of the last server message tells which guild "our server" is
        last = None
        with open(os.path.join(LOGS, "discord_incoming.jsonl"), encoding="utf-8") as f:
            for line in f.readlines()[-400:]:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("source") == "guild" and row.get("channel_id"):
                    last = row["channel_id"]
        if last:
            return str(get("/channels/%s" % last, token)["guild_id"])
    except (OSError, KeyError, urllib.error.URLError):
        pass
    guilds = get("/users/@me/guilds", token)
    return str(guilds[0]["id"]) if len(guilds) == 1 else None


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    sys.path.insert(0, HERE)
    from jarvis_keyring import get_password
    token = get_password("discord", "bot_token")
    if not token:
        print("no bot token in the Credential Manager (service discord, user bot_token)")
        return 1
    try:
        gid = pick_guild(token, argv[0] if argv else None)
        if not gid:
            print("could not tell which server; pass the guild id")
            return 1
        guild = get("/guilds/%s" % gid, token)
        channels = get("/guilds/%s/channels" % gid, token)
        roles = get("/guilds/%s/roles" % gid, token)
        bot_id = get("/users/@me", token)["id"]                       # a bot reads its own member record by its user id (the @me form is for user tokens)
        me = get("/guilds/%s/members/%s" % (gid, bot_id), token)
    except urllib.error.HTTPError as e:
        print("discord said %s %s (the bot may lack access)" % (e.code, e.reason))
        return 1
    except urllib.error.URLError as e:
        print("could not reach discord: %s" % e.reason)
        return 1
    by_id = {str(r["id"]): r for r in roles}
    bot_perms = 0
    for rid in list(me.get("roles", [])) + [gid]:
        bot_perms |= int(by_id.get(str(rid), {}).get("permissions", 0) or 0)
    s = summarise(channels, roles, gid, bot_perms)
    os.makedirs(LOGS, exist_ok=True)
    with open(os.path.join(LOGS, "discord_server_audit.json"), "w", encoding="utf-8") as f:
        json.dump({"guild": {"id": gid, "name": guild.get("name")}, "summary": s, "roles": [{"name": r.get("name"), "managed": r.get("managed"), "perms": perms_of(r.get("permissions"))} for r in roles]}, f, indent=1)
    print("server: %s | %d channels, %d categories, %d roles" % (guild.get("name"), s["counts"]["channels"], s["counts"]["categories"], s["counts"]["roles"]))
    print("the bot can: manage channels=%s, manage roles=%s, admin=%s" % (s["bot"]["manage_channels"], s["bot"]["manage_roles"], s["bot"]["administrator"]))
    print("\n".join(s["tree"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
