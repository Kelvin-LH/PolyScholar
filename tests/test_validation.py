# SPDX-License-Identifier: AGPL-3.0-only
from dataclasses import FrozenInstanceError
import unittest
from polyscholar.validation import SingleLineTextPolicy, QUERY_TEXT_POLICY
from polyscholar.searches import validate_query
from polyscholar.fulltext import FulltextLibrary

class ValidationPolicyTests(unittest.TestCase):
    def test_utf8_budget_precedes_trim_and_preserves_case(self):
        policy=SingleLineTextPolicy(max_bytes=6)
        self.assertEqual(policy.validate('中文'),'中文')
        self.assertEqual(policy.validate('  ß  '),'ß')
        self.assertEqual(policy.validate('ABC'),'ABC')
        for value in ('中文字',' 中文 ','       '):
            with self.assertRaisesRegex(ValueError,'limit'):
                policy.validate(value,'limit')
        self.assertEqual(policy.validate('  '),'')
        with self.assertRaises(ValueError):SingleLineTextPolicy(allow_empty=False).validate('  ')

    def test_control_surrogate_rejection_and_immutable_configuration(self):
        for value in ('a\0b','a\tb','a\nb','a\x7fb','a\x85b','a\u2028b','a\u2029b','\ud800','\udfff',None,False):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ValueError,'safe error'):
                    QUERY_TEXT_POLICY.validate(value,'safe error')
        with self.assertRaises(FrozenInstanceError):QUERY_TEXT_POLICY.max_bytes=100
        for kwargs in (dict(max_bytes=True),dict(max_bytes=0),dict(allow_empty=1)):
            with self.assertRaises(ValueError):SingleLineTextPolicy(**kwargs)

    def test_shared_callers_preserve_errors_before_data_access(self):
        for value in ('\ud800','a\u2028b','中'*1400):
            with self.assertRaisesRegex(ValueError,'每个搜索值必须是最多 4 KiB'):
                validate_query(dict(match='all',conditions=[dict(field='title',operator='contains',value=value)]))
            # No store is needed: validation must fail before any database call.
            # 不依赖文献库：校验必须在访问数据库前失败。
            with self.assertRaisesRegex(ValueError,'全文搜索值必须是最多 4 KiB'):
                FulltextLibrary().search_fulltext(value)

if __name__=='__main__':unittest.main()
