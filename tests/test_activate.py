import json
import unittest
from unittest import mock

from support import FakeSes, load


def event(request_type, name="inbox", **extra):
    return {
        "RequestType": request_type,
        "ResourceProperties": {"RuleSetName": name},
        "ResponseURL": "https://example.invalid/response",
        "StackId": "stack",
        "RequestId": "request",
        "LogicalResourceId": "ActiveRuleSet",
        **extra,
    }


class ActivateTest(unittest.TestCase):
    def run_handler(self, active, request):
        ses = FakeSes(active)
        activate = load("ActivateFunction", {"ses": ses})
        with mock.patch.object(activate.urllib.request, "urlopen") as urlopen:
            activate.handler(request, None)
        (sent,), _ = urlopen.call_args
        return ses, sent, json.loads(sent.data)

    def test_create_activates_when_nothing_is_active(self):
        ses, sent, response = self.run_handler(None, event("Create"))
        self.assertEqual(ses.calls, ["inbox"])
        self.assertEqual(response["Status"], "SUCCESS")
        self.assertEqual(sent.get_method(), "PUT")
        self.assertEqual(sent.full_url, "https://example.invalid/response")
        self.assertEqual(
            {key: response[key] for key in ("StackId", "RequestId", "LogicalResourceId", "PhysicalResourceId")},
            {
                "StackId": "stack",
                "RequestId": "request",
                "LogicalResourceId": "ActiveRuleSet",
                "PhysicalResourceId": "active-rule-set-inbox",
            },
        )

    def test_create_refuses_to_replace_another_active_set(self):
        ses, _, response = self.run_handler("someone-elses", event("Create"))
        self.assertEqual(ses.calls, [])
        self.assertEqual(response["Status"], "FAILED")
        self.assertIn("someone-elses", response["Reason"])

    def test_update_leaves_its_own_active_set_alone(self):
        ses, _, response = self.run_handler("inbox", event("Update", PhysicalResourceId="active-rule-set-inbox"))
        self.assertEqual(ses.calls, [])
        self.assertEqual(response["Status"], "SUCCESS")

    def test_delete_deactivates_its_own_set(self):
        ses, _, response = self.run_handler("inbox", event("Delete", PhysicalResourceId="active-rule-set-inbox"))
        self.assertEqual(ses.calls, [None])
        self.assertEqual(response["Status"], "SUCCESS")

    def test_delete_leaves_another_active_set_alone(self):
        ses, _, response = self.run_handler("someone-elses", event("Delete", PhysicalResourceId="active-rule-set-inbox"))
        self.assertEqual(ses.calls, [])
        self.assertEqual(response["Status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
