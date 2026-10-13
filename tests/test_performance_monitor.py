from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from launcher.performance_monitor import PerformanceMonitor, read_performance


class PerformanceMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def write(self,path,text):
        file = self.root/path; file.parent.mkdir(parents=True,exist_ok=True); file.write_text(str(text))

    def test_observed_gpu_clocks_and_hwmon_units(self):
        base = 'class/drm/card1/device/'
        for key,value in {'gpu_busy_percent':42,'mem_busy_percent':17,'pp_dpm_sclk':'0: 400Mhz\n1: 1800Mhz *\n',
                          'pp_dpm_mclk':'0: 800Mhz *','mem_info_vram_used':33554432,'power_dpm_force_performance_level':'auto',
                          'hwmon/hwmon1/temp1_input':65500,'hwmon/hwmon1/power1_average':18000000,
                          'hwmon/hwmon1/freq1_input':1750000000}.items():self.write(base+key,value)
        result = read_performance(self.root); self.assertTrue(result['available'])
        gpu = result['gpus'][0]
        self.assertEqual(gpu['gpu_busy_percent'],42)
        self.assertEqual(gpu['selected_sclk_mhz'],1800)
        self.assertEqual(gpu['sensors'][0],{'temperature_c':65.5,'average_power_w':18,'clock_mhz':1750})
        self.assertNotIn('power_cap_w',gpu['sensors'][0])

    def test_unavailable_and_malformed_values_not_invented(self):
        self.assertFalse(read_performance(self.root)['available'])
        self.write('class/drm/card0/device/gpu_busy_percent','101')
        self.write('class/drm/card0/device/pp_dpm_sclk','0: 500Mhz')
        self.write('class/drm/card0/device/hwmon/hwmon0/temp1_input','NaN')
        self.assertEqual(read_performance(self.root)['gpus'],[])

    def test_cpu_frequency_and_power_source_are_only_observations(self):
        for name,value in {'scaling_cur_freq':3150000,'scaling_min_freq':400000,'scaling_max_freq':5100000,'scaling_governor':'powersave'}.items():
            self.write('devices/system/cpu/cpufreq/policy0/'+name,value)
        self.write('class/power_supply/AC/type','Mains');self.write('class/power_supply/AC/online',1)
        self.write('class/power_supply/BAT0/type','Battery');self.write('class/power_supply/BAT0/capacity',83)
        self.write('class/power_supply/BAT0/status','Charging')
        result=read_performance(self.root)
        self.assertEqual(result['cpu_policies'][0]['reported_frequency_mhz'],3150)
        self.assertEqual(result['power_supplies'],[{'type':'Mains','online':1},{'type':'Battery','capacity_percent':83,'status':'Charging'}])

    def test_idle_never_reads_hardware_and_snapshot_never_resamples(self):
        context={'active':False,'phase':'idle'};record=[]
        monitor=PerformanceMonitor(lambda:context,lambda *args:record.append(args),sysfs=self.root,auto_start=False)
        with patch('launcher.performance_monitor.read_performance') as read:
            monitor.sample();monitor.snapshot();read.assert_not_called()
        self.assertEqual(record,[])
        context['active']=True;context['phase']='starting'
        with patch('launcher.performance_monitor.read_performance',return_value={'available':False}) as read:
            monitor.sample();first=monitor.snapshot();first['active']=False
            self.assertTrue(monitor.snapshot()['active']);read.assert_called_once()
        self.assertEqual(record[0][0],'performance-sample')
        self.assertEqual(record[0][1]['phase'],'starting')
        monitor.close()

    def test_second_drm_name_does_not_duplicate_device(self):
        self.write('class/drm/card0/device/gpu_busy_percent',12)
        card = self.root/'class/drm/card1';card.mkdir()
        (card/'device').symlink_to(self.root/'class/drm/card0/device',target_is_directory=True)
        self.assertEqual(len(read_performance(self.root)['gpus']),1)

if __name__=='__main__':unittest.main()
