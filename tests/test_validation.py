import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from launcher.validation import check_machine


class ReadinessRegistryTests(unittest.TestCase):
    def test_windows_bom_and_null_driver_list_keep_valid_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'openvr').mkdir();runtime=root/'SteamVR';runtime.mkdir()
            executable=root/'ALVR Dashboard.exe';executable.touch()
            (root/'openvr/openvrpaths.vrpath').write_text(json.dumps({'runtime':[str(runtime)],'external_drivers':None}),encoding='utf-8-sig')
            with patch.dict('os.environ',{'LOCALAPPDATA':directory}),patch('launcher.validation.platform.system',return_value='Windows'):
                result=check_machine('host',executable)
            runtime_checks=[check for check in result['checks'] if check['name']=='SteamVR runtime']
            self.assertEqual(len(runtime_checks),1)
            self.assertEqual(runtime_checks[0]['result'],'pass')
            registration=next(check for check in result['checks'] if check['name']=='Host driver registration')
            self.assertEqual(registration['result'],'needs-attention')

    def test_null_runtime_list_is_clear_readiness_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'openvr').mkdir()
            (root/'openvr/openvrpaths.vrpath').write_text(json.dumps({'runtime':None,'external_drivers':[None]}))
            with patch.dict('os.environ',{'LOCALAPPDATA':directory}):
                result=check_machine('host',root/'ALVR Dashboard.exe')
            checks=[check for check in result['checks'] if check['name']=='SteamVR runtime']
            self.assertEqual(len(checks),1)
            self.assertEqual(checks[0]['result'],'needs-attention')

if __name__=='__main__':unittest.main()
