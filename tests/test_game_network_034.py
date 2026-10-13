import threading
import unittest
from unittest.mock import Mock
from launcher.game_ui import View

class GameNetworkTests(unittest.TestCase):
    def test_cycles_distinct_observed_adapters_and_automatic(self):
        view=View('.',lambda:{},lambda _:None,lambda:{},threading.Event())
        view.action=Mock()
        view.state={'session_link':{'network':{'interface':'','adapters':[
            {'interface':'usb-wifi','address':'192.168.77.8'},
            {'interface':'usb-wifi','address':'192.168.77.9'},
            {'interface':'internal','address':'192.168.77.233'}]}}}
        # RC4.3: the adapter row is 'adapter'; 'network' opens the Network & host page.
        view.choose('adapter')
        view.action.assert_called_with('select-client-network',interface='usb-wifi')
        view.state['session_link']['network']['interface']='usb-wifi'
        view.choose('adapter')
        view.action.assert_called_with('select-client-network',interface='internal')
        view.state['session_link']['network']['interface']='internal'
        view.choose('adapter')
        view.action.assert_called_with('select-client-network',interface='')
