# -*- coding: utf-8 -*-
"""Tests for attachment_saver.py's path-traversal fix: a sender-supplied filename must never
escape dest_dir, and an existing file must never be silently overwritten."""
import base64
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import attachment_saver as asv


def _fake_gmail_module(attachments):
    """attachments: list of (filename, data_bytes). Builds a fake gmail_api module whose
    get_service() returns a service stub that save_attachments() will drive."""
    parts = [
        {"filename": name, "body": {"attachmentId": "att-%d" % i}}
        for i, (name, _) in enumerate(attachments)
    ]
    data_by_id = {"att-%d" % i: data for i, (_, data) in enumerate(attachments)}

    service = mock.MagicMock()
    service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "payload": {"parts": parts}
    }

    def _att_get(userId, messageId, id):
        m = mock.MagicMock()
        m.execute.return_value = {"data": base64.urlsafe_b64encode(data_by_id[id]).decode("ascii")}
        return m

    service.users.return_value.messages.return_value.attachments.return_value.get.side_effect = _att_get

    mod = types.ModuleType("gmail_api")
    mod.get_service = lambda: service
    return mod


class SaveAttachments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        sys.modules.pop("gmail_api", None)

    def _run(self, attachments):
        sys.modules["gmail_api"] = _fake_gmail_module(attachments)
        return asv.save_attachments("msg1", dest_dir=self.dest)

    def test_a_normal_filename_is_saved_inside_dest_dir(self):
        saved = self._run([("invoice.pdf", b"hello")])
        self.assertEqual(len(saved), 1)
        out = pathlib.Path(saved[0])
        self.assertEqual(out.parent.resolve(), self.dest.resolve())
        self.assertEqual(out.read_bytes(), b"hello")

    def test_a_traversal_filename_is_confined_to_the_base_name(self):
        saved = self._run([("../../evil.txt", b"pwned")])
        out = pathlib.Path(saved[0])
        self.assertEqual(out.parent.resolve(), self.dest.resolve())
        self.assertEqual(out.name, "evil.txt")
        self.assertFalse((self.dest.parent.parent / "evil.txt").exists())

    def test_an_absolute_path_filename_is_also_confined(self):
        saved = self._run([(r"C:\Windows\System32\evil.dll", b"pwned")])
        out = pathlib.Path(saved[0])
        self.assertEqual(out.parent.resolve(), self.dest.resolve())
        self.assertEqual(out.name, "evil.dll")

    def test_an_existing_file_is_never_silently_overwritten(self):
        (self.dest / "dup.txt").write_bytes(b"original")
        saved = self._run([("dup.txt", b"new content")])
        out = pathlib.Path(saved[0])
        self.assertNotEqual(out.name, "dup.txt")
        self.assertEqual((self.dest / "dup.txt").read_bytes(), b"original")
        self.assertEqual(out.read_bytes(), b"new content")


if __name__ == "__main__":
    unittest.main()
