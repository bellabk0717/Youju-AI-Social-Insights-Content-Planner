# 有据：小红书门店内容策划助手

面向门店运营与营销人员的 AI 内容研究原型。用户提供门店、产品和商圈信息后，系统通过只读小红书 MCP 搜索真实笔记与评论，按“大市场 → 商圈与同类产品 → 本品牌”逐层收窄，再结合门店资料生成可编辑的小红书内容策划。

仓库自带 nana’s green tea 新加坡 soft serve 展示案例。该案例实际采集 7 篇笔记正文与 44 条评论；公开快照仅保留分析引用的 15 条脱敏证据，不包含浏览器登录信息和 API 密钥。

## 核心流程

1. 上传或填写门店与产品资料，建立本店事实边界。
2. 设置笔记读取范围、生成篇数和策划偏好。
3. 通过本地 Chrome 登录小红书，执行分层搜索与正文、评论读取。
4. 使用中文 BM25 检索相关证据，由兼容 OpenAI Chat Completions 的模型生成结构化分析。
5. 核对逐字引用和门店事实，选择内容方向并生成 1、3 或 5 篇策划。
6. 人工修改标题、正文和拍摄建议，重新检查后导出 Markdown。

## 能力与边界

- FastAPI 管理任务、状态、归档和导出。
- FastMCP 将小红书搜索、正文读取和评论读取封装为只读工具。
- Playwright 使用独立 Chrome 用户目录保存本机登录态。
- jieba 与 BM25 提供可解释的文本检索基线。
- 结构化模型输出包含分析层级、用户关注点、逐字引用、策划角度和内容草稿。
- 程序检查错误品类、引用一致性、未确认数字、标题信息量和创作过程用语。

小批量平台内容不代表统计意义上的市场调查。互动数不等于浏览量，用户评论不等于门店承诺，其他品牌的产品事实不会被移植到本店。真实采集依赖平台页面、登录状态和网络环境。

## 本地运行完整版本

要求 Python 3.12、Google Chrome，以及一个兼容 OpenAI Chat Completions 的模型 API。

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

macOS 也可以双击 `启动工作台.command`。打开 <http://127.0.0.1:8765> 后，在“模型设置”中填写服务地址、模型名称和 API 密钥。密钥只保存在当前进程内存中。

## 运行测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

当前共 21 项测试，覆盖搜索层级、产品类型识别、证据引用、事实匹配、URL 边界、检索、数字检查和标题质量等关键逻辑。

## 产品内展示案例

产品顶部的“查看展示案例”会在当前界面中加载 nana’s green tea 新加坡案例。案例模式可查看三层研究结论、真实来源和三篇笔记策划，也能直接导出 Markdown。点击“返回当前项目”后恢复正在进行的真实任务，案例不会覆盖工作区数据。

展示案例来自 `data/showcase.json`，不需要重新采集或调用模型。产品的登录、真实 MCP 采集、分层检索、RAG、模型分析、笔记生成、人工修改和导出功能保持不变。

## 部署完整产品

完整产品需要运行 FastAPI 后端，不能只放在 OSS 或 OBS。建议部署到阿里云 ECS/轻量应用服务器或华为云 ECS，并配置 Python 3.12、Chrome、持久磁盘和 HTTPS 反向代理。

当前产品采用本机 Chrome 窗口完成小红书扫码登录，因此本地运行体验最完整。要让远程用户使用云端版本，下一步需要把登录二维码显示到产品页面，并为不同用户隔离登录会话。公开给多人使用前，还需要增加产品登录、数据库、对象存储和异步任务队列。

Render 免费实例没有适合当前浏览器会话的持久运行环境，正式产品优先使用带持久磁盘的云服务器。

### 当天可用：Cloudflare Tunnel

Cloudflare Pages 只能托管前端文件，不能直接运行本项目的 FastAPI、MCP 子进程和持久 Chrome。需要快速得到一个可访问地址时，可以让 Cloudflare Tunnel 安全转发到本机完整产品：

1. 使用远程访问模式启动产品：

   ```bash
   ALLOW_REMOTE=1 .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
   ```

2. 在 Cloudflare 控制台进入 **Networking → Tunnels**，创建名为 `youju-xhs` 的 Tunnel。
3. 选择 macOS，复制控制台生成的安装命令并在本机执行；不要把 Tunnel Token 写入仓库或聊天。
4. 在 Tunnel 的 **Routes** 中添加 **Published application**，Service URL 填写 `http://localhost:8765`。
5. 使用自己的域名时填写一个子域名，例如 `youju.example.com`。保存后等待 Tunnel 状态变为 Healthy。
6. 在 **Zero Trust → Access controls → Applications** 中为这个域名创建 Self-hosted 应用，只允许自己的邮箱访问。

此方式运行的是完整产品，模型 API、小红书 Chrome 登录与工作区数据仍保存在本机。电脑关机或本地服务停止后，外部地址会暂时不可用。

## 仓库数据策略

Git 只跟踪 `data/showcase.json` 脱敏展示快照。以下内容始终排除：

- `data/workspace.json` 本地任务状态与运行日志；
- `data/browser-profile/` 小红书登录态与浏览器缓存；
- `.env`、API 密钥和虚拟环境；
- 带临时访问参数的原始采集导出。

## 项目结构

```text
backend/             FastAPI、研究流程、写作检查和只读 MCP
frontend/            原生 HTML、CSS、JavaScript 三步工作台
data/showcase.json   可公开的脱敏展示案例
tests/               核心逻辑测试
启动工作台.command    macOS 本地启动入口
```
