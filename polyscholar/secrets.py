# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit OS credential storage; never silently persist plaintext.

用户显式选择系统凭据库，失败时保留会话输入，不降级为明文保存。
"""
import keyring
from keyring.errors import NoKeyringError, PasswordDeleteError
from pathlib import Path

SERVICE = 'PolyScholar'
ACCOUNT = 'model-api-key'

def _key_file():
    return Path.home() / '.polyscholar' / 'api_key'

def store_secret(value):
    if not isinstance(value, str) or not value:
        raise ValueError('密钥不能为空。')
    try:
        keyring.set_password(SERVICE, ACCOUNT, value)
    except NoKeyringError:
        raise ValueError('此系统没有可用的安全凭据库，密钥无法安全保存；仍可每次会话手动输入。') from None

def load_secret():
    try:
        return keyring.get_password(SERVICE, ACCOUNT)
    except NoKeyringError:
        return None

def clear_secret():
    # Nothing stored behaves the same as cleared for callers.
    try:
        keyring.delete_password(SERVICE, ACCOUNT)
    except (PasswordDeleteError, NoKeyringError):
        pass

def store_secret_file(value):
    """Reject legacy plaintext mode / 拒绝历史明文保存模式。"""
    raise ValueError('不支持明文保存密钥，请选择系统凭据库或仅本次会话。')


def load_secret_file():
    """Do not reactivate legacy plaintext credentials / 不自动启用历史明文密钥。"""
    return None


def clear_secret_file():
    """Allow user-requested legacy cleanup / 支持用户主动清理历史明文文件。"""
    try:
        _key_file().unlink()
    except FileNotFoundError:
        pass
