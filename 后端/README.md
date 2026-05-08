# LLM Helper - 智能聊天助手后端服务 (Intelligent Chatbot Backend)

本项目是一个基于 FastAPI 构建的高性能、高并发、支持上下文记忆的本地大语言模型（LLM）对话后端服务。系统专为 Windows 平台架构设计，通过对接 Ollama 本地推理服务，结合 MySQL 数据持久化、Redis 语义缓存以及动态子进程端口分配机制，为高并发的多用户提供进程级隔离的聊天 API 接口。

---

## 目录

1. 核心技术架构
2. 运行环境与依赖要求
3. 环境部署与初始化
4. 服务启动说明
5. API 接口规范与终端调用示例 (PowerShell)
6. 并发与缓存机制深度解析
7. 测试模块说明
8. 常见异常排查思路

---

## 1. 核心技术架构

- 多进程独立内存隔离 (Multi-process Sandbox)：主网关接收请求后，利用 Python multiprocessing 模块在后台寻找可用端口，并孵化出独享的 FastAPI 子进程供特定 user_id 专用。内置 TCP Socket 阻塞连通性检测，严格阻断异步非阻塞情况下的进程竞态条件（Race Condition），杜绝端口未挂载前导致的连接拒绝异常。
- 智能上下文序列化 (Contextual Memory)：默认从 MySQL 提取该用户的近 5 次历史会话，规范化为 role 数组输入大模型，确立上下文连贯性。
- 差异化多级缓存 (Multi-level Cache)：
  - L1 频控去重防御 (Dedupe)：基于时间戳与问题正则清洗。如发现指定 TTL 内的连续重复提交，将以 0 毫秒耗时直接下发历史结果。
  - L2 散列语义命中 (Redis/Memory)：构建高稳定性序列化字典（_stable_dumps）并进行 SHA-256 散列加密。只要存在相同上下文与前置条件的对话，立即拦截昂贵的 LLM 推理开销。
- 全异步流式输出 (SSE Stream)：提供 Server-Sent Events 端点，支持打字机特性的长文本分块推流输出。

---

## 2. 运行环境与依赖要求

- 操作系统: Windows 10 / Windows 11 (原生支持并针对 PowerShell 请求进行了内容协商与编码适配)。
- 语言版本: Python 3.10.x（核心测试基准为 Python 3.10.10）。
- Web 框架结构: FastAPI (0.104.1) 配合 ASGI 服务器 Uvicorn (0.24.0)。
- 本地大模型基座: Ollama (必需装载 deepseek-r1:1.5b 模型)。
- 数据持久化栈: MySQL Server 8.x + mysql-connector-python 8.2.0。
- 独立缓存栈: Redis 5.0+ + redis 5.0.1 (非强制，降级方案为进程内内存字典)。

---

## 3. 环境部署与初始化

1. MySQL 数据库版本与表结构规范

   - 版本规范：基础架构依赖并推荐运行 MySQL Server 8.0 及以上版本（开发与测试基准为 MySQL 8.x）。此版本提供了更优的并发锁释放能力以及对 `utf8mb4_0900_ai_ci` 规则的原生支持，有效避免大模型生成的特殊字符(如 Emoji 或生僻汉字)在入库时引发的截断异常。
   - 端口与凭据：确保本地实例原生监听 3306 端口。系统预期使用具备 DDL 权限的账户交互，默认凭据设定为：用户名 root，密码 1234（如有异可于 `app.py` 中 `DB_CONFIG` 变量内修改）。
   - 数据表结构与 DDL 说明：无需人工干预介入，框架在 Uvicorn 生命周期回调内自动实例化底层对象。其本质隐式执行的数据库格式规范与引擎特征如下（采用 InnoDB 引擎保障服务高并发下的行级锁与持久化一致性）：
     ```sql
     CREATE DATABASE IF NOT EXISTS chatbot DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;
     USE chatbot;
     CREATE TABLE IF NOT EXISTS conversations (
         id INT AUTO_INCREMENT PRIMARY KEY,             -- 物理自增主键
         user_id VARCHAR(36) NOT NULL,                  -- 客户端挂载的 UUID4 用户隔离标识
         question TEXT NOT NULL,                        -- 前端或终端传入的原始请求特征实体
         answer TEXT NOT NULL,                          -- 大模型引擎基于语境推演的落盘输出
         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- 核心时序依据，为 L1 Dedupe 缓存引擎的时间差阻断提供支撑
     ) ENGINE=InnoDB;
     ```
2. Ollama 环境版本与基座模型配置

   - 架构版本要求：需在宿主机安装 Ollama Windows 原生执行版本（建议选用 >= v0.1.30）。此版本内聚了 DirectML 及 CUDA 跨平台硬件加速引擎的动态调度能力。系统默认通过 HTTP/1.1 协议调用对内环回网络地址 http://localhost:11434。
   - 数据通讯协议与编码：后端调用框架绕过原生类库，直接采用轻量级 REST API 进行封包。通过 `POST /api/chat` 及 JSON 数据载荷对本地推理服务进行单工或流式迭代拉取，默认遵循 UTF-8 字符解码。
   - 权重模型参数规范：核心代码中写死了对 `deepseek-r1:1.5b` 量化模型引擎的对接依赖。参数量级需占据约 1.1GB 的可用系统内存/显存（RAM/VRAM）。应用限制每次上下文滑动窗口推演最大可承接 4096 Tokens 的计算规模。
   - 初始化与依赖隔离：用户须调取 PowerShell 并执行下述命令，主动将远程模型仓库数据镜像缓存在本地 C 盘用户目录下：
     ```powershell
     ollama run deepseek-r1:1.5b
     ```
3. Redis 缓存系统版本与储存结构规范 (系统可选，高并发推荐)

   - 版本约束与运行模式：建议运行 Redis 5.0.x 及更高版本，系统硬绑定于 TCP 端口 6379 进行交互。Windows 平台支持采用微软非官方维护的衍生编译版 `redis-server.exe` 或利用 WSL2 (Windows Subsystem for Linux) 发行版启动原生实例。
   - 内存运维策略指导：对持久运行的聊天机器人平台而言，需要在 Redis Server 层级提前配置 `maxmemory-policy allkeys-lru` 内存淘汰隔离机制。此设定确保海量无效询问累积胀破底层物理内存限制时平稳丢弃冷数据。当无法捕获到服务心跳时，代码会自动断开 TCP 重试队列并降级为应用侧内置的 `SimpleTTLCache` 物理内存闭包字典。
   - K-V 层级储存格式声明：任何进入此组件的大概率耗时（LLM生成）运算结果将被固化落库，其具体解构逻辑如下：
     - Redis Keys 散列格式：对合并的请求数据利用 SHA-256 返回哈希运算，呈现如 `chatbot:cache:<64位特征化哈希字串>` 的绝对无撞击键名。
     - Redis Values 数据载荷：非压缩转换后的纯文本 (TEXT 型)，对应着返回终端客户的实体字符。
     - 生命周期管理 (TTL)：每一对写入缓存的键值均携带独立过期的过期时间戳（默认如 3600 秒）。超出生命周期的废弃语境回答将被 Redis 内核主动清扫。
4. 安装应用依赖库
   在项目根目录启动 PowerShell 制导安装：
   pip install -r .\chatbot-backend\requirements.txt

---

## 4. 服务启动说明

拉起中央网关与代理程序：
cd chatbot-backend
python app.py

启动终端将输出数据库建立状态、缓存节点连通性确认以及 Uvicorn 网络绑定。成功后网关守候在 IP 地址 0.0.0.0 的 8000 端口。
注：当前业务逻辑中，服务关闭退出逻辑被装饰了 @app.on_event("shutdown") 挂钩回调，会自动清洗截断 conversations 数据表以保持脏数据隔离。针对生产环境，请自行注销该清理代码。

---

## 5. API 接口规范与终端调用示例 (PowerShell)

本接口专门针对 Windows 系统提供了命令行可测性支持。请在 PowerShell 5.1 或更新版本中使用以下 Invoke-RestMethod 或 Invoke-WebRequest 命令直接验证数据流收发。

1. 申请独立子进程鉴权端口

- 端点：POST /start_user_service (网关端: 8000)
- 说明：计算安全空闲端口，派生微服务，返回路由与 UUID。
- PowerShell 指令示例：
  Invoke-WebRequest -Uri "http://localhost:8000/start_user_service" -Method POST | Select-Object -ExpandProperty Content
- 响应 JSON 结构：{"message":"用户 `<uuid>` 的服务已启动","port":8001,"user_id":"`<uuid>`","ready":true}

2. 执行主路会话逻辑

- 端点：POST /chat (转发至专属分流端口, 示例为 8001)
- 参数：Query String 类型参，必须包含认证 user_id 以及问题实体 question。
- PowerShell 指令示例：
  Invoke-RestMethod -Uri "http://localhost:8001/chat?user_id=<你获得的uuid>&question=介绍一下Windows" -Method POST
- 响应特征：将返回模型处理时长 specific_ms 及数据获取信道 cache_source。

3. 查询缓存漏斗拦截指标

- 端点：GET /cache/stats (网关端: 8000)
- PowerShell 指令示例：
  Invoke-RestMethod -Uri "http://localhost:8000/cache/stats" -Method GET

4. 执行基站全链路综合健康报告

- 端点：GET /health (网关端: 8000)
- PowerShell 指令示例：
  Invoke-RestMethod -Uri "http://localhost:8000/health" -Method GET

---

## 6. 并发与缓存机制深度解析

1. 异步端口竞争阻断 (Socket Polling Verification)
   微服务进程在操作系统层面申请资源时具有天然的时延特性。如果在启动指令派发完毕后立即将端口号回复给客户端，调用端执行下一跳极易引发 Connection Refused。应用通过内置 while 进程轮次探测（Import socket，阻塞式 connect_ex）充当阻滞锁，强行等待子服务 Uvicorn 绑定完成并确认 TCP 握手就绪后才解开锁释放 JSON 返回，完美解决了分布式初始化的时序冲突。
2. 字典降准稳定化机制 (Serialization Stabilization)
   由于本地推演在算力要求上极为昂贵，应用通过 make_cache_key 构建强验证。需注意 Python Dict 天然无序带来的序列化后 JSON Hash 不一致情况。框架通过自主内构 _stable_dumps 闭包实现强制 sort_keys 排序格式化，并结合 SHA-256 加密防冲突，确立高精度的 L2 并发查询安全防线。

---

## 7. 测试模块说明

执行项目自验流程以排查网络隔离原因。全部路径位于项目根级目录下执行。

1. 数据库状态断点测试
   python .\Test\MySQLTest.py
   直接向 3306 释放 root 控制信令，探测表结构搭建与数据推入连串功能。
2. Ollama 推理端接驳测试
   python .\Test\OllamaTest.py
   采用 POST 阻塞模式直达 localhost:11434，排除本地模型文件丢失或是推理基座过载的问题。
3. 缓存穿透自动巡检打压
   python .\chatbot-backend\test_cache.py
   需要运行应用基座 app.py 的前提下并在新控制台执行。测试流将按序释放具有同等 Payload 的测试包进行压迫，检测结果内 \cached: true\ 状态及其统计面板响应逻辑以论证命中拦截。

---

## 8. 常见异常排查思路

- 系统提示 "[WinError 10048] 通常每个套接字地址(协议/网络地址/端口)只允许使用一次"
  归因：上次运行异常退出或端口被游离主进程锁死挂起。
  对策：在 PowerShell 中执行 Stop-Process -Name python -Force 暴溃杀死孤儿进程即可重新绑定。
- 控制台抛错 "mysql.connector.errors.ProgrammingError: Access denied for user 'root'@'localhost'"
  归因：MySQL 服务器账号口令验证不通过。
  对策：应用代码默认设置密码凭据为 "1234"。校验 MySQL 账号口令，同步修改 app.py 内代码前段设定的 DB_CONFIG 对象元素。
- 持续提示 "降级：未发现 Redis 或产生连接阻断，退化为局域内存字典模式"
  归因：TCP 连接无法抓取至本地监听 6379 端口的 Redis Server。
  对策：非致命异常，应用内部已做容灾拦截。如需接通则确保 redis-server.exe 服务启动无误，同时禁用防护墙对该通信回路的干扰。
