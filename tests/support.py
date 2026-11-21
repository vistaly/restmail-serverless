import io
import os
import pathlib
import re
import sys
import textwrap
import types
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template.yaml"
FIXTURES = ROOT / "tests" / "fixtures"


def inline_code(logical_id):
    lines = TEMPLATE.read_text().splitlines()
    start = lines.index(f"  {logical_id}:")
    for number in range(start + 1, len(lines)):
        line = lines[number]
        if re.match(r"  \S", line):
            break
        if line.strip() == "ZipFile: |":
            indent = len(line) - len(line.lstrip())
            body = []
            for code_line in lines[number + 1:]:
                if code_line.strip() and len(code_line) - len(code_line.lstrip()) <= indent:
                    break
                body.append(code_line)
            return textwrap.dedent("\n".join(body)) + "\n"
    raise LookupError(f"{logical_id} has no inline code")


def load(logical_id, clients, env=None):
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda name, **kwargs: clients[name]
    module = types.ModuleType(logical_id)
    code = compile(inline_code(logical_id), f"template.yaml:{logical_id}", "exec")
    with mock.patch.dict(sys.modules, {"boto3": boto3}), mock.patch.dict(os.environ, env or {}):
        exec(code, module.__dict__)
    return module


class FakeS3:
    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, page_size=1000):
        self.objects = {}
        self.page_size = page_size

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def delete_objects(self, Bucket, Delete):
        for item in Delete["Objects"]:
            self.objects.pop(item["Key"], None)

    def list_objects_v2(self, Bucket, Prefix="", MaxKeys=1000, ContinuationToken=None):
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        if ContinuationToken:
            keys = [key for key in keys if key > ContinuationToken]
        page = keys[:min(MaxKeys, self.page_size)]
        listing = {"IsTruncated": len(page) < len(keys)}
        if page:
            listing["Contents"] = [{"Key": key} for key in page]
        if listing["IsTruncated"]:
            listing["NextContinuationToken"] = page[-1]
        return listing


class FakeSes:
    def __init__(self, active=None):
        self.active = active
        self.calls = []

    def describe_active_receipt_rule_set(self):
        return {"Metadata": {"Name": self.active}, "Rules": []} if self.active else {}

    def set_active_receipt_rule_set(self, RuleSetName=None):
        self.calls.append(RuleSetName)
        self.active = RuleSetName


def ses_event(message_id, recipients, timestamp="2026-11-20T18:00:01.936Z"):
    return {
        "Records": [
            {
                "eventSource": "aws:ses",
                "ses": {
                    "mail": {"messageId": message_id},
                    "receipt": {"timestamp": timestamp, "recipients": recipients},
                },
            }
        ]
    }


def url_event(method, path, headers=None):
    return {
        "rawPath": path,
        "headers": headers or {},
        "requestContext": {"http": {"method": method, "path": path}},
    }
