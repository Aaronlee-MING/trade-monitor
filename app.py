import streamlit as st
import requests
import time
import re
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

# 引入穿墙库
try:
    from curl_cffi import requests as c_requests
except ImportError:
    # 兼容本地未安装的情况
    import requests as c_requests

# SSL设置
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# ================= ⚙️ 核心配置 =================

SILICON_KEY = "sk-lvnzrlhumujjhpzjkslhhuqjdukioscebcoeuawumtyqoqiz" 
API_URL = "https://api.siliconflow.cn/v1/chat/completions"
MODEL_NAME = "deepseek-ai/DeepSeek-V3"

# 关键词过滤器
KEYWORDS = [
    "Sanction", "Trade", "Export", "Import", "Tariff", "Entity List", 
    "China", "Russia", "Control", "Violation", "Security", "Semiconductor",
    "Chip", "UFLPA", "Investment", "Laundering", "Blacklist", "Ban",
    "制裁", "贸易", "出口", "进口", "关税", "实体清单", "半导体", "芯片", "管制"
]

# 站点清单
SITES = [
    {"name": "🇺🇸 BIS News (美商务部)", "url": "https://www.bis.gov/news-updates", "engine": "curl"},
    {"name": "🇺🇸 OFAC Actions (美财政部)", "url": "https://ofac.treasury.gov/recent-actions", "engine": "curl"},
    {"name": "🇺🇸 Commerce Press (美商务部新闻)", "url": "https://www.commerce.gov/news/press-releases", "engine": "curl"},
    {"name": "🇺🇸 DOJ Press (美司法部)", "url": "https://www.justice.gov/news/press-releases", "engine": "curl"},
    {"name": "🇬🇧 Reuters (路透社-国防)", "url": "https://www.reuters.com/business/aerospace-defense/", "engine": "curl"},
    {"name": "🇪🇺 EU Council (欧盟理事会)", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "engine": "curl"},
    {"name": "🇨🇳 MOFCOM (中国商务部)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "engine": "standard"},
    {"name": "🇨🇳 外交部发言人", "url": "https://www.fmprc.gov.cn/fyrbt_673021/", "engine": "standard"},
    {"name": "🏛️ CSIS (智库)", "url": "https://www.csis.org/analysis", "engine": "curl"},
    {"name": "🇺🇸 Congress (美国国会)", "url": "https://www.congress.gov/search?q=%7B%22source%22%3A%22legislation%22%2C%22congress%22%3A%22118%22%7D", "engine": "curl"},
    {"name": "🇭🇰 SCMP (南华早报)", "url": "https://www.scmp.com/news/china/diplomacy", "engine": "curl"},
    {"name": "📰 Foreign Policy", "url": "https://foreignpolicy.com/latest/", "engine": "curl"}
]

# ================= 🎨 页面 UI 设置 =================

st.set_page_config(
    page_title="全球贸易合规情报雷达",
    page_icon="🌍",
    layout="wide"
)

st.markdown("""
<style>
    .report-card {
        background-color: #ffffff;
        padding: 20px;
        border-radius: 8px;
        border: 1px solid #e0e0e0;
        border-left: 5px solid #FF4B4B;
        margin-bottom: 15px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
    .tag {
        background-color: #f0f2f6;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 12px;
        color: #31333F;
        font-weight: 600;
        margin-right: 10px;
    }
</style>
""", unsafe_allow_html=True)

# ================= 🧠 后端逻辑函数 =================

def generate_strict_date_keywords(selected_date, report_type, include_timezone):
    """
    (核心修复) 生成严格带年份的日期格式
    """
    dates_to_check = []
    
    # 1. 确定日期范围
    if report_type == "日报 (Daily)":
        dates_to_check = [selected_date]
        if include_timezone:
            # 如果勾选时差，多查一天（比如查21号，同时也查美国的20号）
            dates_to_check.append(selected_date - timedelta(days=1))
            
    elif report_type == "周报 (Weekly)":
        dates_to_check = [selected_date - timedelta(days=i) for i in range(8)]
    elif report_type == "月报 (Monthly)":
        dates_to_check = [selected_date - timedelta(days=i) for i in range(31)]
    
    # 2. 生成严格格式 (必须包含年份)
    keywords = []
    for d in dates_to_check:
        year = str(d.year)
        day_no_pad = str(d.day) # 不带0的日期 (5号)
        day_pad = d.strftime("%d") # 带0的日期 (05号)
        
        month_full = d.strftime("%B") # November
        month_abbr = d.strftime("%b") # Nov
        
        # 组合各种官方写法
        formats = [
            d.strftime("%B %d, %Y"),       # November 21, 2025 (BIS/OFAC 标准)
            d.strftime("%b %d, %Y"),       # Nov 21, 2025
            d.strftime("%b. %d, %Y"),      # Nov. 21, 2025
            d.strftime("%Y-%m-%d"),        # 2025-11-21 (MOFCOM/ISO)
            d.strftime("%d %B %Y"),        # 21 November 2025 (EU/Reuters)
            f"{month_full} {day_no_pad}, {year}", # November 5, 2025 (去零)
            f"{month_abbr} {day_no_pad}, {year}", # Nov 5, 2025 (去零)
            d.strftime("%m/%d/%Y"),        # 11/21/2025 (US Short)
        ]
        keywords.extend(formats)
    
    return list(set(keywords))

def fetch_links_hybrid(site):
    url = site['url']
    try:
        if site['engine'] == "standard":
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
            resp = requests.get(url, headers=headers, timeout=10, verify=False)
            if "mofcom" in url: resp.encoding = "utf-8" if "charset=utf-8" in resp.text.lower() else "gbk"
            if resp.status_code == 200: return parse_links(resp.text, url)
        else:
            for fp in ["chrome120", "safari15_3", "edge101"]:
                try:
                    resp = c_requests.get(url, impersonate=fp, timeout=15)
                    if resp.status_code == 200: return parse_links(resp.text, url)
                except: continue
    except: pass
    return []

def parse_links(html, base_url):
    soup = BeautifulSoup(html, 'html.parser')
    data = []
    seen = set()
    for a in soup.find_all('a'):
        title = a.get_text(strip=True)
        href = a.get('href')
        if title and len(title) > 10 and href and "javascript" not in href:
            full = urljoin(base_url, href)
            if full not in seen:
                seen.add(full)
                data.append((title, full))
    return data

def analyze_news_item(url, title, site_name, engine, date_keywords):
    """
    详情页分析：只有找到严格匹配的日期字符串才返回
    """
    try:
        page_text = ""
        if engine == "standard":
            resp = requests.get(url, timeout=10, verify=False)
            if "mofcom" in url: resp.encoding = "gbk"
            page_text = resp.text
        else:
            resp = c_requests.get(url, impersonate="chrome120", timeout=10)
            page_text = resp.text
            
        # === 严格日期匹配 ===
        found_date = None
        for dk in date_keywords:
            # 检查页面中是否存在这个精确的日期字符串
            if dk in page_text:
                found_date = dk
                break
        
        if not found_date: 
            return None
        
        # 提取正文
        soup = BeautifulSoup(page_text, 'html.parser')
        # 移除脚本和样式
        for script in soup(["script", "style", "nav", "footer"]):
            script.extract()
            
        content = "\n".join([p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 50])
        if len(content) < 50: return None
        
        return call_ai_api(content[:3000], title, site_name, found_date, url)
    except: return None

def call_ai_api(content, title, source, date_str, url):
    prompt = f"""
    你是一名国际贸易合规专家。
    来源：{source}
    原文标题：{title}
    发布日期：{date_str}
    
    任务：翻译并总结为中文简报。
    格式要求（纯文本，不要Markdown代码块）：
    日期：{date_str}
    标题：(中文标题)
    核心事实：(3句话概括，包含主体、行为、后果)
    合规提示：(对中国企业的具体建议)
    
    正文：{content}
    """
    try:
        resp = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {SILICON_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3},
            timeout=20
        )
        if resp.status_code == 200:
            return resp.json()['choices'][0]['message']['content']
    except: pass
    return None

# ================= 🖥️ 前端交互逻辑 =================

def main():
    if 'results' not in st.session_state:
        st.session_state.results = []

    with st.sidebar:
        st.title("🔍 检索配置")
        report_type = st.radio("报告类型", ["日报 (Daily)", "周报 (Weekly)", "月报 (Monthly)"])
        selected_date = st.date_input("选择日期", datetime.now())
        
        # 新增：时差开关
        include_timezone = st.checkbox("包含美国时差 (自动查前一天)", value=True, help="因为美国比亚洲晚半天，查今天的新闻通常需要包含美国的'昨天'。")
        
        st.divider()
        selected_sites_names = st.multiselect("扫描范围", [s['name'] for s in SITES], default=[s['name'] for s in SITES])
        
        run_btn = st.button("🚀 开始精确检索", type="primary")

    st.title("🌍 全球贸易合规情报雷达")
    
    if run_btn:
        st.session_state.results = []
        
        # 1. 生成精准日期关键词
        date_keywords = generate_strict_date_keywords(selected_date, report_type, include_timezone)
        
        st.success(f"📅 已锁定 {len(date_keywords)} 个精准日期格式 (例如: {date_keywords[0]})")
        if include_timezone and report_type == "日报 (Daily)":
            st.caption(f"正在同时扫描: {selected_date} 和 {selected_date - timedelta(days=1)}")
        
        target_sites = [s for s in SITES if s['name'] in selected_sites_names]
        progress_bar = st.progress(0)
        status_text = st.empty()
        result_container = st.container()
        
        for i, site in enumerate(target_sites):
            status_text.markdown(f"### 📡 正在扫描: **{site['name']}** ...")
            links = fetch_links_hybrid(site)
            
            relevant_links = []
            for title, url in links:
                if any(k.lower() in title.lower() for k in KEYWORDS):
                    relevant_links.append((title, url))
            
            if relevant_links:
                status_text.markdown(f"🔍 {site['name']}: 标题命中 {len(relevant_links)} 条，正在核对日期...")
                
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = {executor.submit(analyze_news_item, url, title, site['name'], site['engine'], date_keywords): url for title, url in relevant_links}
                    
                    for future in as_completed(futures):
                        res = future.result()
                        if res:
                            item = {"source": site['name'], "url": futures[future], "content": res}
                            st.session_state.results.append(item)
                            with result_container:
                                with st.expander(f"🚨 {site['name']} | {item['url']}", expanded=True):
                                    st.markdown(res)
            
            progress_bar.progress((i + 1) / len(target_sites))
            
        status_text.success(f"✅ 扫描完成！共发现 {len(st.session_state.results)} 条精准情报。")

    # 结果展示
    if st.session_state.results:
        st.divider()
        full_text = ""
        for item in st.session_state.results:
            full_text += f"\n{'='*40}\n{item['content']}\n链接：{item['url']}\n"
            
            st.markdown(f"""
            <div class="report-card">
                <span class="tag">{item['source']}</span>
                <div style="margin-top: 10px; white-space: pre-wrap; font-family: sans-serif;">{item['content']}</div>
                <div style="margin-top: 10px;">
                    <a href="{item['url']}" target="_blank" style="text-decoration: none; color: #FF4B4B; font-weight: bold;">🔗 查看原文</a>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
        st.download_button("📥 导出简报 (TXT)", full_text, file_name=f"Report_{selected_date}.txt")
    elif run_btn:
        st.warning(f"在 {selected_date} 未发现符合条件的精准制裁信息。")

if __name__ == "__main__":
    main()
