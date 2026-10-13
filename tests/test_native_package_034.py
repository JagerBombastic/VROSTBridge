"""Reject a stale native DLL or changed patch set before creating installers."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('vrost_distribution_builder',ROOT/'packaging/build_distributions.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class NativePackageTests(unittest.TestCase):
    def test_native_build_requires_matching_complete_patch_set_and_file_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'patches').mkdir();(root/'patches/0001.patch').write_bytes(b'complete source patch')
            native=root/'native';dll=native/'bin/win64/driver_alvr_server.dll';dll.parent.mkdir(parents=True);dll.write_bytes(b'tested driver bytes 21.0.0-dev14')
            metadata={'alvr_commit':builder.PIN,'native_protocol':'vrostbridge-native-1','api_version':'21.0.0-dev14',
                'source_patch_sha256':{'0001.patch':hashlib.sha256(b'complete source patch').hexdigest()},
                'files':[{'name':'bin/win64/driver_alvr_server.dll','sha256':hashlib.sha256(dll.read_bytes()).hexdigest()}]}
            record=native/'vrostbridge-build.json';record.write_text(json.dumps(metadata))
            with patch.object(builder,'ROOT',root):
                self.assertEqual(builder.verify_native_overlay(native)[1],['bin/win64/driver_alvr_server.dll'])
                (root/'patches/0002.patch').write_bytes(b'later required fix')
                with self.assertRaisesRegex(RuntimeError,'complete current patch set'):builder.verify_native_overlay(native)
                (root/'patches/0002.patch').unlink();dll.write_bytes(b'old driver replaced the new build')
                with self.assertRaisesRegex(RuntimeError,'integrity failure'):builder.verify_native_overlay(native)
                metadata['files'].append({'name':'../../outside','sha256':'a'*64});record.write_text(json.dumps(metadata))
                dll.write_bytes(b'tested driver bytes 21.0.0-dev14')
                with self.assertRaisesRegex(RuntimeError,'Unsafe or missing'):builder.verify_native_overlay(native)

    def test_profile_seed_removes_old_nightly_metadata_and_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            windows=Path(temporary);native=windows/'dist/host';native.mkdir(parents=True)
            (native/'session.json').write_text(json.dumps({'server_version':'21.0.0-dev14+nightly.2026.09.18','client_connections':{'private-client':{}}}))
            for name in ('session.json.old.bak','session_log.txt','crash_log.txt'):
                (native/name).write_text('previous runtime state')
            builder.prepare_host_profile(windows,{'api_version':'21.0.0-dev14'})
            fresh=json.loads((native/'session.json').read_text())
            self.assertEqual(fresh['server_version'],'21.0.0-dev14')
            self.assertEqual(fresh['client_connections'],{})
            self.assertEqual(sorted(p.name for p in native.iterdir()),['session.json'])

    def test_manifest_cannot_claim_a_different_api_or_omit_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'patches').mkdir()
            dll=root/'bin/win64/driver_alvr_server.dll';dll.parent.mkdir(parents=True)
            dll.write_bytes(b'built api 21.0.0-dev14')
            metadata={'alvr_commit':builder.PIN,'native_protocol':'vrostbridge-native-1',
                      'source_patch_sha256':{},'files':[{'name':'bin/win64/driver_alvr_server.dll','sha256':hashlib.sha256(dll.read_bytes()).hexdigest()}]}
            record=root/'vrostbridge-build.json'
            with patch.object(builder,'ROOT',root):
                record.write_text(json.dumps(metadata))
                with self.assertRaisesRegex(RuntimeError,'API version is missing'):builder.verify_native_overlay(root)
                metadata['api_version']='21.0.0-dev14+nightly.2026.09.18';record.write_text(json.dumps(metadata))
                with self.assertRaisesRegex(RuntimeError,'API version disagrees'):builder.verify_native_overlay(root)
                metadata['api_version']='21.0.0-dev14';record.write_text(json.dumps(metadata))
                self.assertEqual(builder.verify_native_overlay(root)[0]['api_version'],'21.0.0-dev14')
