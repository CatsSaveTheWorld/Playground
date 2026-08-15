import sys
from unittest import TestCase
from unittest.mock import patch

from deploy.pi5 import ytmusic_cookie_agent


class PiAgentStartupTests(TestCase):
    @patch.object(ytmusic_cookie_agent, "PiAgent")
    def test_main_constructs_pi_agent(self, pi_agent):
        with patch.object(sys, "argv", ["ytmusic_cookie_agent.py"]):
            exit_code = ytmusic_cookie_agent.main()

        self.assertEqual(exit_code, 0)
        pi_agent.assert_called_once_with()
        pi_agent.return_value.run.assert_called_once_with()
