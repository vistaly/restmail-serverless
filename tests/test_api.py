import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from support import FIXTURES, FakeS3, load, ses_event, url_event

NOW = datetime(2026, 11, 20, 18, 30, tzinfo=timezone.utc)
ENV = {"BUCKET": "bucket", "DOMAIN": "inbox.example.com", "RETENTION_DAYS": "1", "TOKEN": ""}


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.ingest = load("IngestFunction", {"s3": self.s3}, {"BUCKET": "bucket"})
        self.api = self.load_api()
        clock = mock.patch.object(self.api.time, "time", return_value=NOW.timestamp())
        clock.start()
        self.addCleanup(clock.stop)

    def load_api(self, **env):
        return load("ApiFunction", {"s3": self.s3}, {**ENV, **env})

    def deliver(self, message_id, recipient, minutes_ago=1, fixture="plain"):
        self.s3.objects["raw/" + message_id] = (FIXTURES / f"{fixture}.eml").read_bytes()
        timestamp = stamp(NOW - timedelta(minutes=minutes_ago))
        self.ingest.handler(ses_event(message_id, [recipient], timestamp), None)

    def get(self, path, api=None, headers=None):
        response = (api or self.api).handler(url_event("GET", path, headers), None)
        return response["statusCode"], json.loads(response["body"])

    def test_unknown_mailbox_is_empty(self):
        self.assertEqual(self.get("/mail/nobody"), (200, []))

    def test_returns_the_parsed_message(self):
        self.deliver("m1", "test@inbox.example.com", fixture="html-only")
        status, (mail,) = self.get("/mail/test")
        self.assertEqual(status, 200)
        self.assertIn("24681357", mail["html"])
        self.assertNotIn("text", mail)

    def test_returns_the_newest_ten_oldest_first(self):
        for number in range(12):
            self.deliver(f"m{number:02d}", "test@inbox.example.com", minutes_ago=60 - number)
        _, mails = self.get("/mail/test")
        received = [mail["receivedAt"] for mail in mails]
        self.assertEqual(len(mails), 10)
        self.assertEqual(received, sorted(received))
        self.assertEqual(received[0], stamp(NOW - timedelta(minutes=58)))

    def test_mailbox_can_be_named_in_several_ways(self):
        self.deliver("m1", "test@inbox.example.com")
        for path in ("/mail/test", "/mail/test@inbox.example.com", "/mail/test%40inbox.example.com", "/mail/TEST"):
            with self.subTest(path):
                self.assertEqual(len(self.get(path)[1]), 1)

    def test_mailboxes_do_not_leak_into_each_other(self):
        self.deliver("m1", "test@inbox.example.com.au")
        self.deliver("m2", "test2@inbox.example.com")
        self.assertEqual(self.get("/mail/test@inbox.example.com"), (200, []))

    def test_expired_messages_are_hidden(self):
        self.deliver("old", "test@inbox.example.com", minutes_ago=24 * 60 + 1)
        self.deliver("new", "test@inbox.example.com", minutes_ago=24 * 60 - 1)
        _, mails = self.get("/mail/test")
        self.assertEqual(len(mails), 1)

    def test_message_deleted_while_reading_is_skipped(self):
        self.deliver("m1", "test@inbox.example.com", minutes_ago=2)
        self.deliver("m2", "test@inbox.example.com", minutes_ago=1)
        get_object = self.s3.get_object

        def racing_get(Bucket, Key):
            if Key.endswith("-m2.json"):
                raise self.s3.exceptions.NoSuchKey(Key)
            return get_object(Bucket=Bucket, Key=Key)

        self.s3.get_object = racing_get
        self.assertEqual(len(self.get("/mail/test")[1]), 1)

    def test_oversized_response_drops_the_oldest(self):
        self.deliver("m1", "test@inbox.example.com", minutes_ago=2)
        self.deliver("m2", "test@inbox.example.com", minutes_ago=1)
        self.api.MAX_RESPONSE_BYTES = len(next(iter(self.s3.objects.values()))) + 1
        _, mails = self.get("/mail/test")
        self.assertEqual([mail["receivedAt"] for mail in mails], [stamp(NOW - timedelta(minutes=1))])

    def test_delete_clears_only_that_mailbox(self):
        self.s3.page_size = 2
        for number in range(5):
            self.deliver(f"m{number}", "test@inbox.example.com", minutes_ago=number + 1)
        self.deliver("keep", "other@inbox.example.com")
        response = self.api.handler(url_event("DELETE", "/mail/test"), None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(self.get("/mail/test"), (200, []))
        self.assertEqual(len(self.get("/mail/other")[1]), 1)

    def test_special_users_are_never_returned(self):
        self.s3.objects["mail/postmaster@inbox.example.com/0000000000001-m1.json"] = b"{}"
        self.assertEqual(self.get("/mail/postmaster"), (200, []))

    def test_token_is_required_when_configured(self):
        api = self.load_api(TOKEN="s3cret")
        for headers in ({}, {"authorization": "Bearer wrong"}, {"authorization": "s3cret"}):
            with self.subTest(headers):
                self.assertEqual(self.get("/mail/test", api, headers)[0], 401)
        self.assertEqual(self.get("/mail/test", api, {"authorization": "Bearer s3cret"}), (200, []))

    def test_other_paths_and_methods_are_rejected(self):
        for method, path, status in (("GET", "/", 404), ("GET", "/mail/", 404), ("GET", "/mailx", 404), ("POST", "/mail/test", 405)):
            with self.subTest(path=path, method=method):
                self.assertEqual(self.api.handler(url_event(method, path), None)["statusCode"], status)


if __name__ == "__main__":
    unittest.main()
