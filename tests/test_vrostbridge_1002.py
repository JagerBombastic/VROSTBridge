"""1.0.0.2: Baballonia's calibration no longer crashes as a SteamVR overlay on Linux (both programs 1.0.0.2 and on).

SteamVR takes an overlay's Vulkan picture through extension commands on the program's own device (external memory and
semaphore file descriptors, timeline semaphores). Baballonia's Godot calibration does not enable the semaphore ones,
Mesa returns no command for them, and SteamVR called the missing command as soon as the calibration showed its first
picture: in the first 1.0.0.1 test on Bazzite the window flashed and closed, in a session and with SteamVR alone, and
Baballonia kept waiting for it. The calibration launcher now gives the program VROSTBridge's Vulkan layer
(client/platform/linux/openvr_texture_layer.c), which enables them where the driver offers them. The host changes only
its version.
"""
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))

from launcher import baballonia  # noqa: E402
from launcher.baballonia import BaballoniaManager  # noqa: E402
from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402

LAYER_SOURCE = ROOT / 'client/platform/linux/openvr_texture_layer.c'
MANIFEST = ROOT / 'client/platform/linux/VkLayer_vrostbridge_openvr.json'
BUILT = ROOT / 'dist/client/vulkan'


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


class VersionTests(unittest.TestCase):
    def test_both_programs_share_one_version_from_1_0_0_2_on(self):
        # The exact numbers of the current release: tests/test_vrostbridge_1003.py and later.
        self.assertEqual(CLIENT_VERSION, HOST_VERSION)
        self.assertGreaterEqual(tuple(int(part) for part in HOST_VERSION.split('.')), (1, 0, 0, 2))
        import build_distributions
        self.assertEqual(build_distributions.client_archive_name(), 'VROSTBridge-Bazzite-Installer-%s.zip' % CLIENT_VERSION)
        self.assertEqual(build_distributions.host_setup_name(), 'VROSTBridge-Host-Setup-%s.exe' % HOST_VERSION)


class DocumentTests(unittest.TestCase):
    def test_the_release_notes_and_guide_say_so(self):
        notes = read('docs/history/RELEASE_1.0.0.2.md')
        self.assertIn('Vulkan layer', notes)
        self.assertIn('VROSTBRIDGE_VULKAN_LAYER=off', notes)
        self.assertIn('RELEASE_1.0.0.2.md', read('docs/DOCUMENTATION_INDEX.md'))
        guide = read('docs/BEYOND_EYE_TRACKING.md')
        self.assertIn(baballonia.VULKAN_LAYER_NAME, guide)
        self.assertIn('client/platform/linux/openvr_texture_layer.c', guide)

    def test_the_rendered_specification(self):
        try:
            import pypdf
        except ImportError:
            self.skipTest('pypdf is not installed')
        reader = pypdf.PdfReader(str(ROOT / 'docs/PROJECT_SPEC.pdf'))
        text = ' '.join(' '.join((page.extract_text() or '').split()) for page in reader.pages)
        self.assertIn(baballonia.VULKAN_LAYER_NAME, text)


class LayerTests(unittest.TestCase):
    """The layer's source, manifest, build and package agree with the launcher."""

    def test_the_manifest_names_what_the_library_exports(self):
        manifest = json.loads(MANIFEST.read_text())['layer']
        self.assertEqual(manifest['name'], baballonia.VULKAN_LAYER_NAME)
        self.assertEqual(manifest['library_path'], './' + baballonia.VULKAN_LAYER_FILES[0], 'beside the manifest')
        self.assertEqual(MANIFEST.name, baballonia.VULKAN_LAYER_FILES[1])
        source = LAYER_SOURCE.read_text()
        for exported in manifest['functions'].values():
            self.assertRegex(source, r'EXPORT VKAPI_ATTR \w+ VKAPI_CALL ' + exported + r'\(')
        self.assertIn(baballonia.VULKAN_LAYER_NAME, baballonia.CALIBRATION_LAUNCHER)
        self.assertIn(baballonia.VULKAN_LAYER_FOLDER + '"', baballonia.CALIBRATION_LAUNCHER)
        self.assertIn(baballonia.VULKAN_LAYER_FILES[1], baballonia.CALIBRATION_LAUNCHER)

    def test_it_enables_what_steamvr_calls(self):
        source = LAYER_SOURCE.read_text()
        wanted = re.search(r'DEVICE_EXTENSIONS\[\] = \{(.*?)\};', source, re.S).group(1)
        for extension in ('VK_KHR_external_memory_fd', 'VK_KHR_external_semaphore_fd', 'VK_KHR_external_fence_fd',
                          'VK_KHR_timeline_semaphore', 'VK_KHR_synchronization2', 'VK_EXT_external_memory_dma_buf'):
            self.assertIn('"' + extension + '"', wanted)
        for command in ('vkGetSemaphoreFdKHR', 'vkImportSemaphoreFdKHR', 'vkWaitSemaphoresKHR'):
            self.assertIn('"' + command + '"', source)
        self.assertIn('VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES', source, 'never doubles a feature')

    def test_it_is_built_installed_and_packaged(self):
        cmake = read('CMakeLists.txt')
        self.assertIn('add_library(VkLayer_vrostbridge_openvr MODULE client/platform/linux/openvr_texture_layer.c)', cmake)
        self.assertIn('install(TARGETS VkLayer_vrostbridge_openvr LIBRARY DESTINATION vulkan)', cmake)
        self.assertIn('install(FILES client/platform/linux/VkLayer_vrostbridge_openvr.json DESTINATION vulkan)', cmake)
        self.assertIn('add_test(NAME openvr_texture_layer COMMAND vulkan_layer_tests)', cmake)
        self.assertIn("'vulkan/libVkLayer_vrostbridge_openvr.so','vulkan/VkLayer_vrostbridge_openvr.json'",
                      read('packaging/build_distributions.py'), 'the client package refuses a build without it')

    def test_the_built_layer(self):
        library = BUILT / baballonia.VULKAN_LAYER_FILES[0]
        if not library.is_file():
            self.skipTest('the client is not built (cmake --install)')
        data = library.read_bytes()
        self.assertTrue(data.startswith(b'\x7fELF'))
        for name in (b'vkNegotiateLoaderLayerInterfaceVersion', b'vrost_GetInstanceProcAddr', b'vrost_GetDeviceProcAddr'):
            self.assertIn(name, data)
        self.assertEqual((BUILT / baballonia.VULKAN_LAYER_FILES[1]).read_bytes(), MANIFEST.read_bytes())
        readelf = shutil.which('readelf')
        if readelf:
            dynamic = subprocess.run([readelf, '-d', str(library)], capture_output=True, text=True).stdout
            self.assertEqual(re.findall(r'\(NEEDED\)\s+Shared library: \[(.*?)\]', dynamic), ['libc.so.6'],
                             'nothing but libc: the loader loads it into a program that has its own libraries')

    def test_it_is_found_in_an_installation_and_a_source_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            install = Path(temp)
            (install / 'vulkan').mkdir()
            for name in baballonia.VULKAN_LAYER_FILES:
                (install / 'vulkan' / name).write_text(name)
            with patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, 'executable', str(install / 'VROSTBridge-Client')):
                self.assertEqual(baballonia.vulkan_layer_source(), install / 'vulkan')
            (install / 'vulkan' / baballonia.VULKAN_LAYER_FILES[1]).unlink()
            with patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, 'executable', str(install / 'VROSTBridge-Client')):
                self.assertIsNone(baballonia.vulkan_layer_source(), 'both files or none')
        if (BUILT / baballonia.VULKAN_LAYER_FILES[0]).is_file():
            self.assertEqual(baballonia.vulkan_layer_source(), BUILT)


class Folder(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def layer(self, content=b'layer one'):
        folder = self.root / 'vrostbridge/vulkan'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / baballonia.VULKAN_LAYER_FILES[0]).write_bytes(content)
        (folder / baballonia.VULKAN_LAYER_FILES[1]).write_bytes(MANIFEST.read_bytes())
        return folder

    def manager(self, layer=None, logs=None):
        manager = BaballoniaManager(log=(logs if logs is not None else []).append, root=self.root / 'data/baballonia',
                                    config=self.root / 'config', state=self.root / 'state/setup.json', curl=None,
                                    systemd_run=None, systemctl=None, vulkan_layer=layer)
        folder = manager.install_dir / baballonia.CALIBRATION_FOLDER
        folder.mkdir(parents=True)
        (folder / 'BabbleCalibration.x86_64').write_bytes(b'\x7fELF' + b'godot' * 10)
        (folder / 'BabbleCalibration.x86_64').chmod(0o755)
        return manager, folder


class PrepareTests(Folder):
    def test_the_layer_goes_beside_the_launcher_and_follows_updates(self):
        source = self.layer()
        manager, folder = self.manager(source)
        self.assertEqual(manager.prepare_calibration(), 'installed')
        target = folder / baballonia.VULKAN_LAYER_FOLDER
        library, manifest = (target / name for name in baballonia.VULKAN_LAYER_FILES)
        self.assertEqual(library.read_bytes(), b'layer one')
        self.assertEqual(manifest.read_bytes(), MANIFEST.read_bytes())
        self.assertEqual(library.stat().st_mode & 0o777, 0o755)
        self.assertEqual(manifest.stat().st_mode & 0o777, 0o644)
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ['BabbleCalibration.program', 'BabbleCalibration.x86_64', baballonia.VULKAN_LAYER_FOLDER])
        self.assertEqual(sorted(p.name for p in target.iterdir()), sorted(baballonia.VULKAN_LAYER_FILES), 'no leftovers')
        self.assertEqual(manager.prepare_calibration(), 'ready')
        self.layer(b'layer two')  # VROSTBridge updated: its layer changed, the launcher did not
        self.assertEqual(manager.prepare_calibration(), 'installed')
        self.assertEqual(library.read_bytes(), b'layer two')
        self.assertEqual(manager.prepare_calibration(), 'ready')

    def test_without_a_layer_the_launcher_still_goes_in_place(self):
        manager, folder = self.manager(None)
        self.assertEqual(manager.prepare_calibration(), 'installed')
        self.assertEqual((folder / 'BabbleCalibration.x86_64').read_text(), baballonia.CALIBRATION_LAUNCHER)
        self.assertFalse((folder / baballonia.VULKAN_LAYER_FOLDER).exists())

    def test_a_failed_copy_is_said_and_the_launcher_still_goes_in_place(self):
        logs = []
        manager, folder = self.manager(self.layer(), logs)
        with patch.object(Path, 'write_bytes', side_effect=OSError('disk full')):
            self.assertEqual(manager.prepare_calibration(), 'installed')
        self.assertEqual((folder / 'BabbleCalibration.x86_64').read_text(), baballonia.CALIBRATION_LAUNCHER)
        self.assertIn('could not be put in place (disk full); the calibration starts without it', logs[-1])
        self.assertFalse(list((folder / baballonia.VULKAN_LAYER_FOLDER).glob('.*')), 'no partial file left')
        self.assertEqual(manager.prepare_calibration(), 'installed', 'put in place the next time')
        self.assertTrue((folder / baballonia.VULKAN_LAYER_FOLDER / baballonia.VULKAN_LAYER_FILES[1]).is_file())

    def start(self, environment):
        logs = []
        manager, folder = self.manager(self.layer(), logs)
        manager.executable.write_text('#!/bin/sh\nsleep 30\n')
        manager.executable.chmod(0o755)
        (manager.install_dir / 'vrostbridge-receipt.json').write_text(json.dumps({'sha256': manager.release['sha256']}))
        seen = {}

        def popen(command, **options):
            seen.update(options['env'])
            return Mock(pid=4321, poll=Mock(return_value=None))
        manager.popen = popen
        with patch.dict('os.environ', dict({'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root / 'config')},
                                           **environment), clear=True):
            self.assertEqual(manager.start(), 'started')
        return logs, seen

    def test_start_says_the_layer_is_there(self):
        logs, seen = self.start({})
        self.assertTrue(any("with VROSTBridge's Vulkan layer for SteamVR's overlay textures" in line for line in logs), logs)
        self.assertNotIn('VROSTBRIDGE_VULKAN_LAYER', seen)

    def test_the_layer_can_be_left_out(self):
        logs, seen = self.start({'VROSTBRIDGE_VULKAN_LAYER': 'off'})
        self.assertEqual(seen.get('VROSTBRIDGE_VULKAN_LAYER'), 'off', 'passed on to the calibration launcher')


class LauncherTests(Folder):
    """The calibration launcher (a shell script) gives the program the layer only as a SteamVR overlay."""

    def run_launcher(self, *arguments, steamvr=False, layer=True, library=None, environment=None, program=None):
        folder = self.root / 'Overlay'
        folder.mkdir(exist_ok=True)
        launcher = folder / 'BabbleCalibration.x86_64'
        launcher.write_text(baballonia.CALIBRATION_LAUNCHER)
        launcher.chmod(0o755)
        target = folder / baballonia.VULKAN_LAYER_FOLDER
        if layer:
            target.mkdir(exist_ok=True)
            (target / baballonia.VULKAN_LAYER_FILES[1]).write_bytes(MANIFEST.read_bytes())
            if library:
                shutil.copy(library, target / library.name)
        elif target.exists():
            shutil.rmtree(target)
        (folder / 'BabbleCalibration.program').write_text(program or (
            '#!/bin/sh\nfor a in "$@"; do printf "[%s]" "$a"; done\n'
            'printf "\\nadd=%s\\nlayers=%s\\n" "$VK_ADD_LAYER_PATH" "$VK_INSTANCE_LAYERS"\n'))
        (folder / 'BabbleCalibration.program').chmod(0o755)
        server = None
        if steamvr:
            fake = self.root / 'vrserver'
            shutil.copy('/bin/sleep', fake)
            server = subprocess.Popen([str(fake), '30'])
            self.addCleanup(lambda: (server.kill(), server.wait()))
            time.sleep(.2)
        base = {key: value for key, value in os.environ.items()
                if key not in ('VK_ADD_LAYER_PATH', 'VK_INSTANCE_LAYERS', 'VROSTBRIDGE_CALIBRATION', 'VROSTBRIDGE_VULKAN_LAYER')}
        result = subprocess.run([str(launcher), *arguments], capture_output=True, text=True, timeout=60,
                                env=dict(base, **(environment or {})))
        if server is not None:
            server.kill()
            server.wait()
        return result, target

    def test_as_a_steamvr_overlay_the_program_gets_the_layer(self):
        result, target = self.run_launcher('-l', 'en', '--xr-mode', 'on', steamvr=True)
        arguments, add, layers = result.stdout.split('\n')[:3]
        self.assertEqual(arguments, '[-l][en][--use-openvr]')
        self.assertEqual(add, 'add=' + str(target))
        self.assertEqual(layers, 'layers=' + baballonia.VULKAN_LAYER_NAME)
        self.assertIn("with VROSTBridge's Vulkan layer for SteamVR's overlay textures", result.stderr)

    def test_layers_of_ones_own_are_kept(self):
        result, target = self.run_launcher('-l', 'en', '--xr-mode', 'on', steamvr=True,
                                           environment={'VK_ADD_LAYER_PATH': '/opt/layers', 'VK_INSTANCE_LAYERS': 'VK_LAYER_mine'})
        self.assertIn('add=' + str(target) + ':/opt/layers\n', result.stdout)
        self.assertIn('layers=' + baballonia.VULKAN_LAYER_NAME + ':VK_LAYER_mine\n', result.stdout)

    def test_a_program_started_with_its_own_openvr_option_gets_it_too(self):
        result, target = self.run_launcher('-l', 'en', '--use-openvr')
        self.assertIn('[-l][en][--use-openvr]\nadd=' + str(target) + '\n', result.stdout)

    def test_otherwise_it_does_not(self):
        cases = {'no SteamVR, OpenXR (Monado, WiVRn)': (('-l', 'en', '--xr-mode', 'on'), {}, {}),
                 'no VR runtime (this screen)': (('-l', 'en', '--use-debug'), {'steamvr': True}, {}),
                 "Baballonia's OpenXR calibration kept": (('-l', 'en', '--xr-mode', 'on'), {'steamvr': True},
                                                          {'VROSTBRIDGE_CALIBRATION': 'openxr'}),
                 'left out on request': (('-l', 'en', '--xr-mode', 'on'), {'steamvr': True}, {'VROSTBRIDGE_VULKAN_LAYER': 'off'}),
                 'no layer beside the launcher': (('-l', 'en', '--xr-mode', 'on'), {'steamvr': True, 'layer': False}, {})}
        for name, (arguments, options, environment) in cases.items():
            with self.subTest(name):
                result, _ = self.run_launcher(*arguments, environment=environment, **options)
                self.assertIn('\nadd=\nlayers=\n', result.stdout)
                self.assertNotIn('Vulkan layer', result.stderr)

    def test_the_real_layer_reaches_a_vulkan_program(self):
        """End to end where a Vulkan device and the built layer are available (lavapipe in CI): the launcher's
        environment makes the loader load the layer into the program, which then has the external memory commands
        on a device it made without them."""
        library = BUILT / baballonia.VULKAN_LAYER_FILES[0]
        if not library.is_file():
            self.skipTest('the client is not built (cmake --install)')
        if not ctypes.util.find_library('vulkan'):
            self.skipTest('no Vulkan loader')
        probe = self.root / 'probe.py'
        probe.write_text(PROBE)
        program = '#!/bin/sh\nexec "%s" "%s"\n' % (sys.executable, probe)
        results = {}
        for name, environment in (('with', {}), ('without', {'VROSTBRIDGE_VULKAN_LAYER': 'off'})):
            result, _ = self.run_launcher('-l', 'en', '--xr-mode', 'on', steamvr=True, library=library, program=program,
                                          environment=environment)
            if result.returncode == 77:
                self.skipTest(result.stdout.strip())
            results[name] = result
        result = results['with']
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertIn('VROSTBridge Vulkan layer: added', result.stderr)
        if 'VK_KHR_external_memory_fd' in report['offered']:
            self.assertTrue(report['commands']['vkGetMemoryFdKHR'], 'enabled by the layer, not by the program')
        if 'VK_KHR_external_semaphore_fd' in report['offered']:
            self.assertTrue(report['commands']['vkGetSemaphoreFdKHR'])
        else:
            self.assertIn('Still missing (this driver does not offer them): vkGetSemaphoreFdKHR', result.stderr)
        without = results['without']
        self.assertEqual(without.returncode, 0, without.stdout + without.stderr)
        self.assertNotIn('VROSTBridge Vulkan layer', without.stderr)


# A Vulkan 1.2 program that enables no extension, run by the launcher in place of the calibration: prints which
# extension commands its device has, as JSON. Exits 77 without a Vulkan device.
PROBE = r'''
import ctypes, json, sys
vk = ctypes.CDLL('libvulkan.so.1')
P, U32, F = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_float
class App(ctypes.Structure):
    _fields_ = [('sType', ctypes.c_int), ('pNext', P), ('pApplicationName', ctypes.c_char_p), ('applicationVersion', U32),
                ('pEngineName', ctypes.c_char_p), ('engineVersion', U32), ('apiVersion', U32)]
class InstanceInfo(ctypes.Structure):
    _fields_ = [('sType', ctypes.c_int), ('pNext', P), ('flags', U32), ('pApplicationInfo', ctypes.POINTER(App)),
                ('enabledLayerCount', U32), ('ppEnabledLayerNames', P), ('enabledExtensionCount', U32),
                ('ppEnabledExtensionNames', P)]
class QueueInfo(ctypes.Structure):
    _fields_ = [('sType', ctypes.c_int), ('pNext', P), ('flags', U32), ('queueFamilyIndex', U32), ('queueCount', U32),
                ('pQueuePriorities', ctypes.POINTER(F))]
class DeviceInfo(ctypes.Structure):
    _fields_ = [('sType', ctypes.c_int), ('pNext', P), ('flags', U32), ('queueCreateInfoCount', U32),
                ('pQueueCreateInfos', ctypes.POINTER(QueueInfo)), ('enabledLayerCount', U32), ('ppEnabledLayerNames', P),
                ('enabledExtensionCount', U32), ('ppEnabledExtensionNames', P), ('pEnabledFeatures', P)]
class Extension(ctypes.Structure):
    _fields_ = [('extensionName', ctypes.c_char * 256), ('specVersion', U32)]
vk.vkGetDeviceProcAddr.restype = P
vk.vkGetDeviceProcAddr.argtypes = [P, ctypes.c_char_p]
app = App(0, None, b'probe', 1, b'probe', 1, (1 << 22) | (2 << 12))
instance = P()
if vk.vkCreateInstance(ctypes.byref(InstanceInfo(1, None, 0, ctypes.pointer(app), 0, None, 0, None)), None,
                       ctypes.byref(instance)):
    print('SKIP: no Vulkan instance'); sys.exit(77)
count = U32(1); gpu = P()
if vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), ctypes.byref(gpu)) < 0 or not count.value:
    print('SKIP: no Vulkan device'); sys.exit(77)
n = U32(0)
vk.vkEnumerateDeviceExtensionProperties(gpu, None, ctypes.byref(n), None)
offers = (Extension * n.value)()
vk.vkEnumerateDeviceExtensionProperties(gpu, None, ctypes.byref(n), offers)
priority = F(1.0)
queue = QueueInfo(2, None, 0, 0, 1, ctypes.pointer(priority))
device = P()
result = vk.vkCreateDevice(gpu, ctypes.byref(DeviceInfo(3, None, 0, 1, ctypes.pointer(queue), 0, None, 0, None, None)),
                           None, ctypes.byref(device))
if result:
    print('vkCreateDevice', result); sys.exit(1)
names = ('vkGetMemoryFdKHR', 'vkGetSemaphoreFdKHR', 'vkImportSemaphoreFdKHR', 'vkWaitSemaphoresKHR')
commands = {name: bool(vk.vkGetDeviceProcAddr(device, name.encode())) for name in names}
vk.vkDestroyDevice(device, None)
vk.vkDestroyInstance(instance, None)
print(json.dumps({'offered': [o.extensionName.decode() for o in offers], 'commands': commands}))
'''


if __name__ == '__main__':
    unittest.main()
