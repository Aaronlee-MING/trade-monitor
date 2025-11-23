import requests
import sys

print("🔥 开始网络连通性测试 (Network Diagnostic)...")
print("-" * 50)

# 1. 模拟浏览器的“身份证” (Headers)
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

targets = [
    # 测试 1: 访问 Google (测试你的网络能不能出外网)
    {"name": "Google Check", "url": "https://www.google.com"},
    
    # 测试 2: 你的核心目标 OFAC (看看是否拦截 GitHub IP)
    {"name": "OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions"},
    
    # 测试 3: 中国商务部 (看看国内网站通不通)
    {"name": "MOFCOM", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/"}
]

for target in targets:
    print(f"\n📡 正在连接: {target['name']} ...")
    try:
        # verify=False 是为了跳过 SSL 证书报错，timeout=10 是防止卡死
        response = requests.get(target['url'], headers=headers, timeout=10, verify=False)
        
        status = response.status_code
        print(f"   [状态码]: {status}")
        
        if status == 200:
            print("   ✅ 连接成功！(Success)")
            # 打印网页长度，确保不是空壳
            print(f"   [数据量]: {len(response.text)} 字符")
        elif status == 403:
            print("   ❌ 拒绝访问 (Forbidden) - 你的 IP 被网站拉黑了")
        elif status == 404:
            print("   ❌ 页面不存在 (Not Found)")
        else:
            print(f"   ⚠️ 其他状态: {status}")
            
    except Exception as e:
        print(f"   ☠️ 连接彻底失败: {e}")

print("\n" + "-" * 50)
print("测试结束。")
