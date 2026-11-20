import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from modlingo.batch import check_game_stopped
from modlingo.core import ProjectError


@unittest.skipUnless(os.name=='nt','Windows native process snapshot')
class WindowsProcessTests(unittest.TestCase):
    def test_real_snapshot_contains_current_executable(self):
        from modlingo.windows_processes import process_names
        self.assertIn(Path(sys.executable).name.casefold(),process_names())

    def test_game_or_smapi_blocks_install(self):
        for name in ('stardew valley.exe','stardewvalley.exe','stardewmoddingapi.exe'):
            with patch('modlingo.windows_processes.process_names',return_value={name}):
                with self.assertRaises(ProjectError):check_game_stopped()

    def test_snapshot_failure_is_closed(self):
        with patch('modlingo.windows_processes.process_names',side_effect=OSError('failed')):
            with self.assertRaisesRegex(ProjectError,'无法检查'):check_game_stopped()

    def test_each_check_takes_fresh_snapshot(self):
        with patch('modlingo.windows_processes.process_names',side_effect=[{'python.exe'},{'stardew valley.exe'}]) as probe:
            check_game_stopped()
            with self.assertRaises(ProjectError):check_game_stopped()
            self.assertEqual(probe.call_count,2)
