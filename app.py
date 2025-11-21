import streamlit as st
import requests
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

try:
    from curl_cffi import requests as c_requests
except ImportError:
    import requests as c_requests

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# ================= ⚙️ 配置区域 =================

SILICON_KEY = "sk-lvnzrlhumujjhpzjkslhhuqjdukioscebcoeuawumtyqoqiz" 
API_URL = "https://api.siliconflow.cn/v1/chat/completions"
MODEL_NAME = "deepseek-ai/DeepSeek-V3"

KEYWORDS = [
    "Sanction", "Trade", "Export", "Import", "Tariff", "Entity List", 
    "China", "Russia", "Control", "Violation", "Security", "Semiconductor",
    "Chip", "UFLPA", "Investment", "Laundering", "Blacklist", "Ban",
    "制裁", "贸易", "出口", "进口", "关税", "实体清单", "半导体", "芯片", "管制"
]

SITES = [
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "engine": "curl"},
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "engine": "curl"},
    {"name": "🇺🇸 Commerce Press", "url": "https://www.commerce.gov/news/press-releases", "engine": "curl"},
    {"name": "🇺🇸 DOJ Press", "url": "https://www.justice.gov/news/press-releases", "engine": "curl"},
    {"name": "🇬🇧 Reuters (Defense)", "url": "https://www.reuters.com/business/aerospace-defense/", "engine": "curl"},
    {"name": "🇪🇺 EU Council", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "engine": "curl"},
    {"name": "🇨🇳 MOFCOM (管制局)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "engine": "standard"},
    {"name": "🇨🇳 外交部发言人", "url": "https://www.fmprc.gov.cn/fyrbt_673021/", "engine": "standard"},
    {"name": "🏛️ CSIS Analysis", "url": "https://www.csis.org/analysis", "engine": "curl"},
    {"name": "🇺🇸 US Congress", "url": "https://www.congress.gov/search?q=%7B%22source%22%3A%22legislation%22%2C%22congress%22%3A%22118%22%7D", "engine": "curl"},
    {"name": "🇭🇰 SCMP", "url": "https://www.scmp.com/news/china/diplomacy", "engine": "curl"},
    {"name": "📰 Foreign Policy", "url": "https://foreignpolicy.com/latest/", "engine": "curl"}
]

# ================= 🎨 界面设计 (保证清晰度) =================

st.set_page_config(
    page_title="Trade Compliance Monitor",
    page_icon="⚖️",
    layout="wide"
)

# 这里是关键：我们定义一个“绝对清晰”的卡片样式
# background-color: #F7F9FB (极淡的蓝灰色，像高级信纸)
# color: #1A202C (深黑色，对比度极高)
st.markdown("""
<style>
    /* 主标题样式 */
    .main-header {
        font-family: "Source Serif Pro", serif;
        color: #0F294D;
        font-size: 2.5rem;
        font-weight: 700;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-family: sans-serif;
        color: #5F6B7C;
        font-size: 1rem;
        margin-bottom: 2rem;
    }

    /* 结果卡片样式 - 强制颜色，防止被主题覆盖 */
    .result-card {
        background-color: #F7F9FB !important; 
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }
    
    /* 来源标签 */
    .source-tag {
        background-color: #E2E8F0;
        color: #2D3748 !important;
        padding: 4px 10px;
        border-radius: 4px;
        font-size: 0.8rem;
        font-weight: 600;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        display: inline-block;
        margin-bottom: 12px;
    }

    /* 正文内容 */
    .content-text {
        color: #1A202C !important; /* 强制深色 */
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 16px;
        line-height: 1.6;
        white-space: pre-wrap;
    }

    /* 链接按钮 */
    .link-btn {
        display: inline-block;
        margin-top: 16px;
        color: #3182ce !important;
        font-weight: 600;
        text-decoration: none;
        font-size: 0.9rem;
    }
    .link-btn:hover {
        text-decoration: underline;
    }
</style>
""", unsafe_allow_html=True)

# ================= 🧠 逻辑层 =================

def generate_strict_date_keywords(selected_date, report_type, include_timezone):
    dates = []
    if report_type == "日报":
        dates = [selected_date]
        if include_timezone: dates.append(selected_date - timedelta(days=1))
    elif report_type == "周报":
        dates = [selected_date - timedelta(days=i) for i in range(8)]
    elif report_type == "月报":
        dates = [selected_date - timedelta(days=i) for i in range(31)]
    
    keywords = []
    for d in dates:
        y, m, day = str(d.year), d.strftime("%B"), str(d.day)
        keywords.extend([
            d.strftime("%B %d, %Y"), d.strftime("%b %d, %Y"), d.strftime("%Y-%m-%d"),
            f"{m} {day}, {y}", d.strftime("%d %B %Y"), d.strftime("%m/%d/%Y")
        ])
    return list(set(keywords))

def fetch_and_analyze(site, date_keywords):
    links = []
    try:
        # Fetch
        if site['engine'] == "standard":
            r = requests.get(site['url'], headers={"User-Agent": "Chrome/120.0"}, timeout=10, verify=False)
            if "mofcom" in site['url']: r.encoding = "utf-8" if "utf-8" in r.text.lower() else "gbk"
            html = r.text
        else:
            r = c_requests.get(site['url'], impersonate="chrome120", timeout=15)
            html = r.text
            
        # Parse
        soup = BeautifulSoup(html, 'html.parser')
        for a in soup.find_all('a'):
            t, h = a.get_text(strip=True), a.get('href')
            if t and len(t)>10 and h and "javascript" not in h:
                links.append((t, urljoin(site['url'], h)))
    except: return []

    # Filter Keywords
    relevant = [item for item in links if any(k.lower() in item[0].lower() for k in KEYWORDS)]
    
    # Analyze Details
    results = []
    for title, url in relevant:
        try:
            if site['engine'] == "standard":
                r = requests.get(url, timeout=10, verify=False)
                if "mofcom" in url: r.encoding = "gbk"
                txt = r.text
            else:
                r = c_requests.get(url, impersonate="chrome120", timeout=10)
                txt = r.text
            
            # Check Date
            found_date = next((dk for dk in date_keywords if dk in txt), None)
            if not found_date: continue

            # Extract Content
            soup = BeautifulSoup(txt, 'html.parser')
            for s in soup(["script", "style"]): s.extract()
            content = "\n".join([p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True))>50])
            if len(content)<50: continue

            # Call AI
            prompt = f"""
            来源：{site['name']} | 日期：{found_date}
            标题：{title}
            
            任务：生成简报。
            格式：
            **标题**：(中文)
            **事实**：(核心内容)
            **建议**：(合规提示)
            
            正文：{content[:3000]}
            """
            res = requests.post(
                API_URL, 
                json={"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3},
                headers={"Authorization": f"Bearer {SILICON_KEY}"}, timeout=20
            )
            if res.status_code == 200:
                results.append({"source": site['name'], "url": url, "content": res.json()['choices'][0]['message']['content']})
        except: continue
    return results

# ================= 🖥️ 主程序 =================

def main():
    if 'data' not in st.session_state: st.session_state.data = []

    # --- 侧边栏 ---
    with st.sidebar:
        st.header("📅 检索选项")
        report_type = st.selectbox("报告周期", ["日报", "周报", "月报"])
        selected_date = st.date_input("选择日期", datetime.now())
        include_tz = st.checkbox("包含美国时差", value=True)
        st.divider()
        sites = st.multiselect("数据源", [s['name'] for s in SITES], default=[s['name'] for s in SITES])
        if st.button("开始检索", type="primary", use_container_width=True):
            st.session_state.data = []
            dates = generate_strict_date_keywords(selected_date, report_type, include_tz)
            
            status = st.status("正在全球扫描中...", expanded=True)
            target_sites = [s for s in SITES if s['name'] in sites]
            
            with ThreadPoolExecutor(max_workers=5) as exe:
                futures = {exe.submit(fetch_and_analyze, s, dates): s['name'] for s in target_sites}
                for f in as_completed(futures):
                    name = futures[f]
                    res = f.result()
                    if res:
                        st.session_state.data.extend(res)
                        status.write(f"✅ {name}: 发现 {len(res)} 条情报")
                    else:
                        status.write(f"⚪ {name}: 无更新")
            status.update(label="扫描完成", state="complete", expanded=False)

    # --- 主界面 ---
    st.markdown('<div class="main-header">Trade Compliance Monitor</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">全球贸易制裁与合规情报系统 | 律师专业版</div>', unsafe_allow_html=True)
    
    if st.session_state.data:
        st.divider()
        for item in st.session_state.data:
            # 使用我们自定义的 HTML 卡片，强制颜色
            st.markdown(f"""
            <div class="result-card">
                <div class="source-tag">{item['source']}</div>
                <div class="content-text">{item['content']}</div>
                <a href="{item['url']}" target="_blank" class="link-btn">🔗 阅读原文 (Read Source) &rarr;</a>
            </div>
            """, unsafe_allow_html=True)
            
        # 导出文本
        raw_text = "\n".join([f"【{d['source']}】\n{d['content']}\n链接：{d['url']}\n" for d in st.session_state.data])
        st.download_button("📥 导出简报文件", raw_text, "report.txt")
        
    elif not st.session_state.data:
        st.info("👋 请在左侧选择日期并点击“开始检索”。(暂无数据)")

if __name__ == "__main__":
    main()
