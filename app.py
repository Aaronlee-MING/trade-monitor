import streamlit as st
import requests
import warnings
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

# 引入穿墙库
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

# ⚠️ 关键词策略：只保留强相关词，剔除泛政治词汇，保证质量
KEYWORDS = [
    "Sanction", "Export Control", "Import Restriction", "Tariff", "Entity List", 
    "SDN List", "Trade Violation", "Semiconductor", "Chip Ban", "UFLPA", 
    "Investment Ban", "Money Laundering", "Blacklist", "Denied Persons",
    "制裁", "出口管制", "贸易限制", "关税", "实体清单", "半导体", "芯片", "洗钱", "黑名单"
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

# ================= 🎨 UI 设计 (严格锁定 v17 风格) =================

st.set_page_config(page_title="Trade Compliance Monitor", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    /* 1. 全局样式：强制白底黑字 */
    .stApp {
        background-color: #ffffff; 
        color: #1a202c;
    }
    
    /* 2. 标题：律所专用衬线体 */
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

    /* 3. 结果卡片：高级淡蓝灰背景，深蓝边框 */
    .result-card {
        background-color: #F7F9FB !important; 
        border: 1px solid #E2E8F0;
        border-left: 5px solid #0F294D; /* 律所蓝 */
        border-radius: 8px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.02);
    }
    .result-card:hover {
        border-color: #cbd5e1;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
    }
    
    /* 4. 来源标签 */
    .source-tag {
        background-color: #E2E8F0;
        color: #1e293b !important; /* 强制深灰 */
        padding: 4px 10px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        display: inline-block;
        margin-bottom: 14px;
    }

    /* 5. 正文内容：强制深黑，确保清晰 */
    .content-text {
        color: #1A202C !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 15px;
        line-height: 1.7;
        white-space: pre-wrap;
    }

    /* 6. 链接按钮 */
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

# ================= 🧠 核心逻辑 =================

def get_target_date_objs(selected_date, report_type, include_tz):
    dates = [selected_date]
    if report_type == "日报" and include_tz: dates.append(selected_date - timedelta(days=1))
    elif report_type == "周报": dates = [selected_date - timedelta(days=i) for i in range(8)]
    elif report_type == "月报": dates = [selected_date - timedelta(days=i) for i in range(31)]
    return dates

def fetch_links_step(site):
    """步骤1: 并发采集"""
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
        for a in soup.find_all('a'):
            t, h = a.get_text(strip=True), a.get('href')
            # 关键词筛选
            if t and len(t)>10 and h and "javascript" not in h:
                full = urljoin(site['url'], h)
                links.append((site['name'], t, full, site['engine']))
        return links
    except: return []

def analyze_article_ai_check(item, target_date_objs):
    """步骤2: AI 深度审核"""
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
        
        soup = BeautifulSoup(txt, 'html.parser')
        for s in soup(["script", "style", "nav", "footer", "aside"]): s.extract()
        raw_text = soup.get_text(separator="\n", strip=True)[:3500]
        if len(raw_text) < 50: return None

        date_range_str = ", ".join([d.strftime("%Y-%m-%d") for d in target_date_objs])

        # Prompt: 严格要求 AI 审核日期 + 审核内容相关性
        prompt = f"""
        你是一名最严谨的贸易合规分析师。
        
        【任务一：日期审核】
        目标日期范围：{date_range_str}
        必须严格核对：如果文章发布日期不在范围内（例如是旧闻），直接返回 "MISMATCH"。
        
        【任务二：内容相关性审核】
        文章标题：{title}
        文章来源：{site_name}
        必须严格核对：是否与**国际贸易制裁、出口管制、实体清单**强相关？
        ⚠️ 如果是普通的**外交访问、一般性政治新闻**（无具体贸易限制），直接返回 "MISMATCH"。
        
        【任务三：生成简报】
        仅当日期和内容均符合时，输出：
        1. **【标题】**：(中文)
        2. **【核心事实】**：(3点摘要)
        3. **【合规提示】**：(针对中企建议)
        
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
        run = st.button("开始检索", type="primary", use_container_width=True)

    st.markdown('<div class="main-header">Trade Compliance Monitor</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-header">律师专业版 v25.0 (金标最终版) | 模式：{report_type}</div>', unsafe_allow_html=True)

    if run:
        st.session_state.results = []
        target_date_objs = get_target_date_objs(selected_date, report_type, include_tz)
        
        status = st.status("🚀 启动双引擎...", expanded=True)
        status.write(f"📅 扫描日期: {[d.strftime('%Y-%m-%d') for d in target_date_objs]}")
        
        # 1. 采集 (Speed: 15线程并发)
        all_raw_candidates = []
        target_sites = [s for s in SITES if s['name'] in sites_selected]
        
        with ThreadPoolExecutor(max_workers=15) as exe: # 提升线程数以保障速度
            futures = {exe.submit(fetch_links_step, s): s['name'] for s in target_sites}
            for f in as_completed(futures):
                links = f.result()
                # 严格关键词初筛 (Quality: 过滤弱相关)
                relevant = [l for l in links if any(k.lower() in l[1].lower() for k in KEYWORDS)]
                if relevant:
                    all_raw_candidates.extend(relevant)
                    status.write(f"✅ {futures[f]}: 采集到 {len(relevant)} 条强相关线索")
        
        # 2. 标题去重 (Quality: 解决重复)
        unique_candidates = []
        seen_titles = set()
        for item in all_raw_candidates:
            title_clean = item[1].strip().lower()
            if title_clean not in seen_titles:
                seen_titles.add(title_clean)
                unique_candidates.append(item)
        
        if len(all_raw_candidates) > len(unique_candidates):
            status.write(f"✂️ 智能去重：移除 {len(all_raw_candidates) - len(unique_candidates)} 条冗余信息")

        if not unique_candidates:
            status.update(label="未发现线索", state="error")
            st.warning("今日无制裁/贸易管制类关键新闻更新。")
        else:
            status.write(f"🧠 AI 深度核查中 ({len(unique_candidates)} 条任务)...")
            result_container = st.container()
            
            # 3. 深度分析 (Speed: 15线程并发)
            with ThreadPoolExecutor(max_workers=15) as exe:
                futures = {exe.submit(analyze_article_ai_check, item, target_date_objs): item for item in unique_candidates}
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
                st.info("AI 已过滤掉所有非贸易制裁类或非今日发布的新闻。")
            else:
                status.update(label=f"✅ 检索完成：锁定 {found_count} 条核心情报", state="complete", expanded=False)

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
