"""查询 VTS 模型所有可注入参数"""
import time
import sys
sys.path.insert(0, "F:\\Ollama")
from voice import vmc

print("正在连接 VTS...")
vmc.init()
time.sleep(2)
print("正在查询参数列表...")
vmc.list_parameters()
time.sleep(3)
print("查询完成")