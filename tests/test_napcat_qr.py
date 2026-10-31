# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Local QR session freshness and GUI transitions, using synthetic PNGs only."""

import json
import os
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import MagicMock, Mock, patch
import zlib

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QBoxLayout

from news_agent.desktop.napcat_qr import read_login_qr
from news_agent.desktop.services import ServiceManager
from news_agent.desktop.window import ConfigWindow
from news_agent.factory_defaults import fresh_config


def png(marker=0):
    # A small test pattern, not a login token or a scannable authentication QR.
    width=33
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    rows=b''.join(b'\x00'+bytes((x+y+marker)%2*255 for x in range(width)) for y in range(width))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,width,8,0,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')


def process(start,pid=22):
    return {'pid':pid,'created':str(int(start*10000000)+116444736000000000)}


class LocalQrTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.path=self.root/'napcat'/'cache'/'qrcode.png'
        self.path.parent.mkdir(parents=True)
        self.now=time.time()
        self.status={'state':'running','processes':[process(self.now-30)]}
        self.alive=Mock(return_value=True)
        self.env=patch.dict(os.environ,{'NAPCAT_WORKDIR':''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)

    def write(self,age=0,marker=0):
        self.path.write_bytes(png(marker))
        os.utime(self.path,(self.now-age,self.now-age))

    def read(self,**kwargs):
        return read_login_qr(self.root,self.status,process_alive=self.alive,now=self.now,**kwargs)

    def test_stopped_service_never_reads_old_image(self):
        self.write()
        self.status={'state':'stopped'}
        result=self.read()
        self.assertEqual(result['message'],'暂未启动 NAPCAT 服务')
        self.assertNotIn('image',result)
        self.alive.assert_not_called()

    def test_missing_image_is_waiting_not_failure(self):
        self.assertEqual(self.read()['state'],'waiting')

    def test_fresh_png_belongs_to_current_process_and_returns_in_memory_only(self):
        self.write(age=2)
        result=self.read()
        self.assertEqual(result['state'],'qr')
        self.assertEqual(result['image'],png())
        self.assertNotIn('path',result)

    def test_previous_process_image_is_rejected_even_when_recent(self):
        self.status={'state':'running','processes':[process(self.now-1)]}
        self.write(age=1.2)
        result=self.read()
        self.assertEqual(result['state'],'waiting')
        self.assertIn('上次运行',result['message'])

    def test_new_file_replaces_previous_version(self):
        self.write(age=3)
        first=self.read()
        self.write(age=1,marker=1)
        second=self.read()
        self.assertNotEqual(first['version'],second['version'])
        self.assertNotEqual(first['image'],second['image'])

    def test_worker_restart_rejects_image_older_than_worker_even_if_parent_is_alive(self):
        self.status={'state':'running','processes':[process(self.now-90,21),process(self.now-1,22)]}
        self.write(age=5)
        result=self.read()
        self.assertEqual(result['state'],'waiting')
        self.assertNotIn('image',result)

    def test_exited_process_clears_even_a_fresh_png(self):
        self.write()
        self.alive.return_value=False
        self.assertEqual(self.read()['state'],'stopped')

    def test_reused_or_unverifiable_session_does_not_show_image(self):
        self.write()
        self.alive.return_value=None
        self.assertEqual(self.read()['state'],'unknown')
        self.alive.return_value=True
        self.status['processes'][0]['created']='unknown'
        self.assertEqual(self.read()['state'],'waiting')

    def test_logged_in_state_hides_png(self):
        self.write()
        result=self.read(logged_in=True)
        self.assertEqual(result['state'],'logged_in')
        self.assertNotIn('image',result)

    def test_old_and_future_dated_pngs_are_hidden(self):
        self.status={'state':'running','processes':[process(self.now-500)]}
        for age in (121,-30):
            self.write(age=age)
            result=self.read()
            self.assertEqual(result['state'],'stale')
            self.assertNotIn('image',result)

    def test_partial_or_oversized_files_are_not_loaded(self):
        for data in (b'partial',b'x'*(256*1024+1)):
            self.path.write_bytes(data)
            self.assertNotIn('image',self.read())

    def test_image_dimensions_are_bounded_before_qt_decode(self):
        data=bytearray(png())
        data[16:24]=struct.pack('>II',100000,100000)
        self.path.write_bytes(data)
        self.assertEqual(self.read()['state'],'error')

    def test_custom_workdir_is_read_without_copying_png(self):
        custom=self.root/'custom'
        (custom/'cache').mkdir(parents=True)
        (custom/'cache'/'qrcode.png').write_bytes(png())
        with patch.dict(os.environ,{'NAPCAT_WORKDIR':str(custom)}):
            self.assertEqual(self.read()['state'],'qr')
        self.assertFalse(self.path.exists())

    def test_manager_clear_only_removes_own_qr_not_external_workdir(self):
        manager=ServiceManager(self.root,self.root/'config.json')
        manager.napcat_root=self.root
        self.write()
        external=self.root/'external'
        (external/'cache').mkdir(parents=True)
        (external/'cache'/'qrcode.png').write_bytes(png())
        with patch.dict(os.environ,{'NAPCAT_WORKDIR':str(external)}):
            manager._clear_napcat_qr()
        self.assertFalse(self.path.exists())
        self.assertTrue((external/'cache'/'qrcode.png').exists())

    @unittest.skipUnless(os.name=='nt','Windows PID identity')
    def test_process_identity_check_rejects_recycled_pid(self):
        api=MagicMock()
        api.OpenProcess.return_value=5
        api.WaitForSingleObject.return_value=0x102
        def times(handle,created,*args):
            created._obj.dwHighDateTime=0
            created._obj.dwLowDateTime=123
            return True
        api.GetProcessTimes.side_effect=times
        with patch('news_agent.desktop.services.ctypes.WinDLL',return_value=api):
            self.assertFalse(ServiceManager._process_alive(process(self.now)))
        api.CloseHandle.assert_called_once_with(5)


class QrUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'config.json'
        self.path.write_text(json.dumps(fresh_config()),encoding='utf-8')
        self.original=self.path.read_bytes()
        self.window=ConfigWindow(self.path,monitor_services=False,cache_path=Path(self.temp.name)/'news.db')

    def tearDown(self):
        self.window.stop_monitoring()
        self.window.deleteLater()
        self.app.processEvents()
        self.assertEqual(self.path.read_bytes(),self.original)
        self.temp.cleanup()

    def show_qr(self,version=1):
        self.window.render_napcat_qr({'state':'qr','image':png(version),'version':version,'message':'测试图片','updated_at':time.time()})
        self.assertFalse(self.window.napcat_qr_image.pixmap().isNull())

    def test_initial_and_stopped_state_have_requested_placeholder(self):
        self.assertEqual(self.window.napcat_qr_image.text(),'暂未启动 NAPCAT 服务')
        self.show_qr()
        self.window.update_napcat_qr_source({'state':'stopped'}, {})
        self.assertEqual(self.window.napcat_qr_image.text(),'暂未启动 NAPCAT 服务')
        self.assertTrue(self.window.napcat_qr_image.pixmap().isNull())

    def test_old_async_result_cannot_restore_image_after_stop(self):
        epoch=self.window._napcat_qr_epoch
        self.window.update_napcat_qr_source({'state':'stopped'}, {})
        self.window.background_finished('napcat-qr',{'epoch':epoch,'result':{'state':'qr','image':png(),'version':1}})
        self.assertEqual(self.window.napcat_qr_image.text(),'暂未启动 NAPCAT 服务')

    def test_login_success_and_unknown_status_hide_sensitive_image(self):
        self.show_qr()
        self.window.update_napcat_qr_source({'state':'running','processes':[process(time.time())]}, {'ok':True})
        self.assertEqual(self.window.napcat_qr_image.text(),'QQ 已登录')
        self.assertTrue(self.window.napcat_qr_image.pixmap().isNull())
        self.window.update_napcat_qr_source({'state':'unknown'}, {})
        self.assertTrue(self.window.napcat_qr_image.pixmap().isNull())

    def test_failed_decode_clears_old_image(self):
        self.show_qr()
        self.window.render_napcat_qr({'state':'qr','image':b'invalid','version':2})
        self.assertTrue(self.window.napcat_qr_image.pixmap().isNull())

    def test_new_running_session_immediately_hides_previous_image(self):
        self.show_qr()
        self.window.update_napcat_qr_source({'state':'running','processes':[process(time.time())]}, {})
        self.assertTrue(self.window.napcat_qr_image.pixmap().isNull())
        self.assertIn('会话已更新',self.window.napcat_qr_message.text())

    def test_selected_napcat_restart_hides_old_image_and_invalidates_epoch(self):
        self.show_qr()
        old=self.window._napcat_qr_epoch
        self.window.service_selectors['backend'].setChecked(False)
        self.window.service_selectors['napcat'].setChecked(True)
        with patch.object(self.window,'run_background',return_value=True):
            self.window.control_selected_services('restart')
        self.assertGreater(self.window._napcat_qr_epoch,old)
        self.assertTrue(self.window.napcat_qr_image.pixmap().isNull())
        self.assertIn('等待新二维码',self.window.napcat_qr_message.text())

    def test_image_reader_failure_hides_previous_picture(self):
        self.show_qr()
        self.window._napcat_qr_status={'state':'running','processes':[process(time.time())]}
        jobs=[]
        with patch.object(self.window,'run_background',side_effect=lambda name,callback:jobs.append(callback) or True):
            self.window.refresh_napcat_qr()
        with patch.object(self.window.service_manager,'napcat_login_qr',side_effect=OSError):
            self.window.background_finished('napcat-qr',jobs[0]())
        self.assertTrue(self.window.napcat_qr_image.pixmap().isNull())

    def test_qr_card_stacks_on_narrow_window(self):
        self.window.show()
        self.window.resize(1060,850)
        self.app.processEvents()
        self.assertEqual(self.window.service_top_layout.direction(),QBoxLayout.Direction.TopToBottom)
        self.window.resize(1300,850)
        self.app.processEvents()
        self.assertEqual(self.window.service_top_layout.direction(),QBoxLayout.Direction.LeftToRight)

    def test_close_stops_qr_poll_timer(self):
        self.window.napcat_qr_timer.start()
        self.window.stop_monitoring()
        self.assertFalse(self.window.napcat_qr_timer.isActive())


if __name__=='__main__':
    unittest.main()
