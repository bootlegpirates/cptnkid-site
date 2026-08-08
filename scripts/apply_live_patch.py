#!/usr/bin/env python3
"""
Repoint the cptnkid Claude Design bundle at the live Sveltia/CMS JSON files.

The exported index.html ships all content hard-coded inside the bundle. This
rewrites it so the app reads, at startup, from:

    /data/posts.json       -> homepage posts (films / anime / docs)
    /data/amsterdam.json   -> Amsterdam-theme background videos
    /data/instagram.json   -> the Random grid posts
    /data/settings.json    -> wordmark text
    /data/anime-tv.json    -> the CRT "Anime TV" playlist (nested sub-bundle)

Each hard-coded array falls back to itself when the fetch is unavailable
(file://, preview, offline), so the standalone export still renders.

Two independent, idempotent stages, each guarded by its own marker:
  * MAIN    -> the outer app template
  * CRT/TV  -> the camcorder sub-bundle inside the manifest asset

Usage:
    python3 apply_live_patch.py [INPUT_HTML] [OUTPUT_HTML]   # default index.html in place
"""
import re, sys, json, gzip, base64

MARKER    = "__CPTNKID_LIVE_PATCH__"
TV_MARKER = "__CPTNKID_TV_PATCH__"
CRT_UUID  = "77ecc6c3-5fa1-4361-9f69-4d9feccc21cc"

# ---- shared bootstrap helpers (inlined into both docs) ----------------------
_YT = (r"function ytId(u){u=String(u||'');"
       r"var m=u.match(/[?&]v=([A-Za-z0-9_-]{11})/)||"
       r"u.match(/youtu\.be\/([A-Za-z0-9_-]{11})/)||"
       r"u.match(/embed\/([A-Za-z0-9_-]{11})/);"
       r"return m?m[1]:(/^[A-Za-z0-9_-]{11}$/.test(u)?u:'');}")
_GET = (r"function get(u){try{var x=new XMLHttpRequest();"
        r"x.open('GET',u+'?t='+Date.now(),false);x.send();"
        r"if(x.status>=200&&x.status<300)return JSON.parse(x.responseText);}"
        r"catch(e){}return null;}")

MAIN_BOOTSTRAP = ("<!--%s-->\n<script>\n(function(){\n"
    "  if (window.__LIVE) return;\n  var L={};\n  " + _GET + "\n  " + _YT + "\n"
    "  var GENRE={movies:'Movies',anime:'Anime',documentary:'Documentary'};\n"
    "  var p=get('/data/posts.json');\n"
    "  if(p&&p.posts&&p.posts.length){L.posts=p.posts.map(function(e){var o={};for(var k in e)o[k]=e[k];"
    "o.genre=GENRE[e.type]||(e.type?e.type.charAt(0).toUpperCase()+e.type.slice(1):'');"
    "o.rating=typeof e.rating==='number'?e.rating:(parseFloat(e.rating)||0);"
    "o.pluses=e.pluses||[];o.minuses=e.minuses||[];return o;});}\n"
    "  var a=get('/data/amsterdam.json');\n"
    "  if(a&&a.videos&&a.videos.length){var ids=[],mx=[];a.videos.forEach(function(v){var id=ytId(v.url);"
    "if(id){ids.push(id);mx.push(parseInt(v.maxSeconds,10)||0);}});if(ids.length)L.amsterdam={ids:ids,maxs:mx};}\n"
    "  var ig=get('/data/instagram.json');\n  if(ig&&ig.posts&&ig.posts.length)L.instagram=ig.posts;\n"
    "  var s=get('/data/settings.json');\n  if(s)L.settings=s;\n"
    "  window.__LIVE=L;\n})();\n</script>") % MARKER

TV_BOOTSTRAP = ("<!--%s-->\n<script>\n(function(){\n"
    "  if (window.__LIVE_TV) return;\n  " + _GET + "\n  " + _YT + "\n"
    "  var d=get('/data/anime-tv.json'),out=[];\n"
    "  if(d&&d.videos){d.videos.forEach(function(v){var id=ytId(v.url);if(!id)return;"
    "var o={id:id,dur:parseInt(v.dur,10)||600};"
    "if(v.maxStart!=null&&v.maxStart!=='')o.maxStart=parseInt(v.maxStart,10)||0;out.push(o);});}\n"
    "  if(out.length)window.__LIVE_TV=out;\n})();\n</script>") % TV_MARKER

MAIN_PATCHES = [
    ("_bgVideos = ['f1a6W2ZA8v0', 'qoqr_9U6ZoU'];",
     "_bgVideos = (window.__LIVE&&window.__LIVE.amsterdam&&window.__LIVE.amsterdam.ids.length)?window.__LIVE.amsterdam.ids:['f1a6W2ZA8v0', 'qoqr_9U6ZoU'];"),
    ("_bgVideoMax = [3300, 2160];",
     "_bgVideoMax = (window.__LIVE&&window.__LIVE.amsterdam&&window.__LIVE.amsterdam.maxs.length)?window.__LIVE.amsterdam.maxs:[3300, 2160];"),
    ("text: 'BOOTLEG PIRATES & STUDIOS & '",
     "text: ((window.__LIVE&&window.__LIVE.settings&&window.__LIVE.settings.wordmark)||'BOOTLEG PIRATES & STUDIOS & ')"),
]
# Homepage posts (_exCache) and the Random/instagram grid pull ONLY from the CMS.
# Their baked demo arrays are replaced with [] so nothing is hard-coded: if the
# /data/*.json fetch returns nothing, the grid is simply empty (no ghost demo data).
MAIN_EMPTY = [
    ("this._exCache = [",
     "this._exCache = (window.__LIVE&&window.__LIVE.posts&&window.__LIVE.posts.length)?window.__LIVE.posts:[]"),
    ("const posts = [",
     "const posts = (window.__LIVE&&window.__LIVE.instagram&&window.__LIVE.instagram.length)?window.__LIVE.instagram:[]"),
]

def _empty_arrays(text, patches, label):
    """Replace `anchor [ ... ]` (bracket-matched) with `repl`, dropping the array."""
    for anchor, repl in patches:
        i = text.find(anchor)
        if i < 0:
            raise SystemExit(f"[{label}] empty-anchor not found (bundle changed?): {anchor!r}")
        start = i + len(anchor) - 1          # index of the '['
        depth = 0; k = start
        while k < len(text):
            c = text[k]
            if c == '[': depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0: k += 1; break
            k += 1
        text = text[:i] + repl + text[k:]
    return text

TV_PATCHES = [
    ("this.videos = [",
     "this.videos = (window.__LIVE_TV&&window.__LIVE_TV.length)?window.__LIVE_TV:["),
]

def _esc(s):  # keep inner </script> from closing the outer inline tag
    return re.sub(r'</script>', r'<\\/script>', json.dumps(s, ensure_ascii=False), flags=re.I)

def _apply(text, bootstrap, patches, label):
    text = text.replace("<head>", "<head>\n" + bootstrap + "\n", 1)
    for anchor, repl in patches:
        if anchor not in text:
            raise SystemExit(f"[{label}] anchor not found (bundle changed?): {anchor!r}")
        text = text.replace(anchor, repl, 1)
    return text

def patch_main(html):
    m = re.search(r'(<script type="__bundler/template">\s*)(.*?)(\s*</script>)', html, re.S)
    tmpl = json.loads(m.group(2))
    if MARKER in tmpl:
        return html, False
    tmpl = _apply(tmpl, MAIN_BOOTSTRAP, MAIN_PATCHES, "main")
    tmpl = _empty_arrays(tmpl, MAIN_EMPTY, "main")
    return html[:m.start(2)] + _esc(tmpl) + html[m.end(2):], True

def patch_crt(html):
    mm = re.search(r'(<script type="__bundler/manifest">\s*)(.*?)(\s*</script>)', html, re.S)
    manifest = json.loads(mm.group(2))
    e = manifest.get(CRT_UUID)
    if not e:
        return html, False
    raw = base64.b64decode(e['data'])
    crt_js = (gzip.decompress(raw) if e.get('compressed') else raw).decode('utf-8')
    lit = re.search(r'window\.__CRT_DOC\s*=\s*(".*")\s*;?\s*$', crt_js, re.S)
    crt_html = json.loads(lit.group(1))
    if TV_MARKER in crt_html:
        return html, False
    tm = re.search(r'(<script type="__bundler/template">\s*)(.*?)(\s*</script>)', crt_html, re.S)
    crt_tmpl = json.loads(tm.group(2))
    crt_tmpl = _apply(crt_tmpl, TV_BOOTSTRAP, TV_PATCHES, "crt")
    crt_html = crt_html[:tm.start(2)] + _esc(crt_tmpl) + crt_html[tm.end(2):]
    new_js = "window.__CRT_DOC = " + json.dumps(crt_html, ensure_ascii=False) + ";"
    e['data'] = base64.b64encode(gzip.compress(new_js.encode('utf-8'))).decode('ascii')
    e['compressed'] = True
    new_manifest = json.dumps(manifest, ensure_ascii=False)
    return html[:mm.start(2)] + new_manifest + html[mm.end(2):], True

def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "index.html"
    dst = sys.argv[2] if len(sys.argv) > 2 else src
    html = open(src, encoding="utf-8").read()
    html, did_main = patch_main(html)
    html, did_crt  = patch_crt(html)
    if not (did_main or did_crt):
        print("Already patched (both markers present) — nothing to do.")
        return
    open(dst, "w", encoding="utf-8").write(html)
    print(f"Patched {src} -> {dst}  (main={did_main}, animeTV={did_crt}, {len(html):,} bytes)")

if __name__ == "__main__":
    main()
