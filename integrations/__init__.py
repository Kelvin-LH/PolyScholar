# SPDX-License-Identifier: AGPL-3.0-only
"""Local worker subprocess package (parse, summary) and shared environment allow-list."""
import os

def limited_environment():
    # Do not copy API credentials or provider config from the parent environment.
    # Keep the genuine OS home; never repurpose HOME or CODEX_HOME.
    allowed = ('PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP',
               'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
               # OS configuration/cache discovery and native subprocess resolution.
               'APPDATA', 'LOCALAPPDATA', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT')
    return {key: os.environ[key] for key in allowed if key in os.environ}
