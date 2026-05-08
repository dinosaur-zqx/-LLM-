"""
=============================================================================
文件名称：test_cache.py
作用：专用于在应用外部通过 HTTP 客户端模拟访问者，测试 app.py 的 API 缓存 (Redis 
或 Memory) 是否按照预期拦截并返回复用的回答，顺带验证监控分析端点。
适用系统：Windows 10/11
测试环境：Python 3.10.x
前提条件：必须先使用 `python app.py` 分离拉起后端服务端应用并使 8000 处于可用态。
=============================================================================
"""
import requests
import json
import time

# 测试缓存功能的目标探测主服务器地址
BASE_URL = "http://localhost:8000"

def wait_for_server(max_attempts=10):
    """
    循环阻塞等待目标服务器完全启动。
    发送最基本的根目录 GET 探测请求，当收到 200 返回码证明 FastAPI 挂载的 Uvicorn
    在内网就绪，此时能够防止测试脚本先于后端应用启动带来错漏连接测试报毁。
    """
    for i in range(max_attempts):
        try:
            # 加入 2 秒极短超时测试
            response = requests.get(f"{BASE_URL}/", timeout=2)
            if response.status_code == 200:
                print(f"服务器已成功在线捕获 (位于第 {i+1} 次循环)")
                return True
        except:
            # 异常忽略交出 CPU 去重试
            pass
        print(f"耐心等待后端应用启动挂载... (第 {i+1}/{max_attempts} 次尝试)")
        time.sleep(2)
        
    return False

def test_cache():
    """
    执行主测试函数，依序执行四个完整操作链。
    健康状态测试 -> 首次查询并保存缓存 -> 再次发送高亮命中缓存判断 -> 获取内存占用和命中指标
    """
    print("=== 高性能应用缓存功能验证自动测试 ===\n")

    # 强阻滞直到服务器启动
    if not wait_for_server():
        print("未检测到服务器存活在线信标，退出缓存测试程序。")
        return

    # ----- 1. 检查健康排查端点 -----
    print("1. 询问主服务目前的各层健康监控状态:")
    try:
        response = requests.get(f"{BASE_URL}/health")
        health = response.json()
        print(f"   返回的节点监测健康树: \n{json.dumps(health, indent=2, ensure_ascii=False)}")
    except Exception as e:
        print(f"   查询健康端点失败，网络不畅通: {e}")
        return

    # ----- 2. 发送第一次查询任务 (预期应当被 LLM 推演，并持久化到缓存里) -----
    print("\n2. 首发询问 (初次提速不命中测试):")
    # 填入测试所需的模拟负载
    payload = {"user_id": "test_user", "question": "请介绍一下Python编程语言"}
    try:
        response = requests.post(f"{BASE_URL}/chat", json=payload)
        result1 = response.json()
        
        # 捕捉返回体内置好的缓存信标（预期 cached: False）
        print(f"   缓存命中标记状态: {result1.get('cached')}")
        print(f"   缓存调用执行源: {result1.get('cache_source')}")
        print(f"   本地模型推演产生字符长度: {len(result1.get('answer', ''))}")
    except Exception as e:
        print(f"   请求后端解析产生抛错断线: {e}")
        return

    # ----- 3. 发送结构等同的任务测试拦截 (预期应当光速由缓存回退拦截) -----
    print("\n3. 重复发放同样负载（探测 API 回文与缓存拦截加速有效性）:")
    time.sleep(1)  # 避开去重或系统粘连
    try:
        response = requests.post(f"{BASE_URL}/chat", json=payload)
        result2 = response.json()
        
        # 对比两者数据应当相同，但是 cached 变为 True
        print(f"   新回答缓存标记状态: {result2.get('cached')}")
        print(f"   数据所调用来源: {result2.get('cache_source')}")
        print(f"   两次返回字符文体内容是否等同: {result1.get('answer') == result2.get('answer')}")
    except Exception as e:
        print(f"   缓存复借阶段由于报错断开: {e}")
        return

    # ----- 4. 请求查看应用自带的管理视图 -----
    print("\n4. 查看内部收集的请求漏斗统计与命中仪表板:")
    try:
        # 探测当前运行期间积累的数据与成功防止击穿的数量
        response = requests.get(f"{BASE_URL}/cache/stats")
        stats = response.json()
        
        print(f"   驱动此请求的底层机制: {stats.get('cache_type')}")
        print(f"   累积向模型查询发起的总 API 规模数: {stats.get('total_requests')}")
        print(f"   成功防止穿透直接取出的次数: {stats.get('cache_hits')}")
        print(f"   实际驱动 LLM 运算耗时的次数: {stats.get('cache_misses')}")
        print(f"   综合成功防击穿比率: {stats.get('hit_rate_percent')}%")
        print(f"   当前字典或 Redis 持有数据的物理体积量 (键值对): {stats.get('cache_size')}")
    except Exception as e:
        print(f"   调取仪表板出现拦截无报错: {e}")

# 若脚本为主文件直接运行，则接管
if __name__ == "__main__":
    test_cache()