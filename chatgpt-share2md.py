#!/usr/bin/env python3
import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import urllib.request

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f', '"': '"', "'": "'", '\\': '\\', '/': '/'}
CI_VER = 'v0.6.1'
CI_DIR = os.path.expanduser('~/.local/share/curl-impersonate')
REDACTED = 'This block is not supported on your current device yet.'


def run_curl(url, extra=()):
    import subprocess
    r = subprocess.run(['curl', '-sL', '--compressed', '-A', UA,
                        '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        '-H', 'Accept-Language: ja,en;q=0.9', *extra,
                        url], capture_output=True, timeout=90)
    return r.returncode, r.stdout.decode('utf-8', errors='replace'), r.stderr.decode(errors='replace')[:200]


def fetch_html(share_url):
    rc, out, err = run_curl(share_url)
    if rc != 0:
        raise SystemExit(f'error: fetch failed (curl exit {rc}): {err}')
    return out


def curl_impersonate_path():
    name = {'x86_64': 'x86_64', 'aarch64': 'aarch64', 'arm64': 'aarch64'}.get(platform.machine(), 'x86_64')
    wrapper = os.path.join(CI_DIR, 'curl_chrome116')
    if os.path.exists(wrapper):
        return wrapper
    tar = f'curl-impersonate-{CI_VER}.{name}-linux-gnu.tar.gz'
    url = f'https://github.com/lwthiker/curl-impersonate/releases/download/{CI_VER}/{tar}'
    print(f'downloading curl-impersonate (Cloudflare bypass) to {CI_DIR} ...', file=sys.stderr)
    os.makedirs(CI_DIR, exist_ok=True)
    tgz = os.path.join(CI_DIR, tar)
    urllib.request.urlretrieve(url, tgz)
    with tarfile.open(tgz) as tf:
        tf.extractall(CI_DIR)
    os.remove(tgz)
    return wrapper


def fetch_json(url, referer):
    extra = ['-H', 'Accept: application/json', '-H', f'Referer: {referer}']
    rc, out, err = run_curl(url, extra)
    if rc == 0 and out.lstrip().startswith('{'):
        return json.loads(out)
    wrapper = curl_impersonate_path()
    r = subprocess.run([wrapper, '-s', '--max-time', '60', *extra, url],
                       capture_output=True, timeout=90)
    body = r.stdout.decode('utf-8', errors='replace')
    if r.returncode != 0:
        raise SystemExit(f'error: fetch failed: {r.stderr.decode(errors="replace")[:200]}')
    if not body.lstrip().startswith('{'):
        raise SystemExit('error: Claude API is still blocked by bot protection; try again later')
    return json.loads(body)


def js_unescape(s):
    s = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), s)
    return re.sub(r'\\(.)', lambda m: ESCAPES.get(m.group(1), m.group(1)), s)


def flat_array(html):
    chunks = re.findall(r'streamController\.enqueue\("((?:[^"\\]|\\.)*)"\);', html, re.S)
    if not chunks:
        raise SystemExit('error: embedded conversation payload not found (page layout may have changed)')
    arr = []
    for c in chunks:
        u = js_unescape(c)
        if not u.lstrip().startswith('['):
            continue
        arr.extend(json.loads(u))
    if not arr:
        raise SystemExit('error: no conversation payload found')
    return arr


def decode(arr, i, seen=frozenset()):
    if i in seen:
        return '<cyclic>'
    v = arr[i] if isinstance(i, int) and 0 <= i < len(arr) else i
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            if isinstance(k, str) and k.startswith('_'):
                keyidx = int(k[1:])
                realkey = arr[keyidx] if 0 <= keyidx < len(arr) else k
                out[realkey] = decode(arr, val, seen | {i}) if isinstance(val, int) else val
            else:
                out[k] = val
        return out
    if isinstance(v, list):
        return [decode(arr, x, seen | {i}) if isinstance(x, int) else x for x in v]
    return v


def find_conversation(obj):
    if isinstance(obj, dict):
        if 'mapping' in obj and 'current_node' in obj:
            return obj
        for v in obj.values():
            c = find_conversation(v)
            if c:
                return c
    elif isinstance(obj, list):
        for v in obj:
            c = find_conversation(v)
            if c:
                return c
    return None


def message_text(m):
    if not m:
        return None
    c = m.get('content') or {}
    if c.get('content_type') == 'text':
        return '\n\n'.join(p for p in c.get('parts', []) if isinstance(p, str))
    parts = m.get('parts')
    if isinstance(parts, list):
        return '\n\n'.join(p.get('text', '') for p in parts if isinstance(p, dict) and p.get('type') == 'text')
    return None


SKIP = ('Original custom instructions no longer available',)


def to_markdown(conv):
    mapping = conv['mapping']
    node_id = conv.get('current_node')
    chain = []
    while node_id:
        n = mapping.get(node_id)
        if not n:
            break
        chain.append(n.get('message'))
        node_id = n.get('parent')
    chain.reverse()
    title = conv.get('title') or 'ChatGPT conversation'
    lines = ['# ' + title, '']
    for m in chain:
        if not m:
            continue
        role = (m.get('author') or {}).get('role') or m.get('role')
        if role not in ('user', 'assistant'):
            continue
        t = message_text(m)
        if not t or t in SKIP:
            continue
        lines += ['## ' + role.capitalize(), '', t, '']
    return title, '\n'.join(lines)


def block_to_md(b):
    t = b.get('type')
    if t == 'text' or (t is None and b.get('text')):
        return b.get('text')
    if t == 'thinking':
        return None
    if t == 'tool_use':
        body = json.dumps(b.get('input') or {}, ensure_ascii=False, indent=2)
        return f"**Tool call: {b.get('name') or 'unknown'}**\n\n```json\n{body}\n```"
    if t == 'tool_result':
        c = b.get('content')
        if isinstance(c, list):
            c = '\n'.join(x.get('text', '') for x in c if isinstance(x, dict) and x.get('type') == 'text')
        if not isinstance(c, str):
            c = json.dumps(c, ensure_ascii=False) if c is not None else ''
        return f"**Tool result**\n\n```\n{c}\n```"
    if b.get('text'):
        return b['text']
    return None


def claude_to_markdown(snap):
    title = snap.get('snapshot_name') or snap.get('name') or 'Claude conversation'
    lines = ['# ' + title, '']
    for m in snap.get('chat_messages') or []:
        role = 'User' if m.get('sender') == 'human' else 'Assistant'
        blocks = m.get('content')
        texts = []
        if isinstance(blocks, list) and blocks:
            for b in blocks:
                if b.get(REDACTED) or b.get('text') == REDACTED:
                    texts.append('*(artifact / tool output removed by Claude before publishing)*')
                    continue
                t = block_to_md(b)
                if t and t != REDACTED:
                    texts.append(t)
        elif m.get('text'):
            texts.append(m['text'])
        for f in (m.get('files') or []) + (m.get('attachments') or []):
            name = f.get('file_name') or f.get('name')
            if name:
                texts.append(f'*(attachment: {name})*')
        if not texts:
            continue
        lines += [f'## {role}', '', '\n\n'.join(texts), '']
    return title, '\n'.join(lines)


CDOM = 'cla' + 'ude.ai'
CURL_RE = r'cla' + r'ude\.(?:ai|site)/share/([0-9a-f][0-9a-f-]{15,})'


def handle_claude(share_id):
    snap = fetch_json(f'https://{CDOM}/api/chat_snapshots/{share_id}?rendering_mode=messages&render_all_tools=true',
                      f'https://{CDOM}/share/{share_id}')
    if snap.get('type') == 'error':
        raise SystemExit('error: ' + (snap.get('error') or {}).get('message', 'unknown API error'))
    title, md = claude_to_markdown(snap)
    return title, md


def main():
    ap = argparse.ArgumentParser(description='ChatGPT/Claude share link -> Markdown (no dependencies, Python 3 only)')
    ap.add_argument('url', help='https://chatgpt.com/share/... or https://' + CDOM + '/share/...')
    ap.add_argument('out', nargs='?', help='output .md file or directory (default: current directory)')
    ap.add_argument('--stdout', action='store_true', help='print to stdout instead of writing a file')
    args = ap.parse_args()

    m = re.search(r'chatgpt\.com/(share|c)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{2,4}-[0-9a-f]{4}-[0-9a-f]{12})', args.url)
    if m:
        arr = flat_array(fetch_html(f'https://chatgpt.com/share/{m.group(2)}'))
        root = decode(arr, 0)
        conv = find_conversation(root)
        if not conv:
            routes = (root.get('loaderData') or {}).get('routes/share.$shareId.($action)') or {}
            resp = routes.get('serverResponse') or {}
            if resp.get('type') == 'error' and resp.get('error'):
                raise SystemExit('error: ' + resp['error'])
            raise SystemExit('error: conversation data not found in payload')
        title, md = to_markdown(conv)
    else:
        m = re.search(CURL_RE, args.url)
        if not m:
            raise SystemExit('error: chat ID not found in URL (expected chatgpt.com/share/... or ' + CDOM + '/share/...)')
        title, md = handle_claude(m.group(1))

    if args.stdout:
        print(md)
        return
    out = args.out or '.'
    if os.path.isdir(out) or out.endswith('/'):
        path = os.path.join(out, re.sub(r'[\\/:*?"<>|\n\r\t]', '_', title).strip() + '.md')
    else:
        path = out if out.endswith('.md') else out + '.md'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(md)
    print(path)


if __name__ == '__main__':
    main()
