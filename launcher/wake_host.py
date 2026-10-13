"""Wake-on-LAN for a host PC in another room.

The rendering PC usually sits in another room. When it has gone to sleep, or
has been shut down, the player would otherwise have to walk over and press a
key. Wake-on-LAN avoids that: the client sends a "magic packet" (six 0xFF
bytes followed by the host adapter's MAC address sixteen times) and the
sleeping network adapter powers the PC on. The host must allow it; see
host/power.py wake_readiness() for the Windows side.

The MAC address is learnt, not typed: right after any successful contact with
the host companion, the kernel's neighbour (ARP) table holds the MAC address
of the adapter that answered, and `ip -j neigh show <ip>` reads it without
privileges or any extra protocol. A host on another subnet has no neighbour
entry of its own (the router answers for it), so nothing wrong is learnt for
a routed host.

A sleeping PC does not answer ARP, so magic packets are normally broadcast.
The client sends to three destinations: the directed broadcast of the host's
subnet (leaves through the client adapter on that subnet), the limited
broadcast 255.255.255.255 (leaves through the default-route adapter), and
the host's own unicast address. Some routers do not pass broadcasts from
Wi-Fi to their wired ports, but the client's neighbour entry for the host
stays usable for a while after the host sleeps, and switches flood frames
for a MAC address they have forgotten. UDP ports 9 (discard) and 7 (echo) are
the usual choices; the adapter ignores the port.

Nothing here needs root or changes system settings, and HostWaker reports
ordinary failures instead of raising them.

Windows: the neighbour table comes from GetIpNetTable2 (launcher/windows_network.py),
its states named as Linux names them; `arp -a <ip>` is the fallback.
"""
import ipaddress
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

from launcher.client_network import system_run
from launcher import windows_network

WAKE_PORTS = (9, 7)
LIMITED_BROADCAST = '255.255.255.255'
# Neighbour states whose link-layer address was confirmed at some point.
# FAILED/INCOMPLETE entries have no usable address; NOARP entries are not
# real neighbours.
NEIGHBOUR_STATES = ('REACHABLE', 'DELAY', 'PROBE', 'STALE', 'PERMANENT')
POLL_SECONDS = 2.0
RESEND_SECONDS = 10.0

MESSAGE_WAKING = 'Waking the host PC…'
MESSAGE_AWAKE = 'The host PC is awake.'
MESSAGE_NO_MAC = ("The host PC's network adapter address is not known yet, so it cannot be woken from here. "
                  'Connect to the host PC once while it is switched on.')
MESSAGE_SEND_FAILED = ('The wake-up message could not be sent. '
                       'Check that this PC is connected to the home network.')
MESSAGE_SENT = 'Wake-up message sent to the host PC.'
MESSAGE_NO_RESPONSE = ('The host PC did not respond after waking. Check that Wake-on-LAN is enabled in its BIOS '
                       'and network adapter settings, or that it is not at the Windows sign-in screen after a '
                       'full shutdown.')
MESSAGE_CANCELLED = 'Stopped waiting for the host PC.'

_MAC_SEPARATED = re.compile(r'[0-9a-f]{2}([:-])[0-9a-f]{2}(?:\1[0-9a-f]{2}){4}')
_MAC_PLAIN = re.compile(r'[0-9a-f]{12}')


def normalise_mac(text):
    """Return 'aa:bb:cc:dd:ee:ff' for a usable unicast MAC address, else None.

    Accepts aa:bb:cc:dd:ee:ff, aa-bb-cc-dd-ee-ff (Windows) and aabbccddeeff in
    either case. Rejects broadcast and multicast addresses (group bit set in
    the first octet) and the all-zero address.
    """
    if not isinstance(text, str) or len(text) > 64:
        return None
    value = text.strip().lower()
    if _MAC_SEPARATED.fullmatch(value):
        digits = value.replace(value[2], '')
    elif _MAC_PLAIN.fullmatch(value):
        digits = value
    else:
        return None
    raw = bytes.fromhex(digits)
    if raw[0] & 1 or raw == bytes(6):
        return None
    return ':'.join(digits[index:index + 2] for index in range(0, 12, 2))


def magic_packet(mac):
    """Six 0xFF bytes followed by the MAC address sixteen times (102 bytes)."""
    value = normalise_mac(mac)
    if value is None:
        raise ValueError('Not a usable network adapter (MAC) address.')
    return b'\xff' * 6 + bytes.fromhex(value.replace(':', '')) * 16


def _unicast_ipv4(value):
    if isinstance(value, ipaddress.IPv4Address):
        address = value
    elif isinstance(value, str) and len(value) <= 15:
        try:
            address = ipaddress.IPv4Address(value)
        except ValueError:
            return None
    else:
        return None
    if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_reserved:
        return None
    return address


def _tool(name, candidates):
    for path in candidates:
        if Path(path).is_file():
            return path
    return shutil.which(name)


def learn_mac(host_ip, runner=system_run, ip_tool=None):
    """Read the host's MAC address from the neighbour table, or None.

    Call this right after a successful contact with the host companion, while
    the neighbour entry is fresh. Only entries whose address was confirmed
    (REACHABLE, DELAY, PROBE, STALE or PERMANENT) are trusted.
    """
    address = _unicast_ipv4(host_ip)
    if address is None:
        return None
    if sys.platform == 'win32':
        return _best_neighbour(_windows_neighbours(address, runner), address)
    tool = ip_tool or _tool('ip', ('/usr/sbin/ip', '/usr/bin/ip', '/sbin/ip', '/bin/ip'))
    if not tool:
        return None
    try:
        result = runner([tool, '-j', 'neigh', 'show', str(address)], 2)
        entries = json.loads(result.stdout or '[]') if result.returncode == 0 else []
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return None
    return _best_neighbour(entries, address)


_ARP_LINE = re.compile(r'^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5})\s+(\S+)')


def _windows_neighbours(address, runner):
    """`ip -j neigh` style entries for address: GetIpNetTable2, or `arp -a <ip>` if that API is unavailable.
    arp prints only the entry type (dynamic/static, translated on some Windows languages): a static entry
    counts as PERMANENT and any other as STALE (confirmed at some point)."""
    try:
        return [{'dst': row.get('address'), 'lladdr': row.get('mac'), 'state': [row.get('state') or 'FAILED']}
                for row in windows_network.neighbours(str(address))]
    except windows_network.WindowsNetworkError:
        pass
    try:
        result = runner([windows_network.system32('ARP.EXE'), '-a', str(address)], 3)
        text = result.stdout if result.returncode == 0 else ''
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return []
    entries = []
    for line in (text or '').splitlines()[:64]:
        match = _ARP_LINE.match(line)
        if match:
            entries.append({'dst': match.group(1), 'lladdr': match.group(2),
                            'state': ['PERMANENT' if match.group(3).lower().startswith(('static', 'statisch', 'statique'))
                                      else 'STALE']})
    return entries


def _best_neighbour(entries, address):
    """The best confirmed MAC address among `ip -j neigh` style entries for address, or None."""
    if not isinstance(entries, list):
        return None
    candidates = []
    for entry in entries[:32]:
        if not isinstance(entry, dict) or entry.get('dst', str(address)) != str(address):
            continue
        states = entry.get('state')
        states = [states] if isinstance(states, str) else states
        if not isinstance(states, list):
            continue
        names = {state.upper() for state in states if isinstance(state, str)}
        if names & {'FAILED', 'INCOMPLETE', 'NOARP'}:
            continue
        ranks = [NEIGHBOUR_STATES.index(name) for name in names if name in NEIGHBOUR_STATES]
        mac = normalise_mac(entry.get('lladdr'))
        if ranks and mac:
            candidates.append((min(ranks), mac))
    return min(candidates)[1] if candidates else None


def local_interfaces(records):
    """IPv4Interface values from launcher.client_identity.lan_addresses() records."""
    result = []
    for record in records or ():
        if not isinstance(record, dict) or record.get('connected') is False:
            continue
        prefix, address = record.get('prefix'), record.get('address')
        if type(prefix) is not int or not 1 <= prefix <= 32 or not isinstance(address, str):
            continue
        try:
            value = ipaddress.IPv4Interface(address + '/' + str(prefix))
        except ValueError:
            continue
        if value not in result:
            result.append(value)
    return result


def wake_targets(host_ip, local_networks=()):
    """Destination addresses for magic packets, most specific first.

    With the last known host address: the directed broadcast of each local
    network that contains it, 255.255.255.255 and the host's unicast address.
    Without a usable host address: the directed broadcasts of the client's
    private networks and 255.255.255.255.
    """
    host = _unicast_ipv4(host_ip)
    targets = []
    for item in local_networks or ():
        try:
            interface = item if isinstance(item, ipaddress.IPv4Interface) else ipaddress.IPv4Interface(item)
        except (ValueError, TypeError):
            continue
        network = interface.network
        # /31 and /32 networks have no broadcast address of their own.
        if network.prefixlen > 30 or interface.ip.is_loopback:
            continue
        if host is not None:
            if host not in network or host in (network.network_address, network.broadcast_address):
                continue
        elif not interface.ip.is_private:
            continue
        broadcast = str(network.broadcast_address)
        if broadcast not in targets:
            targets.append(broadcast)
    targets.append(LIMITED_BROADCAST)
    if host is not None and str(host) not in targets:
        targets.append(str(host))
    return targets


def send_wake(mac, targets, sock_factory=socket.socket, repeats=3, interval=0.2, ports=WAKE_PORTS,
              sleep=time.sleep):
    """Send the magic packet to every target and port, `repeats` times.

    Returns the number of datagrams sent. A failing target or port is skipped,
    never raised; an unusable MAC address raises ValueError.
    """
    packet = magic_packet(mac)
    destinations = []
    for target in targets or ():
        try:
            address = ipaddress.IPv4Address(target)
        except (ValueError, TypeError):
            continue
        if address.is_loopback or address.is_unspecified or address.is_multicast:
            continue
        for port in ports:
            if type(port) is int and 0 < port < 65536 and (str(address), port) not in destinations:
                destinations.append((str(address), port))
    if not destinations:
        return 0
    repeats = max(1, min(int(repeats), 10))
    try:
        sock = sock_factory(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return 0
    sent = 0
    try:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass  # Unicast can still work.
        for attempt in range(repeats):
            if attempt:
                sleep(interval)
            for destination in destinations:
                try:
                    sock.sendto(packet, destination)
                    sent += 1
                except OSError:
                    continue
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return sent


class HostWaker:
    """Wake the host PC and wait until its companion answers.

    discover() must return a truthy value once the host companion answers (for
    example a session-link discovery reply). wake_and_wait() blocks, so call it
    from a worker thread; status() is safe to read from any thread.
    """

    def __init__(self, sender=send_wake, discover=None, clock=time.monotonic, sleep=time.sleep, log=print,
                 poll_seconds=POLL_SECONDS, resend_seconds=RESEND_SECONDS):
        self.sender, self.discover = sender, discover
        self.clock, self.sleep, self.log = clock, sleep, log
        self.poll_seconds, self.resend_seconds = poll_seconds, resend_seconds
        self.lock = threading.Lock()
        self.value = {'state': 'idle', 'message': '', 'seconds': 0.0}

    def status(self):
        with self.lock:
            return dict(self.value)

    def _set(self, state, message, seconds=0.0):
        with self.lock:
            changed = self.value.get('state') != state
            self.value = {'state': state, 'message': message, 'seconds': round(float(seconds), 1)}
        if message and changed:
            self.log('WAKE ' + state + ': ' + message)

    def _finish(self, state, woken, seconds, message, **extra):
        self._set(state, message, seconds)
        return {'woken': woken, 'seconds': round(float(seconds), 1), 'message': message, 'state': state, **extra}

    def _send(self, mac, targets):
        try:
            return int(self.sender(mac, targets) or 0)
        except (OSError, ValueError, TypeError):
            return 0

    def _answered(self):
        try:
            return bool(self.discover())
        except Exception as error:  # A failing probe means "not yet", never a crash.
            self.log('WAKE probe: ' + str(error)[:200])
            return False

    def _waited_out(self, until, cancel):
        """Sleep until `until` in short slices; True when cancelled."""
        while True:
            if cancel is not None and cancel.is_set():
                return True
            remaining = until - self.clock()
            if remaining <= 0:
                return False
            self.sleep(min(0.25, remaining))

    def wake_and_wait(self, mac, targets, timeout=90, cancel=None, progress=None):
        """Send magic packets, then poll discover() every 2 s and re-send every 10 s.

        Returns {'woken', 'seconds', 'message', 'state', 'packets'}; never raises
        for network failures. cancel is an Event-like object with is_set();
        progress(seconds_elapsed) is called before each check.
        """
        address = normalise_mac(mac)
        if address is None:
            return self._finish('unknown', False, 0.0, MESSAGE_NO_MAC, packets=0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 900:
            timeout = 90
        start = self.clock()
        self._set('waking', MESSAGE_WAKING)
        packets = self._send(address, targets)
        if not packets:
            return self._finish('failed', False, 0.0, MESSAGE_SEND_FAILED, packets=0)
        if self.discover is None:
            return self._finish('sent', False, 0.0, MESSAGE_SENT, packets=packets)
        last_send = start
        while True:
            if cancel is not None and cancel.is_set():
                return self._finish('cancelled', False, self.clock() - start, MESSAGE_CANCELLED,
                                    packets=packets, cancelled=True)
            checked = self.clock()
            elapsed = checked - start
            self._set('waking', MESSAGE_WAKING + ' (' + str(int(elapsed)) + ' s)', elapsed)
            if progress is not None:
                try:
                    progress(elapsed)
                except Exception as error:  # UI callbacks must not end the wait.
                    self.log('WAKE progress: ' + str(error)[:200])
            if self._answered():
                return self._finish('awake', True, self.clock() - start, MESSAGE_AWAKE, packets=packets)
            now = self.clock()
            if now - start >= timeout:
                return self._finish('timeout', False, now - start, MESSAGE_NO_RESPONSE, packets=packets)
            if now - last_send >= self.resend_seconds:
                packets += self._send(address, targets)
                last_send = now
            if self._waited_out(min(checked + self.poll_seconds, start + timeout), cancel):
                return self._finish('cancelled', False, self.clock() - start, MESSAGE_CANCELLED,
                                    packets=packets, cancelled=True)
