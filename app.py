import streamlit as st
import requests
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
import json
import time

# ==========================================
# 1. 配置区域 (Configuration)
# ==========================================

# 硅基流动 DeepSeek API 地址
AI_API_URL = "https://api.siliconflow.cn/v1/chat/completions"

# 监控关键词 (中英双语)
KEYWORDS = [
    "Sanction", "Entity List", "Tariff", "Chip", "Semiconductor", 
    "Ban", "China", "Russia", "制裁", "清单", "关税", "芯片", "出口管制"
]

# 12个硬编码的数据源 (Hardcoded Sources)
SOURCES = [
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "type": "vip"},
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "type": "vip"},
    {"name": "🇨🇳 MOFCOM", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "type": "standard"},
    {"name": "🇬🇧 Reuters Defense", "url": "https://www.reuters.com/business/aerospace-defense/", "type": "media"},
    {"name": "🇺🇸 Commerce Press", "url": "https://www.commerce.gov/news/press-releases", "type": "vip"},
    {"name": "🇺🇸 DOJ Press", "url": "https://www.justice.gov/news/press-releases", "type": "vip"},
    {"name": "🇪🇺 EU Council", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "type": "vip"},
    {"name": "🇨🇳 外交部发言人", "url": "https://www.fmprc.gov.cn/fyrbt_673021/", "type": "standard"},
    {"name": "🇺🇸 BIS Enforcement", "url": "https://www.bis.gov/enforcement/export-violations", "type": "vip"},
    {"name": "🏛️ CSIS Analysis", "url": "https://www.csis.org/analysis", "type": "media"},
    {"name": "🇺🇸 US Congress", "url": "https://www.congress.gov/search?q=%7B%22source%22%3A%22legislation%22%2C%22congress%22%3A%22118%22%7D", "type": "media"},
    {"name": "🇭🇰 SCMP", "url": "https://www.scmp.com/news/china/diplomacy", "type": "media"},
]

# ==========================================
# 2. 核心功能模块 (Core Functions)
# ==========================================

def fetch_content(source):
    """
    使用 curl_cffi 伪装成 Chrome 120 浏览器抓取网页。
    这是为了绕过 Cloudflare 等反爬虫机制。
    """
    try:
        # 针对不同网站使用不同策略，这里统一使用抗封锁能力最强的 impersonate="chrome120"
        # timeout 设置为 15 秒，防止卡死
        response = cffi_requests.get(
            source["url"], 
            impersonate="chrome120", 
            timeout=15
        )
        
        # 使用 BeautifulSoup 解析 HTML
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 简单粗暴但有效：提取所有段落和标题的文本
        texts = [p.get_text().strip() for p in soup.find_all(['p', 'h2', 'h3', 'a']) if len(p.get_text().strip()) > 20]
        
        # 截取前 3000 个字符用于 AI 分析，避免 Token 溢出
        full_text = " ... ".join(texts[:30]) 
        return full_text[:3000]
        
    except Exception as e:
        return f"Connection Error: {str(e)}"

def analyze_risk(text, source_name, api_key):
    """
    调用 DeepSeek-V3 API 进行智能分析。
    逻辑：宁可错杀，不可漏报 (Zero False Negatives)。
    """
    if "Connection Error" in text or len(text) < 50:
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # 提示词策略：保持中立，只过滤纯行政噪音
    prompt = f"""
    You are a strict Trade Compliance Analyst. Analyze the following text from {source_name}.
    
    Text: "{text}..."
    
    Task: Determine if this contains ANY information related to:
    - Sanctions, Entity Lists, Tariffs, Export Controls
    - Supply Chain disruptions, Semiconductor/Chip bans
    - Geopolitical tension (China/Russia/US/EU)
    
    Rules:
    1. "Balanced Filter": KEEP the news if it is remotely relevant.
    2. REJECT only pure noise (e.g., "Site Maintenance", "Cookie Policy", "Happy Holidays", "Subscribe now").
    3. Output JSON format: {{"relevant": true/false, "summary": "One sentence summary in Chinese", "risk_level": "High/Medium/Low"}}
    """

    data = {
        "model": "deepseek-ai/DeepSeek-V3", # 使用 DeepSeek V3
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }

    try:
        # 这里使用标准 requests 库调用 API
        resp = requests.post(AI_API_URL, headers=headers, json=data, timeout=20)
        result = resp.json()['choices'][0]['message']['content']
        
        # 清理 JSON 格式（防止 AI 返回 markdown 代码块）
        clean_json = result.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        return None

# ==========================================
# 3. 界面 UI 设置 (Law Firm Style)
# ==========================================

st.set_page_config(page_title="Global Trade Monitor", layout="wide")

# 注入自定义 CSS：律所风格 (白底、藏青色文字、衬线字体)
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Source+Serif+Pro:wght@400;600&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Source Serif Pro', serif;
        color: #1a202c;
    }
    .stApp {
        background-color: #ffffff;
    }
    h1, h2, h3 {
        color: #0F294D; /* Navy Blue */
        font-weight: 600;
    }
    .report-card {
        border-left: 5px solid #0F294D;
        background-color: #f8f9fa;
        padding: 15px;
        margin-bottom: 15px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .tag {
        background-color: #e2e8f0;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.8em;
        font-weight: bold;
        color: #2d3748;
    }
    .risk-High { color: #c53030; }
    .risk-Medium { color: #d69e2e; }
    .risk-Low { color: #38a169; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 4. 主程序逻辑 (Main Execution)
# ==========================================

def main():
    st.title("⚖️ 贸易合规监控系统 | Trade Compliance Monitor")
    st.markdown("---")

    # 侧边栏配置
    with st.sidebar:
        st.header("系统设置")
        api_key = st.text_input("输入 SiliconFlow API Key", type="password")
        st.info("提示：无需担心，Key 仅存储在内存中。")
        run_btn = st.button("🚀 启动全网扫描")

    if run_btn and api_key:
        status_area = st.empty()
        status_area.info("⏳ 正在初始化 10 个并发线程进行全球扫描...")
        
        results_container = st.container()
        
        # 使用 ThreadPoolExecutor 进行并发抓取 (速度提升 10 倍)
        # max_workers=12 意味着同时打开 12 个网页
        with ThreadPoolExecutor(max_workers=12) as executor:
            # 第一步：并发抓取内容
            future_to_source = {executor.submit(fetch_content, src): src for src in SOURCES}
            
            for future in future_to_source:
                source = future_to_source[future]
                content = future.result()
                
                status_area.write(f"✅ 已抓取: {source['name']} (分析中...)")
                
                # 第二步：AI 分析 (串行或并行均可，这里简单处理)
                # 如果内容中包含任一关键词，则送入 AI 分析，节省 Token
                if any(k.lower() in content.lower() for k in KEYWORDS):
                    analysis = analyze_risk(content, source['name'], api_key)
                    
                    if analysis and analysis.get('relevant'):
                        with results_container:
                            # 渲染卡片 UI
                            st.markdown(f"""
                            <div class="report-card">
                                <h3>{source['name']} <span class="tag">{source['type']}</span></h3>
                                <p><b>风险等级:</b> <span class="risk-{analysis['risk_level']}">{analysis['risk_level']}</span></p>
                                <p><b>AI 摘要:</b> {analysis['summary']}</p>
                                <a href="{source['url']}" target="_blank">🔗 查看原文 Source Link</a>
                            </div>
                            """, unsafe_allow_html=True)
                else:
                    # 关键词初筛未通过
                    pass

        status_area.success("🏁 全球扫描完成。")

    elif run_btn and not api_key:
        st.error("⚠️ 请先在左侧输入 API Key")

if __name__ == "__main__":
    main()
