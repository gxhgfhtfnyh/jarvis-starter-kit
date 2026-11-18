# -*- coding: utf-8 -*-
"""Real bug fix for discord.py 2.7.1 + davey 0.2.0's DAVE/E2EE voice handshake: a bot that
joins a voice channel already containing a human (the owner, 2026-11-05 live VC test) gets its
dave_session permanently stuck at ready=False/can_encrypt=False, so every audio frame it sends
silently falls back to non-upgraded (non-E2E) transport encryption while Discord's real client
-- which saw the same epoch-1 MLS commit -- expects the upgraded kind, and just drops every
packet with zero error anywhere. Confirmed via discord_daemon.py's check_dave outbox command:
status=active ready=False can_encrypt=False epoch=1 user_ids=[bot, the owner] -- stayed stuck
for 30+ seconds, not a timing race.

2026-11-05, later same night: a dispatched research agent found the REAL upstream fix --
Rapptz/discord.py PR #10548 (opened 2026-11-02 by Snazzah, davey's own author; open, unmerged
as of tonight). Its diff is the actual authority here, not my own earlier guess (an earlier
version of this module called `_execute_transition(0)` directly for transition_id==0 -- that
ran clean with no exceptions but never flipped `ready`, because it wasn't what the real fix
does). The real fix:

1. gateway.py's MLS_ANNOUNCE_COMMIT_TRANSITION (opcode 29) and MLS_WELCOME (opcode 30) handlers
   are both missing a call to `dave_session.prepare_transition(transition_id, protocol_version)`
   right after `process_commit()`/`process_welcome()` -- unconditionally, for EVERY transition_id
   including 0. davey's native code auto-executes transition_id 0 internally as part of
   `prepare_transition` itself; it does NOT need (and the real fix does not add) a separate
   manual `_execute_transition(0)` call the way my first attempt assumed.
2. voice_state.py's `_execute_transition()` (driven by the DAVE_EXECUTE_TRANSITION opcode 22,
   for non-zero transition ids only -- PR #10548's comment: "ID 0 already gets executed by
   davey") never calls the native `davey.DaveSession.execute_transition(transition_id)` method at
   all in upstream 2.7.1; the fix adds that call, guarded to non-zero ids.

Fix applied here: both gaps, matching PR #10548 as closely as possible without depending on an
unmerged, unreleased version. Narrow, targeted, logged. Remove this module once discord.py ships
#10548 (or whatever release absorbs it) -- it will have become a no-op duplicate at that point,
not a maintenance burden, since it only intercepts the exact opcodes/methods named above.
"""
import struct


def apply(log_fn=None):
    """log_fn: optional callable(str) for a one-line confirmation in the caller's own log.
    Same "importable without the real discord.py" contract as discord_voice.py and
    discord_daemon.py (test_discord_daemon.py stubs `discord` in sys.modules before importing
    the daemon, which doesn't have a `.gateway`/`.voice_state` submodule) -- no-op rather than
    raise when those aren't real discord.py submodules."""
    def _log(msg):
        if log_fn:
            log_fn(f"DAVE TRANSITION FIX: {msg}")

    try:
        import discord.gateway as gw
        import discord.voice_state as vs
    except (ImportError, ModuleNotFoundError):
        _log("skipped (discord.py not installed or stubbed)")
        return

    _orig_execute_transition = vs.VoiceConnectionState._execute_transition

    async def _patched_execute_transition(self, transition_id):
        await _orig_execute_transition(self, transition_id)
        # PR #10548: "ID 0 already gets executed by davey" via prepare_transition (see the
        # patched received_binary_message below) -- only non-zero ids need this manual call.
        if transition_id != 0 and self.dave_session is not None:
            try:
                self.dave_session.execute_transition(transition_id)
                _log(f"native dave_session.execute_transition({transition_id}) called, ready={self.dave_session.ready}")
            except Exception as e:
                _log(f"native execute_transition({transition_id}) failed: {e!r}")

    vs.VoiceConnectionState._execute_transition = _patched_execute_transition

    _orig_received_binary_message = gw.DiscordVoiceWebSocket.received_binary_message

    async def _patched_received_binary_message(self, msg: bytes) -> None:
        # Re-implements discord.py's own dispatch for MLS_ANNOUNCE_COMMIT_TRANSITION and
        # MLS_WELCOME only (both get the same missing prepare_transition() call per PR #10548);
        # every other opcode is left to the original, unpatched method.
        op = msg[2]
        state = self._connection
        is_commit = op == gw.DiscordVoiceWebSocket.MLS_ANNOUNCE_COMMIT_TRANSITION
        is_welcome = op == gw.DiscordVoiceWebSocket.MLS_WELCOME
        if (is_commit or is_welcome) and state.dave_session is not None:
            self.seq_ack = struct.unpack_from(">H", msg, 0)[0]
            transition_id = struct.unpack_from(">H", msg, 3)[0]
            try:
                if is_commit:
                    state.dave_session.process_commit(msg[5:])
                else:
                    state.dave_session.process_welcome(msg[5:])
                state.dave_session.prepare_transition(transition_id, state.dave_protocol_version)
                if transition_id != 0:
                    state.dave_pending_transitions[transition_id] = state.dave_protocol_version
                    await self.send_transition_ready(transition_id)
                _log(
                    "%s processed for transition id %d (prepare_transition called), ready=%s"
                    % ("commit" if is_commit else "welcome", transition_id, state.dave_session.ready)
                )
            except Exception as e:
                _log(f"transition id {transition_id} handling failed, falling back to invalid-commit recovery: {e!r}")
                await state._recover_from_invalid_commit(transition_id)
            return
        await _orig_received_binary_message(self, msg)

    gw.DiscordVoiceWebSocket.received_binary_message = _patched_received_binary_message
    _log("applied (patched VoiceConnectionState._execute_transition + DiscordVoiceWebSocket.received_binary_message)")
