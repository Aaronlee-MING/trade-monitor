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

# --- 策略 1：分级关键词库 ---

# A. 严格关键词 (用于大众媒体/智库)：必须强相关
STRICT_KEYWORDS = [
    "Sanction", "Export Control", "Entity List", "SDN", "Trade War", 
    "Tariff", "Semiconductor", "Chip Ban", "UFLPA", "Blacklist", 
    "Money Laundering", "Denied Persons", "制裁", "出口管制", "实体清单", "关税"
]

# B. 宽泛关键词 (用于 VIP 政府网站)：只要沾边就抓，防止漏掉重要公告
VIP_KEYWORDS = STRICT_KEYWORDS + [
    "Rule", "Final Order", "Announcement", "Statement", "Guidance", 
    "Policy", "Security", "Investigation", "Compliance", "Update", 
    "Advisory", "Action", "Regulation", "公告", "声明", "谈话", "办法", "措施"
]

# --- 策略 2：站点分级配置 ---
SITES = [
    # === VIP 核心源 (使用宽泛标准) ===
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "engine": "curl", "group": "vip"},
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "engine": "curl", "group": "vip"},
    {"name": "🇺🇸 Commerce Press", "url": "https://www.commerce.gov/news/press-releases", "engine": "curl", "group": "vip"},
    {"name": "🇺🇸 DOJ Press", "url": "https://www.justice.gov/news/press-releases", "engine": "curl", "group": "vip"},
    {"name": "🇪🇺 EU Council", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "engine": "curl", "group": "vip"},
    {"name": "🇨🇳 MOFCOM (管制局)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "engine": "standard", "group": "vip"},
    {"name": "🇨🇳 外交部发言人", "url": "https://www.fmprc.gov.cn/fyrbt_673021/", "engine": "standard", "group": "vip"},
    {"name": "🇺🇸 BIS Enforcement", "url": "https://www.bis.gov/enforcement/export-violations", "engine": "curl", "group": "vip"},

    # === General 泛读源 (使用严格标准) ===
    {"name": "🇬🇧 Reuters (Defense)", "url": "https://www.reuters.com/business/aerospace-defense/", "engine": "curl", "group": "general"},
    {"name": "🏛️ CSIS Analysis", "url": "https://www.csis.org/analysis", "engine": "curl", "group": "general"},
    {"name": "🇺🇸 US Congress", "url": "https://www.congress.gov/search?q=%7B%22source%22%3A%22legislation%22%2C%22congress%22%3A%22118%22%7D", "engine": "curl", "group": "general"},
    {"name": "🇭🇰 SCMP", "url": "https://www.scmp.com/news/china/diplomacy", "engine": "curl", "group": "general"},
    {"name": "📰 Foreign Policy", "url": "https://foreignpolicy.com/latest/", "engine": "curl", "group": "general"}
]

# ================= 🎨 UI 设计 (v17/v25 经典复刻) =================

st.set_page_config(page_title="Trade Compliance Monitor", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    /* 全局 */
    .stApp { background-color: #ffffff; color: #1a202c; }
    
    /* 标题 - 律所衬线体 */
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

    /* 结果卡片 - 强制深色字 */
    .result-card {
        background-color: #F7F9FB !important; 
        border: 1px solid #E2E8F0;
        border-left: 5px solid #0F294D; 
        border-radius: 8px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.02);
    }
    .result-card:hover {
        border-color: #cbd5e1;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
    }
    
    /* 标签 */
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
    
    /* VIP 徽章 (新增) */
    .vip-badge {
        background-color: #FEF3C7;
        color: #92400E !important;
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 0.7rem;
        font-weight: 800;
        margin-left: 8px;
        display: inline-block;
        vertical-align: middle;
    }

    /* 正文 */
    .content-text {
        color: #1A202C !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 15px;
        line-height: 1.7;
        white-space: pre-wrap;
    }

    /* 链接 */
    .link-btn {
        display: inline-block;
        margin-top: 18px;
        color: #2563eb !important;
        font-weight: 600;
        text-decoration: none;
        font-size: 0.9rem;
        border-bottom: 1px dotted #2563eb;
    }
    .link-btn:hover { border-bottom: 1px solid #2563eb; }
</style>
""", unsafe_allow_html=True)

# ================= 🧠 核心逻辑 (分级双标) =================

def get_target_date_objs(selected_date, report_type, include_tz):
    dates = [selected_date]
    if report_type == "日报" and include_tz: dates.append(selected_date - timedelta(days=1))
    elif report_type == "周报": dates = [selected_date - timedelta(days=i) for i in range(8)]
    elif report_type == "月报": dates = [selected_date - timedelta(days=i) for i in range(31)]
    return dates

def fetch_links_step(site):
    """步骤1: 分级采集 (VIP用宽泛词，General用严格词)"""
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
        
        # === 分级策略 ===
        # 如果是 VIP 网站，使用宽泛关键词库
        # 如果是 General 网站，使用严格关键词库
        current_keywords = VIP_KEYWORDS if site.get('group') == 'vip' else STRICT_KEYWORDS
        
        for a in soup.find_all('a'):
            t, h = a.get_text(strip=True), a.get('href')
            if t and len(t)>5 and h and "javascript" not in h:
                # 关键词匹配
                if any(k.lower() in t.lower() for k in current_keywords):
                    full = urljoin(site['url'], h)
                    links.append((site['name'], t, full, site['engine'], site.get('group')))
        return links
    except: return []

def analyze_article_tiered(item, target_date_objs):
    """步骤2: 分级 AI 审核"""
    site_name, title, url, engine, group = item
    try:
        txt = ""
        if engine == "standard":
            r = requests.get(url, headers={"User-Agent": "Chrome/120.0"}, timeout=10, verify=False)
            if "mofcom" in url: r.encoding = "gbk"
            txt = r.text
        else:
            r = c_requests.get(url, impersonate="chrome120", timeout=10)
            txt = r.text
        
        soup = BeautifulSoup(txt, 'html.parser')
        for s in soup(["script", "style", "nav", "footer", "aside"]): s.extract()
        raw_text = soup.get_text(separator="\n", strip=True)[:3500]
        if len(raw_text) < 50: return None

        date_range_str = ", ".join([d.strftime("%Y-%m-%d") for d in target_date_objs])

        # === 构建分级 Prompt ===
        
        if group == 'vip':
            # VIP 策略：宽容模式
            # 只要日期对，且不是纯粹的招聘广告或放假通知，就保留
            # 重点提取“法律依据”
            relevance_instruction = """
            【VIP 通道】
            该来源为核心政府机构（BIS/OFAC/商务部等）。
            1. 日期必须符合。
            2. 内容只要涉及**法规更新、执法行动、声明、政策调整**，即可保留。
            3. 不要因为没出现“制裁”二字就过滤，只要是官方实质性通告都保留。
            """
        else:
            # General 策略：严格去噪
            # 必须强相关，剔除泛政治/外交口水战
            relevance_instruction = """
            【普通通道】
            该来源为大众媒体或智库。
            1. 日期必须符合。
            2. 内容必须**强相关**于：经济制裁、出口管制名单、实体清单、关税战。
            3. ⚠️ 如果只是普通的外交访问、人事变动、常规军事报道，**直接返回 'MISMATCH'**。
            """

        prompt = f"""
        你是一名贸易合规情报分析师。
        
        【任务一：日期核查】
        目标范围：{date_range_str}
        如果文章发布日期不在范围内（是旧闻），直接返回 "MISMATCH"。
        
        【任务二：相关性核查】
        文章标题：{title}
        文章来源：{site_name}
        {relevance_instruction}
        
        【任务三：生成简报】
        如果通过上述核查，请生成：
        1. **【标题】**：(中文翻译，专业准确)
        2. **【核心事实】**：(简练概括主体、行为、结果)
        3. **【合规提示】**：(针对企业的具体风险提示，**区分法律依据或市场风险**)
        
        文章摘要：
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
            return {"source": site_name, "url": url, "content": ai_reply, "group": group}
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
        run = st.button("开始分级检索", type="primary", use_container_width=True)

    st.markdown('<div class="main-header">Trade Compliance Monitor</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-header">律师专业版 v26.0 (分级双标) | 模式：{report_type}</div>', unsafe_allow_html=True)

    if run:
        st.session_state.results = []
        target_date_objs = get_target_date_objs(selected_date, report_type, include_tz)
        
        status = st.status("🚀 启动分级引擎...", expanded=True)
        status.write(f"📅 扫描日期: {[d.strftime('%Y-%m-%d') for d in target_date_objs]}")
        
        # 1. 采集
        all_raw_candidates = []
        target_sites = [s for s in SITES if s['name'] in sites_selected]
        
        with ThreadPoolExecutor(max_workers=15) as exe:
            futures = {exe.submit(fetch_links_step, s): s['name'] for s in target_sites}
            for f in as_completed(futures):
                links = f.result()
                if links:
                    all_raw_candidates.extend(links)
                    status.write(f"✅ {futures[f]}: 采集到 {len(links)} 条线索")
        
        # 2. 去重
        unique_candidates = []
        seen_titles = set()
        for item in all_raw_candidates:
            title_clean = item[1].strip().lower()
            if title_clean not in seen_titles:
                seen_titles.add(title_clean)
                unique_candidates.append(item)
        
        if not unique_candidates:
            status.update(label="未发现线索", state="error")
            st.warning("今日无相关更新。")
        else:
            status.write(f"🧠 AI 分级研判中 ({len(unique_candidates)} 条任务)... VIP 源优先保留，普通源严格去噪。")
            result_container = st.container()
            
            # 3. 分析
            with ThreadPoolExecutor(max_workers=15) as exe:
                futures = {exe.submit(analyze_article_tiered, item, target_date_objs): item for item in unique_candidates}
                found_count = 0
                
                for f in as_completed(futures):
                    res = f.result()
                    if res:
                        found_count += 1
                        st.session_state.results.append(res)
                        
                        # 渲染卡片 (带 VIP 标识)
                        vip_badge = '<span class="vip-badge">CORE</span>' if res['group'] == 'vip' else ''
                        
                        with result_container:
                            st.markdown(f"""
                            <div class="result-card">
                                <div class="source-tag">{res['source']} {vip_badge}</div>
                                <div class="content-text">{res['content']}</div>
                                <a href="{res['url']}" target="_blank" class="link-btn">🔗 查看法律原文 (Source) &rarr;</a>
                            </div>
                            """, unsafe_allow_html=True)
            
            if found_count == 0:
                status.update(label="检索完成：无有效情报", state="complete")
                st.info("经 AI 核查，采集到的线索均为旧闻或无关内容。")
            else:
                status.update(label=f"✅ 检索完成：锁定 {found_count} 条情报", state="complete", expanded=False)

    elif st.session_state.results:
        for res in st.session_state.results:
            vip_badge = '<span class="vip-badge">CORE</span>' if res.get('group') == 'vip' else ''
            st.markdown(f"""
            <div class="result-card">
                <div class="source-tag">{res['source']} {vip_badge}</div>
                <div class="content-text">{res['content']}</div>
                <a href="{res['url']}" target="_blank" class="link-btn">🔗 查看法律原文 (Source) &rarr;</a>
            </div>
            """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
