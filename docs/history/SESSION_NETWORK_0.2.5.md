# Session discovery and client firewall — 0.2.5

The host companion waits without SteamVR running. The client discovers it on the
connected private IPv4 home network, pairs once, and owns session Start and Stop.

After upgrading from 0.2.4, choose **Allow home-network connection** on the client
if that button is shown. The new discovery allowance needs the normal Bazzite
administrator authentication once. Existing approved rules remain intact. The
application never changes firewall rules merely because Start was pressed.
A saved or manually entered host IPv4 address uses direct TCP and does not need
broadcast discovery, but the streaming and diagnostic rules still apply.

| Endpoint | Purpose | Exposure |
| --- | --- | --- |
| Host TCP 9946 | Authenticated pairing, session Start/Stop, status and heartbeat | Windows Private network and LocalSubnet; companion also checks the physical private subnet |
| Host UDP 9946 | Read-only discovery replies | Same private LAN restrictions; no control commands |
| Client UDP 9946 | Receives discovery replies | Bound to each actual physical interface only during discovery; source-scoped firewalld allowance |
| Client TCP 9943 and UDP 9944 | Existing ALVR control and streaming | Existing source-scoped allowances |
| Client UDP 5353 | Existing ALVR discovery | Existing mDNS service, or source-scoped allowance if absent |
| Client TCP 9945 | Connected-host diagnostic collection | Source-scoped allowance plus native control-socket peer authorization |

The client does not need an inbound TCP 9946 rule: its broker control requests
are outbound unicast HTTP requests. Browser controls remain authenticated and
local to each PC. UDP advertisements have no authority to start a session.

## Why discovery uses a fixed receiving port

A UDP broadcast request followed by a host's unicast reply does not have the
ordinary reversed source/destination IP tuple. Therefore the application must
not assume that connection tracking will allow the reply to an arbitrary
client ephemeral port. The Linux kernel implements explicit broadcast tracking
expectations for protocols using a registered helper; VROSTBridge does not
install or depend on such a helper.

The client now binds UDP 9946 on each connected physical private IPv4 interface
and the existing firewall action explicitly allows that destination port from
the observed private subnet. Discovery still checks source subnet, protocol and
request nonce. It does not enable a TCP server on the client or create a global
port exception. Uninstall removes only these rules when the application's
ownership record proves it created them.

This is a packet-flow inference supported by the kernel helper implementation,
not a claim that every Bazzite firewall configuration was physically tested:

- [Linux broadcast connection-tracking helper](https://github.com/torvalds/linux/blob/master/net/netfilter/nf_conntrack_broadcast.c)
- [Linux NetBIOS helper explaining reply expectations](https://github.com/torvalds/linux/blob/master/net/netfilter/nf_conntrack_netbios_ns.c)
- [firewalld source-scoped rich rules](https://firewalld.org/documentation/man-pages/firewalld.richlanguage.html)

Focused tests check the fixed discovery source port, physical-interface
selection, addition of only the missing UDP 9946 allowance during upgrade, and
ownership-limited removal during uninstall. Actual broadcast discovery with the
Bazzite firewall enabled remains part of the next two-PC acceptance test.
