import json
HEADSET={'model':'Index','manufacturer':'Valve','tracking_system':'lighthouse',
         'serial':'LHR-test','render_model':'generic_hmd',
         'registered_type':'valve/index','input_profile':'{indexhmd}/input/indexhmd_profile.json',
         'icon_ready':'{indexhmd}/icons/index_headset_ready.png',
         'icon_off':'{indexhmd}/icons/index_headset_off.png',
         'icon_searching':'{indexhmd}/icons/index_headset_searching.gif',
         'icon_alert':'{indexhmd}/icons/index_headset_ready_alert.png',
         'width':2016,'height':2240,'refresh_hz':90.0}
HEADSET_OUTPUT='RUNTIME_READY display_progress frames=12 measured_hz=90\nHEADSET '+json.dumps(HEADSET)+'\nSTATUS Stream-only runtime verified\n'
