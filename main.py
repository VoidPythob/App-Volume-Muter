#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
App Volume Muter - PyQt6 GUI Application with QSS Styling
支持全局模式和单独模式，可配置每个应用的独立热键
Requirements: PyQt6, pycaw, comtypes, keyboard
"""

import argparse
from pathlib import Path
import sys
import json
import os
import logging
import time
from typing import Dict, List, Optional

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QGroupBox,
    QLineEdit,
    QCheckBox,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QKeySequence, QFont

# Windows Audio APIs
try:
    from pycaw.pycaw import AudioUtilities

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
LOGGER = logging.getLogger(__name__)

class ConfigManager:
    """管理 config.json 的读写"""

    DEFAULT_CONFIG = {
        "mode": "global",  # "global" 或 "individual"
        "hotkey": "ctrl+shift+m",
        "apps": {},  # { "process_name.exe": {"enabled": false, "individual_hotkey": null} }
    }

    def __init__(self, filepath: str = CONFIG_FILE):
        self.filepath = filepath
        self.config = self.load()

    def load(self) -> dict:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    merged = self.DEFAULT_CONFIG.copy()
                    merged.update(loaded)
                    # 确保 apps 中的每个项都有完整字段
                    if "apps" in merged:
                        for _, app_data in merged["apps"].items():
                            if "enabled" not in app_data:
                                app_data["enabled"] = False
                            if "individual_hotkey" not in app_data:
                                app_data["individual_hotkey"] = None
                    return merged
            except Exception as e:
                LOGGER.warning(f"加载配置失败: {e}")
        return self.DEFAULT_CONFIG.copy()

    def save(self) -> bool:
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            LOGGER.warning(f"保存配置失败: {e}")
            return False

    def get_mode(self) -> str:
        return self.config.get("mode", "global")

    def set_mode(self, mode: str):
        self.config["mode"] = mode

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

    def get_app_individual_hotkey(self, process_name: str) -> Optional[str]:
        return (
            self.config.get("apps", {}).get(process_name, {}).get("individual_hotkey")
        )

    def set_app_individual_hotkey(self, process_name: str, hotkey: Optional[str]):
        if "apps" not in self.config:
            self.config["apps"] = {}
        if process_name not in self.config["apps"]:
            self.config["apps"][process_name] = {}
        self.config["apps"][process_name]["individual_hotkey"] = hotkey

    def remove_app(self, process_name: str):
        if process_name in self.config.get("apps", {}):
            del self.config["apps"][process_name]


class AudioController:
    """使用 pycaw 控制应用音量"""

    @staticmethod
    def get_audio_sessions() -> List[dict]:
        if not AUDIO_AVAILABLE:
            return []
        sessions = []
        try:
            for session in AudioUtilities.GetAllSessions():
                process = session.Process
                if process is None:
                    continue
                volume = session.SimpleAudioVolume
                sessions.append(
                    {
                        "name": process.name(),
                        "pid": process.pid,
                        "mute": volume.GetMute(),
                        "volume": volume.GetMasterVolume(),
                        "session": session,
                        "volume_interface": volume,
                    }
                )
        except Exception as e:
            LOGGER.warning(f"获取音频会话失败: {e}")
        return sessions

    @staticmethod
    def set_app_mute(process_name: str, mute: bool) -> bool:
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
            LOGGER.warning(f"设置静音失败 [{process_name}]: {e}")
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
            LOGGER.warning(f"切换静音失败 [{process_name}]: {e}")
        return None


class HotkeySignals(QObject):
    triggered = pyqtSignal(str)  # "global" 或 process_name


class HotkeyListenerThread(QThread):
    """在后台线程监听全局热键，支持全局模式和单独模式"""

    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.config = config_manager
        self.signals = HotkeySignals()
        self._running = True
        self._handlers = []

    def run(self):
        if not HOTKEY_AVAILABLE:
            return
        self._register_hotkeys()
        while self._running:
            self.msleep(100)

    def _register_hotkeys(self):
        """根据当前配置注册热键"""
        # 清除旧热键
        for h in self._handlers:
            try:
                keyboard.remove_hotkey(h)
            except:
                pass
        self._handlers = []

        mode = self.config.get_mode()

        if mode == "global":
            hotkey = self.config.get_hotkey()
            if hotkey:
                try:
                    h = keyboard.add_hotkey(
                        hotkey, lambda: self.signals.triggered.emit("global")
                    )
                    self._handlers.append(h)
                except Exception as e:
                    LOGGER.warning(f"注册全局热键失败: {e}")
        else:
            # 单独模式：为每个启用的应用注册热键
            for name, info in self.config.get_apps().items():
                if info.get("enabled") and info.get("individual_hotkey"):
                    hk = info["individual_hotkey"]
                    try:
                        # 使用默认参数捕获 name，避免闭包问题
                        h = keyboard.add_hotkey(
                            hk, lambda n=name: self.signals.triggered.emit(n)
                        )
                        self._handlers.append(h)
                    except Exception as e:
                        LOGGER.warning(f"注册单独热键失败 [{name}]: {e}")

    def stop(self):
        self._running = False
        for h in self._handlers:
            try:
                keyboard.remove_hotkey(h)
            except:
                pass
        self._handlers = []
        self.wait(1000)

    def refresh(self):
        """重新注册所有热键（配置变化时调用）"""
        if not HOTKEY_AVAILABLE or not self._running:
            return
        for h in self._handlers:
            try:
                keyboard.remove_hotkey(h)
            except:
                pass
        self._handlers = []
        self._register_hotkeys()


class HotkeyCaptureButton(QPushButton):
    """可捕获按键组合的按钮 - 修复撑大盒子问题"""

    captured = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__("点击设置", parent)
        self.setCheckable(True)
        self.capturing = False
        # 固定尺寸，防止撑大父布局
        self.setFixedWidth(130)
        self.setFixedHeight(28)
        self.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: #ffffff;
                border: 1px solid #2563eb;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
        """)
        self.clicked.connect(self.on_click)

    def on_click(self):
        if self.isChecked():
            self.capturing = True
            self.setText("按组合键...")
            self.setStyleSheet("""
                QPushButton {
                    background-color: #f59e0b;
                    color: #ffffff;
                    border: 2px solid #d97706;
                    border-radius: 4px;
                    padding: 1px 5px;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)
            self.grabKeyboard()
        else:
            self.cancel_capture()

    def cancel_capture(self):
        self.capturing = False
        self.setChecked(False)
        self.setText("点击设置")
        self.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: #ffffff;
                border: 1px solid #2563eb;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 12px;
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

    def set_hotkey_text(self, text: Optional[str]):
        """设置显示的热键文本（非捕获状态）"""
        if not self.capturing:
            display = text if text else "点击设置"
            self.setText(display)
            self.setToolTip(text if text else "点击设置快捷键")

    def keyPressEvent(self, event):
        if not self.capturing:
            super().keyPressEvent(event)
            return

        key = event.key()
        mod = event.modifiers()
        keys = []

        if mod & Qt.KeyboardModifier.ControlModifier:
            keys.append("ctrl")
        if mod & Qt.KeyboardModifier.ShiftModifier:
            keys.append("shift")
        if mod & Qt.KeyboardModifier.AltModifier:
            keys.append("alt")
        if mod & Qt.KeyboardModifier.MetaModifier:
            keys.append("win")

        key_name = QKeySequence(key).toString().lower().strip()
        if key_name and key_name not in ["ctrl", "shift", "alt", "meta"]:
            keys.append(key_name)
        elif 48 <= key <= 57:
            keys.append(chr(key).lower())
        elif 65 <= key <= 90:
            keys.append(chr(key).lower())
        elif 112 <= key <= 123:
            keys.append(f"f{key - 111}")

        if len(keys) >= 2 or (
            len(keys) == 1 and keys[0] not in ["ctrl", "shift", "alt", "win"]
        ):
            hotkey_str = "+".join(keys)
            self.captured.emit(hotkey_str)
            self.cancel_capture()

    def keyReleaseEvent(self, event):
        if not self.capturing:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        """失去焦点时自动取消捕获，避免卡住"""
        if self.capturing:
            self.cancel_capture()
        super().focusOutEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("应用音量静音控制器")
        self.setMinimumSize(820, 560)

        self.config = ConfigManager()
        self.hotkey_thread: Optional[HotkeyListenerThread] = None

        self.init_ui()
        self.apply_styles()
        self.start_hotkey_listener()
        self.refresh_app_list()
        self.update_mode_ui()

    def apply_styles(self):
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
                padding: 4px;
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
        hotkey_group = QGroupBox("热键配置")
        hotkey_layout = QHBoxLayout(hotkey_group)
        hotkey_layout.setSpacing(12)

        # 模式切换按钮
        self.mode_btn = QPushButton()
        self.mode_btn.setFixedWidth(120)
        self.mode_btn.setFixedHeight(34)
        self.mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_btn.clicked.connect(self.toggle_mode)
        hotkey_layout.addWidget(self.mode_btn)

        hotkey_layout.addWidget(QLabel("全局热键:"))
        self.hotkey_display = QLineEdit(self.config.get_hotkey())
        self.hotkey_display.setReadOnly(True)
        self.hotkey_display.setFixedWidth(160)
        self.hotkey_display.setFixedHeight(32)
        self.hotkey_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hotkey_layout.addWidget(self.hotkey_display)

        self.capture_btn = HotkeyCaptureButton()
        self.capture_btn.setFixedWidth(130)
        self.capture_btn.setFixedHeight(32)
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
        self.refresh_btn.setFixedWidth(120)
        self.refresh_btn.setFixedHeight(34)
        self.refresh_btn.clicked.connect(self.refresh_app_list)
        toolbar.addWidget(self.refresh_btn)

        self.mute_all_btn = QPushButton("一键静音已启用")
        self.mute_all_btn.setFixedWidth(130)
        self.mute_all_btn.setFixedHeight(34)
        self.mute_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #ef4444;
                border-radius: 6px;
                padding: 7px 18px;
                font-size: 13px;
                font-weight: 500;
                color: #ffffff;
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
        self.unmute_all_btn.setFixedWidth(130)
        self.unmute_all_btn.setFixedHeight(34)
        self.unmute_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #10b981;
                border-radius: 6px;
                padding: 7px 18px;
                font-size: 13px;
                font-weight: 500;
                color: #ffffff;
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
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["启用", "应用名称", "当前状态", "PID", "单独热键"]
        )

        self.table.horizontalHeader().setSectionResizeMode( # type: ignore
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode( # type: ignore
            2, QHeaderView.ResizeMode.Fixed
        )
        self.table.horizontalHeader().setSectionResizeMode( # type: ignore
            4, QHeaderView.ResizeMode.Fixed
        )
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 150)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False) # type: ignore
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
        self.save_btn.setFixedWidth(120)
        self.save_btn.setFixedHeight(34)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366f1;
                border-radius: 6px;
                padding: 7px 18px;
                font-size: 13px;
                font-weight: 500;
                color: #ffffff;
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
            self.status_label.setText(
                "未安装 pycaw，音量控制不可用 (pip install pycaw)"
            )
            self.status_label.setStyleSheet("color: #dc2626; font-weight: bold;")
        elif not HOTKEY_AVAILABLE:
            self.status_label.setText(
                "未安装 keyboard，全局热键不可用 (pip install keyboard)"
            )
            self.status_label.setStyleSheet("color: #dc2626; font-weight: bold;")

    def update_mode_ui(self):
        """根据当前模式更新 UI 状态"""
        mode = self.config.get_mode()
        is_global = mode == "global"

        # 模式按钮
        self.mode_btn.setText("全局模式" if is_global else "单独模式")
        self.mode_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {'#3b82f6' if is_global else '#8b5cf6'};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {'#2563eb' if is_global else '#7c3aed'};
            }}
        """)

        # 全局热键区域
        self.hotkey_display.setEnabled(is_global)
        self.capture_btn.setEnabled(is_global)
        if not is_global:
            self.capture_btn.setStyleSheet("""
                QPushButton {
                    background-color: #d1d5db;
                    color: #9ca3af;
                    border: 1px solid #d1d5db;
                    border-radius: 4px;
                    padding: 2px 6px;
                    font-size: 12px;
                    font-weight: 500;
                }
            """)
        else:
            self.capture_btn.setStyleSheet("""
                QPushButton {
                    background-color: #3b82f6;
                    color: #ffffff;
                    border: 1px solid #2563eb;
                    border-radius: 4px;
                    padding: 2px 6px;
                    font-size: 12px;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background-color: #2563eb;
                }
                QPushButton:pressed {
                    background-color: #1d4ed8;
                }
            """)

    def toggle_mode(self):
        """切换全局/单独模式"""
        current = self.config.get_mode()
        new_mode = "individual" if current == "global" else "global"
        self.config.set_mode(new_mode)
        self.update_mode_ui()
        self.refresh_app_list()
        if self.hotkey_thread:
            self.hotkey_thread.refresh()
        self.auto_save()
        self.status_label.setText(
            f"已切换到{'单独' if new_mode == 'individual' else '全局'}模式"
        )
        self.status_label.setStyleSheet("color: #3b82f6; font-weight: bold;")

    def start_hotkey_listener(self):
        if not HOTKEY_AVAILABLE:
            return
        if self.hotkey_thread and self.hotkey_thread.isRunning():
            self.hotkey_thread.stop()
        self.hotkey_thread = HotkeyListenerThread(self.config)
        self.hotkey_thread.signals.triggered.connect(self.on_hotkey_triggered)
        self.hotkey_thread.start()

    def on_hotkey_triggered(self, target: str):
        if target == "global":
            self.on_global_hotkey()
        else:
            self.on_individual_hotkey(target)

    def on_global_hotkey(self):
        apps = self.config.get_apps()
        enabled_apps = [
            name for name, info in apps.items() if info.get("enabled", False)
        ]
        if not enabled_apps:
            self.status_label.setText("没有启用的应用")
            self.status_label.setStyleSheet("color: #f59e0b; font-weight: bold;")
            return

        first_state = None
        toggled_count = 0
        for app_name in enabled_apps:
            if first_state is None:
                sessions = AudioController.get_audio_sessions()
                for s in sessions:
                    if s["name"].lower() == app_name.lower():
                        first_state = not s["mute"]
                        break
            target_mute = first_state if first_state is not None else True
            if AudioController.set_app_mute(app_name, target_mute):
                toggled_count += 1

        action = "静音" if (first_state is True) else "取消静音"
        self.status_label.setText(f"热键触发: {action}了 {toggled_count} 个应用")
        self.status_label.setStyleSheet("color: #059669; font-weight: bold;")
        self.refresh_app_list()

    def on_individual_hotkey(self, process_name: str):
        result = AudioController.toggle_app_mute(process_name)
        if result is not None:
            action = "静音" if result else "取消静音"
            self.status_label.setText(f"[{process_name}] 已{action}")
            self.status_label.setStyleSheet("color: #059669; font-weight: bold;")
        else:
            self.status_label.setText(f"[{process_name}] 未运行或无法静音")
            self.status_label.setStyleSheet("color: #dc2626; font-weight: bold;")
        self.refresh_app_list()

    def on_hotkey_captured(self, hotkey_str: str):
        self.hotkey_display.setText(hotkey_str)
        self.config.set_hotkey(hotkey_str)
        if self.hotkey_thread:
            self.hotkey_thread.refresh()
        self.auto_save()
        self.status_label.setText(f"全局热键已设置为: {hotkey_str}")
        self.status_label.setStyleSheet("color: #3b82f6; font-weight: bold;")

    def on_individual_hotkey_captured(self, process_name: str, hotkey_str: str):
        self.config.set_app_individual_hotkey(process_name, hotkey_str)
        if self.hotkey_thread:
            self.hotkey_thread.refresh()
        self.auto_save()
        self.status_label.setText(f"[{process_name}] 单独热键: {hotkey_str}")
        self.status_label.setStyleSheet("color: #3b82f6; font-weight: bold;")
        self.refresh_app_list()

    def refresh_app_list(self):
        self.table.setRowCount(0)
        sessions = AudioController.get_audio_sessions()
        config_apps = self.config.get_apps()
        is_global = self.config.get_mode() == "global"

        all_apps = {}
        for s in sessions:
            name = s["name"]
            all_apps[name] = {
                "running": True,
                "pid": s["pid"],
                "mute": s["mute"],
                "enabled": config_apps.get(name, {}).get("enabled", False),
                "individual_hotkey": config_apps.get(name, {}).get("individual_hotkey"),
            }

        for name, info in config_apps.items():
            if name not in all_apps:
                all_apps[name] = {
                    "running": False,
                    "pid": "-",
                    "mute": False,
                    "enabled": info.get("enabled", False),
                    "individual_hotkey": info.get("individual_hotkey"),
                }

        for row, (name, data) in enumerate(sorted(all_apps.items())):
            self.table.insertRow(row)
            self.table.setRowHeight(row, 38)

            # 启用复选框
            chk = QCheckBox()
            chk.setChecked(data["enabled"])
            chk.stateChanged.connect(
                lambda state, n=name: self.on_app_check_changed(n, state)
            )
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
            else:
                status = "未运行"
            status_item = QTableWidgetItem(status)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if not data["running"]:
                status_item.setForeground(Qt.GlobalColor.gray)
            elif data["mute"]:
                status_item.setForeground(Qt.GlobalColor.red)
            else:
                status_item.setForeground(Qt.GlobalColor.darkGreen)
            self.table.setItem(row, 2, status_item)

            # PID
            pid_item = QTableWidgetItem(str(data["pid"]))
            pid_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, pid_item)

            # 单独热键
            hotkey_btn = HotkeyCaptureButton()
            hotkey_btn.set_hotkey_text(data.get("individual_hotkey") or "")
            if is_global:
                hotkey_btn.setEnabled(False)
                hotkey_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #e5e7eb;
                        color: #9ca3af;
                        border: 1px solid #d1d5db;
                        border-radius: 4px;
                        padding: 2px 6px;
                        font-size: 12px;
                        font-weight: 500;
                    }
                """)
            else:
                hotkey_btn.captured.connect(
                    lambda hk, n=name: self.on_individual_hotkey_captured(n, hk)
                )

            hotkey_widget = QWidget()
            hotkey_layout = QHBoxLayout(hotkey_widget)
            hotkey_layout.addWidget(hotkey_btn)
            hotkey_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hotkey_layout.setContentsMargins(4, 2, 4, 2)
            self.table.setCellWidget(row, 4, hotkey_widget)

    def on_app_check_changed(self, process_name: str, state: int):
        enabled = state == Qt.CheckState.Checked.value
        self.config.set_app_enabled(process_name, enabled)
        if self.hotkey_thread:
            self.hotkey_thread.refresh()
        self.auto_save()

    def mute_all_enabled(self):
        apps = self.config.get_apps()
        count = 0
        for name, info in apps.items():
            if info.get("enabled", False):
                if AudioController.set_app_mute(name, True):
                    count += 1
        self.status_label.setText(f"已静音 {count} 个应用")
        self.status_label.setStyleSheet("color: #dc2626; font-weight: bold;")
        self.refresh_app_list()
        self.auto_save()

    def unmute_all_enabled(self):
        apps = self.config.get_apps()
        count = 0
        for name, info in apps.items():
            if info.get("enabled", False):
                if AudioController.set_app_mute(name, False):
                    count += 1
        self.status_label.setText(f"已取消静音 {count} 个应用")
        self.status_label.setStyleSheet("color: #059669; font-weight: bold;")
        self.refresh_app_list()
        self.auto_save()

    def auto_save(self):
        """在关键节点自动保存配置"""
        if not self.config.save():
            return

        self.status_label.setText("✓ 配置已自动保存")
        self.status_label.setStyleSheet("color: #6366f1; font-size: 12px;")

    def save_config(self):
        if not self.config.save():
            QMessageBox.critical(self, "保存失败", "无法写入配置文件")
            return

        self.status_label.setText(f"配置已保存到 {CONFIG_FILE}")
        self.status_label.setStyleSheet("color: #6366f1; font-weight: bold;")
        QMessageBox.information(
            self, "保存成功", f"配置已保存到 {os.path.abspath(CONFIG_FILE)}"
        )

    def closeEvent(self, event):
        self.config.save()
        if self.hotkey_thread:
            self.hotkey_thread.stop()
        event.accept()


def configure_logging(level: int = logging.INFO, log_file: str | None = None):
    """
    注入式日志配置：通过参数控制日志级别和输出目标
    
    Args:
        level: 日志级别，如 logging.DEBUG / logging.INFO
        log_file: 日志文件路径，None 则只输出到控制台
    """
    handlers = [logging.StreamHandler(sys.stdout)]

    if log_file is None and level == logging.INFO:
        cur_time = time.strftime("%Y_%m_%d_%H-%M-%S", time.localtime())
        log_file = os.path.join("logs", f"{cur_time}.log")
    
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))

    
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] [%(levelname)-8s] [%(name)-s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True  # Python 3.8+，强制重新配置
    )


def parse_args():
    parser = argparse.ArgumentParser(description="带日志注入的 CLI 示例")
    
    parser.add_argument(
        '-d', '--debug',
        action='store_true',
        help='开启 DEBUG 级别（默认 INFO）'
    )
    parser.add_argument(
        '-l', '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help='指定日志级别（默认 INFO，优先级低于 --debug）'
    )
    parser.add_argument(
        '-f', '--log-file',
        type=str,
        default=None,
        help='日志输出文件路径，不指定则只输出到控制台'
    )
    
    return parser.parse_args()


def main():
    args = parse_args()
    
    # 优先级：--debug > --log-level
    level = logging.DEBUG if args.debug else getattr(logging, args.log_level)
    
    configure_logging(level=level, log_file=args.log_file)
    app = QApplication(sys.argv)
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
