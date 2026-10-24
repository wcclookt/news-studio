"""Selective service controls only operate on explicitly selected mock workers."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from news_agent.desktop.services import ServiceManager
from news_agent.desktop.window import ConfigWindow
from news_agent.factory_defaults import fresh_config


class ServiceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.config=self.root/'config.json'
        self.config.write_text('{}',encoding='utf-8')
        (self.root/'run.py').write_text('',encoding='utf-8')
        self.napcat=self.root/'NapCat.Shell.Windows.Node'
        self.napcat.mkdir()
        (self.napcat/'node.exe').write_bytes(b'')
        (self.napcat/'index.js').write_bytes(b'')
        self.steam=self.root/'steam++'/'Steam++.exe'
        self.steam.parent.mkdir()
        self.steam.write_bytes(b'')
        self.manager=ServiceManager(self.root,self.config)
        self.processes=[]
        self.inventory=patch.object(self.manager,'_inventory',side_effect=lambda:{'processes':list(self.processes),'service':None}).start()
        self.terminate=patch.object(self.manager,'_terminate_process',side_effect=self.remove).start()
        self.launch=patch.object(self.manager,'_launch',return_value=200).start()
        self.startfile=patch('os.startfile',create=True).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.temp.cleanup)

    def process(self,name,pid):
        if name=='backend':
            exe=self.root/'python.exe'
            args=[str(exe),str(self.root/'run.py'),'--mode','service','--config',str(self.config)]
        elif name=='napcat':
            exe=self.napcat/'node.exe'
            args=[str(exe),str(self.napcat/'index.js')]
        else:
            exe=self.steam
            args=[str(exe)]
        return {'pid':pid,'ppid':1,'name':exe.name,'exe':str(exe),'created':'133000000000000000',
                'command':subprocess.list2cmdline(args)}

    def remove(self,process):
        self.processes[:]=[item for item in self.processes if item['pid']!=process['pid']]
        return True

    def test_start_only_napcat_does_not_start_backend_or_accelerator(self):
        self.manager.control_services({},['napcat'],'start')
        self.assertEqual([call.args[0] for call in self.launch.call_args_list],['napcat'])
        self.startfile.assert_not_called()

    def test_start_only_steampp_does_not_launch_other_workers(self):
        self.manager.control_services({},['steampp'],'start')
        self.startfile.assert_called_once_with(str(self.steam.resolve()))
        self.launch.assert_not_called()

    def test_stop_napcat_keeps_other_services_running(self):
        self.processes=[self.process(name,pid) for name,pid in [('backend',10),('napcat',11),('steampp',12)]]
        self.manager.control_services({},['napcat'],'stop')
        self.assertEqual({item['pid'] for item in self.processes},{10,12})
        self.launch.assert_not_called()

    def test_restart_backend_stops_it_before_launch_and_keeps_companions(self):
        self.processes=[self.process(name,pid) for name,pid in [('backend',10),('napcat',11),('steampp',12)]]
        self.manager.control_services({},['backend'],'restart')
        self.assertEqual([call.args[0] for call in self.launch.call_args_list],['backend'])
        self.assertEqual({item['pid'] for item in self.processes},{11,12})
        self.startfile.assert_not_called()

    def test_restart_all_selected_stops_backend_first_then_companions(self):
        self.processes=[self.process(name,pid) for name,pid in [('backend',10),('napcat',11),('steampp',12)]]
        self.manager.control_services({},['backend','napcat','steampp'],'restart')
        self.assertEqual([call.args[0]['pid'] for call in self.terminate.call_args_list],[10,11,12])
        self.assertEqual([call.args[0] for call in self.launch.call_args_list],['napcat','backend'])
        self.startfile.assert_called_once()

    def test_failed_stop_never_starts_a_duplicate_on_restart(self):
        self.processes=[self.process('backend',10)]
        self.terminate.side_effect=lambda process:False
        with patch.object(self.manager,'_wait_stopped',return_value=False):
            result=self.manager.control_services({},['backend'],'restart')
        self.assertIn('取消',''.join(result))
        self.launch.assert_not_called()

    def test_invalid_or_empty_selection_does_not_inspect_or_mutate(self):
        for names,action in [([], 'start'),(['unknown'],'restart'),(['backend'],'unknown')]:
            self.manager.control_services({},names,action)
        self.inventory.assert_not_called()
        self.terminate.assert_not_called()
        self.launch.assert_not_called()

    def test_other_window_control_lock_blocks_selected_operations(self):
        @contextmanager
        def busy():
            yield False
        with patch.object(self.manager,'_control_lock',busy):
            self.assertIn('另一个窗口',self.manager.control_services({},['backend'],'restart')[0])
        self.inventory.assert_not_called()

    def test_verified_exit_does_not_report_a_stale_stop_error(self):
        self.processes=[]
        with patch.object(self.manager,'stop_backend',return_value=['新闻后台 部分进程未能停止']), \
                patch.object(self.manager,'_wait_stopped',return_value=True):
            result=self.manager.control_services({},['backend'],'restart')
        self.assertNotIn('未能',''.join(result))
        self.launch.assert_called_once()


class ServiceSelectionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'config.json'
        self.path.write_text(json.dumps(fresh_config()),encoding='utf-8')
        self.window=ConfigWindow(self.path,monitor_services=False,cache_path=Path(self.temp.name)/'news.db')
        self.window.select_page(6)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.stop_monitoring()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_selected_restart_button_uses_only_checked_services(self):
        self.window.service_selectors['backend'].setChecked(False)
        self.window.service_selectors['napcat'].setChecked(True)
        scheduled=[]
        with patch.object(self.window,'run_background',side_effect=lambda name,callback:scheduled.append(callback) or True):
            QTest.mouseClick(self.window.restart_selected_button,Qt.MouseButton.LeftButton)
        with patch.object(self.window.service_manager,'control_services',return_value=['done']) as control:
            self.assertEqual(scheduled[0](),['done'])
            self.assertEqual(control.call_args.args[1:],(['napcat'],'restart'))

    def test_empty_selection_never_calls_service_manager(self):
        for control in self.window.service_selectors.values():
            control.setChecked(False)
        with patch.object(self.window,'run_background') as work:
            self.window.control_selected_services('stop')
        work.assert_not_called()
        self.assertIn('至少一项',self.window.service_operation_result.text())


if __name__=='__main__':
    unittest.main()
