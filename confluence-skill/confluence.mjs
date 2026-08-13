#!/usr/bin/env node
/**
 * confluence.mjs — read-only Confluence Server / Data Center CLI for coding agents.
 *
 * Zero dependencies. Node 18+. Uses the Confluence REST v1 API (Server/DC),
 * NOT the Cloud v2 API.
 *
 * Design notes:
 *  - READ ONLY. There is no code path that issues POST/PUT/DELETE. This is
 *    deliberate so security review is trivial and an agent cannot edit the wiki.
 *  - No caching. A wiki page you read twice in one session should be the same
 *    page; stale caches cause worse failures than an extra HTTP call.
 *  - Auth header is never sent across an origin change (redirect protection).
 *  - Secrets are redacted from all error output.
 *
 * See --help.
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import https from 'node:https';
import { fileURLToPath } from 'node:url';

const VERSION = '1.0.0';
const FENCE = '```';

/* ------------------------------------------------------------------ *
 * Errors / exit codes
 * ------------------------------------------------------------------ */

const EXIT = { OK: 0, GENERIC: 1, USAGE: 2, AUTH: 3, NOTFOUND: 4, NETWORK: 5, RATELIMIT: 6 };

class CliError extends Error {
  constructor(message, code = EXIT.GENERIC, hint = '') {
    super(message);
    this.code = code;
    this.hint = hint;
  }
}

/* ------------------------------------------------------------------ *
 * Config
 * ------------------------------------------------------------------ */

const SECRETS = [];

function redact(s) {
  let out = String(s ?? '');
  for (const secret of SECRETS) {
    if (secret && secret.length >= 6) out = out.split(secret).join('«redacted»');
  }
  return out;
}

function readConfigFile() {
  const candidates = [
    process.env.CONFLUENCE_CONFIG,
    path.join(os.homedir(), '.config', 'confluence-cli', 'config.json'),
    path.join(os.homedir(), '.confluence-cli.json'),
  ].filter(Boolean);

  for (const file of candidates) {
    if (!fs.existsSync(file)) continue;
    try {
      const stat = fs.statSync(file);
      if (process.platform !== 'win32' && (stat.mode & 0o077) !== 0) {
        process.stderr.write(
          `warning: ${file} is readable by other users. Run: chmod 600 ${file}\n`
        );
      }
      return { file, data: JSON.parse(fs.readFileSync(file, 'utf8')) };
    } catch (e) {
      throw new CliError(`Could not parse config file ${file}: ${e.message}`, EXIT.USAGE);
    }
  }
  return { file: null, data: {} };
}

function loadConfig() {
  const { file, data } = readConfigFile();
  const pick = (env, key) => process.env[env] ?? data[key];

  const rawBase = pick('CONFLUENCE_BASE_URL', 'baseUrl');
  if (!rawBase) {
    throw new CliError(
      'CONFLUENCE_BASE_URL is not set.',
      EXIT.USAGE,
      'Example: export CONFLUENCE_BASE_URL=https://confluence.corp.example.com\n' +
        'Include the context path if your instance has one, e.g. .../confluence'
    );
  }

  let baseUrl;
  try {
    baseUrl = new URL(rawBase.endsWith('/') ? rawBase : rawBase + '/');
  } catch {
    throw new CliError(`CONFLUENCE_BASE_URL is not a valid URL: ${rawBase}`, EXIT.USAGE);
  }

  const token = pick('CONFLUENCE_TOKEN', 'token');
  const user = pick('CONFLUENCE_USER', 'user');
  const password = pick('CONFLUENCE_PASSWORD', 'password');

  if (token) SECRETS.push(token);
  if (password) SECRETS.push(password);

  if (!token && !(user && password)) {
    throw new CliError(
      'No credentials found.',
      EXIT.USAGE,
      'Set CONFLUENCE_TOKEN to a Personal Access Token (preferred), or\n' +
        'set CONFLUENCE_USER + CONFLUENCE_PASSWORD for older instances without PAT support.'
    );
  }

  const truthy = (v) => v === true || v === '1' || v === 'true' || v === 'yes';

  const cfg = {
    configFile: file,
    baseUrl,
    token,
    user,
    password,
    ca: pick('CONFLUENCE_CA', 'ca') || null,
    clientCert: pick('CONFLUENCE_CLIENT_CERT', 'clientCert') || null,
    clientKey: pick('CONFLUENCE_CLIENT_KEY', 'clientKey') || null,
    clientKeyPassphrase: pick('CONFLUENCE_CLIENT_KEY_PASSPHRASE', 'clientKeyPassphrase') || null,
    insecure: truthy(pick('CONFLUENCE_INSECURE', 'insecure')),
    timeoutMs: Number(pick('CONFLUENCE_TIMEOUT_MS', 'timeoutMs') || 30000),
    retries: Number(pick('CONFLUENCE_RETRIES', 'retries') || 3),
    defaultSpaces: (pick('CONFLUENCE_SPACES', 'spaces') || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean),
  };

  if (cfg.clientCert && !cfg.clientKey) {
    throw new CliError('CONFLUENCE_CLIENT_CERT set without CONFLUENCE_CLIENT_KEY', EXIT.USAGE);
  }
  if (cfg.insecure) {
    process.stderr.write(
      'warning: TLS verification disabled (CONFLUENCE_INSECURE). Prefer CONFLUENCE_CA with your corporate root CA.\n'
    );
  }
  return cfg;
}

function buildAgent(cfg) {
  const opts = { keepAlive: true, maxSockets: 8 };
  if (cfg.baseUrl.protocol === 'https:') {
    if (cfg.ca) {
      if (!fs.existsSync(cfg.ca)) throw new CliError(`CA bundle not found: ${cfg.ca}`, EXIT.USAGE);
      opts.ca = fs.readFileSync(cfg.ca);
    }
    if (cfg.clientCert) {
      if (!fs.existsSync(cfg.clientCert)) {
        throw new CliError(`Client cert not found: ${cfg.clientCert}`, EXIT.USAGE);
      }
      if (!fs.existsSync(cfg.clientKey)) {
        throw new CliError(`Client key not found: ${cfg.clientKey}`, EXIT.USAGE);
      }
      opts.cert = fs.readFileSync(cfg.clientCert);
      opts.key = fs.readFileSync(cfg.clientKey);
      if (cfg.clientKeyPassphrase) opts.passphrase = cfg.clientKeyPassphrase;
    }
    if (cfg.insecure) opts.rejectUnauthorized = false;
    return new https.Agent(opts);
  }
  return new http.Agent(opts);
}

/* ------------------------------------------------------------------ *
 * HTTP
 * ------------------------------------------------------------------ */

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function authHeader(cfg) {
  if (cfg.token) return `Bearer ${cfg.token}`;
  return 'Basic ' + Buffer.from(`${cfg.user}:${cfg.password}`).toString('base64');
}

function rawRequest(ctx, urlObj, { method = 'GET', headers = {}, toFile = null, depth = 0 }) {
  const mod = urlObj.protocol === 'https:' ? https : http;
  return new Promise((resolve, reject) => {
    const req = mod.request(
      urlObj,
      { method, agent: ctx.agent, headers },
      (res) => {
        const status = res.statusCode;

        // Redirects: never forward credentials to a different origin.
        if ([301, 302, 303, 307, 308].includes(status) && res.headers.location) {
          res.resume();
          if (depth >= 5) return reject(new CliError('Too many redirects', EXIT.NETWORK));
          let next;
          try {
            next = new URL(res.headers.location, urlObj);
          } catch {
            return reject(new CliError('Invalid redirect target', EXIT.NETWORK));
          }
          if (next.origin !== urlObj.origin) {
            return reject(
              new CliError(
                `Refusing cross-origin redirect ${urlObj.origin} -> ${next.origin}`,
                EXIT.NETWORK,
                'This can indicate an SSO portal or a proxy intercepting the request.'
              )
            );
          }
          return resolve(rawRequest(ctx, next, { method, headers, toFile, depth: depth + 1 }));
        }

        if (toFile && status >= 200 && status < 300) {
          const out = fs.createWriteStream(toFile);
          res.pipe(out);
          out.on('finish', () => resolve({ status, headers: res.headers, body: null, file: toFile }));
          out.on('error', reject);
          res.on('error', reject);
          return;
        }

        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => resolve({ status, headers: res.headers, body: Buffer.concat(chunks) }));
        res.on('error', reject);
      }
    );

    req.setTimeout(ctx.cfg.timeoutMs, () => {
      req.destroy(new CliError(`Request timed out after ${ctx.cfg.timeoutMs}ms`, EXIT.NETWORK));
    });
    req.on('error', reject);
    req.end();
  });
}

const RETRYABLE_CODES = new Set([
  'ECONNRESET', 'ETIMEDOUT', 'ECONNREFUSED', 'EAI_AGAIN', 'EPIPE', 'ENOTFOUND', 'ECONNABORTED',
]);

async function httpGet(ctx, urlObj, { toFile = null, accept = 'application/json' } = {}) {
  const headers = {
    Authorization: authHeader(ctx.cfg),
    Accept: accept,
    'User-Agent': `confluence-cli/${VERSION}`,
    'X-Atlassian-Token': 'no-check',
  };

  let lastErr;
  for (let attempt = 0; attempt <= ctx.cfg.retries; attempt++) {
    try {
      const res = await rawRequest(ctx, urlObj, { headers, toFile });
      if (res.status === 429 || (res.status >= 500 && res.status <= 504)) {
        if (attempt === ctx.cfg.retries) return res;
        const retryAfter = Number(res.headers['retry-after']);
        const backoff = Number.isFinite(retryAfter) && retryAfter > 0
          ? retryAfter * 1000
          : Math.min(15000, 500 * 2 ** attempt) + Math.random() * 250;
        process.stderr.write(
          `warning: HTTP ${res.status} from Confluence, retrying in ${Math.round(backoff)}ms ` +
            `(attempt ${attempt + 1}/${ctx.cfg.retries})\n`
        );
        await sleep(backoff);
        continue;
      }
      return res;
    } catch (err) {
      lastErr = err;
      const retryable = RETRYABLE_CODES.has(err.code) || err.code === EXIT.NETWORK;
      if (!retryable || attempt === ctx.cfg.retries) break;
      await sleep(Math.min(15000, 500 * 2 ** attempt) + Math.random() * 250);
    }
  }

  if (lastErr instanceof CliError) throw lastErr;
  const msg = lastErr?.message || 'unknown network error';
  throw new CliError(
    `Cannot reach ${urlObj.origin}: ${redact(msg)}`,
    EXIT.NETWORK,
    tlsHint(lastErr)
  );
}

function tlsHint(err) {
  const c = err?.code || '';
  if (c === 'UNABLE_TO_VERIFY_LEAF_SIGNATURE' || c === 'SELF_SIGNED_CERT_IN_CHAIN' ||
      c === 'DEPTH_ZERO_SELF_SIGNED_CERT' || c === 'CERT_HAS_EXPIRED') {
    return 'TLS chain not trusted. Point CONFLUENCE_CA at your corporate root CA (PEM), e.g.\n' +
      '  export CONFLUENCE_CA=/etc/ssl/certs/corp-root-ca.pem\n' +
      'Only as a last resort: CONFLUENCE_INSECURE=1';
  }
  if (c === 'ENOTFOUND' || c === 'EAI_AGAIN') {
    return 'DNS lookup failed. On a corporate network check split-DNS/VPN, and set\n' +
      '  export NO_PROXY=$NO_PROXY,<confluence-host>';
  }
  if (c === 'ECONNREFUSED') return 'Connection refused — check host, port and context path.';
  return '';
}

function apiUrl(ctx, pathname, params = {}) {
  const url = new URL('rest/api/' + pathname.replace(/^\/+/, ''), ctx.cfg.baseUrl);
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    url.searchParams.set(k, String(v));
  }
  return url;
}

function describeHttpError(res, urlObj) {
  const bodyText = res.body ? res.body.toString('utf8').slice(0, 600) : '';
  let detail = '';
  try {
    const parsed = JSON.parse(bodyText);
    detail = parsed.message || parsed.reason || '';
  } catch {
    detail = bodyText.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 300);
  }
  const where = `${urlObj.pathname}${urlObj.search}`;

  switch (res.status) {
    case 401:
      return new CliError(
        `401 Unauthorized on ${where}`,
        EXIT.AUTH,
        'The PAT is missing, expired, or revoked. Confirm with:  confluence doctor'
      );
    case 403:
      return new CliError(
        `403 Forbidden on ${where}. ${redact(detail)}`,
        EXIT.AUTH,
        'Your account lacks permission for this space/page, or a WAF/CAPTCHA is in front of Confluence.'
      );
    case 404:
      return new CliError(`404 Not found: ${where}`, EXIT.NOTFOUND,
        'Check the page ID, or that CONFLUENCE_BASE_URL includes the context path (e.g. /confluence).');
    case 429:
      return new CliError(`429 Rate limited on ${where}`, EXIT.RATELIMIT,
        'Lower --limit, avoid deep --tree runs, and re-run in a moment.');
    default:
      return new CliError(`HTTP ${res.status} on ${where}. ${redact(detail)}`, EXIT.GENERIC);
  }
}

async function getJson(ctx, urlObj) {
  const res = await httpGet(ctx, urlObj);
  if (res.status < 200 || res.status >= 300) throw describeHttpError(res, urlObj);
  const text = res.body.toString('utf8');
  if (!text.trim()) return {};
  try {
    return JSON.parse(text);
  } catch {
    const looksLikeLogin = /<html/i.test(text) && /login|sso|sign in/i.test(text);
    throw new CliError(
      `Expected JSON from ${urlObj.pathname} but got ${looksLikeLogin ? 'an HTML login page' : 'non-JSON content'}.`,
      EXIT.AUTH,
      looksLikeLogin
        ? 'An SSO portal is intercepting API calls. Use a Personal Access Token, not session auth.'
        : 'Is CONFLUENCE_BASE_URL pointing at the Confluence root (including context path)?'
    );
  }
}

/** Follow Confluence paging until maxItems or exhaustion. */
async function getPaged(ctx, pathname, params, { maxItems = 100, pageSize = 50 } = {}) {
  const out = [];
  let start = 0;
  while (out.length < maxItems) {
    const limit = Math.min(pageSize, maxItems - out.length);
    const data = await getJson(ctx, apiUrl(ctx, pathname, { ...params, start, limit }));
    const results = data.results || [];
    out.push(...results);
    if (results.length < limit) break;
    if (data._links && !data._links.next) break;
    start += results.length;
    if (results.length === 0) break;
  }
  return out.slice(0, maxItems);
}

/* ------------------------------------------------------------------ *
 * Minimal HTML / Confluence storage-format parser
 * ------------------------------------------------------------------ */

const VOID_TAGS = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
  'param', 'source', 'track', 'wbr',
]);

const ENTITIES = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', ndash: '–', mdash: '—',
  hellip: '…', rsquo: '’', lsquo: '‘', ldquo: '“', rdquo: '”', middot: '·', bull: '•',
  copy: '©', reg: '®', trade: '™', deg: '°', laquo: '«', raquo: '»', times: '×',
};

function decodeEntities(str) {
  return String(str).replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);/g, (m, ent) => {
    if (ent[0] === '#') {
      const num = ent[1] === 'x' || ent[1] === 'X'
        ? parseInt(ent.slice(2), 16)
        : parseInt(ent.slice(1), 10);
      return Number.isFinite(num) ? String.fromCodePoint(num) : m;
    }
    return Object.prototype.hasOwnProperty.call(ENTITIES, ent) ? ENTITIES[ent] : m;
  });
}

function findTagEnd(input, from) {
  let quote = null;
  for (let i = from + 1; i < input.length; i++) {
    const ch = input[i];
    if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
    } else if (ch === '>') {
      return i;
    }
  }
  return -1;
}

function parseAttrs(str) {
  const attrs = {};
  const re = /([\w:.\-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
  let m;
  while ((m = re.exec(str))) {
    const name = m[1].toLowerCase();
    const value = m[2] ?? m[3] ?? m[4] ?? '';
    attrs[name] = decodeEntities(value);
  }
  return attrs;
}

/** Parse HTML / Confluence storage XHTML into a light DOM. Tolerant of bad nesting. */
function parseHtml(input) {
  const root = { type: 'el', name: '#root', attrs: {}, children: [] };
  const stack = [root];
  const top = () => stack[stack.length - 1];
  const pushNode = (n) => top().children.push(n);

  let i = 0;
  while (i < input.length) {
    const lt = input.indexOf('<', i);
    if (lt === -1) {
      const text = input.slice(i);
      if (text) pushNode({ type: 'text', value: decodeEntities(text) });
      break;
    }
    if (lt > i) pushNode({ type: 'text', value: decodeEntities(input.slice(i, lt)) });

    if (input.startsWith('<!--', lt)) {
      const end = input.indexOf('-->', lt);
      i = end === -1 ? input.length : end + 3;
      continue;
    }
    if (input.startsWith('<![CDATA[', lt)) {
      const end = input.indexOf(']]>', lt);
      const value = input.slice(lt + 9, end === -1 ? input.length : end);
      pushNode({ type: 'text', value, raw: true });
      i = end === -1 ? input.length : end + 3;
      continue;
    }
    if (input.startsWith('<!', lt) || input.startsWith('<?', lt)) {
      const end = input.indexOf('>', lt);
      i = end === -1 ? input.length : end + 1;
      continue;
    }

    const gt = findTagEnd(input, lt);
    if (gt === -1) {
      pushNode({ type: 'text', value: decodeEntities(input.slice(lt)) });
      break;
    }

    const inner = input.slice(lt + 1, gt).trim();
    i = gt + 1;
    if (!inner) continue;

    if (inner[0] === '/') {
      const name = inner.slice(1).trim().toLowerCase();
      for (let s = stack.length - 1; s > 0; s--) {
        if (stack[s].name === name) {
          stack.length = s;
          break;
        }
      }
      continue;
    }

    const selfClosing = inner.endsWith('/');
    const body = selfClosing ? inner.slice(0, -1) : inner;
    const nameMatch = body.match(/^([\w:.\-]+)/);
    if (!nameMatch) continue;
    const name = nameMatch[1].toLowerCase();
    const attrs = parseAttrs(body.slice(nameMatch[1].length));
    const node = { type: 'el', name, attrs, children: [] };

    // Implicit close for common unclosed tags.
    if ((name === 'li' && top().name === 'li') || (name === 'p' && top().name === 'p')) {
      stack.pop();
    }
    pushNode(node);
    if (!selfClosing && !VOID_TAGS.has(name)) stack.push(node);
  }
  return root;
}

/* ------------------------------------------------------------------ *
 * DOM -> Markdown
 * ------------------------------------------------------------------ */

const textOf = (node) => {
  if (node.type === 'text') return node.value;
  return (node.children || []).map(textOf).join('');
};

const childEls = (node, name) =>
  (node.children || []).filter((c) => c.type === 'el' && (!name || c.name === name));

function macroParams(node) {
  const params = {};
  for (const c of childEls(node, 'ac:parameter')) {
    params[c.attrs['ac:name'] || ''] = textOf(c).trim();
  }
  return params;
}

function fenced(code, lang = '') {
  const body = String(code).replace(/\s+$/, '');
  let fence = FENCE;
  while (body.includes(fence)) fence += '`';
  return `${fence}${lang}\n${body}\n${fence}`;
}

function collapse(str) {
  // Protect fenced code so indentation and blank lines inside it survive.
  const fences = [];
  const masked = str.replace(/(^|\n)(`{3,})[^\n]*\n[\s\S]*?\n\2(?=\n|$)/g, (m) => {
    fences.push(m);
    return `\u0000F${fences.length - 1}\u0000`;
  });
  const cleaned = masked
    .replace(/[ \t]+\n/g, '\n')   // trailing whitespace only; leading indent is meaningful
    .replace(/\n{3,}/g, '\n\n');
  return cleaned.replace(/\u0000F(\d+)\u0000/g, (_, i) => fences[Number(i)]);
}

class MdRenderer {
  constructor({ baseUrl } = {}) {
    // Must end in "/" or URL() resolution drops the Confluence context path.
    this.baseUrl = baseUrl ? String(baseUrl).replace(/\/?$/, '/') : null;
  }

  resolveLink(href) {
    if (!href) return '';
    if (!this.baseUrl) return href;
    try {
      return new URL(href, this.baseUrl).toString();
    } catch {
      return href;
    }
  }

  inline(nodes) {
    return (nodes || []).map((n) => this.inlineNode(n)).join('');
  }

  inlineNode(node) {
    if (node.type === 'text') return node.value.replace(/\s+/g, ' ');
    switch (node.name) {
      case 'br': return '\n';
      case 'strong': case 'b': {
        const t = this.inline(node.children).trim();
        return t ? `**${t}**` : '';
      }
      case 'em': case 'i': {
        const t = this.inline(node.children).trim();
        return t ? `*${t}*` : '';
      }
      case 'del': case 's': case 'strike': {
        const t = this.inline(node.children).trim();
        return t ? `~~${t}~~` : '';
      }
      case 'code': {
        const t = textOf(node).trim();
        return t ? '`' + t.replace(/`/g, '\u02cb') + '`' : '';
      }
      case 'a': {
        const label = this.inline(node.children).trim();
        const href = this.resolveLink(node.attrs.href);
        if (!href) return label;
        return label ? `[${label}](${href})` : `<${href}>`;
      }
      case 'img': {
        const alt = node.attrs.alt || 'image';
        const src = this.resolveLink(node.attrs.src);
        return src ? `![${alt}](${src})` : `![${alt}]`;
      }
      case 'ac:image': return this.acImage(node);
      case 'ac:link': return this.acLink(node);
      case 'ac:structured-macro': return this.macro(node, true);
      case 'ac:emoticon': return node.attrs['ac:name'] ? `:${node.attrs['ac:name']}:` : '';
      case 'ri:page': return node.attrs['ri:content-title'] || '';
      case 'time': return node.attrs.datetime || textOf(node);
      case 'sup': return `^${this.inline(node.children)}`;
      case 'sub': return `~${this.inline(node.children)}`;
      default:
        return this.inline(node.children);
    }
  }

  acLink(node) {
    const page = childEls(node, 'ri:page')[0];
    const attachment = childEls(node, 'ri:attachment')[0];
    const user = childEls(node, 'ri:user')[0];
    const bodyEl = childEls(node, 'ac:plain-text-link-body')[0] || childEls(node, 'ac:link-body')[0];
    const label = bodyEl ? this.inline(bodyEl.children).trim() || textOf(bodyEl).trim() : '';

    if (page) {
      const title = page.attrs['ri:content-title'] || label || 'page';
      const space = page.attrs['ri:space-key'];
      const target = this.baseUrl
        ? this.resolveLink(`display/${space || ''}/${encodeURIComponent(title).replace(/%20/g, '+')}`)
        : '';
      const text = label || title;
      return target && space ? `[${text}](${target})` : `[[${space ? space + ':' : ''}${title}]]`;
    }
    if (attachment) return `[attachment: ${attachment.attrs['ri:filename'] || label}]`;
    if (user) return `@${user.attrs['ri:username'] || user.attrs['ri:userkey'] || 'user'}`;
    return label;
  }

  acImage(node) {
    const attachment = childEls(node, 'ri:attachment')[0];
    const url = childEls(node, 'ri:url')[0];
    const alt = node.attrs['ac:alt'] || '';
    if (attachment) return `![${alt || attachment.attrs['ri:filename']}](attachment:${attachment.attrs['ri:filename']})`;
    if (url) return `![${alt || 'image'}](${this.resolveLink(url.attrs['ri:value'])})`;
    return `![${alt || 'image'}]`;
  }

  macro(node, isInline = false) {
    const name = (node.attrs['ac:name'] || '').toLowerCase();
    const params = macroParams(node);
    const plain = childEls(node, 'ac:plain-text-body')[0];
    const rich = childEls(node, 'ac:rich-text-body')[0];

    switch (name) {
      case 'code':
      case 'noformat':
        return fenced(plain ? textOf(plain) : textOf(node), params.language || '');
      case 'status':
        return `\`${params.title || ''}\``;
      case 'jira':
        return `[Jira: ${params.key || params.jqlQuery || 'query'}]`;
      case 'toc':
      case 'pagetree':
      case 'children':
        return isInline ? '' : `_[${name} macro omitted]_`;
      case 'info': case 'note': case 'warning': case 'tip': case 'panel': case 'expand': {
        const title = params.title || (name === 'expand' ? params.title : '') || name.toUpperCase();
        const inner = rich ? this.blocks(rich.children).trim() : textOf(node).trim();
        if (!inner) return '';
        const quoted = inner.split('\n').map((l) => `> ${l}`).join('\n');
        return `> **${title}**\n>\n${quoted}`;
      }
      case 'excerpt':
        return rich ? this.blocks(rich.children) : '';
      default: {
        if (rich) return this.blocks(rich.children);
        if (plain) return fenced(textOf(plain), '');
        const hint = params.title || params.key || '';
        return isInline ? '' : `_[macro: ${name}${hint ? ' ' + hint : ''}]_`;
      }
    }
  }

  table(node) {
    const rows = [];
    const walk = (n) => {
      for (const c of childEls(n)) {
        if (c.name === 'tr') rows.push(c);
        else if (['thead', 'tbody', 'tfoot'].includes(c.name)) walk(c);
      }
    };
    walk(node);
    if (!rows.length) return '';

    const cellText = (cell) => {
      const raw = this.blocks(cell.children).trim() || this.inline(cell.children).trim();
      return raw.replace(/\|/g, '\\|').replace(/\n+/g, '<br>');
    };
    const matrix = rows.map((r) => childEls(r).filter((c) => c.name === 'td' || c.name === 'th').map(cellText));
    const width = Math.max(...matrix.map((r) => r.length));
    const pad = (r) => { while (r.length < width) r.push(''); return r; };

    const firstRowIsHeader = childEls(rows[0]).some((c) => c.name === 'th');
    const header = firstRowIsHeader ? pad(matrix[0]) : Array.from({ length: width }, () => '');
    const bodyRows = (firstRowIsHeader ? matrix.slice(1) : matrix).map(pad);

    const lines = [
      `| ${header.join(' | ')} |`,
      `| ${header.map(() => '---').join(' | ')} |`,
      ...bodyRows.map((r) => `| ${r.join(' | ')} |`),
    ];
    return lines.join('\n');
  }

  list(node, depth = 0) {
    const ordered = node.name === 'ol';
    const items = childEls(node, 'li');
    const indent = '  '.repeat(depth);
    const out = [];
    items.forEach((li, idx) => {
      const marker = ordered ? `${idx + 1}. ` : '- ';
      const nested = [];
      const own = [];
      for (const c of li.children) {
        if (c.type === 'el' && (c.name === 'ul' || c.name === 'ol')) nested.push(c);
        else own.push(c);
      }
      let content = this.blocks(own).trim() || this.inline(own).trim();
      content = content.split('\n').map((l, k) => (k === 0 ? l : `${indent}  ${l}`)).join('\n');
      out.push(`${indent}${marker}${content}`);
      for (const n of nested) out.push(this.list(n, depth + 1));
    });
    return out.join('\n');
  }

  taskList(node) {
    return childEls(node, 'ac:task')
      .map((task) => {
        const status = (textOf(childEls(task, 'ac:task-status')[0] || { children: [] }) || '').trim();
        const bodyEl = childEls(task, 'ac:task-body')[0];
        const body = bodyEl ? this.inline(bodyEl.children).trim() : '';
        return `- [${status === 'complete' ? 'x' : ' '}] ${body}`;
      })
      .join('\n');
  }

  blocks(nodes) {
    const out = [];
    const inlineBuf = [];
    const flush = () => {
      if (!inlineBuf.length) return;
      const text = this.inline(inlineBuf).trim();
      inlineBuf.length = 0;
      if (text) out.push(text);
    };

    for (const node of nodes || []) {
      if (node.type === 'text') {
        if (node.value.trim()) inlineBuf.push(node);
        continue;
      }
      const n = node.name;
      if (/^h[1-6]$/.test(n)) {
        flush();
        const level = Number(n[1]);
        const t = this.inline(node.children).trim();
        if (t) out.push(`${'#'.repeat(level)} ${t}`);
      } else if (n === 'p') {
        flush();
        const t = this.inline(node.children).trim();
        if (t) out.push(t);
      } else if (n === 'ul' || n === 'ol') {
        flush();
        const t = this.list(node);
        if (t.trim()) out.push(t);
      } else if (n === 'ac:task-list') {
        flush();
        const t = this.taskList(node);
        if (t.trim()) out.push(t);
      } else if (n === 'table') {
        flush();
        const t = this.table(node);
        if (t) out.push(t);
      } else if (n === 'pre') {
        flush();
        const codeEl = childEls(node, 'code')[0];
        out.push(fenced(textOf(codeEl || node)));
      } else if (n === 'blockquote') {
        flush();
        const inner = this.blocks(node.children).trim();
        if (inner) out.push(inner.split('\n').map((l) => `> ${l}`).join('\n'));
      } else if (n === 'hr') {
        flush();
        out.push('---');
      } else if (n === 'ac:structured-macro') {
        flush();
        const t = this.macro(node, false);
        if (t.trim()) out.push(t);
      } else if (n === 'ac:layout' || n === 'ac:layout-section' || n === 'ac:layout-cell' ||
                 n === 'div' || n === 'section' || n === 'article' || n === 'main' ||
                 n === 'body' || n === 'html' || n === '#root' || n === 'ac:rich-text-body' ||
                 n === 'dl' || n === 'details') {
        flush();
        const t = this.blocks(node.children).trim();
        if (t) out.push(t);
      } else if (n === 'dt') {
        flush();
        const t = this.inline(node.children).trim();
        if (t) out.push(`**${t}**`);
      } else if (n === 'dd') {
        flush();
        const t = this.inline(node.children).trim();
        if (t) out.push(`  ${t}`);
      } else if (n === 'script' || n === 'style' || n === 'head' || n === 'noscript') {
        continue;
      } else if (n === 'br') {
        inlineBuf.push(node);
      } else {
        inlineBuf.push(node);
      }
    }
    flush();
    return collapse(out.join('\n\n')).trim();
  }
}

function toMarkdown(html, baseUrl) {
  if (!html) return '';
  const dom = parseHtml(html);
  return new MdRenderer({ baseUrl }).blocks(dom.children);
}

/* ------------------------------------------------------------------ *
 * Target resolution (id / URL / SPACE:Title)
 * ------------------------------------------------------------------ */

function parseTarget(target) {
  if (!target) throw new CliError('Missing page target', EXIT.USAGE);
  const t = String(target).trim();

  if (/^\d+$/.test(t)) return { kind: 'id', id: t };

  if (/^https?:\/\//i.test(t)) {
    let url;
    try {
      url = new URL(t);
    } catch {
      throw new CliError(`Not a valid URL: ${t}`, EXIT.USAGE);
    }
    const pageIdParam = url.searchParams.get('pageId');
    if (pageIdParam && /^\d+$/.test(pageIdParam)) return { kind: 'id', id: pageIdParam };

    // /spaces/SPACE/pages/12345/Title  (newer DC)
    let m = url.pathname.match(/\/pages\/(\d+)(?:\/|$)/);
    if (m) return { kind: 'id', id: m[1] };

    // /display/SPACE/Page+Title
    m = url.pathname.match(/\/display\/([^/]+)\/([^/?#]+)/);
    if (m) {
      return {
        kind: 'title',
        space: decodeURIComponent(m[1]),
        title: decodeURIComponent(m[2].replace(/\+/g, ' ')),
      };
    }
    // /x/AbCdEf tiny links cannot be resolved without a redirect fetch
    if (/\/x\/[A-Za-z0-9]+/.test(url.pathname)) {
      throw new CliError(
        'Tiny links (/x/...) cannot be resolved offline.',
        EXIT.USAGE,
        'Open the link once and use the numeric page ID from the full URL.'
      );
    }
    throw new CliError(`Could not extract a page ID from URL: ${t}`, EXIT.USAGE);
  }

  const m = t.match(/^([A-Za-z0-9_~][A-Za-z0-9_~-]*):(.+)$/);
  if (m) return { kind: 'title', space: m[1], title: m[2].trim() };

  throw new CliError(
    `Cannot interpret target "${t}".`,
    EXIT.USAGE,
    'Use a numeric page ID, a full page URL, or SPACEKEY:Exact Page Title'
  );
}

async function resolveId(ctx, target) {
  const parsed = parseTarget(target);
  if (parsed.kind === 'id') return parsed.id;

  const data = await getJson(
    ctx,
    apiUrl(ctx, 'content', { spaceKey: parsed.space, title: parsed.title, limit: 2, expand: 'version' })
  );
  const results = data.results || [];
  if (!results.length) {
    throw new CliError(
      `No page titled "${parsed.title}" in space ${parsed.space}`,
      EXIT.NOTFOUND,
      'Titles must match exactly (case sensitive). Try: confluence search "<words>" --space ' + parsed.space
    );
  }
  return results[0].id;
}

/* ------------------------------------------------------------------ *
 * Output helpers
 * ------------------------------------------------------------------ */

function out(str) {
  process.stdout.write(str.endsWith('\n') ? str : str + '\n');
}

function emitJson(value) {
  out(JSON.stringify(value, null, 2));
}

function truncate(text, maxChars) {
  if (!maxChars || maxChars <= 0 || text.length <= maxChars) return text;
  const cut = text.slice(0, maxChars);
  const lastBreak = cut.lastIndexOf('\n\n');
  const body = lastBreak > maxChars * 0.6 ? cut.slice(0, lastBreak) : cut;
  return `${body}\n\n_[truncated: ${text.length - body.length} of ${text.length} characters omitted — re-run with --max-chars 0 for the full page]_`;
}

function pageUrl(ctx, content) {
  const webui = content?._links?.webui;
  if (webui) {
    try {
      return new URL(webui.replace(/^\//, ''), ctx.cfg.baseUrl).toString();
    } catch { /* fall through */ }
  }
  return new URL(`pages/viewpage.action?pageId=${content?.id}`, ctx.cfg.baseUrl).toString();
}

function ageDays(iso) {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return null;
  return Math.floor((Date.now() - then) / 86400000);
}

function stalenessNote(iso) {
  const days = ageDays(iso);
  if (days === null) return '';
  if (days > 365) return ` (⚠ ${days} days old)`;
  if (days > 180) return ` (${days} days old)`;
  return '';
}

/* ------------------------------------------------------------------ *
 * Commands
 * ------------------------------------------------------------------ */

function escapeCql(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function buildCql(ctx, args) {
  if (args.cql) return args.cql;
  const terms = (args._[1] || '').trim();
  if (!terms) throw new CliError('Nothing to search for.', EXIT.USAGE, 'confluence search "release process" --space DOCS');

  const clauses = [`text ~ "${escapeCql(terms)}"`];
  const type = args.type || 'page';
  if (type !== 'any') clauses.push(`type = "${escapeCql(type)}"`);

  const spaces = args.space
    ? String(args.space).split(',').map((s) => s.trim()).filter(Boolean)
    : args['all-spaces'] ? [] : ctx.cfg.defaultSpaces;
  if (spaces.length) clauses.push(`space in (${spaces.map((s) => `"${escapeCql(s)}"`).join(', ')})`);

  if (args.label) {
    const labels = String(args.label).split(',').map((s) => s.trim()).filter(Boolean);
    if (labels.length) clauses.push(`label in (${labels.map((l) => `"${escapeCql(l)}"`).join(', ')})`);
  }
  if (args.since) clauses.push(`lastmodified >= "${escapeCql(args.since)}"`);

  return clauses.join(' AND ') + ' ORDER BY lastmodified DESC';
}

function cleanExcerpt(text) {
  return String(text || '')
    .replace(/@@@[a-zA-Z]*@@@/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

async function cmdSearch(ctx, args) {
  const cql = buildCql(ctx, args);
  const limit = Number(args.limit || 10);
  let results;
  let usedFallback = false;

  try {
    const data = await getJson(ctx, apiUrl(ctx, 'search', { cql, limit, expand: 'content.version,content.space' }));
    results = (data.results || []).map((r) => ({
      id: r.content?.id ?? null,
      type: r.content?.type ?? r.entityType ?? 'unknown',
      title: r.content?.title || r.title || '(untitled)',
      space: r.content?.space?.key || r.resultGlobalContainer?.title || '',
      updated: r.lastModified || r.friendlyLastModified || r.content?.version?.when || null,
      url: r.content ? pageUrl(ctx, r.content) : (r.url ? new URL(r.url.replace(/^\//, ''), ctx.cfg.baseUrl).toString() : ''),
      excerpt: cleanExcerpt(r.excerpt),
    }));
  } catch (err) {
    if (err.code !== EXIT.NOTFOUND) throw err;
    usedFallback = true;
    const data = await getJson(ctx, apiUrl(ctx, 'content/search', { cql, limit, expand: 'version,space' }));
    results = (data.results || []).map((c) => ({
      id: c.id,
      type: c.type,
      title: c.title,
      space: c.space?.key || '',
      updated: c.version?.when || null,
      url: pageUrl(ctx, c),
      excerpt: '',
    }));
  }

  if (args.json) return emitJson({ cql, count: results.length, fallbackEndpoint: usedFallback, results });

  if (!results.length) {
    out(`No results for CQL: ${cql}`);
    out('\nTry fewer words, add --all-spaces, or use --cql for an exact query.');
    return;
  }

  const lines = [`# Confluence search — ${results.length} result(s)`, '', `CQL: \`${cql}\``, ''];
  results.forEach((r, i) => {
    lines.push(`## ${i + 1}. ${r.title}`);
    const meta = [
      r.id ? `id: ${r.id}` : null,
      r.space ? `space: ${r.space}` : null,
      r.type ? `type: ${r.type}` : null,
      r.updated ? `updated: ${r.updated}${stalenessNote(r.updated)}` : null,
    ].filter(Boolean).join(' · ');
    if (meta) lines.push(meta);
    if (r.url) lines.push(r.url);
    if (r.excerpt) lines.push('', `> ${r.excerpt}`);
    lines.push('');
  });
  lines.push(`_Read one with:_ \`confluence page <id>\``);
  out(lines.join('\n'));
}

async function cmdPage(ctx, args) {
  const id = await resolveId(ctx, args._[1]);
  const source = args.source || 'view';
  const expand = [
    source === 'storage' ? 'body.storage' : 'body.view',
    'version', 'space', 'ancestors', 'metadata.labels', 'history.lastUpdated',
  ].join(',');

  const content = await getJson(ctx, apiUrl(ctx, `content/${encodeURIComponent(id)}`, { expand }));
  let html = content.body?.view?.value ?? content.body?.storage?.value ?? '';

  if (!html && source === 'view') {
    const alt = await getJson(ctx, apiUrl(ctx, `content/${encodeURIComponent(id)}`, { expand: 'body.storage' }));
    html = alt.body?.storage?.value || '';
  }

  const labels = (content.metadata?.labels?.results || []).map((l) => l.name);
  const ancestors = (content.ancestors || []).map((a) => a.title);
  const updated = content.version?.when || content.history?.lastUpdated?.when || null;
  const updatedBy = content.version?.by?.displayName || content.history?.lastUpdated?.by?.displayName || '';
  const url = pageUrl(ctx, content);

  const body = args.raw ? html : toMarkdown(html, ctx.cfg.baseUrl.toString());

  let comments = [];
  if (args.comments) {
    const raw = await getPaged(ctx, `content/${encodeURIComponent(id)}/child/comment`,
      { expand: 'body.view,version' }, { maxItems: Number(args['max-comments'] || 25), pageSize: 25 });
    comments = raw.map((c) => ({
      author: c.version?.by?.displayName || 'unknown',
      when: c.version?.when || null,
      text: toMarkdown(c.body?.view?.value || '', ctx.cfg.baseUrl.toString()),
    }));
  }

  if (args.json) {
    return emitJson({
      id: content.id, title: content.title, space: content.space?.key || null,
      version: content.version?.number ?? null, updated, updatedBy, labels, ancestors, url,
      ageDays: ageDays(updated),
      body: truncate(body, Number(args['max-chars'] || 0)),
      comments,
    });
  }

  const header = [
    `# ${content.title}`,
    '',
    `- **Page ID:** ${content.id}`,
    `- **Space:** ${content.space?.key || '?'}${content.space?.name ? ` (${content.space.name})` : ''}`,
    ancestors.length ? `- **Path:** ${ancestors.join(' › ')} › ${content.title}` : null,
    `- **Version:** ${content.version?.number ?? '?'}`,
    updated ? `- **Last updated:** ${updated}${updatedBy ? ` by ${updatedBy}` : ''}${stalenessNote(updated)}` : null,
    labels.length ? `- **Labels:** ${labels.join(', ')}` : null,
    `- **URL:** ${url}`,
    '',
    '---',
    '',
  ].filter((l) => l !== null);

  const parts = [header.join('\n'), truncate(body, Number(args['max-chars'] || 0))];

  if (args.comments) {
    parts.push('\n---\n\n## Comments' + (comments.length ? '' : '\n\n_None._'));
    for (const c of comments) {
      parts.push(`**${c.author}** — ${c.when || 'unknown date'}\n\n${c.text}`);
    }
  }
  out(parts.join('\n'));
}

async function cmdChildren(ctx, args) {
  const id = await resolveId(ctx, args._[1]);
  const kids = await getPaged(ctx, `content/${encodeURIComponent(id)}/child/page`,
    { expand: 'version' }, { maxItems: Number(args.limit || 100) });
  const rows = kids.map((k) => ({
    id: k.id, title: k.title, updated: k.version?.when || null, url: pageUrl(ctx, k),
  }));
  if (args.json) return emitJson({ parent: id, count: rows.length, children: rows });
  if (!rows.length) return out(`Page ${id} has no child pages.`);
  out([`# Child pages of ${id}`, '', ...rows.map((r) => `- **${r.title}** — id ${r.id}${r.updated ? ` · updated ${r.updated}${stalenessNote(r.updated)}` : ''}`)].join('\n'));
}

async function cmdTree(ctx, args) {
  const rootId = await resolveId(ctx, args._[1]);
  const maxDepth = Number(args.depth || 2);
  const maxNodes = Number(args['max-nodes'] || 200);
  const nodes = [];
  let visited = 0;

  async function walk(id, depth, prefix) {
    if (depth > maxDepth || visited >= maxNodes) return;
    const kids = await getPaged(ctx, `content/${encodeURIComponent(id)}/child/page`,
      { expand: 'version' }, { maxItems: Math.min(100, maxNodes - visited) });
    for (const k of kids) {
      if (visited >= maxNodes) return;
      visited++;
      nodes.push({ id: k.id, title: k.title, depth, updated: k.version?.when || null });
      out(`${prefix}- ${k.title} _(id ${k.id})_`);
      await walk(k.id, depth + 1, prefix + '  ');
    }
  }

  const root = await getJson(ctx, apiUrl(ctx, `content/${encodeURIComponent(rootId)}`, { expand: 'version' }));
  if (args.json) {
    const collected = [];
    const collect = async (id, depth) => {
      if (depth > maxDepth || collected.length >= maxNodes) return;
      const kids = await getPaged(ctx, `content/${encodeURIComponent(id)}/child/page`, { expand: 'version' },
        { maxItems: Math.min(100, maxNodes - collected.length) });
      for (const k of kids) {
        collected.push({ id: k.id, title: k.title, depth, updated: k.version?.when || null });
        await collect(k.id, depth + 1);
      }
    };
    await collect(rootId, 1);
    return emitJson({ root: { id: rootId, title: root.title }, depth: maxDepth, count: collected.length, pages: collected });
  }

  out(`# Page tree: ${root.title} _(id ${rootId})_\n`);
  await walk(rootId, 1, '');
  if (visited >= maxNodes) out(`\n_[stopped at --max-nodes ${maxNodes}]_`);
  if (visited === 0) out('_No child pages._');
}

async function cmdAttachments(ctx, args) {
  const id = await resolveId(ctx, args._[1]);
  const items = await getPaged(ctx, `content/${encodeURIComponent(id)}/child/attachment`,
    { expand: 'version' }, { maxItems: Number(args.limit || 100) });
  const rows = items.map((a) => ({
    id: a.id,
    name: a.title,
    mediaType: a.metadata?.mediaType || a.extensions?.mediaType || '',
    size: a.extensions?.fileSize ?? null,
    download: a._links?.download || '',
  }));
  if (args.json) return emitJson({ page: id, count: rows.length, attachments: rows });
  if (!rows.length) return out(`Page ${id} has no attachments.`);
  out([`# Attachments on page ${id}`, '',
    ...rows.map((r) => `- **${r.name}** — ${r.mediaType || 'unknown type'}${r.size ? `, ${Math.round(r.size / 1024)} KB` : ''}`),
    '', `_Download with:_ \`confluence download ${id} --dest ./docs\``].join('\n'));
}

function globToRegExp(pattern) {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.');
  return new RegExp(`^${escaped}$`, 'i');
}

function safeFileName(name) {
  return path.basename(String(name)).replace(/[/\\:*?"<>|]/g, '_').replace(/^\.+/, '_') || 'attachment';
}

async function cmdDownload(ctx, args) {
  const id = await resolveId(ctx, args._[1]);
  const dest = path.resolve(args.dest || '.');
  fs.mkdirSync(dest, { recursive: true });

  const items = await getPaged(ctx, `content/${encodeURIComponent(id)}/child/attachment`, {},
    { maxItems: Number(args.limit || 100) });
  const match = args.pattern ? globToRegExp(args.pattern) : null;
  const saved = [];

  for (const a of items) {
    if (match && !match.test(a.title)) continue;
    const link = a._links?.download;
    if (!link) continue;

    const target = path.join(dest, safeFileName(a.title));
    if (!target.startsWith(dest + path.sep) && target !== dest) {
      process.stderr.write(`warning: skipping suspicious attachment name ${a.title}\n`);
      continue;
    }

    // Context-path aware: resolve against the configured base first, origin second.
    const candidates = [
      new URL(link.replace(/^\//, ''), ctx.cfg.baseUrl),
      new URL(link, ctx.cfg.baseUrl.origin),
    ];
    let ok = false;
    for (const url of candidates) {
      const res = await httpGet(ctx, url, { toFile: target, accept: '*/*' });
      if (res.status >= 200 && res.status < 300) { ok = true; break; }
      if (fs.existsSync(target)) fs.unlinkSync(target);
    }
    if (ok) saved.push({ name: a.title, path: target });
    else process.stderr.write(`warning: could not download ${a.title}\n`);
  }

  if (args.json) return emitJson({ page: id, dest, count: saved.length, files: saved });
  if (!saved.length) return out('No attachments downloaded.');
  out([`Downloaded ${saved.length} file(s) to ${dest}:`, ...saved.map((s) => `- ${s.path}`)].join('\n'));
}

async function cmdSpaces(ctx, args) {
  const spaces = await getPaged(ctx, 'space', { type: args.type || 'global' },
    { maxItems: Number(args.limit || 100) });
  const rows = spaces.map((s) => ({ key: s.key, name: s.name, type: s.type }));
  if (args.json) return emitJson({ count: rows.length, spaces: rows });
  out([`# Spaces (${rows.length})`, '', ...rows.map((r) => `- \`${r.key}\` — ${r.name}`)].join('\n'));
}

async function cmdWhoami(ctx, args) {
  const me = await getJson(ctx, apiUrl(ctx, 'user/current', {}));
  const info = {
    username: me.username || me.userKey || null,
    displayName: me.displayName || null,
    email: me.email || null,
    type: me.type || null,
  };
  if (args.json) return emitJson(info);
  out(`Authenticated as ${info.displayName || info.username || 'unknown'}${info.email ? ` <${info.email}>` : ''}`);
}

async function cmdDoctor(ctx, args) {
  const checks = [];
  const add = (name, ok, detail) => checks.push({ name, ok, detail });

  add('Base URL', true, ctx.cfg.baseUrl.toString());
  add('Config file', true, ctx.cfg.configFile || 'none (using environment variables)');
  add('Auth mode', true, ctx.cfg.token ? 'Personal Access Token (Bearer)' : `Basic auth as ${ctx.cfg.user}`);
  add('TLS', !ctx.cfg.insecure,
    ctx.cfg.insecure ? 'verification DISABLED — fix by setting CONFLUENCE_CA'
      : ctx.cfg.ca ? `custom CA: ${ctx.cfg.ca}` : 'system trust store');
  if (ctx.cfg.clientCert) add('Client cert', true, ctx.cfg.clientCert);
  add('Default space filter', true, ctx.cfg.defaultSpaces.length ? ctx.cfg.defaultSpaces.join(', ') : 'none');

  let user = null;
  try {
    user = await getJson(ctx, apiUrl(ctx, 'user/current', {}));
    add('Authentication', true, `${user.displayName || user.username}`);
  } catch (err) {
    add('Authentication', false, redact(err.message));
  }

  try {
    const spaces = await getJson(ctx, apiUrl(ctx, 'space', { limit: 1 }));
    add('Space read access', true, `${spaces.size ?? (spaces.results || []).length} space(s) visible on first page`);
  } catch (err) {
    add('Space read access', false, redact(err.message));
  }

  try {
    const search = await getJson(ctx, apiUrl(ctx, 'search', { cql: 'type = "page"', limit: 1 }));
    add('CQL search endpoint', true, `/rest/api/search OK (${(search.results || []).length} result)`);
  } catch (err) {
    add('CQL search endpoint', false, redact(err.message) + ' — will fall back to /rest/api/content/search');
  }

  if (args.json) return emitJson({ ok: checks.every((c) => c.ok), checks });
  out(['# confluence doctor', '', ...checks.map((c) => `${c.ok ? '✅' : '❌'} **${c.name}** — ${c.detail}`)].join('\n'));
  if (!checks.every((c) => c.ok)) process.exitCode = EXIT.GENERIC;
}

async function cmdRaw(ctx, args) {
  const p = args._[1];
  if (!p) throw new CliError('Usage: confluence raw <rest-path> [--param key=value ...]', EXIT.USAGE);
  const params = {};
  for (const kv of [].concat(args.param || [])) {
    const idx = String(kv).indexOf('=');
    if (idx > 0) params[String(kv).slice(0, idx)] = String(kv).slice(idx + 1);
  }
  emitJson(await getJson(ctx, apiUrl(ctx, p, params)));
}

/* ------------------------------------------------------------------ *
 * CLI
 * ------------------------------------------------------------------ */

const HELP = `confluence ${VERSION} — read-only Confluence Server/Data Center client

USAGE
  confluence <command> [options]

COMMANDS
  search <words>            Search pages (CQL under the hood)
  page <target>             Print one page as Markdown
  children <target>         List direct child pages
  tree <target>             Print a page tree
  attachments <target>      List attachments on a page
  download <target>         Download attachments to disk
  spaces                    List visible spaces
  whoami                    Show the authenticated user
  doctor                    Diagnose config, TLS and API reachability
  raw <rest-path>           GET any /rest/api path (escape hatch)

TARGETS
  12345                                  numeric page ID
  https://wiki.corp/display/DOCS/My+Page  full URL
  https://wiki.corp/pages/viewpage.action?pageId=12345
  DOCS:My Page                           SPACEKEY:Exact Title

COMMON OPTIONS
  --json                 Machine-readable output
  --limit N              Max results (default 10 for search, 100 for lists)
  --max-chars N          Truncate page body (0 = no limit, default 0)
  --space KEY[,KEY]      Restrict search to spaces
  --all-spaces           Ignore CONFLUENCE_SPACES default filter
  --label a,b            Restrict search by label
  --since 2026-01-01     Only pages modified on/after a date
  --cql "..."            Raw CQL, overrides the word search
  --type page|blogpost|any
  --comments             Include comments (page)
  --source view|storage  Page body source (default view: macros rendered)
  --raw                  Print the raw HTML/storage body instead of Markdown
  --depth N              Tree depth (default 2)
  --max-nodes N          Tree node cap (default 200)
  --dest DIR             Download destination
  --pattern "*.png"      Filter attachments
  --help, --version

ENVIRONMENT
  CONFLUENCE_BASE_URL    https://confluence.corp.example.com[/context-path]   (required)
  CONFLUENCE_TOKEN       Personal Access Token  (preferred)
  CONFLUENCE_USER        Username     \\ fallback for instances without PATs
  CONFLUENCE_PASSWORD    Password     /
  CONFLUENCE_SPACES      Default space filter for search, e.g. DOCS,ARCH
  CONFLUENCE_CA          Path to corporate root CA (PEM)
  CONFLUENCE_CLIENT_CERT / CONFLUENCE_CLIENT_KEY [ / CONFLUENCE_CLIENT_KEY_PASSPHRASE ]
  CONFLUENCE_INSECURE=1  Disable TLS verification (last resort)
  CONFLUENCE_TIMEOUT_MS  Default 30000
  CONFLUENCE_RETRIES     Default 3
  CONFLUENCE_CONFIG      Path to a JSON config file (default ~/.config/confluence-cli/config.json)

EXAMPLES
  confluence doctor
  confluence search "deployment runbook" --space OPS --limit 5
  confluence page 65539 --max-chars 20000
  confluence page DOCS:Release Process --comments
  confluence tree 65539 --depth 3
  confluence download 65539 --dest ./wiki-assets --pattern "*.pdf"

This client is read-only. It never writes to Confluence.
`;

const BOOLEAN_FLAGS = new Set([
  'json', 'help', 'version', 'comments', 'raw', 'all-spaces', 'quiet',
]);

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const token = argv[i];
    if (token === '--') { args._.push(...argv.slice(i + 1)); break; }
    if (token.startsWith('--')) {
      const eq = token.indexOf('=');
      const key = eq === -1 ? token.slice(2) : token.slice(2, eq);
      if (BOOLEAN_FLAGS.has(key)) {
        args[key] = eq === -1 ? true : !['false', '0', 'no'].includes(token.slice(eq + 1));
        continue;
      }
      let value;
      if (eq !== -1) value = token.slice(eq + 1);
      else if (i + 1 < argv.length && !argv[i + 1].startsWith('--')) value = argv[++i];
      else value = true;
      if (key === 'param') args.param = [].concat(args.param || [], value);
      else args[key] = value;
    } else if (token === '-h') args.help = true;
    else args._.push(token);
  }
  // Let unquoted multi-word targets work: `confluence page DOCS:My Page`
  if (args._.length > 2 && ['page', 'children', 'tree', 'attachments', 'download'].includes(args._[0])) {
    args._ = [args._[0], args._.slice(1).join(' ')];
  }
  if (args._.length > 2 && args._[0] === 'search') {
    args._ = ['search', args._.slice(1).join(' ')];
  }
  return args;
}

const COMMANDS = {
  search: cmdSearch,
  page: cmdPage,
  get: cmdPage,
  children: cmdChildren,
  tree: cmdTree,
  attachments: cmdAttachments,
  download: cmdDownload,
  spaces: cmdSpaces,
  whoami: cmdWhoami,
  doctor: cmdDoctor,
  raw: cmdRaw,
};

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (args.version) { out(VERSION); return; }
  if (args.help || args._.length === 0) { process.stdout.write(HELP); return; }

  const command = args._[0];
  const handler = COMMANDS[command];
  if (!handler) {
    throw new CliError(`Unknown command "${command}"`, EXIT.USAGE, 'Run: confluence --help');
  }

  const cfg = loadConfig();
  const ctx = { cfg, agent: buildAgent(cfg) };
  await handler(ctx, args);
  ctx.agent.destroy?.();
}

const invokedDirectly = process.argv[1] &&
  path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  main().catch((err) => {
    if (err instanceof CliError) {
      process.stderr.write(`error: ${redact(err.message)}\n`);
      if (err.hint) process.stderr.write(`\n${redact(err.hint)}\n`);
      process.exit(err.code);
    }
    process.stderr.write(`error: ${redact(err?.stack || err?.message || err)}\n`);
    process.exit(EXIT.GENERIC);
  });
}

export { toMarkdown, parseTarget, parseHtml, buildCql, globToRegExp, safeFileName };
