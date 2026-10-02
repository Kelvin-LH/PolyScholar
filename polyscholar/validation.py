# SPDX-License-Identifier: AGPL-3.0-only
"""Reusable immutable input policies. 可复用且不可变的输入校验策略。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SingleLineTextPolicy:
    """Validate and trim single-line text; required-value semantics stay with callers.

    校验并去除单行文本两侧空白；是否为业务必填项由调用者决定。
    The byte budget applies before trimming, so whitespace cannot bypass it.
    字节上限在 trim 前计算，空白不能绕过限制；UTF-8 字节数不等于字符数。
    """
    max_bytes: int = 4096
    allow_empty: bool = True

    def __post_init__(self):
        if type(self.max_bytes) is not int or self.max_bytes < 1 or type(self.allow_empty) is not bool:
            raise ValueError('文本校验策略配置无效。')

    def validate(self, value, error_message='单行文本无效。'):
        if not isinstance(value, str):
            raise ValueError(error_message)
        try:
            size = len(value.encode('utf-8'))
        except UnicodeEncodeError:
            # Lone surrogates are not UTF-8 text. 孤立代理码点不是有效 UTF-8 文本。
            raise ValueError(error_message) from None
        # Reject NUL/C0 controls, DEL, NEL and Unicode line/paragraph separators.
        # 拒绝 NUL/C0 控制符、DEL、NEL 与 Unicode 行/段分隔符，保持单行语义。
        if size > self.max_bytes or any(ord(char) < 32 or ord(char) in (127, 133, 8232, 8233) for char in value):
            raise ValueError(error_message)
        normalized = value.strip()
        if not self.allow_empty and not normalized:
            raise ValueError(error_message)
        return normalized


# Casefold belongs to matching, not validation. casefold 属于匹配层，不改写保存的输入。
QUERY_TEXT_POLICY = SingleLineTextPolicy()
