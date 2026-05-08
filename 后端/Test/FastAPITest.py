"""
=============================================================================
文件名称：FastAPITest.py
作用：用于测试 FastAPI 环境是否配置成功，以及基础的路由和静态文件挂载。
适用系统：Windows 10/11
测试环境：Python 3.10.x
=============================================================================
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# 初始化 FastAPI 实例
app = FastAPI()

# 挂载静态文件目录 'static'，可以在浏览器中直接访问静态资源
app.mount('/static', StaticFiles(directory='static'), name='static')

@app.get("/")
def read_root():
    """
    根路由测试：
    返回个最基础的 JSON 对象确认服务健康，即输出 Hello World。
    """
    return {"Hello": "World"}

if __name__ == '__main__':
    # 依靠 Uvicorn 提供 ASGI 服务器驱动运行
    import uvicorn
    # 本地启动此文件即可在 127.0.0.1:8000 测试运行
    uvicorn.run(app, host="127.0.0.1", port=8000)
    
# 备用命令行运行方式：
# python -m uvicorn FastAPITest:app --reload