#!/usr/bin/env python3
"""Book Summary Auto Poster: RSS -> OpenRouter free models -> Blogger."""
import os
import re
import json
import time
import html as H
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests
import feedparser
from bs4 import BeautifulSoup

CATEGORIES = [
    "Non-Fiction",
    "Business & Investing",
    "Self-Help & Self-Improvement",
    "Psychology",
    "Philosophy",
    "Science & Technology",
    "Biography & Memoir",
    "History",
    "Fiction",
    "Classics",
    "Sci-Fi & Fantasy",
    "Personal Finance",
    "Productivity & Time Management",
]

KW = {
    "Non-Fiction": ["nonfiction", "non-fiction", "essays", "reportage", "investigation", "true story", "journalist"],
    "Business & Investing": ["business", "investing", "investor", "entrepreneur", "startup", "economy", "economics", "management", "leadership", "marketing", "wall street", "stock", "capital", "ceo", "market"],
    "Self-Help & Self-Improvement": ["self-help", "self help", "self-improvement", "habits", "mindset", "happiness", "motivation", "confidence", "resilience", "personal growth", "wellbeing"],
    "Psychology": ["psychology", "psychological", "mind", "brain", "behavior", "behaviour", "therapy", "anxiety", "trauma", "cognitive", "mental health", "emotion", "neuroscience"],
    "Philosophy": ["philosophy", "philosopher", "ethics", "stoic", "stoicism", "existential", "metaphysics", "moral", "nietzsche", "plato", "kant", "meaning of life"],
    "Science & Technology": ["science", "scientist", "technology", "physics", "biology", "climate", "ai", "artificial intelligence", "space", "astronomy", "evolution", "quantum", "internet", "engineering", "medicine", "mathematics"],
    "Biography & Memoir": ["biography", "memoir", "autobiography", "life of", "her life", "his life", "diaries", "letters"],
    "History": ["history", "historian", "historical", "war", "empire", "ancient", "medieval", "revolution", "century", "colonial"],
    "Fiction": ["novel", "fiction", "short stories", "thriller", "mystery", "romance", "debut novel", "literary fiction", "crime"],
    "Classics": ["classic", "classics", "dostoevsky", "tolstoy", "austen", "dickens", "shakespeare", "homer", "orwell", "great gatsby", "anniversary edition", "canon"],
    "Sci-Fi & Fantasy": ["science fiction", "sci-fi", "fantasy", "dystopian", "dragon", "magic", "space opera", "speculative", "wizard", "cyberpunk"],
    "Personal Finance": ["personal finance", "money", "savings", "debt", "retirement", "budget", "wealth", "financial independence", "mortgage", "frugal"],
    "Productivity & Time Management": ["productivity", "time management", "focus", "procrastination", "deep work", "getting things done", "workflow", "organize", "efficiency", "routine"],
}

BATCH = int(os.getenv("POSTS_PER_CATEGORY", "5") or "5")
MAX_ATTEMPTS = 10
STATE_FILE = "book_state.json"
FEEDS_FILE = "book_feeds.txt"
START = time.time()
DEADLINE = 40 * 60
HEAD = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120 Safari/537.36"
}
BOOKISH = re.compile(
    r"\b(book|books|novel|memoir|author|biography|autobiography|essays|poems|"
    r"stories|read|reading|review|publish\w*)\b", re.I)
BAD_IMG = re.compile(
    r"(logo|avatar|icon|sprite|pixel|1x1|blank|placeholder|gravatar|badge|\.svg|\.gif)", re.I)
OR_KEY = os.getenv("OPENROUTER_API_KEY", "")


# ---------------------------------------------------------------- state
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        s = {}
    s.setdefault("cat_index", 0)
    s.setdefault("count", 0)
    s.setdefault("posted", [])
    return s


def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- feeds
def text_of(h):
    return BeautifulSoup(h or "", "html.parser").get_text(" ", strip=True)


def load_feeds():
    with open(FEEDS_FILE, encoding="utf-8") as f:
        txt = f.read()
    urls = re.findall(r"\((https?://[^)\s]+)\)", txt)
    if not urls:
        urls = re.findall(r"https?://[^\s\]\)]+", txt)
    return list(dict.fromkeys(urls))


def feed_images(e, link):
    urls = []
    for k in ("media_content", "media_thumbnail"):
        for m in e.get(k, []) or []:
            if m.get("url"):
                urls.append(m["url"])
    for l in (e.get("links", []) or []) + (e.get("enclosures", []) or []):
        if str(l.get("type", "")).startswith("image"):
            u = l.get("href") or l.get("url")
            if u:
                urls.append(u)
    blobs = [e.get("summary", "")]
    for c in e.get("content", []) or []:
        blobs.append(c.get("value", ""))
    for b in blobs:
        for i in BeautifulSoup(b or "", "html.parser").find_all("img"):
            src = i.get("src") or i.get("data-src")
            if src:
                urls.append(src)
    return [urljoin(link, u) for u in urls]


def fetch_feed(url):
    try:
        r = requests.get(url, headers=HEAD, timeout=20)
        d = feedparser.parse(r.content)
    except Exception:
        return []
    out = []
    for e in d.entries[:25]:
        link = e.get("link")
        title = text_of(e.get("title", ""))
        if not link or not title:
            continue
        ts = 0
        if e.get("published_parsed"):
            try:
                ts = time.mktime(e.published_parsed)
            except Exception:
                ts = 0
        out.append({
            "title": title,
            "link": link,
            "feed": d.feed.get("title", url),
            "summary": text_of(e.get("summary", "")),
            "images": feed_images(e, link),
            "ts": ts,
        })
    return out


def scores(x):
    t = ((x["title"] + " ") * 2 + x["summary"][:1500]).lower()
    result = []
    for c in CATEGORIES:
        total = 0
        for k in KW[c]:
            total += len(re.findall(r"\b" + re.escape(k) + r"\b", t))
        result.append(total)
    return result
    # ---------------------------------------------------------------- images
def fetch_page(link):
    try:
        r = requests.get(link, headers=HEAD, timeout=25)
        if r.status_code != 200:
            return "", []
        s = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return "", []
    imgs = []
    for k in ("og:image", "twitter:image"):
        m = s.find("meta", attrs={"property": k}) or s.find("meta", attrs={"name": k})
        if m and m.get("content"):
            imgs.append(urljoin(link, m["content"]))
    art = s.find("article") or s
    for i in art.find_all("img")[:8]:
        src = i.get("src") or i.get("data-src")
        if src:
            imgs.append(urljoin(link, src))
    paras = [p.get_text(" ", strip=True) for p in art.find_all("p")]
    text = " ".join(p for p in paras if len(p) > 40)[:6000]
    return text, imgs


def valid_image(u):
    if not u.startswith("http") or BAD_IMG.search(u):
        return False
    try:
        r = requests.get(u, headers=HEAD, timeout=15, stream=True)
        ok = r.status_code == 200 and r.headers.get("content-type", "").startswith("image")
        size = int(r.headers.get("content-length", "0") or 0)
        r.close()
        return ok and (size == 0 or size > 8000)
    except Exception:
        return False


# ---------------------------------------------------------------- AI
def free_models():
    ms = []
    pref = os.getenv("OPENROUTER_MODEL", "").strip()
    if pref:
        ms.append(pref)
    ms.append("openrouter/free")
    try:
        data = requests.get("https://openrouter.ai/api/v1/models", timeout=30).json()["data"]
        fr = [m for m in data if str(m.get("id", "")).endswith(":free")]
        fr.sort(key=lambda m: -(m.get("context_length") or 0))
        ms += [m["id"] for m in fr[:6]]
    except Exception as ex:
        print("model list error:", ex)
    return list(dict.fromkeys(ms))


def ask(model, system, user):
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": "Bearer " + OR_KEY,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com",
            "X-Title": "Book Summary Bot",
        },
        json={
            "model": model,
            "temperature": 0.7,
            "max_tokens": 5000,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=300,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


SYSTEM = (
    "You are an expert SEO book blogger. You write original, engaging, well-structured "
    "English blog posts that summarize and review books. Never invent quotes, ISBNs, "
    "dates or facts that are not supported by the source or by well-established knowledge."
)


def build_prompt(x, cat, text, n_img):
    return (
        "Write a complete SEO-optimized blog post for a book blog.\n\n"
        f"CATEGORY: {cat}\n"
        f"SOURCE HEADLINE: {x['title']}\n"
        f"SOURCE (from {x['feed']}):\n{text[:5500]}\n\n"
        "RULES:\n"
        "- Identify the book (or books) the source is about and write an original summary/review post. "
        "Do NOT copy sentences from the source; rewrite everything in your own words.\n"
        "- If it is fiction, avoid major spoilers. If the source is not about one specific book, "
        "write a helpful book-focused article (reading guide / key ideas) in the same category.\n"
        "- Length: 1200-1800 words. Natural keyword use, short paragraphs, scannable.\n"
        "- Output ONLY in this exact format (no markdown, no code fences):\n\n"
        "TITLE: <SEO title, max 62 characters, includes the book name and a hook such as "
        "Summary, Review or Key Ideas>\n"
        "DESCRIPTION: <meta description, max 150 characters>\n"
        "===BODY===\n"
        "<HTML fragment only: use <h2>, <h3>, <p>, <ul>, <li>, <strong>, <em>, <blockquote>. "
        "No <h1>, no <html>/<body>.>\n\n"
        "BODY STRUCTURE (in this order):\n"
        "1. [[IMAGE_1]] on its own line first, then a hooking introduction.\n"
        "2. <h2>Book at a Glance</h2> (title, author, genre/category, and only details present in the source)\n"
        "3. <h2>Quick Summary</h2>\n"
        "4. <h2>Key Ideas and Themes</h2> with several <h3> sub-points\n"
        "5. <h2>Who Should Read This Book</h2>\n"
        "6. <h2>Strengths and Criticisms</h2>\n"
        "7. <h2>Key Takeaways</h2> as a bulleted list\n"
        "8. <h2>Frequently Asked Questions</h2> with 3 questions as <h3> and answers as <p>\n"
        "9. <h2>Final Thoughts</h2>\n\n"
        f"IMAGES: there are {n_img} images. Put the placeholders [[IMAGE_1]] .. [[IMAGE_{n_img}]] "
        "each on its own line, spread between sections (IMAGE_1 at the very top)."
    )


def parse(raw):
    raw = re.sub(r"```(?:html)?", "", raw)
    m = re.search(r"TITLE:\s*(.+)", raw)
    d = re.search(r"DESCRIPTION:\s*(.+)", raw)
    if "===BODY===" not in raw or not m:
        print("rejected: format not followed")
        return None
    body = raw.split("===BODY===", 1)[1].strip()
    body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
    body = re.sub(r"^#{1,3}\s*(.+)$", r"<h2>\1</h2>", body, flags=re.M)
    title = m.group(1).strip().strip('"*')[:90]
    desc = (d.group(1).strip() if d else "")[:150]
    words = len(text_of(body).split())
    if words < 700 or "<h2" not in body or "<p" not in body:
        print("rejected: too short or bad structure, words =", words)
        return None
    return title, desc, body


def figure(u, alt):
    a = H.escape(alt, quote=True)
    return (
        '<figure style="text-align:center;margin:24px 0">'
        f'<img src="{u}" alt="{a}" style="max-width:100%;height:auto;border-radius:8px"/>'
        f'<figcaption style="font-size:13px;color:#666">{a}</figcaption></figure>'
    )


def finalize(body, imgs, title, x):
    for n, u in enumerate(imgs, 1):
        body = body.replace(f"[[IMAGE_{n}]]", figure(u, f"{title} - image {n}"))
    body = re.sub(r"\[\[IMAGE_\d+\]\]", "", body)
    if imgs[0] not in body:
        body = figure(imgs[0], title) + body
    body += (
        '<hr/><p><em>Source and further reading: '
        f'<a href="{x["link"]}" rel="nofollow noopener" target="_blank">{H.escape(x["feed"])}</a>'
        '</em></p>'
    )
    return body


def make_post(x, cat, models):
    text, pimgs = fetch_page(x["link"])
    imgs = []
    for u in dict.fromkeys(x["images"] + pimgs):  # RSS images first
        if valid_image(u):
            imgs.append(u)
        if len(imgs) >= 4:
            break
    if not imgs:
        print("skip (no valid image):", x["title"])
        return None
    source = text if len(text) > 500 else x["summary"]
    if len(source) < 150:
        print("skip (too little info):", x["title"])
        return None
    for model in models:
        if time.time() - START > DEADLINE:
            return None
        try:
            print("model:", model)
            res = parse(ask(model, SYSTEM, build_prompt(x, cat, source, len(imgs))))
            if res:
                title, desc, body = res
                return title, finalize(body, imgs, title, x), desc
        except Exception as ex:
            print("model error:", model, str(ex)[:150])
            time.sleep(3)
    return None
    # ---------------------------------------------------------------- Blogger
def blogger_token():
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": os.environ["BLOGGER_CLIENT_ID"],
            "client_secret": os.environ["BLOGGER_CLIENT_SECRET"],
            "refresh_token": os.environ["BLOGGER_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def publish(token, title, content, label, desc):
    url = "https://www.googleapis.com/blogger/v3/blogs/%s/posts/" % os.environ["BLOG_ID"]
    payload = {
        "kind": "blogger#post",
        "title": title,
        "content": content,
        "labels": [label],
        "customMetaData": desc,
    }
    for _ in range(3):
        try:
            r = requests.post(url, headers={"Authorization": "Bearer " + token},
                              json=payload, timeout=60)
            if r.status_code == 200:
                print("PUBLISHED:", r.json().get("url"))
                return True
            print("blogger error", r.status_code, r.text[:200])
        except Exception as ex:
            print("blogger exception", ex)
        time.sleep(10)
    return False


# ---------------------------------------------------------------- main
def main():
    state = load_state()
    posted = set(state["posted"])
    feeds = load_feeds()
    print("feeds:", len(feeds))
    with ThreadPoolExecutor(12) as ex:
        lists = list(ex.map(fetch_feed, feeds))

    seen = set()
    fresh = []
    for x in [i for l in lists for i in l]:
        if x["link"] in posted or x["link"] in seen:
            continue
        seen.add(x["link"])
        if not BOOKISH.search(x["title"] + " " + x["summary"][:800]):
            continue
        x["sc"] = scores(x)
        fresh.append(x)
    print("fresh candidates:", len(fresh))

    n = len(CATEGORIES)
    ci = state["cat_index"] % n
    plan = []
    for off in range(n):
        c = (ci + off) % n
        cands = [x for x in fresh if x["sc"][c] > 0]
        cands.sort(key=lambda x: (bool(x["images"]), x["sc"][c], x["ts"]), reverse=True)
        plan += [(c, x) for x in cands[:3]]
    rest = [x for x in fresh if max(x["sc"]) == 0]
    rest.sort(key=lambda x: x["ts"], reverse=True)
    plan += [(ci, x) for x in rest[:5]]

    models = free_models()
    print("models:", models)
    token = blogger_token()
    tried = set()
    attempts = 0
    for c, x in plan:
        if attempts >= MAX_ATTEMPTS or time.time() - START > DEADLINE:
            break
        if x["link"] in tried:
            continue
        tried.add(x["link"])
        attempts += 1
        print("[%d] %s | %s" % (attempts, CATEGORIES[c], x["title"]))
        res = make_post(x, CATEGORIES[c], models)
        if not res:
            continue
        title, content, desc = res
        if publish(token, title, content, CATEGORIES[c], desc):
            state["posted"] = (state["posted"] + [x["link"]])[-4000:]
            if c == state["cat_index"]:
                state["count"] += 1
            else:
                state["cat_index"] = c
                state["count"] = 1
            if state["count"] >= BATCH:
                state["cat_index"] = (c + 1) % n
                state["count"] = 0
            save_state(state)
            return 0
    print("No post published this run")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
