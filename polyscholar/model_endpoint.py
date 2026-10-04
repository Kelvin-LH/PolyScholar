# SPDX-License-Identifier: AGPL-3.0-only
"""Shared provider URL boundary / 共用模型供应商 URL 校验边界。"""
from urllib.parse import urlsplit


def endpoint_url(value):
    """Allow HTTPS or explicit loopback HTTP without embedded credentials.

    允许 HTTPS 与明确的本机环回 HTTP，拒绝内嵌凭据和查询参数。
    """
    try:
        parsed = urlsplit(value)
        parsed.port
        local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
        valid = parsed.hostname and (parsed.scheme == 'https' or (local and parsed.scheme == 'http'))
        if (not valid or parsed.username or parsed.password or parsed.query or parsed.fragment
                or any(ord(character) < 32 for character in value)):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('模型地址必须是无凭据、查询参数和片段的 HTTPS 地址，本机环回可用 HTTP。') from None
    return value.rstrip('/')
