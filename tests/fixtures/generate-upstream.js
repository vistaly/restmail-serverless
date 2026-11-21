// Writes <name>.upstream.json beside each .eml, parsed the way restmail.net's
// bin/emailserver.js does. Run with mailparser 0.6.2 on NODE_PATH:
//   npm install --prefix /tmp/upstream --ignore-scripts mailparser@0.6.2
//   NODE_PATH=/tmp/upstream/node_modules node generate-upstream.js .
const { MailParser } = require('mailparser');
const fs = require('fs');
const path = require('path');

const dir = process.argv[2];
for (const file of fs.readdirSync(dir).filter((name) => name.endsWith('.eml'))) {
  const parser = new MailParser({ streamAttachments: true });
  parser.on('end', (mail) => {
    const target = path.join(dir, file.replace(/\.eml$/, '.upstream.json'));
    fs.writeFileSync(target, JSON.stringify(mail, null, 2) + '\n');
  });
  fs.createReadStream(path.join(dir, file)).pipe(parser);
}
