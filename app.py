import streamlit as st
import requests
import warnings
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

# 尝试引入穿墙库
try:
    from curl_cffi import requests as c_requests
except ImportError:
    import requests as c_requests

# 屏蔽 SSL 警告
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# ================= ⚙️ 核心配置 =================

SILICON_KEY = "sk-lvnzrlhumujjhpzjkslhhuqjdukioscebcoeuawumtyqoqiz" 
API_URL = "https://api.siliconflow.cn/v1/chat/completions"
MODEL_NAME = "deepseek-ai/DeepSeek-V3"

# 关键词
KEYWORDS = [
    "Sanction", "Trade", "Export", "Import", "Tariff", "Entity List", 
    "China", "Russia", "Control", "Violation", "Security", "Semiconductor",
    "Chip", "UFLPA", "Investment", "Laundering", "Blacklist", "Ban",
    "制裁", "贸易", "出口", "进口", "关税", "实体清单", "半导体", "芯片", "管制"
]

# 站点配置
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

# ================= 🎨 UI 样式 (强制高对比度) =================

st.set_page_config(page_title="Trade Compliance Monitor", page_icon="⚖️", layout="wide")

# 这里的 CSS 只负责全局背景和标题，卡片样式全部在 HTML 里写死
st.markdown("""
<style>
    /* 强制全局亮色背景 */
    .stApp { background-color: #F0F2F6; }
    
    /* 标题样式 */
    .main-title {
        font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
        color: #1F2937;
        font-weight: 800;
        font-size: 2rem;
        margin-bottom: 0.5rem;
    }
    
    /* 侧边栏 */
    [data-testid="stSidebar"] {
        background-color: #FFFFFF;
        border-right: 1px solid #E5E7EB;
    }
</style>
""", unsafe_allow_html=True)

# ================= 🧠 核心逻辑 (并发+严谨清洗) =================

def generate_dates(selected_date, report_type, include_tz):
    dates = []
    if report_type == "日报":
        dates = [selected_date]
        if include_tz: dates.append(selected_date - timedelta(days=1))
    elif report_type == "周报":
        dates = [selected_date - timedelta(days=i) for i in range(8)]
    elif report_type == "月报":
        dates = [selected_date - timedelta(days=i) for i in range(31)]
    
    keywords = []
    for d in dates:
        y, m_full, m_abbr, day, day0 = str(d.year), d.strftime("%B"), d.strftime("%b"), str(d.day), d.strftime("%d")
        keywords.extend([
            f"{m_full} {day}, {y}", f"{m_abbr} {day}, {y}", f"{m_abbr}. {day}, {y}", 
            f"{y}-{m_abbr}-{day0}", f"{y}/{m_abbr}/{day0}", 
            f"{day} {m_full} {y}", d.strftime("%m/%d/%Y"), f"{y}-{month_num(m_full)}-{day0}"
        ])
    return list(set(keywords))

def month_num(m_full):
    try: return datetime.strptime(m_full, "%B").strftime("%m")
    except: return "00"

def fetch_links_step(site):
    """步骤1: 快速抓取链接"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
        if site['engine'] == "standard":
            resp = requests.get(site['url'], headers=headers, timeout=10, verify=False)
            if "mofcom" in site['url']: resp.encoding = "gbk" if "gbk" in resp.text.lower() else "utf-8"
            html = resp.text
        else:
            for fp in ["chrome120", "safari15_3"]:
                try:
                    resp = c_requests.get(site['url'], impersonate=fp, timeout=15)
                    html = resp.text
                    break
                except: continue
            else: return []
            
        soup = BeautifulSoup(html, 'html.parser')
        links = []
        seen = set()
        for a in soup.find_all('a'):
            t, h = a.get_text(strip=True), a.get('href')
            if t and len(t)>10 and h and "javascript" not in h:
                full = urljoin(site['url'], h)
                if full not in seen:
                    seen.add(full)
                    links.append((site['name'], t, full, site['engine']))
        return links
    except: return []

def analyze_article_step(item, date_keywords):
    """步骤2: 深入分析 (并发执行)"""
    site_name, title, url, engine = item
    try:
        if engine == "standard":
            r = requests.get(url, headers={"User-Agent": "Chrome/120.0"}, timeout=10, verify=False)
            if "mofcom" in url: r.encoding = "gbk"
            txt = r.text
        else:
            r = c_requests.get(url, impersonate="chrome120", timeout=10)
            txt = r.text
        
        found_date = None
        for dk in date_keywords:
            if dk in txt:
                found_date = dk
                break
        if not found_date: return None

        soup = BeautifulSoup(txt, 'html.parser')
        for s in soup(["script", "style", "nav", "footer"]): s.extract()
        content = "\n".join([p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 50])
        if len(content) < 50: return None

        prompt = f"""
        来源：{site_name} | 时间：{found_date}
        标题：{title}
        
        请生成一份律师专业级简报：
        1. **【标题】**：翻译为中文。
        2. **【核心事实】**：简明扼要概括主体、事件及法律依据。
        3. **【合规提示】**：对企业风控的实质性建议。
        
        正文：{content[:2500]}
        """
        res = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {SILICON_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2},
            timeout=25
        )
        if res.status_code == 200:
            ai_content = res.json()['choices'][0]['message']['content']
            return {"source": site_name, "url": url, "content": ai_content}
    except: pass
    return None

# ================= 🖥️ 主界面 =================

def main():
    if 'results' not in st.session_state: st.session_state.results = []

    with st.sidebar:
        st.header("⚖️ 控制面板")
        report_type = st.selectbox("报告类型", ["日报", "周报", "月报"])
        selected_date = st.date_input("基准日期", datetime.now())
        include_tz = st.checkbox("包含时差 (推荐)", value=True)
        st.divider()
        sites_selected = st.multiselect("数据源", [s['name'] for s in SITES], default=[s['name'] for s in SITES])
        run = st.button("开始检索", type="primary", use_container_width=True)

    st.markdown('<div class="main-title">全球贸易合规情报雷达</div>', unsafe_allow_html=True)
    st.markdown('**专业律师版 v19.0** | 状态：Ready')
    st.divider()

    if run:
        st.session_state.results = []
        date_keys = generate_dates(selected_date, report_type, include_tz)
        
        status = st.status("🚀 系统运行中...", expanded=True)
        status.write("📡 第一阶段：全网链接扫描...")
        
        all_candidates = []
        target_sites = [s for s in SITES if s['name'] in sites_selected]
        
        # 1. 并发扫描列表
        with ThreadPoolExecutor(max_workers=10) as exe:
            futures = {exe.submit(fetch_links_step, s): s['name'] for s in target_sites}
            for f in as_completed(futures):
                links = f.result()
                relevant = [l for l in links if any(k.lower() in l[1].lower() for k in KEYWORDS)]
                if relevant:
                    all_candidates.extend(relevant)
                    status.write(f"✅ {futures[f]}: 发现 {len(relevant)} 条潜在情报")
        
        if not all_candidates:
            status.update(label="扫描结束：无相关内容", state="error")
            st.warning("未发现包含关键词的标题。")
        else:
            status.write(f"🧠 第二阶段：AI 深度研判 ({len(all_candidates)} 条任务)...")
            
            # 2. 并发分析详情
            with ThreadPoolExecutor(max_workers=10) as exe:
                futures = {exe.submit(analyze_article_step, item, date_keys): item for item in all_candidates}
                result_container = st.container()
                
                for f in as_completed(futures):
                    res = f.result()
                    if res:
                        st.session_state.results.append(res)
                        # 渲染卡片：这里使用了内联样式(inline style)，这是字体绝对清晰的关键！
                        with result_container:
                            st.markdown(f"""
                            <div style="
                                background-color: #FFFFFF; 
                                border: 1px solid #E5E7EB; 
                                border-left: 5px solid #0F172A; 
                                border-radius: 8px; 
                                padding: 20px; 
                                margin-bottom: 15px; 
                                box-shadow: 0 2px 5px rgba(0,0,0,0.05);">
                                <div style="
                                    background-color: #F1F5F9; 
                                    color: #334155; 
                                    padding: 4px 10px; 
                                    border-radius: 4px; 
                                    font-size: 12px; 
                                    font-weight: bold; 
                                    display: inline-block; 
                                    margin-bottom: 10px;">
                                    {res['source']}
                                </div>
                                <div style="
                                    color: #111827 !important; 
                                    font-family: sans-serif; 
                                    font-size: 15px; 
                                    line-height: 1.6; 
                                    white-space: pre-wrap;">
                                    {res['content']}
                                </div>
                                <div style="margin-top: 15px; border-top: 1px solid #F3F4F6; padding-top: 10px;">
                                    <a href="{res['url']}" target="_blank" style="
                                        color: #2563EB; 
                                        text-decoration: none; 
                                        font-weight: 600; 
                                        font-size: 14px;">
                                        🔗 查看法律原文 &rarr;
                                    </a>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
            
            status.update(label="✅ 检索完成", state="complete", expanded=False)

    # 历史结果渲染 (同样的内联样式逻辑)
    elif st.session_state.results:
        for res in st.session_state.results:
            st.markdown(f"""
            <div style="
                background-color: #FFFFFF; 
                border: 1px solid #E5E7EB; 
                border-left: 5px solid #0F172A; 
                border-radius: 8px; 
                padding: 20px; 
                margin-bottom: 15px; 
                box-shadow: 0 2px 5px rgba(0,0,0,0.05);">
                <div style="
                    background-color: #F1F5F9; 
                    color: #334155; 
                    padding: 4px 10px; 
                    border-radius: 4px; 
                    font-size: 12px; 
                    font-weight: bold; 
                    display: inline-block; 
                    margin-bottom: 10px;">
                    {res['source']}
                </div>
                <div style="
                    color: #111827 !important; 
                    font-family: sans-serif; 
                    font-size: 15px; 
                    line-height: 1.6; 
                    white-space: pre-wrap;">
                    {res['content']}
                </div>
                <div style="margin-top: 15px; border-top: 1px solid #F3F4F6; padding-top: 10px;">
                    <a href="{res['url']}" target="_blank" style="
                        color: #2563EB; 
                        text-decoration: none; 
                        font-weight: 600; 
                        font-size: 14px;">
                        🔗 查看法律原文 &rarr;
                    </a>
                </div>
            </div>
            """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
