"""Capture the first three guide indexes and their articles for reproducible research."""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

folder = Path('data/research')
folder.mkdir(parents=True, exist_ok=True)
session = requests.Session()
session.headers['User-Agent'] = 'Mozilla/5.0 (compatible; ETContentResearch/0.1)'
manifest = []
for page in range(1, 4):
    url = 'https://movingtospain.com/guides/' + (f'page/{page}/' if page > 1 else '')
    target = folder / f'guides-{page}.html'
    if not target.exists():
        r = session.get(url, timeout=45)
        r.raise_for_status()
        target.write_text(r.text, encoding='utf-8')
        time.sleep(1)
    soup = BeautifulSoup(target.read_text(encoding='utf-8'), 'html.parser')
    for link in soup.select('h2.entry-title a'):
        article_url = link['href']
        slug = urlsplit(article_url).path.strip('/')
        path = folder / f'{slug}.html'
        item = {'url': article_url, 'title': link.get_text(' ', strip=True), 'path': path.as_posix(), 'index_page': page}
        if not path.exists():
            r = session.get(article_url, timeout=45)
            r.raise_for_status()
            path.write_text(r.text, encoding='utf-8')
            time.sleep(1)
        manifest.append(item)
        (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(article_url, flush=True)
