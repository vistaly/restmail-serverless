# restmail-serverless

Email addresses for automated tests, read over a JSON API. A serverless
reimplementation of [restmail.net](https://github.com/mozilla/restmail.net):
the same two endpoints and the same response shape, running on your own domain
in your own AWS account, scaling to zero, with the inboxes private by default.

This is an independent reimplementation, not a fork, and is not affiliated
with Mozilla.

Mail sent to any address at your domain becomes readable at
`GET /mail/<user>` and is deleted after a day.

It is one CloudFormation template. The three Lambda functions are written
inline in it and use only the Python standard library and the AWS SDK that
ships in the Lambda runtime, so there is nothing to build, no artifact bucket
and no dependencies to keep patched.

## Deploy

You need a domain (or subdomain) whose DNS you control, in a region where
[SES can receive email](https://docs.aws.amazon.com/general/latest/gr/ses.html).
Use a dedicated subdomain: the stack points its MX record at SES.

```sh
aws cloudformation deploy \
  --stack-name test-inbox \
  --template-file template.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides Domain=inbox.example.com HostedZoneId=Z0123456789ABCDEFGHIJ
```

| Parameter | Default | |
| --- | --- | --- |
| `Domain` | required | Domain to receive mail for. Every address at it is a mailbox. |
| `HostedZoneId` | empty | Route 53 zone to add the MX and DKIM records to. Leave empty and create them yourself from the `MxRecord` and `DkimRecords` outputs. |
| `VerifyDomain` | `true` | Create the SES domain identity. Set to `false` if the domain is already verified in this region. |
| `ExistingRuleSetName` | empty | Add the receipt rule to this rule set instead of creating one. See [receipt rule sets](#receipt-rule-sets). |
| `AuthType` | `AWS_IAM` | `AWS_IAM` or `NONE`. See [access](#access). |
| `AccessToken` | empty | When set, requests must send `Authorization: Bearer <token>`. |
| `RetentionDays` | `1` | Days before a message expires. |

SES accepts mail for the domain once it has verified the DKIM records, which
takes a few minutes after they exist. The stack does not wait for that.

### Receipt rule sets

SES evaluates one active receipt rule set per account per region. By default
the stack creates a rule set named after the stack and activates it, and it
fails rather than replace a rule set that is already active. If the account
already receives mail through SES in that region, either pass that rule set's
name as `ExistingRuleSetName`, or deploy this stack to another region.

## API

The `Url` stack output is the base URL.

### `GET /mail/<user>`

Returns the newest ten messages for the mailbox as a JSON array, oldest first.
`<user>` is either the part before the `@` or the full address. A mailbox with
no mail returns `[]`.

```json
[
  {
    "headers": {
      "from": "Ada Lovelace <ada@example.com>",
      "to": "test@inbox.example.com",
      "subject": "Your sign-in code",
      "date": "Fri, 20 Nov 2026 13:00:00 -0500"
    },
    "from": [{ "address": "ada@example.com", "name": "Ada Lovelace" }],
    "to": [{ "address": "test@inbox.example.com", "name": "" }],
    "subject": "Your sign-in code",
    "text": "Your code is 12345678.\n",
    "date": "2026-11-20T18:00:00.000Z",
    "receivedAt": "2026-11-20T18:00:01.936Z"
  }
]
```

A message has `text`, `html` or both, depending on what was sent. Other fields,
when present: `cc`, `bcc`, `messageId`, `inReplyTo`, `references`, `priority`,
`receivedDate` and `attachments` (metadata only).

### `DELETE /mail/<user>`

Deletes all mail for the mailbox. Returns `200`.

## Access

With `AuthType=AWS_IAM` (the default) requests must be signed with SigV4 for
the `lambda` service by a principal in the same account that is allowed both
actions on the function in the `ApiFunctionArn` output:

```json
{
  "Effect": "Allow",
  "Action": ["lambda:InvokeFunctionUrl", "lambda:InvokeFunction"],
  "Resource": "arn:aws:lambda:us-east-1:111122223333:function:test-inbox-ApiFunction-abc123"
}
```

```sh
curl --aws-sigv4 "aws:amz:us-east-1:lambda" \
  --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" \
  -H "x-amz-security-token: $AWS_SESSION_TOKEN" \
  "${URL}mail/test"
```

With `AuthType=NONE` the URL is public. Set `AccessToken` as well unless the
inboxes may be read and emptied by anyone, as on restmail.net. The token is
stored as an environment variable on the function, so anyone who can read the
function's configuration can read it.

## Differences from restmail.net

- A message is readable once SES has handed it to the function, not the
  instant it is accepted.
- Mailbox names are case-insensitive.
- Each message expires on its own; restmail.net restarts a mailbox's clock
  whenever new mail arrives.
- `headers` also carries what SES adds on receipt (`Received`, `Return-Path`,
  `X-SES-Spam-Verdict` and others).
- `text` and `html` are cut at 200,000 characters, and only the first 10 MB of
  a message is parsed (`"truncated": true` marks those).
- There is no web page and there are no heartbeat endpoints.

restmail.net's README says newest first; its code returns oldest first, and so
does this.

## How it works

```
sender ─SMTP─▶ SES receipt rule for the domain
                 1. writes the raw message to s3://<bucket>/raw/
                 2. invokes the ingest function, which parses it and writes
                    one JSON object per recipient under mail/<address>/
client ─HTTPS─▶ function URL ─▶ API function, which lists and reads mail/<address>/
```

Everything is stored in one S3 bucket with a lifecycle rule; there is no
database. Nothing is provisioned, so an idle stack costs nothing beyond the
storage of unexpired mail.

## Removing

Empty the bucket (`BucketName` output), then delete the stack. If the stack
activated its own rule set, deleting it deactivates that rule set.

## Using a pinned copy

The template is self-contained, so another repository can pin a commit of this
one by vendoring that single file:

```sh
curl -fsSLo vendor/restmail-serverless/template.yaml \
  https://raw.githubusercontent.com/vistaly/restmail-serverless/<commit>/template.yaml
```

From the AWS CDK, include it in a stack:

```ts
import { CfnInclude } from "aws-cdk-lib/cloudformation-include";

new CfnInclude(this, "TestInbox", {
  templateFile: "vendor/restmail-serverless/template.yaml",
  parameters: { Domain: "inbox.example.com", HostedZoneId: zone.hostedZoneId },
});
```

## Development

```sh
python3 -m unittest discover -s tests -b
pipx run cfn-lint template.yaml
```

The tests run the functions' code straight out of `template.yaml` against an
in-memory S3. `tests/fixtures/*.upstream.json` is what `mailparser` 0.6.2, the
parser restmail.net pins, produces for each `.eml`; the parser here is held to
that output. `tests/fixtures/generate-upstream.js` regenerates it.

## License

MIT
