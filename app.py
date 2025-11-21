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

# ================= 🎨 UI 设计 (v17 复刻 + 强制清晰) =================

st.set_page_config(page_title="Trade Compliance Monitor", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    /* 1. 全局背景与字体 - 强制白底黑字 */
    .stApp {
        background-color: #ffffff; 
        color: #1a202c;
    }
    
    /* 2. 标题设计 - 律所衬线体风格 */
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

    /* 3. 结果卡片 - 强制配色，防止夜间模式干扰 */
    .result-card {
        background-color: #F7F9FB !important; 
        border: 1px solid #E2E8F0;
        border-left: 5px solid #0F294D; /* 专业深蓝 */
        border-radius: 8px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.02);
    }
    
    /* 4. 来源标签 */
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

    /* 5. 正文内容 - 强制深色，高对比度 */
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

# ================= 🧠 核心逻辑 (速度+质量平衡) =================

def generate_strict_keywords(selected_date, report_type, include_tz):
    target_dates = []
    if report_type == "日报":
        target_dates.append(selected_date)
        if include_tz: target_dates.append(selected_date - timedelta(days=1))
    elif report_type == "周报":
        for i in range(7): target_dates.append(selected_date - timedelta(days=i))
    elif report_type == "月报":
        for i in range(30): target_dates.append(selected_date - timedelta(days=i))

    keywords = []
    for d in target_dates:
        y, m_full, m_abbr, d_str, d_pad = str(d.year), d.strftime("%B"), d.strftime("%b"), str(d.day), d.strftime("%d")
        # 严格必须包含年份
        keywords.extend([
            f"{m_full} {d_str}, {y}", f"{m_abbr} {d_str}, {y}", f"{m_abbr}. {d_str}, {y}",
            f"{y}-{m_abbr}-{d_pad}", f"{y}/{d.strftime('%m')}/{d_pad}", f"{y}-{d.strftime('%m')}-{d_pad}",
            f"{d_str} {m_full} {y}", d.strftime("%m/%d/%Y")
        ])
    return list(set(keywords)), target_dates

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

def analyze_article_strict(item, date_keywords, target_date_objs):
    """步骤2: 并发验证 + AI 裁决"""
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
        
        # 1. Python 严格筛选 (速度层)
        match_hit = False
        for dk in date_keywords:
            if dk in txt:
                match_hit = True
                break
        if not match_hit: return None

        # 2. 提取正文
        soup = BeautifulSoup(txt, 'html.parser')
        for s in soup(["script", "style", "nav", "footer", "aside"]): s.extract()
        content = "\n".join([p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 50])
        if len(content) < 50: return None

        # 3. AI 终极裁决 (质量层)
        date_range_str = ", ".join([d.strftime("%Y-%m-%d") for d in target_date_objs])
        prompt = f"""
        任务：贸易合规简报分析。
        【日期核查】目标范围：{date_range_str}。
        ⚠️ 必须严格核对：如果该新闻**不是**在此日期范围内发布（例如是旧闻），请直接返回 "MISMATCH"。
        
        若符合，请生成律师简报：
        1. **【标题】**：(中文)
        2. **【核心事实】**：(3点摘要)
        3. **【合规提示】**：(风险建议)
        
        原文：
        来源：{site_name}
        标题：{title}
        正文：{content[:2500]}
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
    st.markdown(f'<div class="sub-header">律师专业版 v22.0 | 模式：{report_type}</div>', unsafe_allow_html=True)

    if run:
        st.session_state.results = []
        date_keys, target_date_objs = generate_strict_keywords(selected_date, report_type, include_tz)
        
        # 进度状态栏
        status = st.status("🚀 启动双引擎检索系统...", expanded=True)
        status.write(f"📅 锁定日期范围: {[d.strftime('%Y-%m-%d') for d in target_date_objs]}")
        
        all_candidates = []
        target_sites = [s for s in SITES if s['name'] in sites_selected]
        
        # 第一阶段：高并发采集链接
        with ThreadPoolExecutor(max_workers=10) as exe:
            futures = {exe.submit(fetch_links_step, s): s['name'] for s in target_sites}
            for f in as_completed(futures):
                links = f.result()
                relevant = [l for l in links if any(k.lower() in l[1].lower() for k in KEYWORDS)]
                if relevant:
                    all_candidates.extend(relevant)
                    status.write(f"✅ {futures[f]}: 采集到 {len(relevant)} 条线索")
        
        if not all_candidates:
            status.update(label="未发现相关线索", state="error")
            st.warning("今日无相关关键词更新。")
        else:
            status.write(f"🧠 进入 AI 深度验证 ({len(all_candidates)} 条待审)...")
            result_container = st.container()
            
            # 第二阶段：高并发 AI 分析
            with ThreadPoolExecutor(max_workers=10) as exe:
                futures = {exe.submit(analyze_article_strict, item, date_keys, target_date_objs): item for item in all_candidates}
                found_count = 0
                
                for f in as_completed(futures):
                    res = f.result()
                    if res:
                        found_count += 1
                        st.session_state.results.append(res)
                        # 实时渲染 UI (内联样式确保清晰度)
                        with result_container:
                            st.markdown(f"""
                            <div class="result-card">
                                <div class="source-tag">{res['source']}</div>
                                <div class="content-text">{res['content']}</div>
                                <a href="{res['url']}" target="_blank" class="link-btn">🔗 查看法律原文 (Source) &rarr;</a>
                            </div>
                            """, unsafe_allow_html=True)
            
            if found_count == 0:
                status.update(label="检索完成：无有效情报", state="complete") # 修复了 warning 报错
                st.info(f"已扫描 {len(all_candidates)} 条线索，但 AI 判定均为非目标日期 ({[d.strftime('%Y-%m-%d') for d in target_date_objs]}) 的旧闻。")
            else:
                status.update(label=f"✅ 检索完成：锁定 {found_count} 条有效情报", state="complete", expanded=False)

    # 历史结果展示
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
