import json
import unittest

from support import FIXTURES, FakeS3, load, ses_event

COMPARED = (
    "text",
    "html",
    "headers",
    "subject",
    "messageId",
    "references",
    "inReplyTo",
    "priority",
    "from",
    "to",
    "cc",
    "date",
    "receivedDate",
)
ATTACHMENT_FIELDS = (
    "contentType",
    "fileName",
    "contentDisposition",
    "transferEncoding",
    "generatedFileName",
    "checksum",
    "length",
)


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.ingest = load("IngestFunction", {"s3": self.s3}, {"BUCKET": "bucket"})

    def deliver(self, message_id, recipients, fixture="plain", **kwargs):
        self.s3.objects["raw/" + message_id] = (FIXTURES / f"{fixture}.eml").read_bytes()
        self.ingest.handler(ses_event(message_id, recipients, **kwargs), None)

    def test_parse_matches_upstream_mailparser(self):
        for eml in sorted(FIXTURES.glob("*.eml")):
            with self.subTest(eml.name):
                expected = json.loads(eml.with_suffix(".upstream.json").read_text())
                actual = self.ingest.parse(eml.read_bytes())
                for field in COMPARED:
                    self.assertEqual(actual.get(field), expected.get(field), field)
                self.assertEqual(
                    [{f: a.get(f) for f in ATTACHMENT_FIELDS} for a in actual.get("attachments", [])],
                    [{f: a.get(f) for f in ATTACHMENT_FIELDS} for a in expected.get("attachments", [])],
                )

    def test_files_one_copy_per_recipient_and_removes_the_raw_message(self):
        self.deliver(
            "m1",
            [
                "Test@Inbox.Example.com",
                "test@inbox.example.com",
                "other+tag@inbox.example.com",
                "postmaster@inbox.example.com",
            ],
        )
        self.assertEqual(
            sorted(self.s3.objects),
            [
                "mail/other%2Btag@inbox.example.com/8204802398063-m1.json",
                "mail/test@inbox.example.com/8204802398063-m1.json",
            ],
        )

    def test_stamps_received_at_from_the_ses_receipt(self):
        self.deliver("m1", ["test@inbox.example.com"], timestamp="2026-11-20T18:00:01.936Z")
        (stored,) = self.s3.objects.values()
        self.assertEqual(json.loads(stored)["receivedAt"], "2026-11-20T18:00:01.936Z")

    def test_newer_messages_sort_first(self):
        self.deliver("old", ["test@inbox.example.com"], timestamp="2026-11-20T18:00:00.000Z")
        self.deliver("new", ["test@inbox.example.com"], timestamp="2026-11-20T18:00:00.001Z")
        self.assertEqual([key.rsplit("-", 1)[1] for key in sorted(self.s3.objects)], ["new.json", "old.json"])

    def test_repeat_delivery_is_ignored(self):
        self.deliver("m1", ["test@inbox.example.com"])
        before = dict(self.s3.objects)
        self.ingest.handler(ses_event("m1", ["test@inbox.example.com"]), None)
        self.assertEqual(self.s3.objects, before)

    def test_long_bodies_are_cut(self):
        self.ingest.MAX_BODY_CHARS = 9
        self.deliver("m1", ["test@inbox.example.com"])
        (stored,) = self.s3.objects.values()
        self.assertEqual(json.loads(stored)["text"], "Your code")

    def test_malformed_bytes_are_still_filed(self):
        self.s3.objects["raw/m1"] = b"Subject: caf\xe9 \xff\r\nFrom: \xff\xfe <a@b>\r\n\r\n\xff\x00"
        self.ingest.handler(ses_event("m1", ["test@inbox.example.com"]), None)
        (stored,) = self.s3.objects.values()
        self.assertEqual(json.loads(stored)["from"][0]["address"], "a@b")


if __name__ == "__main__":
    unittest.main()
