#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
App Volume Muter - PyQt6 GUI Application with QSS Styling
Requirements: PyQt6, pycaw, comtypes, keyboard
"""

import sys
import json
import os
import threading
from typing import Dict, List, Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QGroupBox, QLineEdit, QCheckBox, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QKeySequence, QFont

# Windows Audio APIs
try:
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    from comtypes import CLSCTX_ALL
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False

# Global hotkey
try:
    import keyboard
    HOTKEY_AVAILABLE = True
except ImportError:
    HOTKEY_AVAILABLE = False


CONFIG_FILE = "config.json"


class ConfigManager:
    """管理 config.json 的读写"""
    
    DEFAULT_CONFIG = {
        "hotkey": "ctrl+shift+m",
        "apps": {}  # { "process_name.exe": {"enabled": true, "last_mute": false} }
    }
    
    def __init__(self, filepath: str = CONFIG_FILE):
        self.filepath = filepath
        self.config = self.load()
    
    def load(self) -> dict:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    # 合并默认配置，防止缺少字段
                    merged = self.DEFAULT_CONFIG.copy()
                    merged.update(loaded)
                    return merged
            except Exception as e:
                print(f"加载配置失败: {e}")
        return self.DEFAULT_CONFIG.copy()
    
    def save(self) -> bool:
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False
    
    def get_hotkey(self) -> str:
        return self.config.get("hotkey", "ctrl+shift+m")
    
    def set_hotkey(self, hotkey: str):
        self.config["hotkey"] = hotkey
    
    def get_apps(self) -> Dict[str, dict]:
        return self.config.get("apps", {})
    
    def set_app_enabled(self, process_name: str, enabled: bool):
        if "apps" not in self.config:
            self.config["apps"] = {}
        if process_name not in self.config["apps"]:
            self.config["apps"][process_name] = {}
        self.config["apps"][process_name]["enabled"] = enabled
    
    def remove_app(self, process_name: str):
        if process_name in self.config.get("apps", {}):
            del self.config["apps"][process_name]


class AudioController:
    """使用 pycaw 控制应用音量"""
    
    @staticmethod
    def get_audio_sessions() -> List[dict]:
        """获取所有音频会话"""
        if not AUDIO_AVAILABLE:
            return []
        
        sessions = []
        try:
            for session in AudioUtilities.GetAllSessions():
                process = session.Process
                if process is None:
                    continue
                
                volume = session.SimpleAudioVolume
                sessions.append({
                    "name": process.name(),
                    "pid": process.pid,
                    "mute": volume.GetMute(),
                    "volume": volume.GetMasterVolume(),
                    "session": session,
                    "volume_interface": volume
                })
        except Exception as e:
            print(f"获取音频会话失败: {e}")
        
        return sessions
    
    @staticmethod
    def set_app_mute(process_name: str, mute: bool) -> bool:
        """设置指定应用的静音状态"""
        if not AUDIO_AVAILABLE:
            return False
        
        try:
            for session in AudioUtilities.GetAllSessions():
                process = session.Process
                if process and process.name().lower() == process_name.lower():
                    volume = session.SimpleAudioVolume
                    volume.SetMute(mute, None)
                    return True
        except Exception as e:
            print(f"设置静音失败 [{process_name}]: {e}")
        return False
    
    @staticmethod
    def toggle_app_mute(process_name: str) -> Optional[bool]:
        """切换指定应用的静音状态，返回新状态"""
        if not AUDIO_AVAILABLE:
            return None
        
        try:
            for session in AudioUtilities.GetAllSessions():
                process = session.Process
                if process and process.name().lower() == process_name.lower():
                    volume = session.SimpleAudioVolume
                    current = volume.GetMute()
                    volume.SetMute(not current, None)
                    return not current
        except Exception as e:
            print(f"切换静音失败 [{process_name}]: {e}")
        return None


class HotkeySignals(QObject):
    """用于线程间通信的信号"""
    triggered = pyqtSignal()


class HotkeyListenerThread(QThread):
    """在后台线程监听全局热键"""
    
    def __init__(self, hotkey: str):
        super().__init__()
        self.hotkey = hotkey
        self.signals = HotkeySignals()
        self._running = True
        self._handler = None
    
    def run(self):
        if not HOTKEY_AVAILABLE:
            return
        
        try:
            # 注册热键，回调中发射信号到主线程
            self._handler = keyboard.add_hotkey(
                self.hotkey, 
                lambda: self.signals.triggered.emit()
            )
            # 保持线程存活
            while self._running:
                self.msleep(100)
        except Exception as e:
            print(f"热键监听错误: {e}")
    
    def stop(self):
        self._running = False
        if self._handler and HOTKEY_AVAILABLE:
            try:
                keyboard.remove_hotkey(self._handler)
            except:
                pass
        self.wait(1000)
    
    def update_hotkey(self, new_hotkey: str):
        """更新热键，重新注册"""
        if self._handler and HOTKEY_AVAILABLE:
            try:
                keyboard.remove_hotkey(self._handler)
            except:
                pass
        
        self.hotkey = new_hotkey
        try:
            self._handler = keyboard.add_hotkey(
                self.hotkey,
                lambda: self.signals.triggered.emit()
            )
        except Exception as e:
            print(f"更新热键失败: {e}")


class HotkeyCaptureButton(QPushButton):
    """可捕获按键组合的按钮"""
    
    captured = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__("点击设置热键", parent)
        self.setCheckable(True)
        self.capturing = False
        self.pressed_keys = set()
        self.clicked.connect(self.on_click)
        self.setMinimumWidth(150)
    
    def on_click(self):
        if self.isChecked():
            self.capturing = True
            self.setText("请按下按键组合...")
            self.setStyleSheet("""
                QPushButton {
                    background-color: #f59e0b;
                    color: #ffffff;
                    border: 2px solid #d97706;
                    font-weight: bold;
                }
            """)
            self.grabKeyboard()
        else:
            self.cancel_capture()
    
    def cancel_capture(self):
        self.capturing = False
        self.setChecked(False)
        self.setText("点击设置热键")
        self.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: #ffffff;
                border: 1px solid #2563eb;
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
        """)
        self.releaseKeyboard()
        self.pressed_keys.clear()
    
    def keyPressEvent(self, event):
        if not self.capturing:
            super().keyPressEvent(event)
            return
        
        # 忽略单独的修饰键，等待组合键
        key = event.key()
        mod = event.modifiers()
        
        # 构建按键名称列表
        keys = []
        
        # 修饰键
        if mod & Qt.KeyboardModifier.ControlModifier:
            keys.append("ctrl")
        if mod & Qt.KeyboardModifier.ShiftModifier:
            keys.append("shift")
        if mod & Qt.KeyboardModifier.AltModifier:
            keys.append("alt")
        if mod & Qt.KeyboardModifier.MetaModifier:
            keys.append("win")
        
        # 普通键
        key_name = QKeySequence(key).toString().lower().strip()
        if key_name and key_name not in ['ctrl', 'shift', 'alt', 'meta']:
            keys.append(key_name)
        elif 48 <= key <= 57:  # 0-9
            keys.append(chr(key).lower())
        elif 65 <= key <= 90:  # A-Z
            keys.append(chr(key).lower())
        elif 112 <= key <= 123:  # F1-F12
            keys.append(f"f{key - 111}")
        
        if len(keys) >= 2 or (len(keys) == 1 and keys[0] not in ['ctrl', 'shift', 'alt', 'win']):
            hotkey_str = "+".join(keys)
            self.captured.emit(hotkey_str)
            self.cancel_capture()
        else:
            # 只按了修饰键，继续等待
            pass
    
    def keyReleaseEvent(self, event):
        if not self.capturing:
            super().keyReleaseEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("应用音量静音控制器")
        self.setMinimumSize(720, 520)
        
        # 初始化组件
        self.config = ConfigManager()
        self.hotkey_thread: Optional[HotkeyListenerThread] = None
        
        self.init_ui()
        self.apply_styles()
        self.start_hotkey_listener()
        self.refresh_app_list()
    
    def apply_styles(self):
        """应用 QSS 样式表"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f3f4f6;
            }
            
            QGroupBox {
                font-weight: bold;
                font-size: 13px;
                color: #1f2937;
                border: 1px solid #d1d5db;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 12px;
                padding-bottom: 8px;
                padding-left: 12px;
                padding-right: 12px;
                background-color: #ffffff;
            }
            
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: #374151;
            }
            
            QLabel {
                color: #374151;
                font-size: 13px;
            }
            
            QLineEdit {
                background-color: #f9fafb;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 6px 10px;
                color: #111827;
                font-size: 13px;
                selection-background-color: #3b82f6;
            }
            
            QLineEdit:focus {
                border: 2px solid #3b82f6;
            }
            
            QPushButton {
                background-color: #3b82f6;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 7px 18px;
                font-size: 13px;
                font-weight: 500;
            }
            
            QPushButton:hover {
                background-color: #2563eb;
            }
            
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
            
            QPushButton:disabled {
                background-color: #9ca3af;
                color: #e5e7eb;
            }
            
            QTableWidget {
                background-color: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                gridline-color: #f3f4f6;
                font-size: 13px;
                color: #1f2937;
            }
            
            QTableWidget::item {
                padding: 6px;
                border-bottom: 1px solid #f3f4f6;
            }
            
            QTableWidget::item:selected {
                background-color: #dbeafe;
                color: #1e40af;
            }
            
            QHeaderView::section {
                background-color: #f9fafb;
                color: #374151;
                padding: 8px;
                border: none;
                border-bottom: 2px solid #e5e7eb;
                font-weight: 600;
                font-size: 12px;
            }
            
            QCheckBox {
                color: #374151;
                font-size: 13px;
                spacing: 6px;
            }
            
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid #d1d5db;
                background-color: #ffffff;
            }
            
            QCheckBox::indicator:checked {
                background-color: #3b82f6;
                border-color: #3b82f6;
            }
            
            QCheckBox::indicator:hover {
                border-color: #3b82f6;
            }
            
            QScrollBar:vertical {
                background-color: #f9fafb;
                width: 10px;
                border-radius: 5px;
            }
            
            QScrollBar::handle:vertical {
                background-color: #d1d5db;
                border-radius: 5px;
                min-height: 30px;
            }
            
            QScrollBar::handle:vertical:hover {
                background-color: #9ca3af;
            }
            
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
    
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(15)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # === 热键配置区域 ===
        hotkey_group = QGroupBox("全局热键配置")
        hotkey_layout = QHBoxLayout(hotkey_group)
        hotkey_layout.setSpacing(12)
        
        hotkey_layout.addWidget(QLabel("当前热键:"))
        self.hotkey_display = QLineEdit(self.config.get_hotkey())
        self.hotkey_display.setReadOnly(True)
        self.hotkey_display.setMinimumWidth(160)
        self.hotkey_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hotkey_layout.addWidget(self.hotkey_display)
        
        self.capture_btn = HotkeyCaptureButton()
        self.capture_btn.captured.connect(self.on_hotkey_captured)
        hotkey_layout.addWidget(self.capture_btn)
        
        hotkey_layout.addStretch()
        layout.addWidget(hotkey_group)
        
        # === 应用列表区域 ===
        app_group = QGroupBox("应用音量控制 (勾选以启用静音控制)")
        app_layout = QVBoxLayout(app_group)
        app_layout.setSpacing(10)
        
        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        
        self.refresh_btn = QPushButton("刷新应用列表")
        self.refresh_btn.setMinimumWidth(120)
        self.refresh_btn.clicked.connect(self.refresh_app_list)
        toolbar.addWidget(self.refresh_btn)
        
        self.mute_all_btn = QPushButton("一键静音已启用")
        self.mute_all_btn.setMinimumWidth(130)
        self.mute_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #ef4444;
            }
            QPushButton:hover {
                background-color: #dc2626;
            }
            QPushButton:pressed {
                background-color: #b91c1c;
            }
        """)
        self.mute_all_btn.clicked.connect(self.mute_all_enabled)
        toolbar.addWidget(self.mute_all_btn)
        
        self.unmute_all_btn = QPushButton("一键取消静音")
        self.unmute_all_btn.setMinimumWidth(130)
        self.unmute_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #10b981;
            }
            QPushButton:hover {
                background-color: #059669;
            }
            QPushButton:pressed {
                background-color: #047857;
            }
        """)
        self.unmute_all_btn.clicked.connect(self.unmute_all_enabled)
        toolbar.addWidget(self.unmute_all_btn)
        
        toolbar.addStretch()
        app_layout.addLayout(toolbar)
        
        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["启用", "应用名称", "当前状态", "PID"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(3, 80)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        app_layout.addWidget(self.table)
        
        layout.addWidget(app_group, stretch=1)
        
        # === 底部操作栏 ===
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        bottom.addWidget(self.status_label)
        
        bottom.addStretch()
        
        self.save_btn = QPushButton("保存配置")
        self.save_btn.setMinimumWidth(120)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366f1;
            }
            QPushButton:hover {
                background-color: #4f46e5;
            }
            QPushButton:pressed {
                background-color: #4338ca;
            }
        """)
        self.save_btn.clicked.connect(self.save_config)
        bottom.addWidget(self.save_btn)
        
        layout.addLayout(bottom)
        
        # 状态栏
        if not AUDIO_AVAILABLE:
            self.status_label.setText("未安装 pycaw，音量控制不可用 (pip install pycaw)")
            self.status_label.setStyleSheet("color: #dc2626; font-weight: bold;")
        elif not HOTKEY_AVAILABLE:
            self.status_label.setText("未安装 keyboard，全局热键不可用 (pip install keyboard)")
            self.status_label.setStyleSheet("color: #dc2626; font-weight: bold;")
    
    def start_hotkey_listener(self):
        """启动热键监听线程"""
        if not HOTKEY_AVAILABLE:
            return
        
        if self.hotkey_thread and self.hotkey_thread.isRunning():
            self.hotkey_thread.stop()
        
        self.hotkey_thread = HotkeyListenerThread(self.config.get_hotkey())
        self.hotkey_thread.signals.triggered.connect(self.on_global_hotkey)
        self.hotkey_thread.start()
    
    def on_global_hotkey(self):
        """全局热键触发：切换所有启用应用静音"""
        apps = self.config.get_apps()
        enabled_apps = [name for name, info in apps.items() if info.get("enabled", False)]
        
        if not enabled_apps:
            self.status_label.setText("没有启用的应用")
            self.status_label.setStyleSheet("color: #f59e0b; font-weight: bold;")
            return
        
        # 获取第一个启用应用当前状态，然后统一设置
        first_state = None
        toggled_count = 0
        
        for app_name in enabled_apps:
            if first_state is None:
                # 查询当前状态
                sessions = AudioController.get_audio_sessions()
                for s in sessions:
                    if s["name"].lower() == app_name.lower():
                        first_state = not s["mute"]  # 要切换到的目标状态
                        break
            
            target_mute = first_state if first_state is not None else True
            if AudioController.set_app_mute(app_name, target_mute):
                toggled_count += 1
        
        action = "静音" if (first_state is True) else "取消静音"
        self.status_label.setText(f"热键触发: {action}了 {toggled_count} 个应用")
        self.status_label.setStyleSheet("color: #059669; font-weight: bold;")
        self.refresh_app_list()  # 刷新显示
    
    def on_hotkey_captured(self, hotkey_str: str):
        """捕获到新的热键"""
        self.hotkey_display.setText(hotkey_str)
        self.config.set_hotkey(hotkey_str)
        
        # 重新注册热键
        if self.hotkey_thread:
            self.hotkey_thread.update_hotkey(hotkey_str)
        
        self.status_label.setText(f"热键已设置为: {hotkey_str}")
        self.status_label.setStyleSheet("color: #3b82f6; font-weight: bold;")
    
    def refresh_app_list(self):
        """刷新应用列表"""
        self.table.setRowCount(0)
        sessions = AudioController.get_audio_sessions()
        config_apps = self.config.get_apps()
        
        # 合并当前运行的应用和已配置的应用
        all_apps: Dict[str, dict] = {}
        
        # 先加入运行中的应用
        for s in sessions:
            name = s["name"]
            all_apps[name] = {
                "running": True,
                "pid": s["pid"],
                "mute": s["mute"],
                "enabled": config_apps.get(name, {}).get("enabled", False)
            }
        
        # 再加入配置中但当前未运行的应用
        for name, info in config_apps.items():
            if name not in all_apps:
                all_apps[name] = {
                    "running": False,
                    "pid": "-",
                    "mute": False,
                    "enabled": info.get("enabled", False)
                }
        
        # 填充表格
        for row, (name, data) in enumerate(sorted(all_apps.items())):
            self.table.insertRow(row)
            
            # 启用复选框
            chk = QCheckBox()
            chk.setChecked(data["enabled"])
            chk.stateChanged.connect(lambda state, n=name: self.on_app_check_changed(n, state))
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.addWidget(chk)
            chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 0, chk_widget)
            
            # 应用名称
            name_item = QTableWidgetItem(name)
            if not data["running"]:
                name_item.setForeground(Qt.GlobalColor.gray)
                name_item.setToolTip("当前未运行")
            self.table.setItem(row, 1, name_item)
            
            # 状态
            if data["running"]:
                status = "已静音" if data["mute"] else "正常"
                status_color = "#dc2626" if data["mute"] else "#059669"
            else:
                status = "未运行"
                status_color = "#9ca3af"
            status_item = QTableWidgetItem(status)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setForeground(Qt.GlobalColor.gray if not data["running"] else (Qt.GlobalColor.red if data["mute"] else Qt.GlobalColor.darkGreen))
            self.table.setItem(row, 2, status_item)
            
            # PID
            pid_item = QTableWidgetItem(str(data["pid"]))
            pid_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, pid_item)
    
    def on_app_check_changed(self, process_name: str, state: int):
        """应用勾选状态变化"""
        enabled = (state == Qt.CheckState.Checked.value)
        self.config.set_app_enabled(process_name, enabled)
    
    def mute_all_enabled(self):
        """静音所有启用的应用"""
        apps = self.config.get_apps()
        count = 0
        for name, info in apps.items():
            if info.get("enabled", False):
                if AudioController.set_app_mute(name, True):
                    count += 1
        self.status_label.setText(f"已静音 {count} 个应用")
        self.status_label.setStyleSheet("color: #dc2626; font-weight: bold;")
        self.refresh_app_list()
    
    def unmute_all_enabled(self):
        """取消静音所有启用的应用"""
        apps = self.config.get_apps()
        count = 0
        for name, info in apps.items():
            if info.get("enabled", False):
                if AudioController.set_app_mute(name, False):
                    count += 1
        self.status_label.setText(f"已取消静音 {count} 个应用")
        self.status_label.setStyleSheet("color: #059669; font-weight: bold;")
        self.refresh_app_list()
    
    def save_config(self):
        """保存配置到 JSON"""
        if self.config.save():
            self.status_label.setText(f"配置已保存到 {CONFIG_FILE}")
            self.status_label.setStyleSheet("color: #6366f1; font-weight: bold;")
            QMessageBox.information(self, "保存成功", f"配置已保存到 {os.path.abspath(CONFIG_FILE)}")
        else:
            QMessageBox.critical(self, "保存失败", "无法写入配置文件")
    
    def closeEvent(self, event):
        """关闭时清理"""
        if self.hotkey_thread:
            self.hotkey_thread.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    
    # 设置中文字体
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()