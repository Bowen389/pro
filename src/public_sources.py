"""Bounded public source ingestion. No login, private APIs or full-post replication."""
import concurrent.futures
import datetime as dt
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from zoneinfo import ZoneInfo


class Visible(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.links=[]; self.skip=0; self.anchor=None; self.href=None
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'): self.skip+=1
        if tag in ('p','div','br','h1','h2','tr','li'): self.parts.append('\n')
        if tag=='a': self.anchor=[];self.href=dict(attrs).get('href','')
    def handle_data(self,data):
        if not self.skip:
            self.parts.append(data)
            if self.anchor is not None:self.anchor.append(data)
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'):self.skip=max(0,self.skip-1)
        if tag=='a' and self.anchor is not None:
            self.links.append((self.href,''.join(self.anchor).strip()));self.anchor=None
    def text(self):return '\n'.join(x.strip() for x in ''.join(self.parts).splitlines() if x.strip())


def fetch(url,attempts=2):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in {'www.tgb.cn','tgb.cn','olenara.com','www.jikefupan.com'}:
        raise ValueError('Source URL is outside configured public hosts')
    for attempt in range(attempts):
        try:
            request=urllib.request.Request(url,headers={'User-Agent':'LeaderResearch/1.0 (bounded public research)'})
            with urllib.request.urlopen(request,timeout=18) as response:
                final=urllib.parse.urlsplit(response.url)
                if final.hostname not in {'www.tgb.cn','tgb.cn','olenara.com','www.jikefupan.com'}:raise ValueError('Unexpected source redirect')
                data=response.read(3_000_001)
                if len(data)>3_000_000:raise ValueError('Source too large')
                return data.decode('utf-8','replace')
        except Exception:
            if attempt+1==attempts:raise
            time.sleep(1)


def parse_post(url,body,author,observed):
    p=Visible();p.feed(body);text=p.text()
    gate='登录可查看全文' in text or '博主要求身份验证' in text
    title=re.search(r'<title[^>]*>(.*?)</title>',body,re.S|re.I)
    title=re.sub('<[^>]*>','',title[1]).strip() if title else ''
    published=re.search(r'淘股吧原创\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})',text)
    if not published:
        published=re.search(r'(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})',text)
    start=text.find('展开',text.find('淘股吧原创'))
    public=text[start+2:] if start!=-1 else ''
    for stop in ('登录可查看全文','打赏Ta','话题与分类：','相关文章'):
        public=public.split(stop,1)[0]
    # Preserve a short public excerpt only; quotes are never automatically promoted to rules.
    excerpt=' '.join(public.split())[:40]
    status='partial_login_required' if gate else 'public_excerpt' if excerpt else 'unparsed'
    return {'url':url,'author':author,'title':title,'published_at':published[1].replace(' ','T')+':00+08:00' if published else None,'collected_at':observed,'available_at':observed,'status':status,'excerpt':excerpt,'body_sha256':hashlib.sha256(body.encode()).hexdigest(),'evidence_level':'public_author_material_not_independently_verified','semantic_analysis':'pending'}


def collect_sources(config,observed):
    records=[]; jobs=[]; catalogue=[]
    for person in config['authors']:
        try:
            body=fetch(person['blog']);parser=Visible();parser.feed(body)
            title=re.search(r'<title[^>]*>(.*?)</title>',body,re.S|re.I)
            if title is None or person['name'] not in title[1]:raise ValueError('Author identity/title mismatch')
            seen=set(); links=[]
            for href,text in parser.links:
                url=urllib.parse.urljoin(person['blog'],href)
                if re.fullmatch(r'https://(?:www\.)?tgb\.cn/a/[A-Za-z0-9]+(?:-\d+)?',url) and url not in seen:
                    seen.add(url);links.append({'url':url,'title':text})
            catalogue.append({'author':person['name'],'url':person['blog'],'status':'ok','collected_at':observed,'links':links[:20]})
            jobs.extend((x['url'],person['name']) for x in links[:config.get('posts_per_author',2)])
        except Exception as exc:
            catalogue.append({'author':person['name'],'url':person['blog'],'status':'failed','collected_at':observed,'error':type(exc).__name__})
    jobs.extend((x['url'],x['author']) for x in config.get('seed_posts',[]))
    unique={url:author for url,author in jobs}
    def run(pair):
        url,author=pair
        try:return parse_post(url,fetch(url),author,observed)
        except Exception as exc:return {'url':url,'author':author,'status':'fetch_failed','collected_at':observed,'error':type(exc).__name__}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        records=list(pool.map(run,unique.items()))
    return {'collected_at':observed,'catalogues':catalogue,'posts':records,'limitations':['短公开片段，非全文语料库','作者观点不等于行情事实','有登录限制即停止，不尝试补齐隐藏正文','尚无模型语义提炼和观点准确率验证']}
