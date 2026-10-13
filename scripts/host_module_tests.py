"""Run the unit tests of the host driver's VROSTBridge Rust modules on Linux (1.0.0.4; 1.0.0.5; 1.0.0.6).

The driver itself only builds for Windows (scripts/Build-Host-Cross.py), but its gaze
foveation (`alvr/server_openvr/src/vrostbridge_foveation.rs`, patches 0010, 0012, 0017,
0019), its count of frames without a head pose (`alvr/server_openvr/src/vrostbridge_pose.rs`,
patch 0018) and per-session stream options (`alvr/server_core/src/vrostbridge_stream.rs`,
patches 0009, 0012, 0014, 0017, 0019) are plain Rust. This script builds a small crate around
those files in a temporary folder, with stand-ins for the few ALVR types and functions they use
(glam's Quat/Vec3, the view and pose structs, the pacing limits), and runs their tests.

    python3 scripts/host_module_tests.py --alvr upstream/ALVR [--vendor DIR]

--alvr: an ALVR checkout with the VROSTBridge patches applied (scripts/fetch_upstream.py).
--vendor: a `cargo vendor` folder holding glam, parking_lot and serde_json, for an offline
run; without it Cargo downloads them from crates.io.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

MODULES = {'vrostbridge_foveation': 'alvr/server_openvr/src/vrostbridge_foveation.rs',
           'vrostbridge_pose': 'alvr/server_openvr/src/vrostbridge_pose.rs',
           'vrostbridge_pair': 'alvr/server_openvr/src/vrostbridge_pair.rs',  # 1.0.0.6, patch 0020
           'vrostbridge_stream': 'alvr/server_core/src/vrostbridge_stream.rs'}
COMMON = '''pub use glam;
pub use parking_lot;
use glam::{Quat, Vec3};
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Fov { pub left: f32, pub right: f32, pub up: f32, pub down: f32 }
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Pose { pub orientation: Quat, pub position: Vec3 }
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct ViewParams { pub pose: Pose, pub fov: Fov }
#[macro_export]
macro_rules! info { ($($t:tt)*) => { { let _ = format!($($t)*); } } }
'''
SOCKETS = '''pub const MIN_PACING_MBPS: u32 = 50;
pub const MAX_PACING_MBPS: u32 = 40_000;
pub fn set_stream_pacing(_stream: u16, mbps: u32) -> bool { mbps > 0 }
pub const MIN_FEC_GROUP: u32 = 1;
pub const MAX_FEC_GROUP: u32 = 64;
pub fn set_stream_fec(_stream: u16, group: u32) -> bool { group > 0 }
'''


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--alvr', type=Path, required=True)
    parser.add_argument('--vendor', type=Path)
    args = parser.parse_args()
    sources = {name: (args.alvr / path).resolve() for name, path in MODULES.items()}
    for name, path in sources.items():
        if not path.is_file():
            parser.error(path.as_posix() + ' is missing: apply the VROSTBridge patches first')
    with tempfile.TemporaryDirectory(prefix='vrostbridge-host-modules-') as directory:
        root = Path(directory)
        write(root / 'Cargo.toml', '[package]\nname = "vrostbridge_host_modules"\nversion = "0.1.0"\nedition = "2024"\n'
              '[dependencies]\nalvr_common = { path = "alvr_common" }\nalvr_sockets = { path = "alvr_sockets" }\n'
              'serde_json = "1"\n[workspace]\n')
        write(root / 'alvr_common/Cargo.toml', '[package]\nname = "alvr_common"\nversion = "0.1.0"\nedition = "2024"\n'
              '[dependencies]\nglam = "0.32"\nparking_lot = "0.12"\n')
        write(root / 'alvr_common/src/lib.rs', COMMON)
        write(root / 'alvr_sockets/Cargo.toml', '[package]\nname = "alvr_sockets"\nversion = "0.1.0"\nedition = "2024"\n')
        write(root / 'alvr_sockets/src/lib.rs', SOCKETS)
        write(root / 'src/lib.rs', ''.join('#[path = "%s"]\npub mod %s;\n' % (path.as_posix(), name)
                                           for name, path in sources.items()))
        command = ['cargo', 'test', '--quiet']
        if args.vendor:
            write(root / '.cargo/config.toml', '[source.crates-io]\nreplace-with = "vendored"\n[source.vendored]\n'
                  'directory = "%s"\n' % args.vendor.resolve().as_posix())
            command.append('--offline')
        return subprocess.run(command, cwd=root).returncode


if __name__ == '__main__':
    sys.exit(main())
