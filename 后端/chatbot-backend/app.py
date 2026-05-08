"""
=============================================================================
项目名称：智能聊天助手后端服务 (Chatbot Backend Environment)
适用系统：Windows 10/11 (同时也兼容 Linux、macOS环境)
适用 Python：Python 3.10.x (开发及测试环境：Python 3.10.10)
依赖环境需求 (详情参考 requirements.txt)：
  - MySQL Server (需配置 DB_CONFIG 对接现有数据库，如 localhost, root:1234, DB chatbot)
  - Redis Server (可选，若无将自动退退至内存缓存简单实现，默认连接 localhost:6379 且不带密码)
  - Ollama (提供大语言大模型接口，需本地运行 http://localhost:11434 并预先安装好 deepseek-r1:1.5b 或者自行替换 OLLAMA_MODEL)
=============================================================================
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
import mysql.connector
import requests
import json
import os
from datetime import datetime
from multiprocessing import Process
import uvicorn
import socket
import uuid
import hashlib
import time
import re
import redis

# 初始化 FastAPI 主要实例，挂载主服务 (位于所有后台功能之前定义)。
app = FastAPI(title="智能聊天助手", version="1.0.0")

# 数据库配置 - 根据你的MySQL设置调整
# 用于存储用户的历史对话 (由于保存为多轮上下文记忆，推荐高可用的配置)
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',  # 改为你的MySQL用户名
    'password': '1234',  # 改为你的MySQL密码（如果设置了）
    'database': 'chatbot'
}

# Ollama配置
# 在本地直接请求运行了 "deepseek-r1:1.5b" 模型的引擎
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "deepseek-r1:1.5b"  # 使用你已有的模型

# 存储各用户分配端口的多进程间映射缓存
# 注意在复杂多进程下，可考虑将此置于 Redis 以满足集群一致性
allocated_ports = set()  # 记录已分配的端口
user_service_ports = {}  # 记录用户与端口的映射

# Redis缓存配置
# 捕捉连接错误实现灵活回退，如果 Redis 不在线，系统依然使用进程内字典进行简单缓存
try:
    # 建立持久化对象连接 default db 0，并开启 decode_responses 以默认获取字符串
    redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    redis_client.ping()  # 发送 ping 测试连接是否存活
    USE_REDIS = True
    print("Redis缓存已启用 (Connected: localhost:6379)")
except redis.ConnectionError:
    redis_client = None
    USE_REDIS = False
    print("Redis不可用，系统自动回退，使用进程内基础内存缓存...")

# 缓存统计信息
# 用于度量 API 被调用的整体利用率和命中率（监控用）
cache_stats = {
    "hits": 0,
    "misses": 0,
    "total_requests": 0,
    "cache_type": "redis" if USE_REDIS else "memory"
}

# 若 Redis 不可用，则回退到简单的全局字典答案缓存
# 此实现未借助过期清理线程，而是在调用包含包含检测或是设置新的 kv 时被动 Purge(内存淘汰)
if not USE_REDIS:
    class SimpleTTLCache:
        def __init__(self, maxsize: int, ttl: int):
            self.maxsize = maxsize  # 字典的最大容量
            self.ttl = ttl          # KV 生命周期(秒)
            self._data = {}         # {key: (value, expire_timestamp)}

        def _purge(self):
            # 将过去时刻到期的 KV 给清理掉
            now = time.time()
            # 删除过期
            for k, (v, exp) in list(self._data.items()):
                if exp <= now:
                    del self._data[k]
            # 容量保护：超出容量则按过期时间最早的(距现在最近甚至过期)先删
            over = len(self._data) - self.maxsize
            if over > 0:
                # 依据设定时间戳(过期时间早的先被清理)进行排序截取
                for k in sorted(self._data, key=lambda x: self._data[x][1])[:over]:
                    del self._data[k]

        def __contains__(self, key):
            self._purge()
            item = self._data.get(key)
            return bool(item) and item[1] > time.time()

        def get(self, key, default=None):
            if key in self:
                return self._data[key][0]
            return default

        def __setitem__(self, key, value):
            self._purge()
            # 存入元组: 缓存值及其过期的时间点时间戳
            self._data[key] = (value, time.time() + self.ttl)

        def __getitem__(self, key):
            val = self.get(key, None)
            if val is None:
                raise KeyError(key)
            return val

    # 全局实例初始化：容量1000条记录，TTL 1800秒=30分钟
    ANSWER_CACHE = SimpleTTLCache(maxsize=1000, ttl=1800)
else:
    ANSWER_CACHE = None  # Redis模式下避免额外开销不使用内存缓存

# 其他缓存与去重策略参数
DEDUPE_TTL_SECONDS = 600  # 同一用户近期（10分钟内）询问重复问题直接从最近记录复用答案，而非请求大模型

def get_cached_answer(cache_key: str) -> str:
    """
    基于唯一 Hash（cache_key）查询 LLM 历次生成的缓存。
    增加并更新服务统计利用率，若使用 Redis 将借助 get 查询 Redis Key。
    如果 Redis 失效/断开或默认不开启时触发内存 `ANSWER_CACHE` 读取。
    
    返回：缓存的应答字符串（若缓存失效，反回 None）。
    """
    global cache_stats
    cache_stats["total_requests"] += 1
    
    if USE_REDIS and redis_client:
        try:
            result = redis_client.get(cache_key)
            if result is not None:
                cache_stats["hits"] += 1
            else:
                cache_stats["misses"] += 1
            return result
        except redis.ConnectionError:
            return None
    elif ANSWER_CACHE:
        result = ANSWER_CACHE.get(cache_key)
        if result is not None:
            cache_stats["hits"] += 1
        else:
            cache_stats["misses"] += 1
        return result
    return None

def set_cached_answer(cache_key: str, answer: str, ttl: int = 1800):
    """
    对于未命中的请求（获取出新的答案），将其推入缓存系统。
    
    参数:
        - cache_key: 哈希 Key 唯一描述参数。
        - answer: 大模型返回的消息字符。
        - ttl: 有效期（缓存的时间），默认1800秒。
    """
    if USE_REDIS and redis_client:
        try:
            # 建立附带有过期的键值配对缓存
            redis_client.setex(cache_key, ttl, answer)
        except redis.ConnectionError:
            pass  # 静默失败：如果在此期间 Redis 被关闭断联不破坏业务核心正常通信
    elif ANSWER_CACHE:
        ANSWER_CACHE[cache_key] = answer

def _to_halfwidth(s: str) -> str:
    """
    将包含全角的字符（中英文混排引发错乱）规范转换为对应的半角方案
    以消除语义上相同仅字符宽细不对齐产生的重复 LLM 调用。
    """
    res = []
    for ch in s:
        code = ord(ch)
        # 捕捉全角空格进行半角空格转化
        if code == 0x3000:
            res.append(' ')
        # 识别中文全角符号区间（！起到～等），偏移置换回标准的拉定半角字符
        elif 0xFF01 <= code <= 0xFF5E:
            res.append(chr(code - 0xFEE0))
        else:
            res.append(ch)
    return ''.join(res)

# 消除过度用户礼貌导致冗余重复（降低缓存命中和存储浪费）
_POLITE_PREFIX = re.compile(r"^(请问|请帮我|请给我|请你|请|帮我|为我|麻烦你|麻烦|能不能|能否|可以|给我)")

def normalize_question(q: str) -> str:
    """
    轻量级的查询降重与预处理策略化算法：
      （1）. 转为 ASCII/半角 并做前后的剥边。
      （2）. 用无视大小写的约束将其规整为小写形式。
      （3）. 使用预制正则去除多余冗长的提示客套词语。
      （4）. 通过 Re 清除全部中英空白和转义断行，提高字符串唯一识别精度。
      注意：本算法目的是提升基于 hash 对问答调用的内存去重机制命中率，
      它产生的内容并不是用于提供给大模型上下文请求！
    """
    if not q:
        return ''
    q = _to_halfwidth(q)
    q = q.strip()
    # 英文小写
    q = q.lower()
    # 去礼貌前缀（一次）
    q = _POLITE_PREFIX.sub('', q)
    # 去常见冗余词
    q = q.replace('一下', '')
    # 去全部空白
    q = re.sub(r"\s+", "", q)
    return q

def _stable_dumps(obj) -> str:
    """
    序列化包装：生成确定的 JSON 格式并按照 Key 进行稳定排序。
    若传入对象存在无法序列化的嵌套问题，则降级方案回退到原生转换为 String，
    这有助于防范哈希构造阶段系统意外抛出故障 Exception。
    """
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(obj)

def make_cache_key(model: str, user_id: str, messages: list = None, question: str = None, gen_opts: dict = None) -> str:
    """
    结合多模态输入因子生成唯一性的 SHA-256 十六进制加密字符串(哈希)，
    代表这个上下文中当前问题。
    构成因子的 payload 包含使用模型名称、对应用户的ID，以及传递上文的历史对话 messages 和附加选项等。
    """
    payload = {
        "model": model,
        "user_id": user_id,
        "messages": messages or [],
        "question": question,
        "gen_opts": gen_opts or {},
    }
    raw = _stable_dumps(payload)
    # 利用 utf-8 编码对象做加密来减少散列中不同类型文字解析编码的影响
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def init_database():
    """
    执行项目初期的数据库连接建立，动态实例化所需的 Database（若不存在的话）
    并在此 Database（例如“chatbot”）之中加载或检查必备表 conversations。

    对话表 `conversations` 内容包含了对单轮交互中 user_id (识别对话), 
    question (问题文本), answer (回复内容)，以及用于清理/去重的对话建立时间 (created_at) 。
    """
    try:
        # 第一阶段: 连接至本地并使用根节点配置连接库(不提前选中任何 db, 防止尚未被建立抛出)
        conn = mysql.connector.connect(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password']
        )
        cursor = conn.cursor()

        # 第二阶段: 建构项目所需的数据并自动指向(选择)刚初始环境创建的 db
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_CONFIG['database']}")
        cursor.execute(f"USE {DB_CONFIG['database']}")

        # 创建/应用聊天会话记录表架构结构设计
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 固化所有对于数据结构的调整并妥善断开
        conn.commit()
        cursor.close()
        conn.close()
        print("数据库初始化成功 (MySQL 已就绪)")
    except Exception as e:
        print(f"数据库初始化失败，请确认你的服务端口或账密是否受阻碍 : {e}")

def get_db_connection():
    """
    提供给调用端的包装函数。
    向外暴露包含字典 DB_CONFIG 的展开形式，并随时安全打开一个新的 MYSQL.conn 对象。
    """
    return mysql.connector.connect(**DB_CONFIG)

class LLMService:
    """
    定义负责外部 Ollama 服务交互行为的高阶包装器类.
    目前该机制只针对于 RESTful HTTP（经由 requests 模块），调用本地的聊天推演引擎 `OLLAMA_MODEL` 计算逻辑得出消息返回。
    """
    def __init__(self):
        # 挂载预配置的常量配置 (在最开始定义的 OLLAMA_URL 和 OLLAMA_MODEL)
        self.base_url = OLLAMA_URL
        self.model = OLLAMA_MODEL
    
    def generate_response(self, prompt: str) -> str:
        """
        （向下兼容支持功能：处理单请求单问题，不依赖历史等其他信息)
        直接使用纯字符 prompt 问题，并触发封装的通用 chat 调取方法
        """
        return self.chat(messages=[{"role": "user", "content": prompt}], stream=False)

    def chat(self, messages: list, stream: bool = False) -> str:
        """
        执行主干 Ollama REST Api (普通/单同步版请求)。
        传入带有角色标签 role:(system/user/assistant), 和消息文本 content 的字典序列，
        生成并抽取最终回退信息内的内容块 (["message"]["content"]) 返回给服务器调用处。
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream
        }

        try:
            # 开启网络连接请求 API
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=120  # 避免因生成庞长问题/大模型超时无响应导致服务器资源长时间锁死卡住
            )
            response.raise_for_status()  # 若检测到异常的HTTP失败返回码将被拦截为抛错
            response.encoding = 'utf-8'  # 保证对于本地或国际字符解码不出现方块或非预期乱码
            result = response.json()
            return result["message"]["content"]
        except Exception as e:
            # 若连接不顺返回具有意义的辅助错误字符，防止后续应用奔溃
            return f"抱歉，AI服务暂时不可用或超载发生请求拒绝 : {str(e)}"
    
    def stream_response(self, prompt: str):
        """
        （向下兼容流式流转支持：利用纯字符 prompt，以逐步流返回前端消息段迭代包），采用 Yield 生成器操作。
        """
        yield from self.stream_chat(messages=[{"role": "user", "content": prompt}])

    def stream_chat(self, messages: list):
        """
        基于 `stream=True` 设置的参数实现逐步将大模型的长包解构成小输出数据快。
        用于 /chat/stream 端点返回前端 SSE（Server-Sent Events) ，增强用户的交互加载观感并减少等候感。
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True
        }

        try:
            # 开启网络连接请求 API (流模式)
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                stream=True,     # 重要：保持HTTP通道开启以一直接收片段
                timeout=120      # 获取首个响应符的超时时间限制
            )
            response.raise_for_status()

            # 按行迭代流式响应返回的内容，并解析服务器协议 (以 'data: ' 打头为准)
            for line in response.iter_lines():
                if line:
                    line_data = line.decode('utf-8')
                    if line_data.startswith('data: '):
                        try:
                            # 提取核心数据块 payload
                            data = json.loads(line_data[6:])
                            
                            # 收到结束包标识 done 后跳出生成器推演
                            if data.get('done', False):
                                break
                                
                            # 若包正常，产生新字符至终端客户连接
                            if 'message' in data and 'content' in data['message']:
                                yield data['message']['content']
                        except json.JSONDecodeError:
                            # 忽视格式不对等的非标准损坏行
                            continue
        except Exception as e:
             # 生成抛出用于警示客户端的网络/逻辑流故障
            yield f"请求失败: {str(e)}"

# 将整个工具作为单例绑定在当前服务上提供给下方接口调用
llm_service = LLMService()

# --- 生命周期函数注册 ---

@app.on_event("startup")
async def startup_event():
    """
    FastAPI 进程启动时挂载的挂钩(Hook)任务。
    用以进行如确保 DB 可用和表格完备的核心子程序初始化工作。
    """
    init_database()

# --- 核心主服务路由注册 ---

@app.get("/")
def read_root():
    """
    主根路径。
    用于返回应用自身服务健康状态探测，让外部负载均衡或其他调用者可以简单的检查运行良好度。
    """
    content = {
        "message": "智能聊天助手后端服务",
        "model": OLLAMA_MODEL,
        "status": "运行中"
    }
    # 指定 charset=utf-8，帮助 PowerShell 等客户端正确解码
    return JSONResponse(content=content, media_type="application/json; charset=utf-8")

@app.post("/chat")
async def chat(user_id: str, question: str):
    """
    通用同步全量回复端点。
    包含上下文联系处理、数据库操作保存记录以及跨服务去重的能力。
    它不仅会将用户提问发送给本地大模型引擎并保存聊天历史，还会智能判断短时间的高频冗余拦截：对于近期提出过的相同问题将在规定 TTL 内截带直接返还缓存记录。
    """
    if not question.strip():
        # 问题不能全是空或字符空白，否则提前拦截抛错 HTTP 400
        raise HTTPException(status_code=400, detail="问题不能为空")

    print(f"收到用户 {user_id} 的问题: {question}")

    # ===== 第 1 步: 数据准备 =====
    # 打开数据库连接提取属于该用户的历史沟通
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    # 取出最近 5 次的问答对作为上下文推演参数
    cursor.execute(
        "SELECT question, answer, created_at FROM conversations WHERE user_id = %s ORDER BY created_at DESC LIMIT 5",
        (user_id,)
    )
    history = cursor.fetchall()
    cursor.close()
    conn.close()

    # ===== 第 2 步: 处理频移请求限流水位拦截 (Dedupe) =====
    # 近期重复问题去重：若该调用者最近一次的提问记录和本次在字义和时间上(DEDUPE_TTL_SECONDS)基本吻合，直接回填过往模型答案，跳出执行链。
    if history:
        latest = history[0]
        try:
            # 经过字形规范化后的模糊识别比对以抵御诸如全半角，空格等差异产生的无意义未命中
            if normalize_question(latest["question"]) == normalize_question(question):
                created_at = latest.get("created_at")
                if isinstance(created_at, datetime):
                    age = (datetime.now() - created_at).total_seconds()
                    # 控制时间戳差异要在设定的 TTL 内
                    if age <= DEDUPE_TTL_SECONDS:
                        content = {
                            "user_id": user_id,
                            "question": question,
                            "answer": latest["answer"],
                            "model": OLLAMA_MODEL,
                            "cached": True,
                            "cache_source": "dedupe",  # 明确指向由排错去重引擎拦截而产生缓存标识
                            "processing_ms": 0         # 本地查询未动用大计算，标记为 0ms 保护服务器指标健康
                        }
                        return JSONResponse(content=content, media_type="application/json; charset=utf-8")
        except Exception:
            # 去重功能不能阻塞正常主路径，任何发生在其范围的问题都应进行消音捕捉 pass，交由下一步的完整调用接管
            pass

    # ===== 第 3 步: 上下文拼装组建大对象 (Payload Assembly) =====
    # 作为多轮上下文传入的第一项，强迫 LLM 定位自身为中文系统助手
    messages = [
        {"role": "system", "content": "你是一个中文智能助手。请严格依据对话历史进行回答。若用户问‘我问了什么问题’，请准确复述上一轮用户问题。"}
    ]
    
    # 数据库 history 采用倒序 (最新在上), 压入 array 前要取逆反后按照从产生到最近的正序逐一交替添加 user与 assistant 问答片段
    for item in reversed(history):
        messages.append({"role": "user", "content": item['question']})
        messages.append({"role": "assistant", "content": item['answer']})
        
    # 添加本次真正发起的质询以让它闭环
    messages.append({"role": "user", "content": question})

    # ===== 第 4 步: 常量语义缓存比对引擎探测 (Semantic Hit-check) =====
    # 用上下文阵列联合作为签名 Key 加密。这样即使用了相同的词但是前面聊的东西不同，也会打出不一样的散列，防止前后环境穿插污染
    cache_key = make_cache_key(OLLAMA_MODEL, user_id, messages=messages)
    cached = False
    
    # 向 Redis (首选) 或 LruDict (后备) 查询值
    answer = get_cached_answer(cache_key)
    
    # ===== 第 5 步: 未命中缓降兜底并触发远程执行 (LLM Call upon Miss) =====
    start_proc = time.time()
    if answer is None:
         # 同步阻塞调用 LLM (无流式) 等待远端推演的最终长文本。
         # 本计算在模型服务过载时可能会延迟长达数百秒
        answer = llm_service.chat(messages, stream=False)
        set_cached_answer(cache_key, answer) # 由于执行极为昂贵，成功后必须持久进入内存
        cache_source = "none"
    else:
        cached = True
        cache_source = "redis" if USE_REDIS else "memory"
        
    end_proc = time.time()
    processing_ms = int((end_proc - start_proc) * 1000)

    # ===== 第 6 步:  写入数据库记录会话行为 (Sync write) =====
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO conversations (user_id, question, answer) VALUES (%s, %s, %s)",
        (user_id, question, answer)
    )
    conn.commit()
    cursor.close()
    conn.close()

    # ===== 第 7 步: 返回带包体与追踪字段的有效响应格式 =====

    content = {
        "user_id": user_id,
        "question": question,
        "answer": answer,
        "model": OLLAMA_MODEL,
        "cached": cached,
        "cache_source": cache_stats["cache_type"] if cached else None,
        "processing_ms": processing_ms
    }
    # 确保返回的 JSON 响应包含 charset=utf-8
    return JSONResponse(content=content, media_type="application/json; charset=utf-8")

@app.post("/chat/stream")
async def chat_stream(question: str):
    """
    基于 SSE (Server-Sent Events) 的流式文字聊天端点。
    注意：这是简化的旧版函数接口，不处理 user_id 及上下文。
    """
    if not question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")
    
    # 闭包域内的可变承载体，搜集零散发散出的汉字以在末端持久化库写入
    full_answer = ""
    
    def generate():
        nonlocal full_answer
        for chunk in llm_service.stream_response(question):
            full_answer += chunk
            # 格式化为SSE规范的标准推流封装包 "\n\n"，返回 bytes 并强制附加 UTF-8 编码
            s = f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
            yield s.encode('utf-8')
        
        # Generator 结尾拦截：流式传输全部分解结束，意味着对话结束，开始持久落盘数据库
        if full_answer:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO conversations (question, answer) VALUES (%s, %s)",
                (question, full_answer)
            )
            conn.commit()
            cursor.close()
            conn.close()
    
    # 将推演生成器对象附着在 StreamingResponse (FastAPI 高阶对象)返回
    return StreamingResponse(
        generate(), 
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache", # 拦截浏览器和任何前端中介CDN可能实施的 HTTP 重复请求缓存机制
            "Connection": "keep-alive",  # 通知底层 TCP 维持在推流全时段活跃连接不断开
        }
    )

@app.get("/history")
async def get_history(limit: int = 10):
    """
    获取全局会话历史查看页 API.
    使用 LIMIT 防止一次性吐出过多对象堵塞网络和占用数据库开销。
    注意：这里属于无差别的跨用户粗暴取值端, 主要用作管理视图。
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, question, answer, created_at FROM conversations ORDER BY created_at DESC LIMIT %s",
        (limit,)
    )
    conversations = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return {
        "history": conversations,
        "count": len(conversations)
    }

@app.get("/cache/stats")
async def cache_stats_endpoint():
    """
    管理员缓存服务可用性探测和指标可视化图谱端节点。
    暴露了应用的实际工作负荷中缓存层(Redis/Mem)阻挡的占比、存活量和请求规模。
    """
    global cache_stats
    
    # 计算全局命中率百分比指标
    total = cache_stats["total_requests"]
    hit_rate = (cache_stats["hits"] / total * 100) if total > 0 else 0
    
    # 获取缓存当前装载容量大小/元素量（对Redis采用DBSize；如果退为内存应用私有变量长度）
    cache_size = 0
    if USE_REDIS and redis_client:
        try:
            cache_size = redis_client.dbsize()
        except redis.ConnectionError:
            cache_size = "无法连接"
    elif ANSWER_CACHE:
         # 尝试绕过装饰器强行获取内存缓存字典的内容尺寸
        cache_size = len(ANSWER_CACHE._data) if hasattr(ANSWER_CACHE, '_data') else "未知"
    
    stats = {
        "cache_type": cache_stats["cache_type"],     # 当前运行的实现底座： redis 或 memory
        "total_requests": total,                     # API总被探及次数
        "cache_hits": cache_stats["hits"],           # 成功在过期前找到记录
        "cache_misses": cache_stats["misses"],       # 源模型被穿透调用的强求计数
        "hit_rate_percent": round(hit_rate, 2),      # 百分比转换后抹平精度
        "cache_size": cache_size,                    # 承压体积
        "timestamp": datetime.now().isoformat()      # 时序标记，给外部面板提供采样时间
    }
    
    return JSONResponse(content=stats, media_type="application/json; charset=utf-8")

@app.get("/health")
async def health_check():
    """综合全链路健康及状态可用性测试检测端点，探测链条涵盖 Web服务、本地AI服务组件、MYSQL依赖"""
    try:
        # 子探测 1: 扫描 Ollama AI底座存活标志
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        ollama_status = "healthy" if response.status_code == 200 else "unhealthy"
        
        # 子探测 2: 验证本机的 MYSQL 后置存取通道
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        db_status = "healthy"
        cursor.close()
        conn.close()
        
        # 子探测 3: 检查缓存引擎就绪态
        cache_status = "healthy" if (USE_REDIS and redis_client) or ANSWER_CACHE else "unhealthy"
        
    except Exception as e:
        # 如任何一环建立失败，则标记该节点故障抛出
        ollama_status = "unhealthy"
        db_status = "unhealthy"
        cache_status = "unhealthy"
    
    return {
        "status": "ok",
        "ollama": ollama_status,
        "database": db_status,
        "cache": cache_status,
        "cache_type": cache_stats["cache_type"],
        "model": OLLAMA_MODEL,
        "timestamp": datetime.now().isoformat()
    }

# --- 服务生命周期事件注册 ---

@app.on_event("shutdown")
async def shutdown_event():
    """
    程序关闭退出时运行清理脚本，销毁测试与长期累积的污染脏数据，
    保证下次重启环境和聊天是隔离的全新状态（视你的业务需求决定此处是否需要保留表）。
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # 清空所有对话追踪
        cursor.execute("DELETE FROM conversations")  
        conn.commit()
        cursor.close()
        conn.close()
        print("数据库清理：conversations记录已清空")
    except Exception as e:
        print(f"清空数据库失败: {e}")

# --- 分布式架构：多用户子进程生成与绑定控制端 ---

def start_user_service(user_id: str, port: int):
    """
    生成一个只专属于单一 User ID 用户的小型沙盒 FastAPI 应用程序实例。
    每个实例都会被 uvicorn 挂载绑定到服务器随机空闲或指定的一个物理 Port。
    """
    # 建立属于此端口子应用的内联实例
    user_app = FastAPI(title=f"用户 {user_id} 的聊天助手", version="1.0.0")

    @user_app.post("/chat")
    async def user_chat(question: str, client_user_id: str = Query(default=None, alias="user_id")):
        """当前子应用的私有沟通频道入口"""
        if not question.strip():
            raise HTTPException(status_code=400, detail="问题不能为空")

        # 认证环节: 如果客户端传入了多余的 user_id 查询串，鉴权阻止串户跨权会话。
        if client_user_id and client_user_id != user_id:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "user_id_mismatch",
                    "message": "该端口已绑定其它单用户，防串号限制拒绝履行，请使用本端口专属 user_id 调用。",
                    "expected_user_id": user_id,
                    "got_user_id": client_user_id
                },
                media_type="application/json; charset=utf-8",
            )

        # 同样继承执行 DB 上下文查阅逻辑
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT question, answer, created_at FROM conversations WHERE user_id = %s ORDER BY created_at DESC LIMIT 5",
            (user_id,)
        )
        history = cursor.fetchall()
        cursor.close()
        conn.close()

        # DEDUPE - 时间和向量词频级防止短时间多动提交
        if history:
            latest = history[0]
            try:
                if normalize_question(latest["question"]) == normalize_question(question):
                    created_at = latest.get("created_at")
                    if isinstance(created_at, datetime):
                        age = (datetime.now() - created_at).total_seconds()
                        if age <= DEDUPE_TTL_SECONDS:
                            content = {
                                "user_id": user_id, 
                                "question": question, 
                                "answer": latest["answer"], 
                                "model": OLLAMA_MODEL, 
                                "cached": True, 
                                "cache_source": "dedupe",
                                "processing_ms": 0
                            }
                            return JSONResponse(content=content, media_type="application/json; charset=utf-8")
            except Exception:
                pass

        # 组装完整的包含系统 System 伪装词和大文预设规则
        messages = [
            {"role": "system", "content": "你是一个中文智能助手。请严格依据对话历史进行回答。若用户问‘我问了什么问题’，请准确复述上一轮用户问题。"}
        ]
        for item in reversed(history):
            messages.append({"role": "user", "content": item['question']})
            messages.append({"role": "assistant", "content": item['answer']})
        messages.append({"role": "user", "content": question})

        # Cache 和模型下推机制 (同大主端)
        cache_key = make_cache_key(OLLAMA_MODEL, user_id, messages=messages)
        cached = False
        answer = get_cached_answer(cache_key)
        start_proc = time.time()
        
        if answer is None:
            # 真实耗时阻滞点
            answer = llm_service.chat(messages, stream=False)
            set_cached_answer(cache_key, answer)
        else:
            cached = True
            
        end_proc = time.time()
        processing_ms = int((end_proc - start_proc) * 1000)

        # 最后沉淀落库到全局 DB
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO conversations (user_id, question, answer) VALUES (%s, %s, %s)",
            (user_id, question, answer)
        )
        conn.commit()
        cursor.close()
        conn.close()

        # 封装后归还。在内联函数中强制指定 utf8 让 Win平台 收到正确的汉化流
        content = {
            "user_id": user_id, 
            "question": question, 
            "answer": answer, 
            "model": OLLAMA_MODEL, 
            "cached": cached, 
            "cache_source": cache_stats["cache_type"] if cached else None, 
            "processing_ms": processing_ms
        }
        return JSONResponse(content=content, media_type="application/json; charset=utf-8")

    # uvicorn 作为 ASGI 服务器在这个阻塞子进程中接管，绑定独立网络服务
    uvicorn.run(user_app, host="0.0.0.0", port=port)

def find_available_port(start_port: int = 8001) -> int:
    """
    侦测底层系统空闲可用的 TCP 端口分配。
    采用逻辑推演加上试探绑定的双重保险手段防止资源冲突（如外部服务占用了我们的目标段）。
    """
    port = start_port
    # 首先规避本程序自行分配记录过的池内端口
    while port in allocated_ports:
        port += 1
    
    # 随后建立真实的 Socket 检测该端口是否在操作系统层面已经被别的大进程霸占
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # connect_ex 若返回 0 表示链接成功, 即是此路不同(已被监听占用)
        while s.connect_ex(("0.0.0.0", port)) == 0:
            port += 1
            
    allocated_ports.add(port)
    return port

@app.post("/start_user_service")
async def start_service():
    """
    中央路由派发与进程孵化器指令端。
    无需传入参数，它会自动计算并在后台利用 Python 的 Multiprocessing 
    模块独立剥离出一个带自己独享端口的新 Uvicorn 进程来为访问者进行专门会话。
    """
    user_id = str(uuid.uuid4())  # 为连接过来的匿名访客安全配发一张不重叠的 UUID4 卡片
    if user_id in user_service_ports:
        # 该兜底防止由于某种客户端意外重发导致的同一 ID 多开，此时退还先发老端口
        port = user_service_ports[user_id]
        content = {"message": f"用户 {user_id} 的服务已启动", "port": port, "user_id": user_id}
        return JSONResponse(content=content, media_type="application/json; charset=utf-8")

    # 到资源层索要空闲的有效通行号段
    port = find_available_port()  
    
    # 正式分叉产生硬性内存隔离隔离的新 Python Sub-process，将 ID 和 Port作为初始变量携带过去
    process = Process(target=start_user_service, args=(user_id, port))
    process.start()

    # 更新中央网关字典，留作备查
    user_service_ports[user_id] = port

    # ===== 竞态条件阻断机制控制阀 (Race condition stopper) =====
    # 子进程完全拉起操作系统 API 并分配 Uvicorn socket 往往需要大几百毫秒时间。
    # 强制让父主进程在这里主动进行内部心跳探测阻塞(Pooling)，
    # 直到确认那个子网络端口可以接纳 TCP 连接时才放行响应回给 HTTP 用户。
    # 彻底杜绝调用方马上发起二次接口查询时遇到“连接被拒 Connection refused”报错的问题。
    ready = False
    import socket as _socket
    from time import sleep as _sleep
    timeout_seconds = 6               # 给予最坏情况(机器过载)的弹性死等上限时长
    waited = 0

    while waited < timeout_seconds:
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                s.settimeout(1)       # 连接如果悬空立马断开, 进行下一轮短循环重试
                s.connect(("127.0.0.1", port))
                ready = True          # 没有异常说明服务端握手确认建立，服务成功启动!
                break
        except Exception:
            _sleep(0.5)               # 避让出 CPU 时间片休息
            waited += 0.5

    content = {
        "message": f"用户 {user_id} 的服务已启动", 
        "port": port, 
        "user_id": user_id, 
        "ready": ready   # 对外透出门控标志以示程序是否在有限周期内妥善接管完毕
    }
    
    return JSONResponse(content=content, media_type="application/json; charset=utf-8")

# 主程序直接以脚本启动的着陆点
if __name__ == "__main__":
    # 作为总管理进程在内网网卡(0.0.0.0)监听所有来源，暴露出 8000 用于网关功能
    uvicorn.run(app, host="0.0.0.0", port=8000)
#Invoke-WebRequest -Uri "http://localhost:8000/start_user_service" -Method POST | Select-Object -Expand Content
#Invoke-WebRequest -Uri "http://localhost:8001/chat?user_id=<user_id>&question=介绍一下美国" -Method POST
#Invoke-WebRequest -Uri "http://localhost:8001/chat?user_id=<user_id>&question=介绍一下美国" -Method POST
