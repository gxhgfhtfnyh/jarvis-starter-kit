# -*- coding: utf-8 -*-
"""Milestone 3 of the local specialist-swarm AGI experiment (op a_4e656055, Nov 13 2026):
can the swarm propose a change to its OWN config (role prompts), have it verified before it
takes effect, and have that change be genuinely reversible?

Safety boundary (deliberate, per the plan doc): edits land in a plain JSON config file, NEVER
in the actual .py source of swarm_milestone1.py/swarm_milestone2.py. This is "config
self-editing," not code or weight self-editing -- the next rungs up the milestone ladder,
not this one. Every edit is verified before it's applied, and every applied edit keeps the
prior value so it can be rolled back -- nothing here is silent or one-way.
"""
import json
import os
import time

import swarm_milestone1 as sm

CONFIG_PATH = os.path.join(sm.SWARM_MEMORY_DIR, "swarm_config.json")

DEFAULT_CONFIG = {
    "coder": sm.ROLE_PROMPTS["coder"],
    "researcher": sm.ROLE_PROMPTS["researcher"],
    "verifier": sm.ROLE_PROMPTS["verifier"],
}

CONFIG_EDITOR_PROMPT = (
    "You are improving one of this swarm's own role prompts based on an observed problem. "
    "You will be given the field name, its CURRENT prompt text, and the reason it needs to "
    "change. Propose a revised version of that SAME field that fixes the stated problem "
    "without removing its core constraints. Return ONLY the new prompt text, no explanation, "
    "no quotes, no markdown."
)

CONFIG_VERIFIER_PROMPT = (
    "You are checking a proposed change to a role's system prompt before it is allowed to "
    "take effect. You will see the field name, the OLD text, and the NEW proposed text. State "
    "PASS only if the new text keeps every core constraint the old text had (nothing unsafe "
    "added, nothing essential removed) and plausibly fixes the stated problem, or "
    "FAIL: <reason> otherwise. Your ENTIRE reply must be one short line under 25 words -- "
    "PASS, or FAIL followed by the single most important reason, nothing more."
)


def load_config(path=CONFIG_PATH):
    """Returns the current config dict, seeded from DEFAULT_CONFIG if no file exists yet."""
    if not os.path.exists(path):
        return dict(DEFAULT_CONFIG)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def save_config(config, path=CONFIG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def _raw_call(system_prompt, user_content, max_tokens=400, timeout=60):
    import urllib.request
    import urllib.error

    payload = {
        "model": "qwen",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "stream": True,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        sm.LOCAL_QWEN_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    chunks = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    d = json.loads(data)
                except (ValueError, TypeError):
                    continue
                choices = d.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    chunks.append(text)
    except urllib.error.URLError as e:
        raise RuntimeError("local-qwen call failed: %s" % e)
    return "".join(chunks).strip()


def propose_config_edit(field, current_value, reason):
    """Calls the config-editor role. Returns the proposed new text for that one field."""
    user_content = "FIELD: %s\n\nCURRENT PROMPT:\n%s\n\nPROBLEM TO FIX:\n%s" % (field, current_value, reason)
    return _raw_call(CONFIG_EDITOR_PROMPT, user_content)


def verify_config_edit(field, old_value, new_value):
    """Calls the config verifier role on one proposed field change. Returns the verdict text."""
    user_content = "FIELD: %s\n\nOLD:\n%s\n\nNEW:\n%s" % (field, old_value, new_value)
    return _raw_call(CONFIG_VERIFIER_PROMPT, user_content, max_tokens=300)


def is_edit_verified(verdict):
    """Pure. True only on an unambiguous PASS."""
    return verdict.strip().upper().startswith("PASS")


def apply_edit_with_backup(field, new_value, config_path=CONFIG_PATH, backup_dir=None, now=None):
    """Applies a verified edit, keeping the prior config as a timestamped backup so it can be
    rolled back. Returns (new_config, backup_path). Caller must have already verified."""
    backup_dir = backup_dir or os.path.join(os.path.dirname(config_path), "config_backups")
    os.makedirs(backup_dir, exist_ok=True)
    config = load_config(config_path)
    ts = now or time.time()
    backup_path = os.path.join(backup_dir, "config_%d.json" % int(ts))
    save_config(config, backup_path)
    config[field] = new_value
    save_config(config, config_path)
    return config, backup_path


def rollback(backup_path, config_path=CONFIG_PATH):
    """Restores config_path from a prior backup. The reversibility half of the round trip."""
    with open(backup_path, encoding="utf-8") as f:
        config = json.load(f)
    save_config(config, config_path)
    return config


def run_round_trip(field="coder", reason=None, config_path=CONFIG_PATH):
    """The real end-to-end proof: propose an edit to one field, verify it, apply it with a
    backup, confirm a FRESH load_config() call sees the new value, then roll back and confirm
    a fresh load sees the OLD value again. Returns a dict describing every step."""
    config = load_config(config_path)
    old_value = config[field]
    reason = reason or "the current prompt is too generic and could be more specific about its one job"
    proposed = propose_config_edit(field, old_value, reason)
    verdict = verify_config_edit(field, old_value, proposed)
    verified = is_edit_verified(verdict)
    result = {
        "field": field,
        "old_value": old_value,
        "proposed_value": proposed,
        "verdict": verdict,
        "verified": verified,
        "applied": False,
        "new_value_visible_after_reload": False,
        "rolled_back": False,
        "old_value_visible_after_rollback": False,
    }
    if not verified:
        return result
    new_config, backup_path = apply_edit_with_backup(field, proposed, config_path)
    result["applied"] = True
    reloaded = load_config(config_path)
    result["new_value_visible_after_reload"] = reloaded[field] == proposed
    restored = rollback(backup_path, config_path)
    result["rolled_back"] = restored[field] == old_value
    reloaded_again = load_config(config_path)
    result["old_value_visible_after_rollback"] = reloaded_again[field] == old_value
    return result


if __name__ == "__main__":
    result = run_round_trip()
    print(json.dumps(result, indent=2))
