"""Read-only launcher checks; no Python/dependency installers run in tests."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_runtime


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / 'scripts' / 'bootstrap_ui.ps1'
POWERSHELL = shutil.which('powershell.exe')


def compatible_report():
    return {'ok': True, 'errors': [], 'executable': sys.executable,
            'version': '3.11.0', 'bits': 64, 'venv': True}


class RuntimeCheckTests(unittest.TestCase):
    def test_requirement_hash_includes_nested_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'requirements-ui.txt').write_text('-r requirements.txt\nPySide6>=6.8,<7\n', encoding='utf-8')
            nested = root / 'requirements.txt'
            nested.write_text('requests>=2.31\n', encoding='utf-8')
            before, old_hash = check_runtime.read_requirements(root / 'requirements-ui.txt')
            nested.write_text('requests>=2.32\n', encoding='utf-8')
            after, new_hash = check_runtime.read_requirements(root / 'requirements-ui.txt')
        self.assertEqual(before, ['requests>=2.31', 'PySide6>=6.8,<7'])
        self.assertEqual(after, ['requests>=2.32', 'PySide6>=6.8,<7'])
        self.assertNotEqual(old_hash, new_hash)

    def test_cyclic_requirement_includes_terminate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.txt').write_text('-r b.txt\nrequests>=2.31\n', encoding='utf-8')
            (root / 'b.txt').write_text('-r a.txt\nPySide6>=6.8,<7\n', encoding='utf-8')
            lines, fingerprint = check_runtime.read_requirements(root / 'a.txt')
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(fingerprint), 64)

    def test_missing_requirements_reports_clear_error(self):
        with patch.object(check_runtime, 'interpreter_report', return_value=compatible_report()):
            report = check_runtime.dependency_report(ROOT / 'missing-requirements-test.txt')
        self.assertFalse(report['ok'])
        self.assertFalse(report['requirements_valid'])

    def test_missing_package_and_broken_qt_import_are_detected(self):
        original_import = check_runtime.importlib.import_module

        def imports(name):
            if name == 'PySide6.QtWidgets':
                raise ImportError('Qt DLL missing')
            return original_import(name)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'requirements-ui.txt'
            path.write_text('requests>=2.31\nPySide6>=6.8,<7\n', encoding='utf-8')
            with patch.object(check_runtime, 'interpreter_report', return_value=compatible_report()), \
                 patch.object(check_runtime.metadata, 'version', side_effect=lambda name: '1.0' if name == 'requests' else '6.9.0'), \
                 patch.object(check_runtime.importlib, 'import_module', side_effect=imports):
                report = check_runtime.dependency_report(path)
        self.assertFalse(report['ok'])
        self.assertIn('requests', report['missing'])
        self.assertIn('PySide6-Essentials', report['broken'])

    def test_qt_child_timeout_is_bounded_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'requirements-ui.txt'
            path.write_text('', encoding='utf-8')
            with patch.object(check_runtime, 'interpreter_report', return_value=compatible_report()), \
                 patch.object(check_runtime.importlib, 'import_module'), \
                 patch.object(check_runtime.subprocess, 'run', side_effect=subprocess.TimeoutExpired('qt', 25)) as run:
                report = check_runtime.dependency_report(path)
        self.assertFalse(report['qt_ok'])
        self.assertIn('PySide6-Essentials', report['broken'])
        self.assertEqual(run.call_args.kwargs['timeout'], 25)

    def test_complete_environment_does_not_install_or_contact_pip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'requirements-ui.txt'
            path.write_text('requests>=2.31\n', encoding='utf-8')
            with patch.object(check_runtime, 'interpreter_report', return_value=compatible_report()), \
                 patch.object(check_runtime.metadata, 'version', return_value='2.32.0'), \
                 patch.object(check_runtime.importlib, 'import_module'), \
                 patch.object(check_runtime.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
                report = check_runtime.dependency_report(path)
        self.assertTrue(report['ok'])
        self.assertEqual(run.call_count, 1)
        self.assertIn('--qt-smoke', run.call_args.args[0])
        self.assertNotIn('pip', run.call_args.args[0])

    def test_old_python_and_32_bit_versions_are_rejected(self):
        with patch.object(check_runtime.sys, 'version_info', (3, 10, 9)), \
             patch.object(check_runtime.struct, 'calcsize', return_value=4):
            report = check_runtime.interpreter_report()
        self.assertFalse(report['ok'])
        self.assertTrue(any('3.11' in error for error in report['errors']))
        self.assertTrue(any('x64' in error for error in report['errors']))


@unittest.skipUnless(POWERSHELL and os.name == 'nt', 'Windows PowerShell launcher tests')
class PowerShellBootstrapTests(unittest.TestCase):
    def run_script(self, *arguments, timeout=60):
        result = subprocess.run(
            [POWERSHELL, '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
             '-File', str(BOOTSTRAP), *map(str, arguments)],
            capture_output=True, encoding='utf-8', errors='replace', timeout=timeout,
            env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'),
        )
        return result

    def run_helpers(self, root, code):
        source = str(BOOTSTRAP).replace("'", "''")
        project = str(root).replace("'", "''")
        command = f". '{source}' -ProjectRoot '{project}'; {code}"
        return subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command],
                              capture_output=True, encoding='utf-8', errors='replace', timeout=30)

    def test_powershell_51_parses_unicode_and_script(self):
        self.assertTrue(BOOTSTRAP.read_bytes().startswith(b'\xef\xbb\xbf'))
        source = str(BOOTSTRAP).replace("'", "''")
        script = f"$issues=$null; $tokens=$null; $null=[System.Management.Automation.Language.Parser]::ParseFile('{source}',[ref]$tokens,[ref]$issues); if($issues.Count){{$issues | ForEach-Object {{$_.ToString()}}; exit 1}}"
        result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dry_run_handles_spaces_and_non_ascii_path_without_writes(self):
        with tempfile.TemporaryDirectory(prefix='News Studio 测试 ') as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            shutil.copyfile(ROOT / 'scripts' / 'check_runtime.py', root / 'scripts' / 'check_runtime.py')
            shutil.copyfile(ROOT / 'requirements-ui.txt', root / 'requirements-ui.txt')
            shutil.copyfile(ROOT / 'requirements.txt', root / 'requirements.txt')
            (root / 'config_ui.py').write_text('raise RuntimeError("Dry run must not launch this")\n', encoding='utf-8')
            before = {str(path.relative_to(root)) for path in root.rglob('*')}
            result = self.run_script('-ProjectRoot', root, '-PythonPath', sys.executable, '-DryRun')
            after = {str(path.relative_to(root)) for path in root.rglob('*')}
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('预演', result.stdout)
        self.assertEqual(before, after)

    def test_missing_project_fails_before_download_or_install(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_script('-ProjectRoot', directory, '-DryRun')
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(result.returncode, 1)
        self.assertIn('项目文件不完整', result.stdout)

    def test_root_entry_and_legacy_entry_use_same_bootstrap(self):
        root = (ROOT / 'start_ui.bat').read_text(encoding='utf-8')
        legacy = (ROOT / 'scripts' / 'start_ui.bat').read_text(encoding='utf-8')
        self.assertIn('bootstrap_ui.ps1', root)
        self.assertIn('..\\start_ui.bat', legacy)
        self.assertNotIn('pip install', legacy)

    def test_installer_bad_hash_is_never_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / '.runtime' / 'downloads'
            downloads.mkdir(parents=True)
            installer = downloads / 'python-3.13.16-amd64.exe'
            installer.write_bytes(b'not an installer')
            result = self.run_helpers(root, r'''
                function Get-FileHash { return [PSCustomObject]@{ Hash='not-the-official-hash' } }
                function Start-Process { throw 'UNEXPECTED INSTALL' }
                function Invoke-WebRequest { throw 'UNEXPECTED DOWNLOAD' }
                try { Install-LocalPython; exit 1 }
                catch { if ($_.Exception.Message -match '校验失败') { exit 0 }; Write-Host $_; exit 2 }
            ''')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(installer.exists())

    def test_installer_invalid_signature_is_never_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / '.runtime' / 'downloads'
            downloads.mkdir(parents=True)
            (downloads / 'python-3.13.16-amd64.exe').write_bytes(b'mocked installer')
            result = self.run_helpers(root, r'''
                function Get-FileHash { return [PSCustomObject]@{ Hash=$PythonSha256 } }
                function Get-AuthenticodeSignature { return [PSCustomObject]@{ Status='UnknownError'; SignerCertificate=$null } }
                function Start-Process { throw 'UNEXPECTED INSTALL' }
                function Invoke-WebRequest { throw 'UNEXPECTED DOWNLOAD' }
                try { Install-LocalPython; exit 1 }
                catch { if ($_.Exception.Message -match '签名验证失败') { exit 0 }; Write-Host $_; exit 2 }
            ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_verified_install_options_are_project_scoped_without_path_changes(self):
        with tempfile.TemporaryDirectory(prefix='installer path 空格 ') as directory:
            root = Path(directory)
            downloads = root / '.runtime' / 'downloads'
            downloads.mkdir(parents=True)
            (downloads / 'python-3.13.16-amd64.exe').write_bytes(b'mocked installer')
            result = self.run_helpers(root, r'''
                function Get-FileHash { return [PSCustomObject]@{ Hash=$PythonSha256 } }
                function Get-AuthenticodeSignature { return [PSCustomObject]@{ Status='Valid'; SignerCertificate=[PSCustomObject]@{ Subject='CN=Python Software Foundation, O=Python Software Foundation, C=US' } } }
                function Invoke-WebRequest { throw 'UNEXPECTED DOWNLOAD' }
                function Test-Python { return [PSCustomObject]@{ ok=$true; version='3.13.16' } }
                function Start-Process {
                    param($FilePath, $ArgumentList, $WindowStyle, [switch]$PassThru)
                    $script:installedArguments=$ArgumentList
                    $script:installedWindow=$WindowStyle
                    $mock=[PSCustomObject]@{ ExitCode=0 }
                    $mock | Add-Member ScriptMethod WaitForExit { param($milliseconds) return $true }
                    return $mock
                }
                $chosen=Install-LocalPython
                [PSCustomObject]@{arguments=$script:installedArguments; window=$script:installedWindow; python=$chosen.Path} | ConvertTo-Json -Compress
            ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertIn('InstallAllUsers=0', report['arguments'])
        self.assertIn('PrependPath=0', report['arguments'])
        self.assertIn('Include_launcher=0', report['arguments'])
        self.assertIn('"TargetDir=', report['arguments'])
        self.assertEqual(report['window'], 'Hidden')
        self.assertIn('.runtime\\python-3.13.16\\python.exe', report['python'])

    def test_official_pypi_failure_uses_only_one_configured_mirror_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_helpers(directory, r'''
                $script:attempts=New-Object Collections.ArrayList
                function Invoke-InstallCommand {
                    param($Python, $Arguments)
                    $null=$script:attempts.Add(($Arguments -join ' '))
                    if ($script:attempts.Count -eq 1) { return 1 }; return 0
                }
                Install-Requirements 'mock-python'
                ConvertTo-Json -InputObject @($script:attempts) -Compress
            ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        attempts = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(len(attempts), 2)
        self.assertIn('--index-url https://pypi.org/simple', attempts[0])
        self.assertIn('--index-url https://pypi.tuna.tsinghua.edu.cn/simple', attempts[1])
        self.assertTrue(all('--only-binary=PySide6,' in attempt for attempt in attempts))
        self.assertTrue(all('--only-binary=:all:' not in attempt for attempt in attempts))

    def test_vc_runtime_invalid_signature_is_never_elevated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / '.runtime' / 'downloads'
            downloads.mkdir(parents=True)
            (downloads / 'VC_redist.x64.exe').write_bytes(b'mocked runtime installer')
            result = self.run_helpers(root, r'''
                function Get-AuthenticodeSignature { return [PSCustomObject]@{ Status='Valid'; SignerCertificate=[PSCustomObject]@{ Subject='CN=Unknown Publisher' } } }
                function Start-Process { throw 'UNEXPECTED ELEVATION' }
                function Invoke-WebRequest { throw 'UNEXPECTED DOWNLOAD' }
                try { Install-VcRuntime; exit 1 }
                catch { if ($_.Exception.Message -match '签名验证失败') { exit 0 }; Write-Host $_; exit 2 }
            ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_vc_runtime_uses_microsoft_verified_installer_with_uac(self):
        with tempfile.TemporaryDirectory(prefix='VC runtime 空格 ') as directory:
            root = Path(directory)
            downloads = root / '.runtime' / 'downloads'
            downloads.mkdir(parents=True)
            (downloads / 'VC_redist.x64.exe').write_bytes(b'mocked runtime installer')
            result = self.run_helpers(root, r'''
                function Get-AuthenticodeSignature { return [PSCustomObject]@{ Status='Valid'; SignerCertificate=[PSCustomObject]@{ Subject='CN=Microsoft Corporation, O=Microsoft Corporation, C=US' } } }
                function Invoke-WebRequest { throw 'UNEXPECTED DOWNLOAD' }
                function Start-Process {
                    param($FilePath, $ArgumentList, $Verb, $WindowStyle, [switch]$PassThru)
                    $script:installedArguments=$ArgumentList
                    $script:installedVerb=$Verb
                    $mock=[PSCustomObject]@{ ExitCode=0 }
                    $mock | Add-Member ScriptMethod WaitForExit { param($milliseconds) return $true }
                    return $mock
                }
                Install-VcRuntime
                [PSCustomObject]@{arguments=$script:installedArguments; verb=$script:installedVerb} | ConvertTo-Json -Compress
            ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(report['verb'], 'RunAs')
        self.assertIn('/install', report['arguments'])
        self.assertIn('/norestart', report['arguments'])


if __name__ == '__main__':
    unittest.main()
