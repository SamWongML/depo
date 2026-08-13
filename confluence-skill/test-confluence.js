/**
 * Test harness: spins up a fake Confluence Server/DC (with a context path),
 * then exercises the CLI end-to-end plus unit-tests the markdown converter.
 */
import http from 'node:http';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import assert from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { toMarkdown, parseTarget, buildCql, safeFileName } from './confluence.mjs';

const run = promisify(execFile);
const CTX = '/confluence';

const STORAGE_BODY = `
<h1>Deployment Runbook</h1>
<p>This page covers the <strong>production</strong> deploy for <em>service-api</em>.</p>
<ac:structured-macro ac:name="warning"><ac:parameter ac:name="title">Freeze window</ac:parameter><ac:rich-text-body><p>No deploys on Fridays.</p></ac:rich-text-body></ac:structured-macro>
<h2>Steps</h2>
<ol>
  <li>Drain the node
    <ul><li>Wait for connections &lt; 5</li></ul>
  </li>
  <li>Run the migration</li>
</ol>
<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">bash</ac:parameter><ac:plain-text-body><![CDATA[./deploy.sh --env prod --wait
echo "done"]]></ac:plain-text-body></ac:structured-macro>
<table>
  <tbody>
    <tr><th>Env</th><th>Host</th></tr>
    <tr><td>prod</td><td>api-1 | api-2</td></tr>
    <tr><td>stage</td><td>api-stage</td></tr>
  </tbody>
</table>
<p>See <ac:link><ri:page ri:content-title="Rollback Plan" ri:space-key="OPS" /><ac:plain-text-link-body><![CDATA[the rollback plan]]></ac:plain-text-link-body></ac:link> and <a href="/display/OPS/Oncall">oncall</a>.</p>
<ac:task-list><ac:task><ac:task-status>complete</ac:task-status><ac:task-body>Notify #ops</ac:task-body></ac:task><ac:task><ac:task-status>incomplete</ac:task-status><ac:task-body>Update status page</ac:task-body></ac:task></ac:task-list>
<p>Unclosed paragraph
<p>Second paragraph &amp; entity test &#8212; done.</p>
`;

const PAGES = {
  '65539': {
    id: '65539',
    type: 'page',
    title: 'Deployment Runbook',
    space: { key: 'OPS', name: 'Operations' },
    version: { number: 7, when: '2026-07-01T10:00:00.000Z', by: { displayName: 'Dana Ops' } },
    ancestors: [{ title: 'Operations Home' }],
    metadata: { labels: { results: [{ name: 'runbook' }, { name: 'prod' }] } },
    body: { storage: { value: STORAGE_BODY }, view: { value: STORAGE_BODY } },
    _links: { webui: '/display/OPS/Deployment+Runbook' },
  },
  '65540': {
    id: '65540',
    type: 'page',
    title: 'Rollback Plan',
    space: { key: 'OPS', name: 'Operations' },
    version: { number: 2, when: '2023-01-05T10:00:00.000Z', by: { displayName: 'Sam SRE' } },
    ancestors: [],
    metadata: { labels: { results: [] } },
    body: { storage: { value: '<p>Roll back with <code>helm rollback</code>.</p>' }, view: { value: '<p>Roll back with <code>helm rollback</code>.</p>' } },
    _links: { webui: '/display/OPS/Rollback+Plan' },
  },
};

let requestLog = [];
let failNextSearch = 0;

function json(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(body);
}

function startServer() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    requestLog.push({ path: url.pathname, search: url.search, auth: req.headers.authorization });

    if (!url.pathname.startsWith(CTX)) return json(res, 404, { message: 'wrong context path' });
    if (req.headers.authorization !== 'Bearer test-token-123456') {
      return json(res, 401, { message: 'unauthorized' });
    }
    const p = url.pathname.slice(CTX.length);

    if (p === '/rest/api/user/current') {
      return json(res, 200, { username: 'dops', displayName: 'Dana Ops', email: 'dana@corp.example' });
    }
    if (p === '/rest/api/space') {
      return json(res, 200, { size: 2, results: [{ key: 'OPS', name: 'Operations', type: 'global' }, { key: 'DOCS', name: 'Docs', type: 'global' }] });
    }
    if (p === '/rest/api/search') {
      if (failNextSearch-- > 0) return json(res, 404, { message: 'not found on this version' });
      return json(res, 200, {
        results: [{
          content: { ...PAGES['65539'], body: undefined },
          excerpt: '@@@hl@@@Deployment@@@endhl@@@ runbook for service-api',
          lastModified: '2026-07-01T10:00:00.000Z',
        }],
      });
    }
    if (p === '/rest/api/content/search') {
      return json(res, 200, { results: [{ ...PAGES['65540'], body: undefined }] });
    }
    if (p === '/rest/api/content') {
      const title = url.searchParams.get('title');
      const match = Object.values(PAGES).find((x) => x.title === title);
      return json(res, 200, { results: match ? [{ ...match, body: undefined }] : [] });
    }
    let m = p.match(/^\/rest\/api\/content\/(\d+)$/);
    if (m) {
      const page = PAGES[m[1]];
      if (!page) return json(res, 404, { message: 'No content found with id' });
      return json(res, 200, page);
    }
    m = p.match(/^\/rest\/api\/content\/(\d+)\/child\/page$/);
    if (m) {
      const kids = m[1] === '65539' ? [{ ...PAGES['65540'], body: undefined }] : [];
      return json(res, 200, { results: kids, _links: {} });
    }
    m = p.match(/^\/rest\/api\/content\/(\d+)\/child\/comment$/);
    if (m) {
      return json(res, 200, { results: [{ version: { by: { displayName: 'Kim QA' }, when: '2026-07-02T09:00:00.000Z' }, body: { view: { value: '<p>Step 2 needs a <strong>timeout</strong>.</p>' } } }] });
    }
    m = p.match(/^\/rest\/api\/content\/(\d+)\/child\/attachment$/);
    if (m) {
      return json(res, 200, { results: [
        { id: 'att1', title: 'topology.png', extensions: { mediaType: 'image/png', fileSize: 20480 }, _links: { download: '/download/attachments/65539/topology.png?version=1' } },
        { id: 'att2', title: 'checklist.pdf', extensions: { mediaType: 'application/pdf', fileSize: 51200 }, _links: { download: '/download/attachments/65539/checklist.pdf?version=1' } },
      ] });
    }
    if (p.startsWith('/download/attachments/')) {
      res.writeHead(200, { 'content-type': 'application/octet-stream' });
      return res.end(Buffer.from('FAKEFILE'));
    }
    return json(res, 404, { message: 'not found: ' + p });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

/* ------------------------------- tests ------------------------------- */

let passed = 0;
let failed = 0;
async function test(name, fn) {
  try {
    await fn();
    passed++;
    console.log(`  ✅ ${name}`);
  } catch (err) {
    failed++;
    console.log(`  ❌ ${name}\n     ${err.message.split('\n').slice(0, 6).join('\n     ')}`);
  }
}

const server = await startServer();
const port = server.address().port;
const BASE = `http://127.0.0.1:${port}${CTX}`;
const ENV = { ...process.env, CONFLUENCE_BASE_URL: BASE, CONFLUENCE_TOKEN: 'test-token-123456', CONFLUENCE_RETRIES: '1' };

const cli = (args, env = ENV) =>
  run('node', ['./confluence.mjs', ...args], { env, cwd: process.cwd(), maxBuffer: 10 * 1024 * 1024 });

console.log('\nUnit: markdown conversion');

await test('headings, bold, italic', () => {
  const md = toMarkdown(STORAGE_BODY, BASE);
  assert.match(md, /^# Deployment Runbook/m);
  assert.match(md, /\*\*production\*\*/);
  assert.match(md, /\*service-api\*/);
});

await test('code macro becomes a fenced block with language', () => {
  const md = toMarkdown(STORAGE_BODY, BASE);
  assert.match(md, /```bash\n\.\/deploy\.sh --env prod --wait\necho "done"\n```/);
});

await test('warning macro becomes a titled blockquote', () => {
  const md = toMarkdown(STORAGE_BODY, BASE);
  assert.match(md, /> \*\*Freeze window\*\*/);
  assert.match(md, /> No deploys on Fridays\./);
});

await test('nested ordered/unordered list', () => {
  const md = toMarkdown(STORAGE_BODY, BASE);
  assert.match(md, /1\. Drain the node/);
  assert.match(md, /\n {2}- Wait for connections < 5/);
  assert.match(md, /2\. Run the migration/);
});

await test('table renders as GFM with escaped pipes', () => {
  const md = toMarkdown(STORAGE_BODY, BASE);
  assert.match(md, /\| Env \| Host \|/);
  assert.match(md, /\| --- \| --- \|/);
  assert.match(md, /\| prod \| api-1 \\\| api-2 \|/);
});

await test('ac:link to a page resolves to a URL', () => {
  const md = toMarkdown(STORAGE_BODY, BASE);
  assert.match(md, /\[the rollback plan\]\(http:\/\/127\.0\.0\.1:\d+\/confluence\/display\/OPS\/Rollback\+Plan\)/);
});

await test('root-relative anchor resolves against the origin', () => {
  const md = toMarkdown(STORAGE_BODY, BASE);
  assert.match(md, /\[oncall\]\(http:\/\/127\.0\.0\.1:\d+\/display\/OPS\/Oncall\)/);
});

await test('context-path anchor is preserved verbatim', () => {
  const md = toMarkdown('<p><a href="/confluence/display/OPS/X">x</a></p>', BASE);
  assert.match(md, /\[x\]\(http:\/\/127\.0\.0\.1:\d+\/confluence\/display\/OPS\/X\)/);
});

await test('base URL without trailing slash still keeps the context path', () => {
  const md = toMarkdown('<p><ac:link><ri:page ri:content-title="A B" ri:space-key="OPS" /></ac:link></p>', BASE);
  assert.match(md, /\/confluence\/display\/OPS\/A\+B/);
});

await test('task list becomes checkboxes', () => {
  const md = toMarkdown(STORAGE_BODY, BASE);
  assert.match(md, /- \[x\] Notify #ops/);
  assert.match(md, /- \[ \] Update status page/);
});

await test('entities decoded and unclosed <p> tolerated', () => {
  const md = toMarkdown(STORAGE_BODY, BASE);
  assert.match(md, /Unclosed paragraph/);
  assert.match(md, /Second paragraph & entity test — done\./);
});

await test('malformed html does not throw', () => {
  const junk = '<p>one<div><span>two</p></div></b><table><tr><td>x';
  assert.doesNotThrow(() => toMarkdown(junk, BASE));
  assert.match(toMarkdown(junk, BASE), /one/);
});

await test('code fence collision uses longer fence', () => {
  const md = toMarkdown('<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[```\nnested\n```]]></ac:plain-text-body></ac:structured-macro>', BASE);
  assert.match(md, /````\n```\nnested\n```\n````/);
});

console.log('\nUnit: targets, CQL, filenames');

await test('numeric id target', () => assert.deepEqual(parseTarget('65539'), { kind: 'id', id: '65539' }));
await test('pageId URL target', () =>
  assert.equal(parseTarget('https://wiki.corp/pages/viewpage.action?pageId=42').id, '42'));
await test('/spaces/.../pages/ID/Title URL target', () =>
  assert.equal(parseTarget('https://wiki.corp/confluence/spaces/OPS/pages/777/My-Page').id, '777'));
await test('/display/SPACE/Title URL target', () =>
  assert.deepEqual(parseTarget('https://wiki.corp/display/OPS/My+Page'), { kind: 'title', space: 'OPS', title: 'My Page' }));
await test('SPACE:Title target', () =>
  assert.deepEqual(parseTarget('OPS:Deployment Runbook'), { kind: 'title', space: 'OPS', title: 'Deployment Runbook' }));
await test('tiny link gives an actionable error', () => {
  assert.throws(() => parseTarget('https://wiki.corp/x/AbCdEf'), /Tiny links/);
});

await test('CQL escapes quotes and applies space filter', () => {
  const ctx = { cfg: { defaultSpaces: [] } };
  const cql = buildCql(ctx, { _: ['search', 'say "hi"'], space: 'OPS,DOCS' });
  assert.match(cql, /text ~ "say \\"hi\\""/);
  assert.match(cql, /space in \("OPS", "DOCS"\)/);
  assert.match(cql, /ORDER BY lastmodified DESC/);
});

await test('CQL honours CONFLUENCE_SPACES default and --all-spaces', () => {
  const ctx = { cfg: { defaultSpaces: ['OPS'] } };
  assert.match(buildCql(ctx, { _: ['search', 'x'] }), /space in \("OPS"\)/);
  assert.doesNotMatch(buildCql(ctx, { _: ['search', 'x'], 'all-spaces': true }), /space in/);
});

await test('attachment filenames are sanitised', () => {
  assert.equal(safeFileName('../../etc/passwd'), 'passwd');
  assert.equal(safeFileName('a/b:c*d.png'), 'b_c_d.png');
});

console.log('\nEnd-to-end against mock Confluence (with context path)');

await test('doctor passes', async () => {
  const { stdout } = await cli(['doctor']);
  assert.match(stdout, /✅ \*\*Authentication\*\* — Dana Ops/);
  assert.match(stdout, /✅ \*\*Space read access\*\*/);
  assert.match(stdout, /✅ \*\*CQL search endpoint\*\*/);
  assert.doesNotMatch(stdout, /test-token-123456/);
});

await test('whoami --json', async () => {
  const { stdout } = await cli(['whoami', '--json']);
  assert.equal(JSON.parse(stdout).displayName, 'Dana Ops');
});

await test('search renders results with ids and cleaned excerpt', async () => {
  const { stdout } = await cli(['search', 'deployment runbook', '--space', 'OPS']);
  assert.match(stdout, /id: 65539/);
  assert.match(stdout, /> Deployment runbook for service-api/);
  assert.doesNotMatch(stdout, /@@@/);
});

await test('search falls back to /rest/api/content/search on 404', async () => {
  failNextSearch = 1;
  const { stdout } = await cli(['search', 'anything', '--json']);
  const parsed = JSON.parse(stdout);
  assert.equal(parsed.fallbackEndpoint, true);
  assert.equal(parsed.results[0].id, '65540');
});

await test('page prints metadata header and markdown body', async () => {
  const { stdout } = await cli(['page', '65539']);
  assert.match(stdout, /\*\*Page ID:\*\* 65539/);
  assert.match(stdout, /\*\*Space:\*\* OPS \(Operations\)/);
  assert.match(stdout, /\*\*Path:\*\* Operations Home › Deployment Runbook/);
  assert.match(stdout, /\*\*Labels:\*\* runbook, prod/);
  assert.match(stdout, /```bash/);
});

await test('page flags a stale page with an age warning', async () => {
  const { stdout } = await cli(['page', '65540']);
  assert.match(stdout, /⚠ \d+ days old/);
});

await test('page by SPACE:Title resolves via lookup', async () => {
  const { stdout } = await cli(['page', 'OPS:Deployment Runbook', '--json']);
  assert.equal(JSON.parse(stdout).id, '65539');
});

await test('page by URL resolves', async () => {
  const { stdout } = await cli(['page', `${BASE}/pages/viewpage.action?pageId=65539`, '--json']);
  assert.equal(JSON.parse(stdout).title, 'Deployment Runbook');
});

await test('--max-chars truncates with a marker', async () => {
  const { stdout } = await cli(['page', '65539', '--max-chars', '120']);
  assert.match(stdout, /_\[truncated: \d+ of \d+ characters omitted/);
});

await test('--raw returns unconverted storage', async () => {
  const { stdout } = await cli(['page', '65539', '--raw']);
  assert.match(stdout, /<ac:structured-macro/);
});

await test('--comments appends rendered comments', async () => {
  const { stdout } = await cli(['page', '65539', '--comments']);
  assert.match(stdout, /## Comments/);
  assert.match(stdout, /\*\*Kim QA\*\*/);
  assert.match(stdout, /Step 2 needs a \*\*timeout\*\*/);
});

await test('children lists child pages', async () => {
  const { stdout } = await cli(['children', '65539']);
  assert.match(stdout, /Rollback Plan\*\* — id 65540/);
});

await test('tree respects depth and prints hierarchy', async () => {
  const { stdout } = await cli(['tree', '65539', '--depth', '2']);
  assert.match(stdout, /# Page tree: Deployment Runbook/);
  assert.match(stdout, /- Rollback Plan _\(id 65540\)_/);
});

await test('attachments lists files', async () => {
  const { stdout } = await cli(['attachments', '65539', '--json']);
  const parsed = JSON.parse(stdout);
  assert.equal(parsed.count, 2);
  assert.equal(parsed.attachments[0].name, 'topology.png');
});

await test('download writes filtered attachments to disk', async () => {
  const dest = fs.mkdtempSync(path.join(os.tmpdir(), 'conf-dl-'));
  const { stdout } = await cli(['download', '65539', '--dest', dest, '--pattern', '*.pdf', '--json']);
  const parsed = JSON.parse(stdout);
  assert.equal(parsed.count, 1);
  assert.ok(fs.existsSync(path.join(dest, 'checklist.pdf')));
  assert.equal(fs.readFileSync(path.join(dest, 'checklist.pdf'), 'utf8'), 'FAKEFILE');
});

await test('spaces lists visible spaces', async () => {
  const { stdout } = await cli(['spaces']);
  assert.match(stdout, /`OPS` — Operations/);
});

await test('raw escape hatch returns JSON', async () => {
  const { stdout } = await cli(['raw', 'user/current']);
  assert.equal(JSON.parse(stdout).username, 'dops');
});

console.log('\nEnd-to-end: failure modes');

await test('bad token exits 3 with an auth hint, no secret leak', async () => {
  try {
    await cli(['whoami'], { ...ENV, CONFLUENCE_TOKEN: 'wrong-token-abcdef' });
    assert.fail('should have failed');
  } catch (err) {
    assert.equal(err.code, 3);
    assert.match(err.stderr, /401 Unauthorized/);
    assert.match(err.stderr, /confluence doctor/);
    assert.doesNotMatch(err.stderr, /wrong-token-abcdef/);
  }
});

await test('missing base URL exits 2 with guidance', async () => {
  const env = { ...ENV };
  delete env.CONFLUENCE_BASE_URL;
  try {
    await cli(['whoami'], env);
    assert.fail('should have failed');
  } catch (err) {
    assert.equal(err.code, 2);
    assert.match(err.stderr, /CONFLUENCE_BASE_URL is not set/);
    assert.match(err.stderr, /context path/);
  }
});

await test('missing credentials exits 2', async () => {
  const env = { ...ENV };
  delete env.CONFLUENCE_TOKEN;
  try {
    await cli(['whoami'], env);
    assert.fail('should have failed');
  } catch (err) {
    assert.equal(err.code, 2);
    assert.match(err.stderr, /No credentials found/);
  }
});

await test('unknown page id exits 4', async () => {
  try {
    await cli(['page', '999999']);
    assert.fail('should have failed');
  } catch (err) {
    assert.equal(err.code, 4);
    assert.match(err.stderr, /404 Not found/);
  }
});

await test('unreachable host exits 5 with a network hint', async () => {
  const env = { ...ENV, CONFLUENCE_BASE_URL: 'http://127.0.0.1:9/confluence', CONFLUENCE_RETRIES: '0', CONFLUENCE_TIMEOUT_MS: '1500' };
  try {
    await cli(['whoami'], env);
    assert.fail('should have failed');
  } catch (err) {
    assert.equal(err.code, 5);
    assert.match(err.stderr, /Cannot reach/);
  }
});

await test('unknown command exits 2', async () => {
  try {
    await cli(['frobnicate']);
    assert.fail('should have failed');
  } catch (err) {
    assert.equal(err.code, 2);
    assert.match(err.stderr, /Unknown command/);
  }
});

await test('--help works without any config', async () => {
  const { stdout } = await run('node', ['./confluence.mjs', '--help'], { env: { PATH: process.env.PATH } });
  assert.match(stdout, /read-only Confluence Server\/Data Center client/);
});

await test('every request carries the Bearer header and context path', () => {
  const apiCalls = requestLog.filter((r) => r.path.includes('/rest/api/'));
  assert.ok(apiCalls.length > 10, `expected many API calls, got ${apiCalls.length}`);
  assert.ok(apiCalls.every((r) => r.path.startsWith(CTX)), 'context path missing on some call');
  assert.ok(apiCalls.some((r) => r.auth === 'Bearer test-token-123456'));
});

server.close();
console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed ? 1 : 0);
