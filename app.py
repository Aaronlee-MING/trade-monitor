import streamlit as st
import requests
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

# 引入穿墙库
try:
    from curl_cffi import requests as c_requests
except ImportError:
    import requests as c_requests

# SSL设置
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# ================= ⚙️ 核心配置 =================

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

# ================= 🎨 专业律所 UI 设计 (强制亮色) =================

st.set_page_config(
    page_title="全球贸易合规情报系统",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 强制 CSS 注入：覆盖所有暗黑模式，强制使用白底黑字
st.markdown("""
<style>
    /* 1. 强制全局背景为白色 */
    .stApp {
        background-color: #ffffff;
        color: #333333;
    }
    
    /* 2. 侧边栏背景设为浅灰，体现层次感 */
    [data-testid="stSidebar"] {
        background-color: #f8f9fa;
        border-right: 1px solid #e9ecef;
    }
    
    /* 3. 字体优化：使用衬线体或更加正式的字体 */
    h1, h2, h3 {
        color: #0f172a; /* 深蓝黑色 */
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }
    
    /* 4. 修复卡片文字看不见的问题：强制指定内部文字颜色 */
    .report-card {
        background-color: #ffffff;
        border: 1px solid #dee2e6;
        border-left: 4px solid #2c3e50; /* 沉稳的藏青色 */
        padding: 20px;
        margin-bottom: 15px;
        border-radius: 4px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        color: #333333 !important; /* 强制黑色文字 */
    }
    
    /* 5. 标签样式优化 */
    .tag {
        background-color: #e9ecef;
        color: #495057;
        padding: 4px 8px;
        border-radius: 2px;
        font-size: 0.85em;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    
    /* 6. 链接样式 */
    a {
        color: #0056b3;
        text-decoration: none;
    }
    a:hover {
        text-decoration: underline;
    }

    /* 7. 调整按钮样式 */
    .stButton>button {
        background-color: #2c3e50;
        color: white;
        border: none;
        border-radius: 4px;
        height: 45px;
        font-weight: 500;
    }
    .stButton>button:hover {
        background-color: #1a252f;
        color: white;
    }
</style>
""", unsafe_allow_html=True)

# ================= 🧠 后端逻辑函数 =================

def generate_strict_date_keywords(selected_date, report_type, include_timezone):
    dates_to_check = []
    if report_type == "日报 (Daily)":
        dates_to_check = [selected_date]
        if include_timezone:
            dates_to_check.append(selected_date - timedelta(days=1))
    elif report_type == "周报 (Weekly)":
        dates_to_check = [selected_date - timedelta(days=i) for i in range(8)]
    elif report_type == "月报 (Monthly)":
        dates_to_check = [selected_date - timedelta(days=i) for i in range(31)]
    
    keywords = []
    for d in dates_to_check:
        year = str(d.year)
        day_no_pad = str(d.day)
        day_pad = d.strftime("%d")
        month_full = d.strftime("%B")
        month_abbr = d.strftime("%b")
        
        formats = [
            d.strftime("%B %d, %Y"), d.strftime("%b %d, %Y"), d.strftime("%b. %d, %Y"),
            d.strftime("%Y-%m-%d"), d.strftime("%d %B %Y"),
            f"{month_full} {day_no_pad}, {year}", f"{month_abbr} {day_no_pad}, {year}",
            d.strftime("%m/%d/%Y"),
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
    try:
        page_text = ""
        if engine == "standard":
            resp = requests.get(url, timeout=10, verify=False)
            if "mofcom" in url: resp.encoding = "gbk"
            page_text = resp.text
        else:
            resp = c_requests.get(url, impersonate="chrome120", timeout=10)
            page_text = resp.text
            
        found_date = None
        for dk in date_keywords:
            if dk in page_text:
                found_date = dk
                break
        if not found_date: return None
        
        soup = BeautifulSoup(page_text, 'html.parser')
        for script in soup(["script", "style", "nav", "footer"]): script.extract()
        content = "\n".join([p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 50])
        if len(content) < 50: return None
        
        return call_ai_api(content[:3000], title, site_name, found_date, url)
    except: return None

def call_ai_api(content, title, source, date_str, url):
    prompt = f"""
    你是一名国际贸易合规专家。来源：{source}。
    任务：生成一份专业的合规简报。
    格式要求（纯文本）：
    
    【日期】{date_str}
    【标题】(中文翻译)
    【核心事实】(客观陈述，3点以内)
    【合规提示】(针对企业的法律风险提示)
    
    原文标题：{title}
    原文正文：{content}
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
        st.header("⚖️ 检索控制台")
        st.info("全球贸易合规情报系统 v16.0")
        
        report_type = st.radio("报告周期", ["日报 (Daily)", "周报 (Weekly)", "月报 (Monthly)"])
        selected_date = st.date_input("基准日期", datetime.now())
        include_timezone = st.checkbox("包含美国时差 (建议开启)", value=True)
        
        st.markdown("---")
        st.caption("数据源选择")
        selected_sites_names = st.multiselect(
            "监控站点", 
            [s['name'] for s in SITES], 
            default=[s['name'] for s in SITES],
            label_visibility="collapsed"
        )
        
        st.markdown("<br>", unsafe_allow_html=True)
        run_btn = st.button("开始检索", type="primary")

    # 主标题
    st.markdown("## 🌍 Global Trade Compliance Intelligence")
    st.markdown("##### 全球贸易合规情报雷达 | 律师专业版")
    st.markdown("---")
    
    if run_btn:
        st.session_state.results = []
        date_keywords = generate_strict_date_keywords(selected_date, report_type, include_timezone)
        
        status_container = st.container()
        with status_container:
            st.caption(f"📅 正在检索日期锚点: {date_keywords[0]} 等 {len(date_keywords)} 个格式")
            progress_bar = st.progress(0)
        
        target_sites = [s for s in SITES if s['name'] in selected_sites_names]
        result_container = st.container()
        
        for i, site in enumerate(target_sites):
            links = fetch_links_hybrid(site)
            relevant_links = []
            for title, url in links:
                if any(k.lower() in title.lower() for k in KEYWORDS):
                    relevant_links.append((title, url))
            
            if relevant_links:
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = {executor.submit(analyze_news_item, url, title, site['name'], site['engine'], date_keywords): url for title, url in relevant_links}
                    
                    for future in as_completed(futures):
                        res = future.result()
                        if res:
                            item = {"source": site['name'], "url": futures[future], "content": res}
                            st.session_state.results.append(item)
                            
                            # 实时渲染卡片
                            with result_container:
                                st.markdown(f"""
                                <div class="report-card">
                                    <div style="margin-bottom: 10px;">
                                        <span class="tag">{item['source']}</span>
                                    </div>
                                    <div style="white-space: pre-wrap; line-height: 1.6; font-size: 15px;">{item['content']}</div>
                                    <div style="margin-top: 15px; padding-top: 10px; border-top: 1px solid #eee;">
                                        <a href="{item['url']}" target="_blank">🔗 查看法律原文</a>
                                    </div>
                                </div>
                                """, unsafe_allow_html=True)
            
            progress_bar.progress((i + 1) / len(target_sites))
        
        progress_bar.empty()
        if not st.session_state.results:
            st.warning(f"在 {selected_date} 未发现符合条件的合规情报。")
        else:
            st.success(f"检索完成，共生成 {len(st.session_state.results)} 条合规简报。")

    # 如果有历史结果，即使没点击按钮也显示（防止刷新丢失）
    elif st.session_state.results:
        for item in st.session_state.results:
            st.markdown(f"""
            <div class="report-card">
                <div style="margin-bottom: 10px;">
                    <span class="tag">{item['source']}</span>
                </div>
                <div style="white-space: pre-wrap; line-height: 1.6; font-size: 15px;">{item['content']}</div>
                <div style="margin-top: 15px; padding-top: 10px; border-top: 1px solid #eee;">
                    <a href="{item['url']}" target="_blank">🔗 查看法律原文</a>
                </div>
            </div>
            """, unsafe_allow_html=True)
        
        # 生成纯文本简报用于复制
        full_text = f"全球贸易合规情报日报 ({selected_date})\n" + "="*40 + "\n"
        for item in st.session_state.results:
            full_text += f"\n来源：{item['source']}\n{item['content']}\n原文：{item['url']}\n{'-'*40}\n"
        
        st.download_button("📥 导出为法律备忘录 (TXT)", full_text, file_name=f"Legal_Brief_{selected_date}.txt")

if __name__ == "__main__":
    main()
