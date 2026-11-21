import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows source launcher')
class EnvironmentTests(unittest.TestCase):
    def test_venv_under_an_unrelated_directory_is_discovered(self):
        with tempfile.TemporaryDirectory(prefix='ModLingo env ') as directory:
            root = Path(directory) / '任意开发目录'
            (root / 'scripts').mkdir(parents=True)
            script = root / 'scripts/project_environment.ps1'
            shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts/project_environment.ps1', script)
            subprocess.run([sys.executable, '-m', 'venv', '--without-pip', '--system-site-packages', str(root / '.venv')], check=True, capture_output=True, timeout=30)
            environment = dict(os.environ)
            for name in ('MODLINGO_PYTHON', 'MODLINGO_CONDA_ROOT', 'CONDA_EXE', '_CONDA_EXE'):
                environment.pop(name, None)
            runner = root / 'probe.ps1'
            runner.write_text('. "' + str(script) + '"\nUse-ModLingoEnvironment', encoding='utf-8-sig')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(runner)],
                capture_output=True, text=True, env=environment, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()).resolve(), (root / '.venv/Scripts/python.exe').resolve())

    def test_invalid_explicit_override_does_not_fall_back(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/project_environment.ps1'
        # Pass a script file so shell parsing cannot reinterpret the path.
        with tempfile.TemporaryDirectory() as directory:
            runner = Path(directory) / 'probe.ps1'
            runner.write_text('. "' + str(script) + '"\nUse-ModLingoEnvironment -PythonPath "Z:\\missing-modlingo\\python.exe"', encoding='utf-8-sig')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(runner)],
                capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Python environment not found', result.stderr)
