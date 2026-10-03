# SPDX-License-Identifier: AGPL-3.0-only
"""Model API key storage: OS credential store or plain config file (agent-style).

提供两种持久化，均由用户在界面显式选择：OS 凭据库（加密，推荐）与明文配置文件
（与常见 CLI agent 的 auth.json/token 文件一致）。明文文件受用户主目录默认 ACL
（Windows）或 0600（POSIX）保护，但备份/网盘同步与同用户恶意程序可读取，风险在
设置页与文档说明；不提供任何"静默"降级路径。
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
    if not isinstance(value, str) or not value:
        raise ValueError('密钥不能为空。')
    path = _key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as stream:
        stream.write(value)
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows profiles already confine home-directory files to the user.

def load_secret_file():
    try:
        value = _key_file().read_text(encoding='utf-8').strip()
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None
    return value or None

def clear_secret_file():
    try:
        _key_file().unlink()
    except FileNotFoundError:
        pass
