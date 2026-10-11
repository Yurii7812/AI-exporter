#!/usr/bin/env python3
import argparse
import json
import os
import re
import urllib.request

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f', '"': '"', "'": "'", '\\': '\\', '/': '/'}


def fetch_html(share_url):
    req = urllib.request.Request(share_url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8')


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


def main():
    ap = argparse.ArgumentParser(description='ChatGPT share link -> Markdown (no dependencies, Python 3 only)')
    ap.add_argument('url', help='https://chatgpt.com/share/...')
    ap.add_argument('out', nargs='?', help='output .md file or directory (default: current directory)')
    ap.add_argument('--stdout', action='store_true', help='print to stdout instead of writing a file')
    args = ap.parse_args()

    m = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', args.url)
    if not m:
        raise SystemExit('error: share ID not found in URL')
    arr = flat_array(fetch_html('https://chatgpt.com/share/' + m.group(1)))
    conv = find_conversation(decode(arr, 0))
    if not conv:
        raise SystemExit('error: conversation data not found in payload')
    title, md = to_markdown(conv)

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
