"""SQL NULL and missing fetch headers are jsnull on current Python Workers."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from norn import ffi_compat
from norn.cloud_quota import _field


class JSNullTests(unittest.TestCase):
    def test_optional_sql_values_normalize_the_singleton_without_coercing_values(self):
        null = object()
        with patch.object(ffi_compat, "_JS_NULL", null):
            self.assertIsNone(_field({"result_json": null}, "result_json"))
            self.assertIsNone(_field(SimpleNamespace(expires_at=null), "expires_at"))
            for value in (None, 0, False, "", "null", '{"status":200}'):
                self.assertIs(ffi_compat.optional_js(value), value)

    def test_missing_http_header_and_body_normalize_to_none(self):
        null = object()
        with patch.object(ffi_compat, "_JS_NULL", null):
            self.assertIsNone(ffi_compat.optional_js(null))
            self.assertIs(ffi_compat.optional_js({"body": null})["body"], null)
