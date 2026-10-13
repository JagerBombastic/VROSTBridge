"""Reject stale native probes and preserve the installed SteamVR environment path."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from headset_fixture import HEADSET, HEADSET_OUTPUT
from launcher.client_runtime import ClientRuntime, runtime_environment_wrapper, steam_environment


class NativeReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.log=Mock()
        self.runtime=ClientRuntime(self.root/'VROSTBridgeClient',self.root,self.log)
        self.runtime.prepare_settings=Mock()
        process_patch=patch('launcher.client_runtime.runtime_processes',return_value={})
        process_patch.start();self.addCleanup(process_patch.stop)

    def test_old_successful_metadata_probe_cannot_start_host_with_stale_refresh(self):
        old='HEADSET '+json.dumps(HEADSET)+'\nSTATUS Stream-only runtime verified\n'
        self.runtime.helper=Mock(return_value=subprocess.CompletedProcess([],0,old,''))
        with self.assertRaisesRegex(ValueError,'did not verify the running headset display'):
            self.runtime.ensure()
        self.assertIsNone(self.runtime.headset)
        self.assertNotEqual(self.runtime.state,'ready')

    def test_waiting_probe_retains_failure_reason_then_accepts_actual_ready_metadata(self):
        self.runtime.helper=Mock(side_effect=[
            subprocess.CompletedProcess([],2,'RUNTIME_WAIT display_progress\n',''),
            subprocess.CompletedProcess([],0,HEADSET_OUTPUT,'wrapper startup diagnostic')])
        with patch('launcher.client_runtime.shutil.which',return_value='/usr/bin/steam'), \
             patch('launcher.client_runtime.subprocess.Popen') as launch, \
             patch.object(self.runtime.cancelled,'wait'):
            self.runtime.ensure()
        self.assertEqual(self.runtime.headset,HEADSET)
        self.assertEqual(self.runtime.state,'ready')
        self.assertEqual(launch.call_count,1)
        messages='\n'.join(call.args[0] for call in self.log.call_args_list)
        self.assertIn('RUNTIME_WAIT display_progress',messages)
        self.assertIn('wrapper startup diagnostic',messages)


class NativeEnvironmentTests(unittest.TestCase):
    def test_installed_wrapper_executes_exact_native_arguments_and_sets_child_environment(self):
        with tempfile.TemporaryDirectory(prefix='vrostbridge environment ') as temp:
            root=Path(temp);wrapper=root/'SteamVR/bin/vrenv.sh'
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text('#!/bin/sh\nexport STEAMVR_VRENV="$0"\nexport LD_LIBRARY_PATH="/registered/runtime/lib"\nexec "$@"\n')
            wrapper.chmod(0o700)
            executable=root/'client path/VROSTBridgeClient';executable.parent.mkdir()
            executable.write_text('#!'+sys.executable+'\nimport json,os,sys\nprint(json.dumps({"arguments":sys.argv[1:],"vrenv":os.environ.get("STEAMVR_VRENV"),"libraries":os.environ.get("LD_LIBRARY_PATH"),"display":os.environ.get("WAYLAND_DISPLAY")}))\n')
            executable.chmod(0o700)
            runtime=ClientRuntime(executable,root,Mock())
            arguments=['--gaze-socket',str(root/'socket with spaces'),'; literal argument']
            with patch('launcher.client_runtime.runtime_roots',return_value=[root/'SteamVR']), \
                 patch.dict(os.environ,{'LD_LIBRARY_PATH':'/frozen/private'},clear=True):
                command=runtime.native_command(arguments)
                result=subprocess.run(command,env=steam_environment({'WAYLAND_DISPLAY':'/chosen/headset'}),capture_output=True,text=True,check=True)
            data=json.loads(result.stdout)
            self.assertEqual(data,dict(arguments=arguments,vrenv=str(wrapper),libraries='/registered/runtime/lib',display='/chosen/headset'))

    def test_missing_active_wrapper_does_not_use_a_different_runtime_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);other=root/'inactive/bin/vrenv.sh';other.parent.mkdir(parents=True)
            other.touch();other.chmod(0o700)
            runtime=ClientRuntime(root/'VROSTBridgeClient',root,Mock())
            with patch('launcher.client_runtime.runtime_roots',return_value=[root/'active',root/'inactive']):
                self.assertIsNone(runtime_environment_wrapper())
                self.assertEqual(runtime.native_command(['--verify-runtime']),[str(root/'VROSTBridgeClient'),'--verify-runtime'])
            self.assertEqual(runtime.status()['native_environment_setup'],'direct')
            self.assertIn('unavailable',runtime.log.call_args.args[0])

