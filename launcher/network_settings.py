"""1.0.0.14: two Linux network settings a headset PC may need, offered once on the page and kept.

**One network, two adapters.** A headset PC on the home network by cable and by Wi-Fi at once has two addresses on one
subnet. Linux answers an ARP request for either address on either adapter (`arp_ignore` 0, its default), so the
rendering PC learns whichever answer it hears and may send the video to the cable's address through the Wi-Fi card.
The first 1.0.0.14 session with Wi-Fi left connected did exactly that: the cable received 77 thousand packets in 28
minutes and the Wi-Fi card 7.5 million, and every five minutes or so Wi-Fi's background scan stalled the picture for
a few seconds. Until now VROSTBridge disconnected the other Wi-Fi adapter for each session instead (launcher/
wifi_others.py), which depends on NetworkManager leaving the cable's routes alone; on one PC it did not, and the host
became unreachable. `net.ipv4.conf.all.arp_ignore = 1` (answer only for an address on the adapter asked) and
`arp_announce = 2` (ask with that adapter's own address) are the standard settings for a PC with two adapters on one
network: the cable carries the stream and Wi-Fi can stay connected, untouched.

**The receive buffer.** The transport asks the kernel for the largest UDP receive buffer it will give (ALVR's
`Maximum`), and Linux gives twice `net.core.rmem_max`. A stock kernel's 208 KiB limit leaves the stream about a
thirtieth of a second of video at the bitrates in use, so a short pause of the receiving thread drops a run of
packets. Some distributions raise the limit already (Bazzite ships 4 MiB, which gives the stream 8 MiB); only a
limit that leaves the stream less than NEEDED_BYTES is reported. 1.0.0.14's first build took `rmem_default` for the
limit and offered this to PCs that did not need it.

Both settings go into one drop-in, /etc/sysctl.d/70-vrostbridge-network.conf, written and applied through pkexec
(one password prompt) and kept across reboots and system updates; the page offers it only when this PC needs one of
them. check() reads the current values without privileges and the report carries them. Windows needs neither: it
sizes socket buffers per socket and answers ARP only on the adapter that has the address.
"""
import ipaddress
import os
from pathlib import Path
import shutil
import sys
import tempfile

from launcher.client_identity import lan_addresses
from launcher.client_network import system_run

WANTED_BYTES = 16 * 1024 * 1024          # what the drop-in sets
NEEDED_BYTES = 4 * 1024 * 1024           # what the stream's socket must be able to get (a tenth of a second at 300 Mbit/s)
DROP_IN = Path('/etc/sysctl.d/70-vrostbridge-network.conf')
SETTINGS = {'net.core.rmem_max': WANTED_BYTES, 'net.core.rmem_default': WANTED_BYTES,
            'net.ipv4.conf.all.arp_ignore': 1, 'net.ipv4.conf.all.arp_announce': 2}
PROC = Path('/proc/sys/net')
# The Bazzite client's Python is 3.12; the frozen companion must not depend on the system's tools being on PATH.
TOOLS = {'pkexec': ('/usr/bin/pkexec',), 'install': ('/usr/bin/install',), 'sysctl': ('/usr/sbin/sysctl', '/sbin/sysctl', '/usr/bin/sysctl')}


def _tool(name):
    found = shutil.which(name)
    if found:
        return found
    return next((path for path in TOOLS.get(name, ()) if os.path.isfile(path)), None)


def _read_int(path):
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def read_values(proc=PROC, interface=''):
    """The kernel's values now: rmem_max, rmem_default, and arp_ignore / arp_announce as they apply to `interface`
    (the larger of 'all' and the interface's own, as the kernel takes them). A value that cannot be read is left out."""
    proc = Path(proc)
    result = {}
    for key in ('rmem_max', 'rmem_default'):
        value = _read_int(proc / 'core' / key)
        if value is not None:
            result[key] = value
    for key in ('arp_ignore', 'arp_announce'):
        values = [_read_int(proc / 'ipv4' / 'conf' / name / key) for name in ('all', interface) if name]
        values = [value for value in values if value is not None]
        if values:
            result[key] = max(values)
    return result


def shared_network(records, interface=''):
    """Physical adapters that share an IPv4 subnet with another physical adapter: with `interface`, the adapters on
    that adapter's subnet (itself included); without, every such group, flattened."""
    rows = []
    for record in records or ():
        try:
            network = ipaddress.IPv4Network(str(record['address']) + '/' + str(int(record['prefix'])), strict=False)
        except (KeyError, ValueError, TypeError):
            continue
        name = record.get('interface')
        if name and record.get('physical', True) and network.prefixlen < 32 and not network.network_address.is_loopback:
            rows.append((name, network))
    groups = {}
    for name, network in rows:
        groups.setdefault(network, [])
        if name not in groups[network]:
            groups[network].append(name)
    shared = [names for names in groups.values() if len(names) > 1]
    if interface:
        return next((names for names in shared if interface in names), [])
    return [name for names in shared for name in names]


def drop_in_text():
    return ('# VROSTBridge: network settings for a headset PC (see docs/TECHNICAL_REFERENCE.md, the network section).\n'
            '# Answer ARP only on the adapter that has the address (two adapters on one network), and let the stream\n'
            '# keep a tenth of a second of video. Written by the VROSTBridge client; remove it by hand if VROSTBridge goes.\n'
            + ''.join(f'{key} = {value}\n' for key, value in SETTINGS.items()))


class NetworkSettings:
    """Check (and on request apply) the two settings. Linux only; elsewhere it reports 'not_needed'."""

    def __init__(self, runner=system_run, log=print, proc=PROC, drop_in=DROP_IN, platform=None, addresses=lan_addresses):
        self.runner, self.log, self.proc, self.drop_in = runner, log, Path(proc), Path(drop_in)
        self.platform = sys.platform if platform is None else platform
        self.addresses = addresses
        self.interface = ''
        self.value = None

    def can_fix(self):
        return all(_tool(name) for name in ('pkexec', 'install', 'sysctl'))

    def separated(self, interface=''):
        """True when this PC answers for each address only on its own adapter (or needs nothing: Windows)."""
        if not self.platform.startswith('linux'):
            return True
        values = read_values(self.proc, interface)
        return values.get('arp_ignore', 0) >= 1 and values.get('arp_announce', 0) >= 2

    def check(self, interface=None):
        """`interface`: the stream adapter when known (the last one is remembered); otherwise any shared network counts."""
        if not self.platform.startswith('linux'):
            self.value = {'state': 'not_needed', 'message': '', 'needs': []}
            return self.value
        if interface is not None:
            self.interface = interface or ''
        values = read_values(self.proc, self.interface)
        persistent = self.drop_in.is_file()
        try:
            records = list(self.addresses() or ())
        except Exception:
            records = []
        shared = shared_network(records, self.interface) if self.interface else shared_network(records)
        needs, parts = [], []
        if shared and not self.separated(self.interface):
            needs.append('arp')
            parts.append('This PC is on the home network more than once (' + ', '.join(shared[:4]) + '). Linux answers for '
                         'every address on every adapter, so the host can end up sending the video over Wi-Fi while the '
                         'cable sits idle.')
        rmem_max = values.get('rmem_max')
        effective = 2 * rmem_max if rmem_max is not None else None
        if effective is not None and effective < NEEDED_BYTES:
            needs.append('buffer')
            parts.append('The stream can keep only ' + str(effective // 1024) + ' KiB of arriving video '
                         '(net.core.rmem_max), so a short pause of the receiving thread drops a run of packets.')
        if rmem_max is None and 'arp_ignore' not in values:
            state, message = 'unknown', 'The network settings could not be read.'
        elif needs:
            state = 'needed'
            message = (' '.join(parts) + ' Fix this PC\'s network settings once (one password prompt; it stays after '
                       'reboots)' + (', and Wi-Fi can stay connected during sessions.' if 'arp' in needs else '.'))
        else:
            state, message = 'ok', ''
        self.value = dict(state=state, message=message, needs=needs, shared=shared, rmem_max=rmem_max,
                          rmem_default=values.get('rmem_default'), stream_buffer_bytes=effective,
                          needed_bytes=NEEDED_BYTES, arp_ignore=values.get('arp_ignore'),
                          arp_announce=values.get('arp_announce'), persistent=persistent, can_fix=self.can_fix())
        return self.value

    def status(self):
        return dict(self.value) if self.value else self.check()

    def repair(self):
        """Explicit user action: write the sysctl drop-in and apply it, both through pkexec, then check again."""
        before = self.check()
        if before['state'] in ('not_needed', 'ok'):
            return before
        if not self.can_fix():
            return dict(before, message='Changing the network settings needs the system password prompt (pkexec), install '
                                        'and sysctl, which were not found on this PC. Put these lines in a file in '
                                        '/etc/sysctl.d yourself: ' + '; '.join(f'{k} = {v}' for k, v in SETTINGS.items()) + '.')
        pkexec, install, sysctl = (_tool(name) for name in ('pkexec', 'install', 'sysctl'))
        fd, temporary = tempfile.mkstemp(prefix='vrostbridge-sysctl-', suffix='.conf', text=True)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(drop_in_text())
            os.chmod(temporary, 0o644)
            # No shell: pkexec authenticates the system install tool for this one file, then sysctl for that file.
            steps = ([pkexec, install, '-o', 'root', '-g', 'root', '-m', '0644', temporary, str(self.drop_in)],
                     [pkexec, sysctl, '-p', str(self.drop_in)])
            for arguments in steps:
                try:
                    result = self.runner(arguments, 180)
                except Exception as error:  # the prompt dismissed, or pkexec itself failing
                    self.log('NETWORK settings not changed: ' + str(error)[:200])
                    return dict(self.check(), message='The password prompt did not complete (' + str(error)[:120] + ').')
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or '').strip()[-200:]
                    self.log('NETWORK settings not changed: ' + os.path.basename(arguments[1]) + ' exit '
                             + str(result.returncode) + (' ' + detail if detail else ''))
                    return dict(self.check(), message='The network settings were not changed: ' + (detail or 'the password '
                                                      'prompt was cancelled') + '.')
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        after = self.check()
        self.log('NETWORK settings applied and kept in ' + str(self.drop_in) + ': each adapter answers only for its own '
                 'address (arp_ignore ' + str(after.get('arp_ignore')) + ', arp_announce ' + str(after.get('arp_announce'))
                 + '), receive buffer limit ' + str(after.get('rmem_max')) + ' bytes.')
        return after
