import streamlit as st
import requests
import time
import re
import random
import warnings
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

# 引入穿墙库
from curl_cffi import requests as c_requests

# SSL设置
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# ================= ⚙️ 核心配置 (已固化) =================

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
    page_title="Global Trade Intelligence",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 自定义 CSS 美化
st.markdown("""
<style>
    .block-container {padding-top: 2rem; padding-bottom: 2rem;}
    h1 {font-family: 'Helvetica', sans-serif; color: #0e1117;}
    .stButton>button {width: 100%; border-radius: 5px; font-weight: bold;}
    .report-card {
        background-color: #f9f9f9;
        padding: 20px;
        border-radius: 10px;
        border-left: 5px solid #4e8cff;
        margin-bottom: 15px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
    }
    .tag {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: bold;
        margin-right: 5px;
    }
    .tag-date {background-color: #e0e0e0; color: #333;}
    .tag-source {background-color: #d1e7dd; color: #0f5132;}
</style>
""", unsafe_allow_html=True)

# ================= 🧠 后端逻辑函数 =================

def generate_date_keywords(selected_date, report_type):
    """
    (Version 2.0 精准版) 
    根据用户选择生成日期关键词，强制包含年份，防止抓到旧新闻。
    """
    dates_to_check = []
    
    if report_type == "日报 (Daily)":
        # 选中日期 + 前一天 (考虑时差)
        dates_to_check = [selected_date, selected_date - timedelta(days=1)]
    elif report_type == "周报 (Weekly)":
        # 过去7天
        dates_to_check = [selected_date - timedelta(days=i) for i in range(8)]
    elif report_type == "月报 (Monthly)":
        # 过去30天
        dates_to_check = [selected_date - timedelta(days=i) for i in range(31)]
    
    keywords = []
    year_str = str(selected_date.year) # 获取选中的年份，如 "2025"
    
    for d in dates_to_check:
        # 强制带年份的格式
        keywords.extend([
            d.strftime("%B %d, %Y"),      # November 21, 2025 (最常见标准格式)
            d.strftime("%b %d, %Y"),      # Nov 21, 2025
            d.strftime("%b. %d, %Y"),     # Nov. 21, 2025
            d.strftime("%Y-%m-%d"),       # 2025-11-21
            d.strftime("%Y/%m/%d"),       # 2025/11/21
            d.strftime("%d/%m/%Y"),       # 21/11/2025
            d.strftime("%d %B %Y"),       # 21 November 2025 (欧盟常用)
            f"{d.strftime('%B %d')}, {year_str}" # 手动补漏
        ])
    
    return list(set(keywords))

def fetch_links_hybrid(site):
    """混合引擎抓取列表"""
    url = site['url']
    try:
        if site['engine'] == "standard":
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
            resp = requests.get(url, headers=headers, timeout=10, verify=False)
            if "mofcom" in url: resp.encoding = "utf-8" if "charset=utf-8" in resp.text.lower() else "gbk"
            if resp.status_code == 200: return parse_links(resp.text, url)
        else:
            # 穿墙模式
            for fp in ["chrome120", "safari15_3", "edge101"]:
                try:
                    resp = c_requests.get(url, impersonate=fp, timeout=15)
                    if resp.status_code == 200: return parse_links(resp.text, url)
                except: continue
    except Exception: pass
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
    """详情页分析"""
    try:
        page_text = ""
        if engine == "standard":
            resp = requests.get(url, timeout=10, verify=False)
            if "mofcom" in url: resp.encoding = "gbk"
            page_text = resp.text
        else:
            resp = c_requests.get(url, impersonate="chrome120", timeout=10)
            page_text = resp.text
            
        # 日期检查
        found_date = None
        for dk in date_keywords:
            if dk in page_text:
                found_date = dk
                break
        if not found_date: return None
        
        # 提取正文
        soup = BeautifulSoup(page_text, 'html.parser')
        content = "\n".join([p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 50])
        if len(content) < 50: return None
        
        # AI 总结
        return call_ai_api(content[:3000], title, site_name, found_date, url)
    except: return None

def call_ai_api(content, title, source, date_str, url):
    prompt = f"""
    你是一名国际贸易合规专家。来源：{source}，标题：{title}。
    任务：翻译并总结为中文简报。
    格式要求（不要Markdown代码块）：
    日期：{date_str}
    标题：(中文标题)
    核心事实：(简练概括)
    合规提示：(对企业的建议)
    
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
    # 初始化 Session State
    if 'results' not in st.session_state:
        st.session_state.results = []
    if 'is_running' not in st.session_state:
        st.session_state.is_running = False

    # --- 侧边栏 ---
    with st.sidebar:
        st.title("🕹️ 控制台")
        st.write("---")
        
        report_type = st.radio("📊 选择报告类型", ["日报 (Daily)", "周报 (Weekly)", "月报 (Monthly)"])
        
        selected_date = st.date_input("📅 选择基准日期", datetime.now())
        
        st.write("---")
        st.write("📡 监控站点")
        selected_sites_names = st.multiselect(
            "选择要扫描的来源:",
            [s['name'] for s in SITES],
            default=[s['name'] for s in SITES]
        )
        
        st.write("---")
        run_btn = st.button("🚀 开始生成情报", type="primary", disabled=st.session_state.is_running)

    # --- 主界面 ---
    st.title("🌍 全球贸易合规情报雷达")
    st.caption(f"基于 DeepSeek-V3 模型 | 当前目标: {report_type} - {selected_date}")
    
    # 运行逻辑
    if run_btn:
        st.session_state.results = [] # 清空旧结果
        st.session_state.is_running = True
        
        # 1. 生成日期关键词
        date_keywords = generate_date_keywords(selected_date, report_type)
        st.info(f"正在检索以下日期范围的内容: {date_keywords[:3]}... 等 ({len(date_keywords)}个格式)")
        
        # 2. 筛选站点
        target_sites = [s for s in SITES if s['name'] in selected_sites_names]
        
        # 3. 进度条容器
        progress_bar = st.progress(0)
        status_text = st.empty()
        result_container = st.container()
        
        total_sites = len(target_sites)
        
        # 4. 开始扫描
        for i, site in enumerate(target_sites):
            status_text.markdown(f"### 📡 正在扫描: **{site['name']}** ...")
            
            # 抓取链接
            links = fetch_links_hybrid(site)
            
            # 关键词过滤
            relevant_links = []
            for title, url in links:
                if any(k.lower() in title.lower() for k in KEYWORDS):
                    relevant_links.append((title, url))
            
            if relevant_links:
                status_text.markdown(f"🔍 {site['name']}: 发现 {len(relevant_links)} 条潜在情报，AI 正在研判...")
                
                # 并发分析
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = {executor.submit(analyze_news_item, url, title, site['name'], site['engine'], date_keywords): url for title, url in relevant_links}
                    
                    for future in as_completed(futures):
                        res = future.result()
                        if res:
                            # 解析结果并存入 State
                            item = {
                                "source": site['name'],
                                "url": futures[future],
                                "content": res
                            }
                            st.session_state.results.append(item)
                            # 实时显示
                            with result_container:
                                with st.expander(f"🚨 {site['name']} | 新情报发现!", expanded=True):
                                    st.markdown(res)
                                    st.markdown(f"[🔗 点击查看原文]({futures[future]})")
            
            # 更新进度
            progress_bar.progress((i + 1) / total_sites)
            
        st.session_state.is_running = False
        status_text.success("✅ 扫描完成！")
        time.sleep(1)
        status_text.empty()

    # --- 结果展示区 (即使刷新页面也能保留) ---
    if st.session_state.results:
        st.divider()
        st.subheader(f"📝 生成结果 ({len(st.session_state.results)} 条)")
        
        # 导出文本按钮
        full_text = ""
        for item in st.session_state.results:
            full_text += f"\n{'='*40}\n来源：{item['source']}\n{item['content']}\n链接：{item['url']}\n"
        
        st.download_button("📥 下载简报 (TXT)", full_text, file_name=f"Trade_Report_{selected_date}.txt")

        # 卡片式展示
        for item in st.session_state.results:
            st.markdown(f"""
            <div class="report-card">
                <span class="tag tag-source">{item['source']}</span>
                <div style="margin-top: 10px; white-space: pre-wrap;">{item['content']}</div>
                <div style="margin-top: 10px;">
                    <a href="{item['url']}" target="_blank" style="text-decoration: none; color: #4e8cff; font-weight: bold;">🔗 阅读全文 &rarr;</a>
                </div>
            </div>
            """, unsafe_allow_html=True)
    
    elif not st.session_state.is_running and run_btn:
        st.warning("本次扫描未发现符合条件的制裁信息。建议尝试更改日期或报告类型。")

if __name__ == "__main__":
    main()