"""Read-only MCP adaptation of the supplied Redbook-Search-Comment-MCP2.0.
Reuses its persistent-browser strategy and DOM selectors; no publication tools.
"""
import asyncio, os, re
from pathlib import Path
from urllib.parse import quote, urlparse
from datetime import datetime, timezone
from fastmcp import FastMCP
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
mcp = FastMCP('coffee-xiaohongshu-readonly')
context = page = pw = None
last_search_url = ""
lock = asyncio.Lock()

async def browser():
    global context, page, pw
    if page is not None and page.is_closed():
        context = page = None
        if pw is not None:
            await pw.stop()
            pw = None
    if context is None:
        pw = await async_playwright().start()
        context = await pw.chromium.launch_persistent_context(
            str(ROOT/'data/browser-profile'), channel='chrome', headless=False,
            viewport={'width':1280,'height':850}, args=['--disable-session-crashed-bubble'])
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(15000)
    return page

async def gate(p):
    text = await p.locator('body').inner_text()
    for word in ['访问频次异常','安全验证','请完成验证','网络连接异常']:
        if word in text: return {'status':'needs_action','message':f'小红书页面提示：{word}。请在打开的浏览器完成操作。'}
    if await p.locator('.login-container, .login-modal, .login-box').count():
        return {'status':'needs_login','message':'请在打开的小红书浏览器窗口扫码登录，然后重试。'}
    return None

def valid(url):
    u=urlparse(url)
    if u.scheme!='https' or u.hostname!='www.xiaohongshu.com' or not re.match(r'^/(explore|search_result)/[a-fA-F0-9]+',u.path):
        raise ValueError('仅允许小红书 HTTPS 笔记链接')
    return url

@mcp.tool()
async def login_status() -> dict:
    """Open Xiaohongshu for user login and return visible page state. Does not enter credentials."""
    async with lock:
        p=await browser()
        if 'xiaohongshu.com' not in p.url:
            await p.goto('https://www.xiaohongshu.com/explore',wait_until='commit',timeout=25000)
            await p.wait_for_timeout(1800)
        blocked=await gate(p)
        if blocked: return blocked
        me=await p.locator('a[href*="/user/profile/"]').count()
        return {'status':'ready' if me else 'browser_open','message':'浏览器已就绪；真实搜索会进一步确认读取能力。' if me else '已打开小红书；如出现登录提示，请在浏览器完成登录。'}

@mcp.tool()
async def search_notes(keywords: str, limit: int=6) -> dict:
    """Search real Xiaohongshu note cards. Returns source URLs, not generated content."""
    global last_search_url
    async with lock:
        p=await browser()
        last_search_url='https://www.xiaohongshu.com/search_result?keyword='+quote(keywords)
        await p.goto(last_search_url,wait_until='commit',timeout=25000)
        try: await p.locator('section.note-item').first.wait_for(timeout=12000)
        except Exception: pass
        blocked=await gate(p)
        if blocked:return blocked
        rows=await p.locator('section.note-item').evaluate_all(r'''cards => cards.map(c => {
          const links=Array.from(c.querySelectorAll('a[href]'));
          const a=links.find(a=>/\/(search_result|explore)\//.test(a.getAttribute('href')) && a.href.includes('xsec_token='))||links.find(a=>a.getAttribute('href').includes('/search_result/'))||links.find(a=>a.getAttribute('href').includes('/explore/'));
          const metric=c.querySelector('.like-wrapper .count,.like-wrapper,.count')?.textContent?.trim()||'';
          return {url:a?.href,title:c.querySelector('a.title,.title')?.textContent?.trim()||'',visible_engagement:metric};
        }).filter(x=>x.url)''')
        out=[];seen=set()
        for r in rows:
            path=urlparse(r['url']).path
            if path not in seen: out.append(r);seen.add(path)
        return {'status':'ok' if out else 'empty','query':keywords,'notes':out[:max(1,min(limit,12))], 'message':'' if out else '页面没有可读取笔记。请检查浏览器是否需要登录或验证。'}

async def note_page(url):
    p=await browser()
    valid(url)
    note_id=urlparse(url).path.rsplit('/',1)[-1]
    if note_id not in p.url or not await p.locator('#detail-desc,.note-text,.note-content').count():
        if 'xsec_token=' in url:
            await p.goto(url,wait_until='commit',timeout=25000)
        elif last_search_url:
            # Use the actual search-card navigation when its URL is unsigned.
            # The platform handles its own navigation tokens; none are fabricated.
            await p.goto(last_search_url,wait_until='commit',timeout=25000)
            link=p.locator(f'section.note-item a[href*="{note_id}"]').last
            await link.wait_for(timeout=12000)
            await link.click(timeout=10000)
        else:
            await p.goto(url,wait_until='commit',timeout=25000)
    try:await p.locator('#detail-desc,.note-text,.note-content').first.wait_for(timeout=10000)
    except Exception:pass
    return p

@mcp.tool()
async def get_note_content(url: str) -> dict:
    """Read a real note's visible title/body, preserving original URL and retrieval timestamp."""
    async with lock:
        p=await note_page(url)
        blocked=await gate(p)
        if blocked:return blocked
        row=await p.evaluate('''() => ({
          title:document.querySelector('#detail-title,.note-content .title')?.textContent?.trim()||'',
          text:document.querySelector('#detail-desc,.note-text,.note-content .desc')?.textContent?.trim()||'',
          published_text:document.querySelector('.note-content .date,.bottom-container .date')?.textContent?.trim()||''
        })''')
        row.update(url=url,status='ok' if row['text'] else 'empty',collected_at=datetime.now(timezone.utc).isoformat())
        return row

@mcp.tool()
async def get_note_comments(url: str, limit: int=20) -> dict:
    """Read visible real comments only; do not fabricate missing comments or interact/post."""
    async with lock:
        p=await note_page(url)
        blocked=await gate(p)
        if blocked:return blocked
        section=p.locator('.comments-container,.comments-el').first
        if await section.count():
            try:await section.scroll_into_view_if_needed(timeout=3000)
            except Exception:pass
        await p.wait_for_timeout(1000)
        rows=await p.locator('.comment-item').evaluate_all('''items => items.map(c=>({
          platform_id:c.id||'',text:c.querySelector('.content .note-text,.content')?.textContent?.trim()||'',
          published_text:c.querySelector('.date')?.textContent?.trim()||''
        })).filter(x=>x.text)''')
        seen=set();out=[]
        for r in rows:
            if r['text'] not in seen:out.append(r);seen.add(r['text'])
        return {'status':'ok' if out else 'empty','url':url,'comments':out[:max(1,min(limit,30))],
                'collected_at':datetime.now(timezone.utc).isoformat(),'message':'' if out else '未读取到可见评论；不能视为没有消费者意见。'}

@mcp.tool()
async def diagnostics() -> dict:
    """Read bounded connection and selector diagnostics, never credentials/cookies."""
    async with lock:
        p=await browser()
        async with asyncio.timeout(12):
            return await page_diagnostics(p)

async def page_diagnostics(p):
        return {'status':'ok','page_url':p.url,'title':await p.title(),
                'note_cards':await p.locator('section.note-item').count(),
                'detail_nodes':await p.locator('#detail-desc,.note-text,.note-content').count(),
                'comment_nodes':await p.locator('.comment-item').count(),
                'gate':await gate(p)}

if __name__=='__main__':mcp.run(transport='stdio')
