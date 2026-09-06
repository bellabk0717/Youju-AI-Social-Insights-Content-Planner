import asyncio, hashlib, io, json, os, re, secrets, sys, time, zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import httpx, jieba
from rank_bm25 import BM25Okapi
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport
from backend.research import research_plan, scoped_retrieval, check_findings, angle_priority, GROUPS
from backend.writing import content_quality_issues, select_style_references

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'data';DATA.mkdir(exist_ok=True)
SHOWCASE_MODE=os.getenv('SHOWCASE_MODE','').lower() in {'1','true','yes'}
STATEFILE=DATA/'workspace.json'
state=json.loads(STATEFILE.read_text()) if STATEFILE.exists() else {'records':[],'logs':[],'task':{},'analysis':None,'plan':None,'job':{'status':'idle'}}
SHOWCASEFILE=DATA/'showcase.json'
if SHOWCASEFILE.exists():
    archived=json.loads(SHOWCASEFILE.read_text())
    needs_showcase=(not state.get('records') and not state.get('analysis') and not state.get('plan')) or state.get('showcase_version',0)<archived.get('showcase_version',1)
    if needs_showcase:
        state.update(task=archived.get('task',state.get('task',{})),records=archived['records'],coverage=archived['coverage'],analysis=archived['analysis'],plan=archived['plan'],archive_counts=archived['archive_counts'],showcase_version=archived.get('showcase_version',1))
        state['job']={'status':'idle','message':'展示案例已从本地归档恢复'}
if state['job'].get('status')=='running':state['job']={'status':'interrupted','message':'上次任务中断，可重新开始。'}
if state.get('plan') and state.get('task'):
    migrated_issues=content_quality_issues(state['plan'],state['task'])
    if migrated_issues:
        existing=state['plan'].setdefault('validation',{'passed':False,'issues':[]})
        existing['issues']=list(dict.fromkeys(existing.get('issues',[])+migrated_issues))
        existing['passed']=False
config={'base_url':os.getenv('COFFEE_API_BASE','https://dashscope.aliyuncs.com/compatible-mode/v1'),'model':os.getenv('COFFEE_MODEL','qwen-plus'),'api_key':os.getenv('COFFEE_API_KEY','')}
config_ok=False
client=None
job_lock=asyncio.Lock();mcp_lock=asyncio.Lock()

def save():
    tmp=STATEFILE.with_suffix('.tmp');tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2));tmp.replace(STATEFILE)
def log(stage,message,**extra):
    state['logs'].append({'time':datetime.now().strftime('%H:%M:%S'),'stage':stage,'message':message,**extra});state['logs']=state['logs'][-120:];save()

def safe_error(e):
    s=str(e)
    if config['api_key']:s=s.replace(config['api_key'],'[redacted]')
    return s[:600]

@asynccontextmanager
async def lifespan(app):
    global client
    if SHOWCASE_MODE:
        yield
        return
    transport=PythonStdioTransport(ROOT/'backend/xhs_mcp.py',python_cmd=sys.executable,cwd=str(ROOT),env={'FASTMCP_CHECK_FOR_UPDATES':'off','FASTMCP_ENABLE_RICH_LOGGING':'false'})
    async with Client(transport) as c:
        client=c
        names=[x.name for x in await c.list_tools()]
        log('MCP','协议连接成功；已发现只读工具',tools=names)
        yield

app=FastAPI(lifespan=lifespan)
@app.middleware('http')
async def local_only(request:Request,call_next):
    if SHOWCASE_MODE:
        if request.method not in {'GET','HEAD','OPTIONS'}:
            return JSONResponse({'detail':'在线版本用于查看已归档案例；真实采集与生成请在本地运行。'},status_code=403)
        return await call_next(request)
    origin=request.headers.get('origin','')
    if origin and origin not in ['http://127.0.0.1:8765','http://localhost:8765']:
        return Response('Local access only',status_code=403)
    if request.headers.get('host','').split(':')[0] not in ['127.0.0.1','localhost','testserver']:
        return Response('Local access only',status_code=403)
    return await call_next(request)

async def tool(name,args):
    async with mcp_lock:
        log('MCP',f'调用 {name}',arguments=args)
        t=time.monotonic()
        try:
            async with asyncio.timeout(65):
                r=await client.call_tool(name,args,timeout=60)
        except TimeoutError:
            raise ValueError('小红书工具响应超时，请查看采集浏览器是否卡住、需要登录或验证；本次未完成。') from None
        d=r.data
        if not isinstance(d,dict):
            d=json.loads(next(x.text for x in r.content if getattr(x,'type','')=='text'))
        log('MCP',f'{name} → {d.get("status","returned")}',duration=round(time.monotonic()-t,2))
        if d.get('status') in ['needs_login','needs_action']:raise ValueError(d['message'])
        return d

async def background(label,fn):
    if job_lock.locked():raise HTTPException(409,'已有任务运行，请等待完成。')
    # Set status synchronously; lock acquisition takes place in this request, not a scheduled race.
    await job_lock.acquire()
    state['job']={'status':'running','message':label};save()
    async def run():
        try:
            await fn();state['job']={'status':'done','message':label+'完成'}
        except Exception as e:
            message=safe_error(e);state['job']={'status':'error','message':message};log('错误',message)
        finally:job_lock.release();save()
    asyncio.create_task(run());return {'status':'running'}

class Configuration(BaseModel):
    base_url:str
    model:str=Field(min_length=1,max_length=100)
    api_key:str=Field(min_length=1)
class Task(BaseModel):
    store:str=Field(min_length=1,max_length=100)
    city:str=Field(min_length=1,max_length=100)
    product:str=Field(min_length=1,max_length=200)
    facts:str=Field(min_length=5,max_length=4000)
    objective:str=Field(min_length=2,max_length=500)
    area:str=Field(default="",max_length=100)
    query:str=Field(default="",max_length=100)
    limit:int=Field(default=12,ge=2,le=18)
    plan_count:int=Field(default=3,ge=1,le=5)
    focus:Literal['auto','matcha','scene','balanced','custom']=Field(default='auto')
    focus_custom:str=Field(default='',max_length=200)
    materials:list[dict]=Field(default_factory=list,max_length=5)
    confirmed:bool=False

def clean_api_key(value):
    key=value.strip()
    if not key or any(ord(ch)<33 or ord(ch)>126 for ch in key):
        raise ValueError('API 密钥包含中文、空格或不可见字符。请清空后重新粘贴完整原始密钥，不要附带名称、说明或 Bearer 前缀。')
    if key.lower().startswith('bearer'):
        raise ValueError('请只填写密钥本身，不要附带 Bearer 前缀。')
    return key

async def llm(system,user):
    if not config['api_key']:raise ValueError('请先在连接设置中填写并验证模型 API。')
    key=clean_api_key(config['api_key'])
    started=time.monotonic()
    async with httpx.AsyncClient(timeout=90) as h:
        r=await h.post(config['base_url'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':config['model'],'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps(user,ensure_ascii=False)}],'response_format':{'type':'json_object'}})
    if r.status_code!=200:
        try:
            error=r.json().get('error',{})
            message=str(error.get('message','')) if isinstance(error,dict) else str(error)
            if config['api_key']:message=message.replace(config['api_key'],'[redacted]')
            message=re.sub(r'sk-[A-Za-z0-9_-]+','[redacted]',message)
        except Exception:message='服务未提供可读错误详情。'
        raise ValueError(f'模型接口 HTTP {r.status_code}：{message[:700]}')
    obj=r.json();text=obj['choices'][0]['message']['content']
    text=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip())
    out=json.loads(text)
    if not isinstance(out,dict):raise ValueError('模型没有返回 JSON 对象，请重试。')
    log('模型','实际 API 调用完成',model=config['model'],usage=obj.get('usage'),duration=round(time.monotonic()-started,2))
    return out

@app.get('/api/state')
async def get_state():
    return {**state,'configuration':{'base_url':config['base_url'],'model':config['model'],'configured':bool(config['api_key']),'verified':config_ok,'showcase':SHOWCASE_MODE}}

@app.post('/api/config')
async def set_config(c:Configuration):
    global config_ok
    if job_lock.locked():raise HTTPException(409,'请等待当前任务结束再更新配置。')
    if not c.base_url.startswith('https://'):raise HTTPException(400,'模型服务地址必须使用 HTTPS。')
    try:c.api_key=clean_api_key(c.api_key)
    except ValueError as e:raise HTTPException(400,str(e))
    config.update(c.model_dump());config_ok=False
    try:
        d=await llm('只输出 JSON 对象。',{'request':'返回 {"ok":true}，用于连接测试。'})
        config_ok=d.get('ok') is True
        if not config_ok:raise ValueError('接口响应成功，但连接测试结构未通过。')
    except Exception as e:raise HTTPException(400,safe_error(e))
    return {'ok':True}

@app.get('/api/mcp-diagnostics')
async def mcp_diagnostics():
    if job_lock.locked():raise HTTPException(409,'采集进行中，稍后诊断。')
    try:return await tool('diagnostics',{})
    except Exception as e:raise HTTPException(400,safe_error(e))

@app.post('/api/login')
async def login():
    async def run():
        result=await tool('login_status',{});log('登录',result.get('message','浏览器已打开'))
    return await background('打开小红书登录',run)

def record(kind,text,url,parent='',title='',**meta):
    normalized=' '.join(text.split())
    rid=('N' if kind=='note' else 'C')+hashlib.sha256((kind+url.split('?')[0]+normalized).encode()).hexdigest()[:12]
    return {'source_id':rid,'content_type':kind,'text':text,'url':url,'parent_id':parent,'title':title,'collected_at':datetime.now(timezone.utc).isoformat(),'source_type':'unknown','geo_scope':'unknown',**meta}

@app.post('/api/research-plan')
async def preview_research(task:Task):
    return research_plan(task.model_dump())

@app.post('/api/material')
async def extract_material(file:UploadFile=File(...)):
    raw=await file.read()
    if len(raw)>8_000_000:raise HTTPException(400,'单个文件不能超过 8 MB。')
    name=(file.filename or '未命名资料').strip();suffix=Path(name).suffix.lower()
    try:
        if suffix in ['.txt','.md','.csv','.json']:
            text=raw.decode('utf-8-sig')
        elif suffix=='.docx':
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                xml=z.read('word/document.xml').decode('utf-8')
            text=re.sub(r'<w:tab[^>]*/>','\t',xml);text=re.sub(r'</w:p>','\n',text)
            text=re.sub(r'<[^>]+>','',text)
        elif suffix=='.pdf':
            from pypdf import PdfReader
            text='\n'.join((page.extract_text() or '') for page in PdfReader(io.BytesIO(raw)).pages)
        else:raise ValueError('支持 PDF、DOCX、TXT、MD、CSV、JSON。图片菜单请先转成 PDF。')
    except Exception as e:raise HTTPException(400,f'文件无法解析：{safe_error(e)}')
    text='\n'.join(x.strip() for x in text.splitlines() if x.strip())[:30000]
    if not text:raise HTTPException(400,'文件中没有读取到可用文字。')
    return {'name':name,'text':text,'characters':len(text)}

@app.post('/api/collect')
async def collect(task:Task):
    if not task.confirmed:raise HTTPException(400,'请确认门店资料为已核实事实。')
    async def run():
        plan=research_plan(task.model_dump())
        state.update(task=task.model_dump(),records=[],analysis=None,plan=None,research_plan=plan,coverage=[]);save()
        seen_text={};seen_notes=set()
        for spec in plan['queries']:
            coverage={**spec,'status':'pending','found':0,'read':0,'comments':0}
            state['coverage'].append(coverage)
            if len(seen_notes)>=plan['note_budget']:
                coverage['status']='budget_skipped';save();continue
            state['job']['message']=spec['label']+'：'+spec['query'];save()
            try:
                found=await tool('search_notes',{'keywords':spec['query'],'limit':plan['per_query_limit']})
            except Exception as e:
                coverage.update(status='error',error=safe_error(e));save()
                if any(x in str(e) for x in ['登录','验证','closed','Timeout','ERR_']):raise
                continue
            coverage['found']=len(found.get('notes',[]))
            coverage['status']='searched' if coverage['found'] else 'empty'
            for n in found.get('notes',[]):
                urlkey=n['url'].split('?')[0]
                if urlkey in seen_notes:
                    for row in state['records']:
                        if row['url'].split('?')[0]==urlkey:
                            if spec['group'] not in row['research_groups']:row['research_groups'].append(spec['group'])
                            if spec['id'] not in row['query_ids']:row['query_ids'].append(spec['id'])
                    continue
                if len(seen_notes)>=plan['note_budget']:break
                seen_notes.add(urlkey)
                state['job']['message']=f"{spec['label']}：读取第 {len(seen_notes)} 篇（总上限 {plan['note_budget']}）";save()
                def add(row):
                    key=(row['content_type'],' '.join(row['text'].split()))
                    if key in seen_text:
                        old=seen_text[key]
                        if spec['group'] not in old['research_groups']:old['research_groups'].append(spec['group'])
                        if spec['id'] not in old['query_ids']:old['query_ids'].append(spec['id'])
                        return
                    row.update(research_groups=[spec['group']],query_ids=[spec['id']])
                    state['records'].append(row);seen_text[key]=row
                try:
                    item=await tool('get_note_content',{'url':n['url']});parent=''
                    if item.get('text'):
                        row=record('note',item['text'],n['url'],title=item.get('title') or n['title'],published_text=item.get('published_text',''),visible_engagement=n.get('visible_engagement',''))
                        parent=row['source_id'];add(row);coverage['read']+=1
                    comments=await tool('get_note_comments',{'url':n['url'],'limit':15})
                    for c in comments.get('comments',[]):
                        add(record('comment',c['text'],n['url'],parent=parent,title=n['title'],published_text=c.get('published_text','')))
                        coverage['comments']+=1
                    save()
                except Exception as e:
                    coverage.setdefault('errors',[]).append(safe_error(e));log('采集',safe_error(e))
                    if any(x in str(e) for x in ['登录','验证','closed']):raise
            coverage['status']='read' if coverage['read'] else 'no_body';save()
        notes=sum(r['content_type']=='note' for r in state['records']);comments=sum(r['content_type']=='comment' for r in state['records'])
        log('采集',f'四维研究保存真实正文 {notes} 篇、评论 {comments} 条；查询归组不等于内容已验证。')
        if not notes or not comments:raise ValueError(f'当前正文 {notes} 篇、评论 {comments} 条；真实数据链路尚不完整。')
    return await background('执行多维市场研究',run)

def retrieve(query,records,k=18):
    if not records:return []
    tokens=[list(jieba.cut(x['text'])) for x in records]
    scores=BM25Okapi(tokens).get_scores(list(jieba.cut(query)))
    ordered=sorted(zip(scores,records),key=lambda x:x[0],reverse=True)
    return [{**r,'retrieval_score':round(float(score),4)} for score,r in ordered[:k]]

class Angle(BaseModel):
    title:str
    reason:str
    evidence_ids:list[str]
    excerpts:list[str]
    matched_fact:str
    limitations:str
    audience:str=''
    scene:str=''
    need:str=''
    emotion:str=''
    hook_type:str=''
class Finding(BaseModel):
    dimension:Literal['market','segment','brand']
    subject:str
    observation:str
    sentiment:str
    evidence_ids:list[str]
    excerpts:list[str]
    limitation:str

class InsightLayer(BaseModel):
    layer:Literal['market','segment','target']
    title:str
    conclusion:str
    evidence_ids:list[str]
    excerpts:list[str]
    limitation:str

class StylePattern(BaseModel):
    source_id:str
    audience:str
    scene:str
    need:str
    emotion:str
    title_hook:str
    opening_pattern:str
    body_rhythm:str
    cta_pattern:str
    limitation:str

class Analysis(BaseModel):
    findings:list[Finding]=Field(default_factory=list)
    layers:list[InsightLayer]=Field(default_factory=list)
    style_patterns:list[StylePattern]=Field(default_factory=list,max_length=5)
    summary:str
    angles:list[Angle]=Field(min_length=1,max_length=4)

def validate_style_patterns(patterns,rows):
    known={x['source_id'] for x in rows if x.get('content_type')=='note'}
    return [x.model_dump() for x in patterns if x.source_id in known][:5]

def validate_angles(result,rows,facts,focus='auto'):
    known={x['source_id']:x for x in rows};out=[]
    fact_sentences=[x.strip() for x in re.split(r'[。；\n]+',facts) if x.strip()]
    for a in result.angles:
        if not a.evidence_ids or len(a.evidence_ids)!=len(a.excerpts):continue
        if any(i not in known or not q or q not in known[i]['text'] for i,q in zip(a.evidence_ids,a.excerpts)):continue
        matched=a.matched_fact if a.matched_fact and a.matched_fact in facts else ''
        if not matched:
            angle_text=(a.title+' '+a.reason).lower()
            keywords=[x for x in ['soft serve','软冰淇淋','抹茶','matcha','tanjong pagar','环境','插座','隔间','wifi'] if x in angle_text]
            candidates=[s for s in fact_sentences if any(x in s.lower() for x in keywords)]
            if not candidates:continue
            matched=max(candidates,key=lambda s:sum(x in s.lower() for x in keywords))
        threads={known[i]['url'].split('?')[0] for i in a.evidence_ids}
        has_comment=any(known[i]['content_type']=='comment' for i in a.evidence_ids)
        item={**a.model_dump(),'matched_fact':matched,'evidence_status':'sufficient' if len(threads)>=2 and has_comment else 'limited'}
        item['priority_score']=angle_priority(item,rows,focus);out.append(item)
    def sort_key(item):
        direct_brand=any('brand' in known[i].get('research_groups',[]) for i in item['evidence_ids'])
        return (direct_brand,item['priority_score'])
    return sorted(out,key=sort_key,reverse=True)

@app.post('/api/analyze')
async def analyze():
    async def run():
        if not state['records']:raise ValueError('请先通过 MCP 获取真实资料。')
        task=state['task'];rows=scoped_retrieval(state['records'],task['product']+' '+task['objective'],retrieve)
        log('RAG',f'中文 BM25 从 {len(state["records"])} 条真实记录检索 {len(rows)} 条；引用将逐条核对。')
        style_refs=select_style_references(state['records'],task)
        system='''你是小红书门店内容研究员。严格按漏斗分析：第一层大市场（城市咖啡店/甜品店）只发现用户关注维度，如口味、价格、位置、品类、环境、停留条件；第二层缩到商圈、同店型和相同产品形态，判断哪些关注点相关；第三层结合本品牌评价和用户上传资料，确认本店实际能承接什么。soft serve 不是 gelato，也不是球状冰淇淋；错类材料不得成为产品卖点。上传资料定义本店产品、环境和可拍内容；小红书材料发现关注点、内容切口和自然分享语气。本品牌评价找不到也不阻断。若材料中存在“本品牌＋本次产品”的直接评价，排名第一的角度必须引用它；不得让宽泛市场材料压过直接产品证据。不要把互动称作浏览量。若 task.focus 为 custom，必须把 task.focus_custom 作为角度排序偏好，但仍服从证据与本店事实边界。

另外拆解给定 style_reference_notes 的写作结构。每篇只分析 audience（谁会点开）、scene（什么情境）、need（解决什么选择或需求）、emotion（提供什么感受）、title_hook（标题为何一眼能懂）、opening_pattern、body_rhythm、cta_pattern 和 limitation。可见互动只用于挑选值得分析的样本，不代表浏览量或因果效果。只学结构和语气，不复刻句子，也不移植其他门店事实。

输出 JSON：summary；layers 恰好3项，layer依次为market/segment/target，每项含 title,conclusion,evidence_ids,excerpts,limitation；findings 数组每项含 dimension(market/segment/brand),subject,observation,sentiment,evidence_ids,excerpts,limitation；style_patterns 数组2到5项，每项含 source_id,audience,scene,need,emotion,title_hook,opening_pattern,body_rhythm,cta_pattern,limitation；angles 数组2到4项，每项含 title,reason,evidence_ids,excerpts,matched_fact,limitations,audience,scene,need,emotion,hook_type。所有摘录逐字。策划角度必须是“本店可提供的内容”与“相关小红书关注点”的交集。每个角度必须说清人群、场景、需求、情绪价值和标题钩子。优先本品牌直接证据；大市场只帮助发现关注维度，不能把其他店卖点移植成本店卖点。'''
        raw=await llm(system,{'task':task,'research_plan':state.get('research_plan'),'coverage':state.get('coverage',[]),'sources':rows,'style_reference_notes':style_refs})
        facts_basis=task['facts']+'\n'+'\n'.join(x.get('text','') for x in task.get('materials',[]))
        parsed=Analysis.model_validate(raw);priority_focus=task.get('focus_custom','') if task.get('focus')=='custom' else task.get('focus','auto');angles=validate_angles(parsed,rows,facts_basis,priority_focus)
        if not angles:raise ValueError('模型输出未通过原文引用／门店事实匹配检查，请重试分析。')
        state['analysis']={'summary':parsed.summary,'layers':check_findings(parsed.layers,rows),'findings':check_findings(parsed.findings,rows),'style_patterns':validate_style_patterns(parsed.style_patterns,rows),'style_references':style_refs,'angles':angles,'retrieved':rows};state['plan']=None;save()
    return await background('检索证据并分析角度',run)

class Selection(BaseModel):index:int=Field(ge=0)
class ArticlePlan(BaseModel):
    role:str
    audience:str=''
    scene:str=''
    hook:str=''
    titles:list[str]=Field(min_length=3,max_length=3)
    body:str
    shots:list[str]=Field(min_length=1,max_length=4)
    cta:str
class Plan(BaseModel):
    series_strategy:str
    articles:list[ArticlePlan]=Field(min_length=1,max_length=5)
    test_suggestion:str
    limitations:str
class Check(BaseModel):
    passed:bool
    issues:list[str]

def readable_text(value):
    if isinstance(value,str):return value
    if isinstance(value,list):return '；'.join(readable_text(x) for x in value)
    if isinstance(value,dict):return '；'.join(f'{k}：{readable_text(v)}' for k,v in value.items())
    return str(value)

def normalize_plan_payload(raw):
    for field in ['series_strategy','test_suggestion','limitations']:
        if field in raw:raw[field]=readable_text(raw[field])
    return raw

async def check_plan(p,task):
    materials='\n'.join(x.get('text','') for x in task.get('materials',[]))
    result=Check.model_validate(await llm('''你是独立的小红书文案质检员。检查草稿是否编造或违反门店资料中的产品、价格、优惠、设施、地点、时间和效果。用户上传资料可作为策划对象事实；小红书原文只用于关注点和表达参考，不能冒充本店官方事实。再逐篇检查：标题前半段能否一眼看懂产品/地点/人群/具体场景和主旨；标题是否空泛；正文是否像门店账号的自然分享而非报告、品牌宣言或假装顾客；是否出现“这篇要写、发布前确认、资料尚未”等创作过程说明；开头是否具体，整篇是否只讲一个重点，收尾是否自然可互动。问题必须指出篇数和具体原因。只返回 JSON {"passed":布尔值,"issues":[具体问题]}。''',{'facts':task['facts'],'uploaded_materials':materials,'product':task['product'],'plan':p}))
    # Numbers in consumer-facing copy must occur in confirmed facts or product.
    consumer_copy=' '.join(' '.join(a['titles'])+' '+a['body']+' '+a['cta'] for a in p['articles'])
    nums=re.findall(r'\d+(?:\.\d+)?',consumer_copy)
    allowed=re.findall(r'\d+(?:\.\d+)?',task['facts']+' '+materials+' '+task['product'])
    unknown=sorted(set(nums)-set(allowed))
    if unknown:result.issues.append('文案中出现未确认数字：'+','.join(unknown));result.passed=False
    result.issues.extend(content_quality_issues(p,task))
    result.issues=list(dict.fromkeys(result.issues))
    if result.issues:result.passed=False
    return result.model_dump()

@app.post('/api/generate')
async def generate(sel:Selection):
    async def run():
        analysis=state['analysis']
        if not analysis or sel.index>=len(analysis['angles']):raise ValueError('先完成分析并选择有效角度。')
        angle=analysis['angles'][sel.index];count=state['task'].get('plan_count',3)
        raw=await llm('''生成一组可直接修改的小红书笔记策划。账号身份是门店官方账号，但语气要像店里的人在自然分享：具体、松弛、有现场感；不能假装普通顾客，也不要品牌宣言、报告腔、硬广口号或专业广告片分镜。

内容组不能因为选择了一个核心产品角度，就丢掉大市场中已验证的消费场景。requested_count 大于等于3且 task.focus 为 balanced/auto 时，至少包含：一篇核心产品种草、一篇产品选择或系列介绍、一篇真实场景需求。场景可以来自其他 ranked angles 和评论关注点，例如自习间隙、等人、午后停留；未确认的插座、Wifi、座位等设施只能写成“用户关心的问题”或提示到店确认，不能写成门店承诺。场景篇也必须自然出现本次产品，不能变成泛咖啡店推荐。

每篇开始前先明确 audience、scene、need、emotion 和 hook，再写三个标题。标题优先8—20字，最多24字；前10字尽量出现产品、地点、人群或具体场景，让顾客一眼知道主旨；一个标题只表达一个重点。禁用“当天氛围观察、顺便看看、记录一下、是什么感觉、慢一点”这类无信息标题。正文100—320字：开头1—2句直接落到具体场景或需求；中段用已确认的产品/环境事实承接一个核心卖点；结尾给选择或轻问题。不得在消费者正文里写“这篇要、发布前、资料尚未、下次拍到”等创作过程说明；不确定信息只放 limitations。可以学习 style_patterns 的标题钩子和正文节奏，但不得复刻句子或把参考店事实写成本店事实。

每篇只聚焦一个策划点，并自然露出本次产品。拍摄建议只写手机随手可拍的画面，1到4条。不得编造门店事实。严格输出 JSON：series_strategy；articles 数组，每篇含 role,audience,scene,hook,titles(3个),body,shots,cta；test_suggestion；limitations。articles 数量必须等于 requested_count。''',{'task':state['task'],'selected_angle':angle,'other_ranked_angles':analysis['angles'][:3],'research_findings':analysis.get('findings',[]),'style_patterns':analysis.get('style_patterns',[]),'style_reference_notes':analysis.get('style_references',[]),'requested_count':count})
        p=Plan.model_validate(normalize_plan_payload(raw)).model_dump();validation=await check_plan(p,state['task'])
        state['plan']={**p,'angle':angle,'validation':validation,'version':1,'created_at':datetime.now(timezone.utc).isoformat()};save()
    return await background('生成策划并检查事实',run)

@app.post('/api/plan')
async def edit(p:Plan):
    async def run():
        if not state['plan']:raise ValueError('还没有可修改策划。')
        checked=await check_plan(p.model_dump(),state['task'])
        state['plan'].update(p.model_dump());state['plan']['validation']=checked;state['plan']['version']+=1;save()
    return await background('保存修改并重新检查',run)

@app.get('/api/export')
async def export():
    p=state['plan']
    if not p:raise HTTPException(404,'还没有策划单。')
    lines=['# '+state['task']['store']+' · 小红书内容策划单','',f'版本：{p["version"]}；来自真实 MCP 采集。',
           '状态：'+('模型与数字校验通过，仍需人工审阅' if p['validation']['passed'] else '待确认草稿：'+'；'.join(p['validation']['issues'])),
           '证据：来自三层搜索漏斗的可追溯样本，不代表市场总体','',
           '## 内容主线',p['series_strategy'],'## 三层判断',state['analysis']['summary']]
    for i,a in enumerate(p['articles'],1):
        lines.extend([f'## 第 {i} 篇 · {a["role"]}',f'目标人群：{a.get("audience","")}',f'使用场景：{a.get("scene","")}',f'标题钩子：{a.get("hook","")}','### 标题备选',*a['titles'],'### 正文',a['body'],'### 随手拍画面',*a['shots'],'### 互动收尾',a['cta']])
    lines.extend(['## 测试建议',p['test_suggestion'],'## 限制',p['limitations'],'## 原始证据'])
    byid={r['source_id']:r for r in state['records']}
    for rid,q in zip(p['angle']['evidence_ids'],p['angle']['excerpts']):
        r=byid[rid];lines.extend([f'[{rid}]({r["url"]}) · 采集于 {r["collected_at"]}',q])
    return Response('\n\n'.join(lines),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':'attachment; filename="coffee-plan.md"'})

@app.get('/')
async def index():return FileResponse(ROOT/'frontend/index.html')
app.mount('/static',StaticFiles(directory=ROOT/'frontend'),name='static')
