"""
=============================================================================
文件名称：OllamaTest.py
作用：用于测试与本地 Ollama 大语言模型推理服务的连通性。该脚本直接访问 Ollama 
暴露的 /api/generate 纯文本非聊天端口，检验指定模型是否就绪。
适用系统：Windows 10/11
测试环境：Python 3.10.x
前提条件：必须确保本地已成功启动 Ollama (默认 `http://localhost:11434`)，并且通过
         `ollama run deepseek-r1:1.5b` 这类命令提前下发并存在于库中。
=============================================================================
"""
import requests

def query_deepseek(input_text):
    """
    单步问答调用测试：
    包装一个非流式的简单 POST 请求发送给宿主 Ollama 接口。
    该方法仅针对 deepseek-r1:1.5b 模型进行生硬问题问询（区别于 app.py 里的流式与上下文记忆包装）。
    """
    try:
        # 定义目标服务器的 OLLAMA 生成类纯文本 API 的入口点
        url = "http://localhost:11434/api/generate"

        # 整理外置模型发送的数据包，不开启流模式以使其作为一整个 JSON 体阻塞返回
        payload = {
            "model": "deepseek-r1:1.5b",  # 强挂载的模型名称
            # 为测试拼装前后置上下文，迫使 AI 在一句话的基础上尝试答复
            "prompt": "请回答这个问题：" + input_text + "\n请回答我的问题。",
            "stream": False
        }

        # 通过 HTTP POST 发起阻塞远端请求推演（可能遇到数百毫秒的 CPU 占用等待）
        response = requests.post(url, json=payload)

        # HTTP Code 等于 200 证明顺利接收并且服务端没有出错（比如模型没找到的 404 等）
        if response.status_code == 200:
            result = response.json()
            # 抽离返回包中的主文段并输出到操作台供查看情况
            if "response" in result:
                print("模型回答：")
                print(result["response"])  # 直接印出大模型推理的长文本
            else:
                print("未找到模型的回答内容，可能响应结构未如期生成。")
        else:
            # 追踪失败包信息（例如 500 模型过载，或者 404 没装模型）
            print(f"请求服务报错或拒绝，服务器回应 HTTP 状态码：{response.status_code}")
            print(response.text)

    except Exception as e:
        # 如果连 11434 端口都打不开、没连接（意味着 Ollama 未安装或掉线），拦截抛错避免全员崩盘
        print(f"建立网络连接过程发生严重错误（检查 Ollama 客户端是否后台运行）：{e}")

# 当本脚本在命令行直接运行时挂起等待输入，继而传入 query_deepseek 并回显。
if __name__ == "__main__":
    input_text = input("等待发送指令，请输入您想要测试模型回答的问题：")
    query_deepseek(input_text)