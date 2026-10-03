# SPDX-License-Identifier: AGPL-3.0-only
"""Thread workers emit sanitized results; widgets remain on the GUI thread."""
import math
from PySide6.QtCore import QThread, Signal

def safe_error(error):
    return str(error) if isinstance(error, ValueError) else '操作未完成，请检查本地文件或设置后重试。'

def numeric(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0

def valid_progress(value):
    if numeric(value) and value<=100:return f'{value:g}%'
    if isinstance(value,dict):
        current=value.get('current');total=value.get('total')
        if numeric(current) and numeric(total) and total>0 and current<=total:return f'{current:g} / {total:g}'
    return '—'

def valid_usage(value):
    if not isinstance(value,dict):return '—'
    labels={'input_tokens':'输入 token','output_tokens':'输出 token','total_tokens':'总 token','prompt_tokens':'输入 token','completion_tokens':'输出 token'}
    parts=[f'{labels[key]}: {amount:g}' for key,amount in value.items() if key in labels and numeric(amount) and int(amount)==amount]
    return '；'.join(parts) or '—'

class IOWorker(QThread):
    ready=Signal(object)
    failed=Signal(str)
    def __init__(self,work,parent):
        super().__init__(parent);self.work=work
    def run(self):
        try:self.ready.emit(self.work())
        except Exception as error:self.failed.emit(safe_error(error))
        finally:self.work=None

class FetchModels(QThread):
    ready = Signal(list)
    failed = Signal(str)
    def __init__(self, service, endpoint, key):
        super().__init__(); self.service, self.endpoint, self.key = service, endpoint, key
    def run(self):
        try: self.ready.emit(self.service.list_models(self.endpoint, self.key or None))
        except Exception as error:
            # Validation errors are actionable; anything else stays sanitized.
            self.failed.emit(str(error) if isinstance(error, ValueError) else '无法获取模型列表，请检查地址、密钥或手动填写模型名称。')
        finally: self.key = ''

