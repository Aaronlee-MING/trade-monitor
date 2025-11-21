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

# 关键词 (只基于标题筛选主题，不筛日期)
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

# ================= 🎨 UI 设计 (v17/v22 经典样式) =================

st.set_page_config(page_title="Trade Compliance Monitor", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    /* 1. 全局 - 强制白底黑字 */
    .stApp {
        background-color: #ffffff; 
        color: #1a202c;
    }
    
    /* 2. 标题 - 律所风格 */
    .main-header {
        font-family: "Source Serif Pro", "Georgia", serif;
        color: #0F294D;
        font-size: 2.4rem;
        font-weight: 700;
        letter-spacing: -0.5px;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-family: "Helvetica Neue", sans-serif;
        color: #64748b;
        font-size: 1rem;
        font-weight: 400;
        margin-bottom: 2.5rem;
        border-bottom: 1px solid #e2e8f0;
        padding-bottom: 1rem;
    }

    /* 3. 结果卡片 - 强制配色 */
    .result-card {
        background-color: #F7F9FB !important; 
        border: 1px solid #E2E8F0;
        border-left: 5px solid #0F294D; /* 专业深蓝 */
        border-radius: 8px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.02);
    }
    
    /* 4. 标签 */
    .source-tag {
        background-color: #E2E8F0;
        color: #1e293b !important;
        padding: 4px 10px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        display: inline-block;
        margin-bottom: 14px;
    }

    /* 5. 正文 - 强制深黑 */
    .content-text {
        color: #1A202C !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 15px;
        line-height: 1.7;
        white-space: pre-wrap;
    }

    /* 6. 链接 */
    .link-btn {
        display: inline-block;
        margin-top: 18px;
        color: #2563eb !important;
        font-weight: 600;
        text-decoration: none;
        font-size: 0.9rem;
        border-bottom: 1px dotted #2563eb;
    }
    .link-btn:hover {
        border-bottom: 1px solid #2563eb;
    }
</style>
""", unsafe_allow_html=True)

# ================= 🧠 核心逻辑 (v23.0 修正版 - 移除Python死板过滤) =================

def get_target_date_objs(selected_date, report_type, include_tz):
    """只生成日期对象列表，不再生成字符串关键词"""
    dates = []
    if report_type == "日报":
        dates = [selected_date]
        if include_tz: dates.append(selected_date - timedelta(days=1))
    elif report_type == "周报":
        dates = [selected_date - timedelta(days=i) for i in range(8)]
    elif report_type == "月报":
        dates = [selected_date - timedelta(days=i) for i in range(31)]
    return dates

def fetch_links_step(site):
    """步骤1: 并发采集 (不变)"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
        html = ""
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
        
        if not html: return []
        soup = BeautifulSoup(html, 'html.parser')
        links = []
        seen = set()
        for a in soup.find_all('a'):
            t, h = a.get_text(strip=True), a.get('href')
            # 关键词初筛：只看标题里有没有 "Sanction", "Trade" 等
            # 这一步不再检查日期，防止漏掉
            if t and len(t)>10 and h and "javascript" not in h:
                full = urljoin(site['url'], h)
                if full not in seen:
                    seen.add(full)
                    links.append((site['name'], t, full, site['engine']))
        return links
    except: return []

def analyze_article_ai_check(item, target_date_objs):
    """步骤2: 直接交给 AI 查日期"""
    site_name, title, url, engine = item
    try:
        txt = ""
        if engine == "standard":
            r = requests.get(url, headers={"User-Agent": "Chrome/120.0"}, timeout=10, verify=False)
            if "mofcom" in url: r.encoding = "gbk"
            txt = r.text
        else:
            r = c_requests.get(url, impersonate="chrome120", timeout=10)
            txt = r.text
        
        # 提取全文 (不仅是 p 标签，为了获取 meta 日期)
        soup = BeautifulSoup(txt, 'html.parser')
        for s in soup(["script", "style", "nav", "footer", "aside"]): s.extract()
        # 获取前 3500 字符，足以包含标题、日期和正文开头
        raw_text = soup.get_text(separator="\n", strip=True)[:3500]

        if len(raw_text) < 50: return None

        # 构造日期范围
        date_range_str = ", ".join([d.strftime("%Y-%m-%d") for d in target_date_objs])

        # === 关键升级：Prompt 不再依赖 Python 匹配 ===
        prompt = f"""
        你是一个严格的新闻发布时间审核员。
        
        【审核标准】
        目标日期范围：{date_range_str}
        
        请阅读下面的网页内容，找到这篇文章的**发布日期 (Published Date)** 或 **更新日期 (Updated Date)**。
        
        判断逻辑：
        1. 如果文章日期**在**上述范围内（包含起始日），请生成简报。
        2. 如果文章日期**不在**范围内（是旧闻），或者找不到明确日期，请直接返回 "MISMATCH"。
        
        【简报格式】(如果符合日期):
        1. **【标题】**：(中文翻译)
        2. **【核心事实】**：(3点摘要)
        3. **【合规提示】**：(针对中企建议)
        
        【网页内容】
        来源：{site_name}
        标题：{title}
        内容摘要：
        {raw_text}
        """
        
        res = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {SILICON_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=30
        )
        
        if res.status_code == 200:
            ai_reply = res.json()['choices'][0]['message']['content']
            if "MISMATCH" in ai_reply: return None
            return {"source": site_name, "url": url, "content": ai_reply}
    except: pass
    return None

# ================= 🖥️ 主界面 =================

def main():
    if 'results' not in st.session_state: st.session_state.results = []

    with st.sidebar:
        st.header("⚖️ 控制台")
        report_type = st.selectbox("报告周期", ["日报", "周报", "月报"])
        selected_date = st.date_input("基准日期", datetime.now())
        include_tz = st.checkbox("包含时差 (美东时间)", value=True)
        st.divider()
        sites_selected = st.multiselect("数据源", [s['name'] for s in SITES], default=[s['name'] for s in SITES])
        run = st.button("开始精准检索", type="primary", use_container_width=True)

    st.markdown('<div class="main-header">Trade Compliance Monitor</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-header">律师专业版 v23.0 (AI 全托管日期核查) | 模式：{report_type}</div>', unsafe_allow_html=True)

    if run:
        st.session_state.results = []
        # 只生成日期对象，不生成关键词了
        target_date_objs = get_target_date_objs(selected_date, report_type, include_tz)
        
        status = st.status("🚀 启动系统...", expanded=True)
        status.write(f"📅 目标日期范围: {[d.strftime('%Y-%m-%d') for d in target_date_objs]}")
        
        all_candidates = []
        target_sites = [s for s in SITES if s['name'] in sites_selected]
        
        # 1. 采集链接 (只筛主题，不筛日期)
        with ThreadPoolExecutor(max_workers=10) as exe:
            futures = {exe.submit(fetch_links_step, s): s['name'] for s in target_sites}
            for f in as_completed(futures):
                links = f.result()
                # 只要标题里有 Trade/Sanction 就先抓回来
                relevant = [l for l in links if any(k.lower() in l[1].lower() for k in KEYWORDS)]
                if relevant:
                    all_candidates.extend(relevant)
                    status.write(f"✅ {futures[f]}: 采集到 {len(relevant)} 条相关主题线索")
        
        if not all_candidates:
            status.update(label="未发现相关主题线索", state="error")
            st.warning("今日无相关关键词（制裁、贸易等）标题更新。")
        else:
            status.write(f"🧠 全量提交 AI 核查日期 ({len(all_candidates)} 条任务)... 此过程可能较慢，请耐心等待。")
            result_container = st.container()
            
            # 2. 全量 AI 核查
            with ThreadPoolExecutor(max_workers=10) as exe:
                futures = {exe.submit(analyze_article_ai_check, item, target_date_objs): item for item in all_candidates}
                found_count = 0
                
                for f in as_completed(futures):
                    res = f.result()
                    if res:
                        found_count += 1
                        st.session_state.results.append(res)
                        with result_container:
                            st.markdown(f"""
                            <div class="result-card">
                                <div class="source-tag">{res['source']}</div>
                                <div class="content-text">{res['content']}</div>
                                <a href="{res['url']}" target="_blank" class="link-btn">🔗 查看法律原文 (Source) &rarr;</a>
                            </div>
                            """, unsafe_allow_html=True)
            
            if found_count == 0:
                status.update(label="检索完成：无有效情报", state="complete")
                st.info(f"已扫描 {len(all_candidates)} 条线索，经 AI 逐一核对，均不属于目标日期范围。")
            else:
                status.update(label=f"✅ 检索完成：锁定 {found_count} 条有效情报", state="complete", expanded=False)

    elif st.session_state.results:
        for res in st.session_state.results:
            st.markdown(f"""
            <div class="result-card">
                <div class="source-tag">{res['source']}</div>
                <div class="content-text">{res['content']}</div>
                <a href="{res['url']}" target="_blank" class="link-btn">🔗 查看法律原文 (Source) &rarr;</a>
            </div>
            """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
