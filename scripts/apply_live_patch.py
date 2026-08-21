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
def _re_thumb():
    import re as _r
    return _r.compile(r'\s*<div id="__bundler_thumbnail">.*?</div>', _r.S)
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
    "  if(p&&p.posts&&p.posts.length){var pp=p.posts.slice().sort(function(a,b){"
    "var ad=a.dateAdded?Date.parse(a.dateAdded):0,bd=b.dateAdded?Date.parse(b.dateAdded):0;"
    "return (bd||0)-(ad||0);});"
    "L.posts=pp.map(function(e){var o={};for(var k in e)o[k]=e[k];"
    "o.genre=GENRE[e.type]||(e.type?e.type.charAt(0).toUpperCase()+e.type.slice(1):'');"
    "o.rating=typeof e.rating==='number'?e.rating:(parseFloat(e.rating)||0);"
    "o.pluses=e.pluses||[];o.minuses=e.minuses||[];return o;});}\n"
    "  var a=get('/data/amsterdam.json');\n"
    "  if(a&&a.videos&&a.videos.length){var ids=[],mx=[];a.videos.forEach(function(v){var id=ytId(v.url);"
    "if(id){ids.push(id);mx.push(parseInt(v.maxSeconds,10)||0);}});if(ids.length)L.amsterdam={ids:ids,maxs:mx};}\n"
    "  var ig=get('/data/instagram.json');\n  if(ig&&ig.posts&&ig.posts.length)L.instagram=ig.posts;\n"
    "  var s=get('/data/settings.json');\n  if(s)L.settings=s;\n"
    "  var rnd=get('/data/random.json');\n  if(rnd&&rnd.links&&rnd.links.length)L.random=rnd.links;\n"
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
            print(f"[{label}] note: empty-anchor already applied or changed; skipped")
            continue
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
            print(f"[{label}] note: patch anchor already applied or changed; skipped")
            continue
        text = text.replace(anchor, repl, 1)
    return text


def _rename_subgenre_label(tmpl):
    """Rename the on-page 'Subgenre:' meta label to 'Genre:' (the CMS field keeps the
    JSON key `subgenre` to avoid colliding with the category `genre`). Idempotent."""
    return tmpl.replace('>Subgenre:</div>', '>Genre:</div>')

def _empty_live_fallbacks(tmpl):
    """Empty demo arrays in the already-patched `...:[demo]` form so a re-export that
    re-introduced demo content stays CMS-only. Idempotent (empty -> empty)."""
    for anchor in ('?window.__LIVE.posts:[', '?window.__LIVE.instagram:[', '?window.__LIVE.random:['):
        i = tmpl.find(anchor)
        if i < 0:
            continue
        start = i + len(anchor) - 1
        depth = 0; k = start
        while k < len(tmpl):
            c = tmpl[k]
            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0:
                    k += 1; break
            k += 1
        tmpl = tmpl[:start] + '[]' + tmpl[k:]
    return tmpl

def _cms_only_homepage(tmpl):
    """Best-effort: strip the baked demo cards so the homepage/directors come ONLY
    from the CMS. Safe no-ops (with a printed note) if a future export changes shape."""
    import re as _re
    # a) empty the `hard` prefix array that is concat'd in front of CMS posts
    hb = tmpl.find("const hard = [")
    if hb != -1:
        depth=0; k=hb+len("const hard = ")
        while k < len(tmpl):
            c=tmpl[k]
            if c=='[': depth+=1
            elif c==']':
                depth-=1
                if depth==0: k+=1; break
            k+=1
        tmpl = tmpl[:hb] + "const hard = [];" + tmpl[k+1:]
    else:
        print("[homepage] note: `const hard = [` not found; skipped")
    # b) re-index the render loop so CMS posts fill from slot 0 (not 3)
    if "const idx = 3 + k;" in tmpl:
        tmpl = tmpl.replace("const idx = 3 + k;", "const idx = k;", 1)
    else:
        print("[homepage] note: `const idx = 3 + k;` not found; skipped")
    # c) derive the Directors filter from CMS instead of hard-coded names
    tmpl = _re.sub(r"const directors = \[[^\]]*\];",
                   "const directors = [...new Set(this._examples().map(e => e.director).filter(Boolean))];",
                   tmpl, count=1)
    # d0) Random grid: render exactly one tile per CMS post (was a fixed 30 that
    #     cycled/repeated posts). Scoped to the igTiles builder after the IG fallback.
    ig = tmpl.find("window.__LIVE.instagram:[")
    if ig != -1:
        win_end = ig + 2500
        seg = tmpl[ig:win_end]
        if "for (let i = 0; i < 30; i++)" in seg:
            seg = seg.replace("for (let i = 0; i < 30; i++)",
                              "for (let i = 0; i < posts.length; i++)", 1)
            tmpl = tmpl[:ig] + seg + tmpl[win_end:]
        else:
            print("[random] note: 30-tile loop not found; skipped")
    else:
        print("[random] note: igTiles builder not found; skipped")

    # d) remove the three frozen demo cards (postVisible0/1/2 sc-if blocks)
    for idx in ("0","1","2"):
        s0 = tmpl.find('<sc-if value="{{ postVisible%s }}"' % idx)
        if s0 == -1:
            print(f"[homepage] note: static card {idx} not found; skipped"); continue
        tok=_re.compile(r"<sc-if\b|</sc-if>"); depth=0
        for mt in tok.finditer(tmpl, s0):
            if mt.group(0).startswith("</"):
                depth-=1
                if depth==0:
                    e=mt.end()
                    while e<len(tmpl) and tmpl[e] in " \t": e+=1
                    if e<len(tmpl) and tmpl[e]=="\n": e+=1
                    tmpl = tmpl[:s0] + tmpl[e:]; break
            else: depth+=1
    return tmpl


def _random_thumbnails(tmpl):
    """Best-effort: Random grid becomes a full-width masonry of CMS thumbnails (uniform
    column width, natural/variable height); clicking a tile opens an Instagram-post embed
    on the right (image or video viewable inside), with a thumbnail fallback for
    non-Instagram URLs. No-ops safely if the export shape changes."""
    import re as _r
    # (a) full-width: drop the 1500px cap so the grid reaches both page edges
    tmpl = tmpl.replace('display:flex;gap:16px;align-items:flex-start;max-width:1500px;margin:0 auto;padding-top:0;',
                        'display:flex;gap:16px;align-items:flex-start;max-width:none;margin:0;padding-top:0;', 1)
    # (b) igTiles builder -> thumbnails carrying Instagram embed data; click opens panel
    s = tmpl.find("igTiles: (this._igTiles")
    e = tmpl.find("randomDetail: this.state.randomDetail,")
    if s != -1 and e != -1 and s < e:
        builder = (
"igTiles: (this._igTiles || (this._igTiles = (() => {\n"
"        const items = (window.__LIVE&&window.__LIVE.random&&window.__LIVE.random.length)?window.__LIVE.random:[];\n"
"        const _ig = (u) => { u = String(u||''); var keys=['/p/','/reel/','/tv/']; for (var a=0;a<keys.length;a++){ var k=u.indexOf(keys[a]); if(k>=0){ var rest=u.slice(k+keys[a].length); var code=''; for(var b=0;b<rest.length;b++){ var c=rest[b]; if((c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9')||c==='_'||c==='-') code+=c; else break; } if(code) return code; } } return ''; };\n"
"        return items.filter(r => r && r.thumb).map((r, i) => {\n"
"          const code = _ig(r.url || '');\n"
"          const embed = code ? ('https://www.instagram.com/p/' + code + '/embed') : '';\n"
"          const d = { thumb: r.thumb, url: r.url || '', embed: embed, hasEmbed: !!embed, noEmbed: !embed, badge: (r.kind === 'video') ? 'flex' : 'none', index: i };\n"
"          return Object.assign({}, d, { onClick: () => this.setState({ randomDetail: d }) });\n"
"        });\n"
"      })())),\n      ")
        tmpl = tmpl[:s] + builder + tmpl[e:]
    else:
        print("[random] note: igTiles builder anchors not found; skipped")
    # (c) prev/next rebuild randomDetail from the new item shape
    tmpl = tmpl.replace("Object.assign({ bg: t.bg, index: n }, t.post)",
                        "{ thumb: t.thumb, url: t.url, embed: t.embed, hasEmbed: t.hasEmbed, noEmbed: t.noEmbed, badge: t.badge, index: n }")
    # (d) add openRandomLink next to closeRandomDetail
    anc = "closeRandomDetail: () => this.setState({ randomDetail: null }),"
    if anc in tmpl and "openRandomLink" not in tmpl:
        tmpl = tmpl.replace(anc, anc + " openRandomLink: () => { const d = this.state.randomDetail; if (d && d.url) window.open(d.url, '_blank', 'noopener'); },", 1)
    # (e) gradient tile -> masonry thumbnail (uniform width, natural/variable height) + video badge
    old_tile = ('<div sc-camel-on-click="{{ t.onClick }}" style="break-inside:avoid;-webkit-column-break-inside:avoid;'
                'width:100%;margin-bottom:8px;border-radius:6px;overflow:hidden;aspect-ratio:{{ t.ar }};background:{{ t.bg }};cursor:pointer;"></div>')
    new_tile = ('<div sc-camel-on-click="{{ t.onClick }}" style="break-inside:avoid;-webkit-column-break-inside:avoid;'
                'width:100%;margin-bottom:8px;border-radius:6px;overflow:hidden;cursor:pointer;position:relative;background:#111;">'
                '<img src="{{ t.thumb }}" loading="lazy" style="width:100%;display:block;"/>'
                '<div style="position:absolute;top:8px;right:8px;display:{{ t.badge }};width:28px;height:28px;border-radius:999px;background:rgba(0,0,0,0.55);align-items:center;justify-content:center;">'
                '<svg width="12" height="12" viewBox="0 0 12 12" fill="#ffffff"><path d="M3 2l7 4-7 4z"/></svg></div></div>')
    if old_tile in tmpl:
        tmpl = tmpl.replace(old_tile, new_tile, 1)
    else:
        print("[random] note: gradient tile markup not found; skipped")
    # (f) rebuild the rnd-detail panel as an Instagram embed (image/video inside)
    d = tmpl.find('class="rnd-detail"')
    if d != -1:
        s2 = tmpl.rfind('<sc-if value="{{ randomDetail }}"', 0, d)
        tok = _r.compile(r'<sc-if\b|</sc-if>'); depth = 0; e2 = None
        for mt in tok.finditer(tmpl, s2):
            if mt.group(0).startswith('</'):
                depth -= 1
                if depth == 0:
                    e2 = mt.end(); break
            else:
                depth += 1
        if e2:
            old_block = tmpl[s2:e2]
            if '{{ randomDetail.embed }}' in old_block:
                print("[random] note: detail panel already Instagram-embed; left as-is")
                return tmpl
            mtopbar = ''
            mi = old_block.find('<div class="rnd-mtopbar"')
            if mi != -1:
                dtok = _r.compile(r'<div\b|</div>'); dd = 0; mj = None
                for mt in dtok.finditer(old_block, mi):
                    if mt.group(0).startswith('</'):
                        dd -= 1
                        if dd == 0:
                            mj = mt.end(); break
                    else:
                        dd += 1
                if mj:
                    mtopbar = old_block[mi:mj]
            new_panel = ('<sc-if value="{{ randomDetail }}" hint-placeholder-val="{{ false }}">\n'
'          <div class="rnd-detail" style="flex:0 0 48%;max-width:750px;align-self:stretch;">\n'
'            <div class="rnd-detail-card" style="position:sticky;top:66px;border-radius:16px;overflow:hidden;background:#0d0d0f;display:flex;flex-direction:column;height:auto;box-sizing:border-box;box-shadow:0 20px 60px rgba(0,0,0,0.5);">\n'
'              <sc-if value="{{ randomDetail.hasEmbed }}" hint-placeholder-val="{{ true }}">\n'
'                <iframe src="{{ randomDetail.embed }}" title="Instagram post" style="width:100%;height:600px;border:0;display:block;background:#ffffff;" scrolling="no" allowtransparency="true" allow="autoplay; clipboard-write; encrypted-media; picture-in-picture; web-share" allowfullscreen=""></iframe>\n'
'              </sc-if>\n'
'              <sc-if value="{{ randomDetail.noEmbed }}" hint-placeholder-val="{{ false }}">\n'
'                <div style="flex:1 1 auto;min-height:0;position:relative;background:#111;display:flex;align-items:center;justify-content:center;">\n'
'                  <img src="{{ randomDetail.thumb }}" style="max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain;display:block;"/>\n'
'                  <div style="position:absolute;top:12px;right:12px;display:{{ randomDetail.badge }};width:44px;height:44px;border-radius:999px;background:rgba(0,0,0,0.6);align-items:center;justify-content:center;"><svg width="18" height="18" viewBox="0 0 12 12" fill="#ffffff"><path d="M3 2l7 4-7 4z"/></svg></div>\n'
'                </div>\n'
"                <div style=\"flex:none;display:flex;align-items:center;gap:12px;padding:14px 16px;background:#ffffff;\">\n"
"                  <span style=\"flex:1 1 auto;min-width:0;font-family:'Geist',-apple-system,BlinkMacSystemFont,sans-serif;font-size:14px;color:#555;letter-spacing:-0.01em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;\">{{ randomDetail.url }}</span>\n"
"                  <button sc-camel-on-click=\"{{ openRandomLink }}\" style=\"flex:none;display:inline-flex;align-items:center;justify-content:center;height:40px;padding:0 20px;border:none;border-radius:10px;background:#5b6cf5;color:#fff;font-family:'Geist',-apple-system,BlinkMacSystemFont,sans-serif;font-size:15px;font-weight:600;letter-spacing:-0.01em;cursor:pointer;\">Open post</button>\n"
'                </div>\n'
'              </sc-if>\n'
'            </div>\n'
'          </div>\n'
'        </sc-if>')
            if mtopbar:
                new_panel = new_panel.replace(
                    '<div class="rnd-detail" style="flex:0 0 48%;max-width:750px;align-self:stretch;">\n',
                    '<div class="rnd-detail" style="flex:0 0 48%;max-width:750px;align-self:stretch;">\n            ' + mtopbar + '\n', 1)
            tmpl = tmpl[:s2] + new_panel + tmpl[e2:]
    else:
        print("[random] note: rnd-detail panel not found; skipped")
    return tmpl

def _carousel_images(tmpl):
    """Best-effort: homepage post cards fill their carousel with CMS-uploaded images
    (post field `images`, possibly multiple), use a CMS-provided YouTube URL for the
    trailer, and show a video thumbnail (custom `videoThumb`, else the YouTube poster)
    before playback. No-ops safely if the export shape changes."""
    # a) tilesFor: use uploaded images (robust to string/object lists); else placeholders
    tf_old = ("const tilesFor = (i) => { const t = tilePatterns[i % tilePatterns.length]; "
              "return [...t, ...t].map(ar => ({ ar, bg: phBg })); };")
    tf_new = ("const BLANK = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';\n"
              "    const tilesFor = (i, e) => {\n"
              "      const pat = tilePatterns[i % tilePatterns.length];\n"
              "      const imgs = (e && Array.isArray(e.images)) ? e.images.map(function(x){return (typeof x==='string')?x:((x&&(x.src||x.image||x.path))||'');}).filter(Boolean) : ((e && e.poster) ? [e.poster] : []);\n"
              "      if (imgs.length) return imgs.map((src) => ({ ar: 'auto', bg: phBg, img: src, imgShow: 'block' }));\n"
              "      return [...pat, ...pat].map(ar => ({ ar, bg: phBg, img: BLANK, imgShow: 'none' }));\n"
              "    };")
    if tf_old in tmpl:
        tmpl = tmpl.replace(tf_old, tf_new, 1)
        tmpl = tmpl.replace("tiles: tilesFor(k),", "tiles: tilesFor(k, e),", 1)
    else:
        print("[carousel] note: tilesFor not found; skipped")
    # b) render <img> inside each carousel tile
    tile_old = ('<div class="bp-ph" style="flex:0 0 auto;height:var(--th);width:auto;aspect-ratio:{{ t.ar }};'
                'background-color:{{ t.bg }};border-radius:16px;overflow:hidden;cursor:pointer;position:relative;"></div>')
    tile_new = ('<div class="bp-ph" style="flex:0 0 auto;height:var(--th);width:auto;aspect-ratio:{{ t.ar }};'
                'background-color:{{ t.bg }};border-radius:16px;overflow:hidden;cursor:pointer;position:relative;">'
                '<img src="{{ t.img }}" loading="lazy" style="display:{{ t.imgShow }};height:100%;width:auto;object-fit:cover;"/></div>')
    if tile_old in tmpl:
        tmpl = tmpl.replace(tile_old, tile_new, 1)
    else:
        print("[carousel] note: bp-ph tile markup not found; skipped")
    # c) YouTube id helper (regex-free) + per-post vid
    yt = ("const _yt = (u) => { u = String(u||''); var id=''; var keys=['v=','youtu.be/','embed/','shorts/'];"
          " for (var i=0;i<keys.length;i++){ var k=u.indexOf(keys[i]); if(k>=0){ id=u.substr(k+keys[i].length,11); break; } }"
          " if(!id && u.length===11) id=u; var ok=id.length===11;"
          " for(var j=0;j<id.length;j++){ var c=id[j]; if(!((c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9')||c==='_'||c==='-')){ ok=false; break; } }"
          " return ok?id:''; };\n    ")
    if "const examplePosts = [];" in tmpl and "_yt = (u) =>" not in tmpl:
        tmpl = tmpl.replace("const examplePosts = [];", yt + "const examplePosts = [];", 1)
    if "const open = openPost === idx;" in tmpl and "const vid = _yt(" not in tmpl:
        tmpl = tmpl.replace("const open = openPost === idx;",
                            "const open = openPost === idx;\n      const vid = _yt(e.youtube || e.video || e.trailer || '');", 1)
    # d) trailerEmbed uses the CMS YouTube URL (else auto-search); add videoBg poster
    old_tr = ("trailerEmbed: 'https://www.youtube.com/embed?listType=search&list=' + "
              "encodeURIComponent(e.title + ' ' + e.year + ' official trailer') + "
              "'&autoplay=1&rel=0&playsinline=1&modestbranding=1&iv_load_policy=3',")
    new_tr = ("trailerEmbed: vid ? ('https://www.youtube.com/embed/' + vid + '?autoplay=1&rel=0&playsinline=1&modestbranding=1&iv_load_policy=3') : "
              "('https://www.youtube.com/embed?listType=search&list=' + encodeURIComponent(e.title + ' ' + e.year + ' official trailer') + '&autoplay=1&rel=0&playsinline=1&modestbranding=1&iv_load_policy=3'),\n"
              "        videoBg: e.videoThumb ? ('url(\"' + e.videoThumb + '\")') : (vid ? ('url(\"https://img.youtube.com/vi/' + vid + '/hqdefault.jpg\")') : 'none'),")
    if old_tr in tmpl:
        tmpl = tmpl.replace(old_tr, new_tr, 1)
    else:
        print("[carousel] note: trailerEmbed not found; skipped")
    # e) idle video area shows the videoBg poster
    old_idle = 'cursor:pointer;background-color:{{ ex.detailPhBg }};background-size:cover;background-position:center;"></div>'
    new_idle = 'cursor:pointer;background-color:{{ ex.detailPhBg }};background-image:{{ ex.videoBg }};background-size:cover;background-position:center;"></div>'
    if old_idle in tmpl:
        tmpl = tmpl.replace(old_idle, new_idle, 1)
    return tmpl

def _inject_ig_resize(out):
    if "__IG_RESIZE__" in out:
        return out
    script = ('<script>\n(function(){\n  if (window.__IG_RESIZE__) return; window.__IG_RESIZE__ = 1;\n'
              '  window.addEventListener("message", function(e){\n'
              '    if (!e || e.data == null) return;\n'
              '    if (String(e.origin||"").indexOf("instagram.com") < 0) return;\n'
              '    var d = e.data;\n'
              '    try { if (typeof d === "string") d = JSON.parse(d); } catch(_){ return; }\n'
              '    if (!d || d.type !== "MEASURE" || !d.details) return;\n'
              '    var h = parseInt(d.details.height, 10); if (!h) return;\n'
              '    var fr = document.getElementsByTagName("iframe");\n'
              '    for (var i=0;i<fr.length;i++){ if (fr[i].contentWindow === e.source){ fr[i].style.height = h + "px"; break; } }\n'
              '  }, false);\n})();\n</script>\n')
    return out.replace("</head>", script + "</head>", 1)

def patch_main(html):
    m = re.search(r'(<script type="__bundler/template">\s*)(.*?)(\s*</script>)', html, re.S)
    tmpl = json.loads(m.group(2))
    if MARKER in tmpl:
        return html, False
    tmpl = _apply(tmpl, MAIN_BOOTSTRAP, MAIN_PATCHES, "main")
    tmpl = _empty_arrays(tmpl, MAIN_EMPTY, "main")
    tmpl = _empty_live_fallbacks(tmpl)
    tmpl = _rename_subgenre_label(tmpl)
    tmpl = _cms_only_homepage(tmpl)
    tmpl = _random_thumbnails(tmpl)
    tmpl = _carousel_images(tmpl)
    out = html[:m.start(2)] + _esc(tmpl) + html[m.end(2):]
    out = _re_thumb().sub("", out, count=1)  # drop the pre-load "CK" splash
    out = _inject_ig_resize(out)
    return out, True

def _find_crt_uuid(manifest):
    """The anime-TV camcorder sub-bundle's asset id changes between exports; find it
    by content (it is the asset that defines window.__CRT_DOC and this.videos)."""
    if CRT_UUID in manifest:
        return CRT_UUID
    for uuid, e in manifest.items():
        try:
            rawb = base64.b64decode(e['data'])
            txt = (gzip.decompress(rawb) if e.get('compressed') else rawb).decode('utf-8', 'replace')
        except Exception:
            continue
        if '__CRT_DOC' in txt and 'this.videos' in txt:
            return uuid
    return None

def patch_crt(html):
    mm = re.search(r'(<script type="__bundler/manifest">\s*)(.*?)(\s*</script>)', html, re.S)
    manifest = json.loads(mm.group(2))
    uuid = _find_crt_uuid(manifest)
    e = manifest.get(uuid) if uuid else None
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
