"""Restart host SteamVR when the stream stays down and ALVR cannot recover it by itself (RC4.1).

ALVR reconnects on its own after most interruptions: it retries the client's known
address every second (the companion adds that address for the session) as well as its
multicast discovery. A few states never recover by themselves: a teardown stuck in
Disconnecting, or a host that keeps "streaming" to a client that receives nothing.
Restarting the host runtime is the remedy, but it also closes the VR game on the host,
so it is used only when the headset client confirms, through the companion link, that
its video has been gone for a long time:

* ALVR shows the session's client as Disconnecting for 8 s (its teardown is stuck);
* the client has had no video for 20 s while ALVR still shows it Connected or Streaming;
* the client has had no video for 45 s in any other state.

At most two restarts per session, at least three minutes apart. Never without a current
report from the client (a lost companion link ends the session through its lease
instead), and never before the stream was up once (a slow start is not a lost stream:
the client reports no downtime until its first frame). Reported downtime never counts
from before this session started (RC4.2 clients could report the previous session's).
"""
import time

ACTIVE_STATES = ('Connected', 'Streaming')


class StreamRecovery:
    DISCONNECTING_SECONDS = 8
    STALLED_SECONDS = 20
    DOWN_SECONDS = 45
    REPORT_MAX_AGE = 10
    MIN_INTERVAL = 180
    MAX_RESTARTS = 2

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.reset()

    def reset(self):
        """A new session: the restart allowance starts again."""
        self.started = self.clock()
        self.restarts = 0
        self.last_restart = None
        self.disconnecting_since = None
        self.last_reason = ''

    def status(self):
        return {'restarts': self.restarts, 'limit': self.MAX_RESTARTS, 'last_reason': self.last_reason}

    def check(self, lease, rows):
        """The reason to restart host SteamVR now, or ''.

        lease: SessionHost.lease_details() (None without a session); rows: ALVR client rows
        (hostname, connection_state) from the running host.
        """
        now = self.clock()
        lease = lease or {}
        hostname = lease.get('client_hostname') or ''
        row = next((item for item in rows or () if item.get('hostname') == hostname), None) if hostname else None
        state = (row or {}).get('connection_state')
        if state == 'Disconnecting':
            if self.disconnecting_since is None:
                self.disconnecting_since = now
        else:
            self.disconnecting_since = None
        if not lease.get('started') or self.restarts >= self.MAX_RESTARTS:
            return ''
        if self.last_restart is not None and now - self.last_restart < self.MIN_INTERVAL:
            return ''
        stream = lease.get('stream')
        if not isinstance(stream, dict) or stream.get('age_s', self.REPORT_MAX_AGE + 1) > self.REPORT_MAX_AGE:
            return ''
        down = 0.0 if stream.get('up') else float(stream.get('down_s') or 0)
        down = min(down, max(0.0, now - self.started))
        if down <= 0:
            return ''
        reason = ''
        if self.disconnecting_since is not None and now - self.disconnecting_since >= self.DISCONNECTING_SECONDS:
            reason = ('ALVR kept the headset client in Disconnecting for ' + str(round(now - self.disconnecting_since)) +
                      ' s (no video for ' + str(round(down)) + ' s)')
        elif down >= self.STALLED_SECONDS and state in ACTIVE_STATES:
            reason = 'the headset had no video for ' + str(round(down)) + ' s while the host still showed it ' + state
        elif down >= self.DOWN_SECONDS:
            reason = ('the headset had no video for ' + str(round(down)) + ' s and ALVR did not reconnect (' +
                      (state or 'client not listed') + ')')
        if reason:
            self.restarts += 1
            self.last_restart = now
            self.disconnecting_since = None
            self.last_reason = reason
        return reason


__all__ = ['StreamRecovery']
