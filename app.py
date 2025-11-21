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

# --- 策略：分级关键词库 ---

# 1. 严格关键词 (用于媒体/智库)：必须强相关
STRICT_KEYWORDS = [
    "Sanction", "Export Control", "Entity List", "SDN", "Trade War", 
    "Tariff", "Semiconductor", "Chip Ban", "UFLPA", "Blacklist", 
    "Money Laundering", "Denied Persons", "制裁", "出口管制", "实体清单", "关税"
]

# 2. 宽泛关键词 (用于 VIP 政府网站)：包含政策更新、声明等
VIP_KEYWORDS = STRICT_KEYWORDS + [
    "Rule", "Final Order", "Announcement", "Statement", "Guidance", 
    "Policy", "Security", "Investigation", "Compliance", "Update", 
    "Advisory", "Action", "Regulation", "公告", "声明", "谈话", "办法", "措施"
]

# --- 站点配置 (带分级标签) ---
SITES = [
    # === VIP 核心源 (宽泛抓取) ===
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "engine": "curl", "group": "vip"},
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "engine": "curl", "group": "vip"},
    {"name": "🇺🇸 Commerce Press", "url": "https://www.commerce.gov/news/press-releases", "engine": "curl", "group": "vip"},
    {"name": "🇺🇸 DOJ Press", "url": "https://www.justice.gov/news/press-releases", "engine": "curl", "group": "vip"},
    {"name": "🇪🇺 EU Council", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "engine": "curl", "group": "vip"},
    {"name": "🇨🇳 MOFCOM (管制局)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "engine": "standard", "group": "vip"},
    {"name": "🇨🇳 外交部发言人", "url": "https://www.fmprc.gov.cn/fyrbt_673021/", "engine": "standard", "group": "vip"},
    {"name": "🇺🇸 BIS Enforcement", "url": "https://www.bis.gov/enforcement/export-violations", "engine": "curl", "group": "vip"},

    # === General 泛读源 (严格抓取) ===
    {"name": "🇬🇧 Reuters (Defense)", "url": "https://www.reuters.com/business/aerospace-defense/", "engine": "curl", "group": "general"},
    {"name": "🏛️ CSIS Analysis", "url": "https://www.csis.org/analysis", "engine": "curl", "group": "general"},
    {"name": "🇺🇸 US Congress", "url": "https://www.congress.gov/search?q=%7B%22source%22%3A%22legislation%22%2C%22congress%22%3A%22118%22%7D", "engine": "curl", "group": "general"},
    {"name": "🇭🇰 SCMP", "url": "https://www.scmp.com/news/china/diplomacy", "engine": "curl", "group": "general"},
    {"name": "📰 Foreign Policy", "url": "https://foreignpolicy.com/latest/", "engine": "curl", "group": "general"}
]

# ================= 🎨 UI 设计 (v17/v25 经典律所风) =================

st.set_page_config(page_title="Trade Compliance Monitor", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    /* 1. 全局 - 强制白底黑字 */
    .stApp { background-color: #ffffff; color: #1a202c; }
    
    /* 2. 标题 - 衬线体 */
    .main-header { font-family: "Source Serif Pro", "Georgia", serif; color: #0F294D; font-size: 2.4rem; font-weight: 700; margin-bottom: 0.5rem; }
    .sub-header { font-family: "Helvetica Neue", sans-serif; color: #64748b; font-size: 1rem; margin-bottom: 2.5rem; border-bottom: 1px solid #e2e8f0; padding-bottom: 1rem; }

    /* 3. 卡片 - 淡蓝灰背景 + 深蓝边框 */
    .result-card { background-color: #F7F9FB !important; border: 1px solid #E2E8F0; border-left: 5px solid #0F294D; border-radius: 8px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.02); }
    
    /* 4. 标签 */
    .source-tag { background-color: #E2E8F0; color: #1e293b !important; padding: 4px 10px; border-radius: 4px; font-size: 0.75rem; font-weight: 700; display: inline-block; margin-bottom: 14px; }
    
    /* VIP 徽章 */
    .vip-badge { background-color: #FEF3C7; color: #92400E !important; padding: 2px 6px; border-radius: 4px; font-size: 0.7rem; font-weight: 800; margin-left: 8px; vertical-align: middle; }

    /* 5. 正文 - 强制深黑 */
    .content-text { color: #1A202C !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 15px; line-height: 1.7; white-space: pre-wrap; }

    /* 6. 链接 */
    .link-btn { display: inline-block; margin-top: 18px; color: #2563eb !important; font-weight: 600; text-decoration: none; font-size: 0.9rem; border-bottom: 1px dotted #2563eb; }
    .link-btn:hover { border-bottom: 1px solid #2563eb; }
</style>
""", unsafe_allow_html=True)

# ================= 🧠 核心逻辑 (v25速度去重 + v26分级) =================

def get_target_date_objs(selected_date, report_type, include_tz):
    dates = [selected_date]
    if report_type == "日报" and include_tz: dates.append(selected_date - timedelta(days=1))
    elif report_type == "周报": dates = [selected_date - timedelta(days=i) for i in range(8)]
    elif report_type == "月报": dates = [selected_date - timedelta(days=i) for i in range(31)]
    return dates

def fetch_links_step(site):
    """步骤1: 分级采集"""
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
        
        # 根据站点类型选择关键词库
        current_keywords = VIP_KEYWORDS if site.get('group') == 'vip' else STRICT_KEYWORDS
        
        for a in soup.find_all('a'):
            t, h = a.get_text(strip=True), a.get('href')
            if t and len(t)>8 and h and "javascript" not in h:
                # 关键词匹配
                if any(k.lower() in t.lower() for k in current_keywords):
                    full = urljoin(site['url'], h)
                    # 返回带 group 的元组
                    links.append((site['name'], t, full, site['engine'], site.get('group')))
        return links
    except: return []

def analyze_article_final(item, target_date_objs):
    """步骤2: 分级 AI 审核 (确保只输出内容)"""
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

        # === 分级 Prompt 策略 ===
        if group == 'vip':
            relevance_check = "由于是核心政府网站，只要日期符合，且内容涉及法规、政策、声明、执法行动，均通过。"
        else:
            relevance_check = "必须强相关：内容必须直接涉及制裁、出口管制实体清单、关税。普通外交/政治/军事新闻直接剔除。"

        prompt = f"""
        你是一名贸易合规情报专家。
        
        【严格审核步骤】
        1. 日期核对：目标范围 {date_range_str}。如果文章日期不在范围内，输出 "MISMATCH"。
        2. 内容核对：{relevance_check} 不符则输出 "MISMATCH"。
        
        【输出要求】
        如果审核通过，请直接输出简报内容，**不要包含任何问候语、Prompt说明或Markdown代码块标记**。
        格式如下：
        **【标题】**：(中文翻译)
        **【核心事实】**：(3点摘要)
        **【合规提示】**：(专业建议)
        
        原文：
        来源：{site_name}
        标题：{title}
        正文摘要：{raw_text}
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
            # 清洗一下可能残留的 ```markdown 标记
            clean_reply = ai_reply.replace("```markdown", "").replace("```", "").strip()
            return {"source": site_name, "url": url, "content": clean_reply, "group": group}
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
    st.markdown(f'<div class="sub-header">律师专业版 v27.0 (速度/去重/分级/美观) | 模式：{report_type}</div>', unsafe_allow_html=True)

    if run:
        st.session_state.results = []
        target_date_objs = get_target_date_objs(selected_date, report_type, include_tz)
        
        status = st.status("🚀 启动系统...", expanded=True)
        status.write(f"📅 扫描日期: {[d.strftime('%Y-%m-%d') for d in target_date_objs]}")
        
        # 1. 采集 (v25 逻辑：15线程高并发)
        all_raw_candidates = []
        target_sites = [s for s in SITES if s['name'] in sites_selected]
        
        with ThreadPoolExecutor(max_workers=15) as exe:
            futures = {exe.submit(fetch_links_step, s): s['name'] for s in target_sites}
            for f in as_completed(futures):
                links = f.result()
                if links:
                    all_raw_candidates.extend(links)
                    status.write(f"✅ {futures[f]}: 采集到 {len(links)} 条线索")
        
        # 2. 去重 (v25 逻辑：标题去重)
        unique_candidates = []
        seen_titles = set()
        for item in all_raw_candidates:
            # item: (site, title, url, engine, group)
            title_clean = item[1].strip().lower()
            if title_clean not in seen_titles:
                seen_titles.add(title_clean)
                unique_candidates.append(item)
        
        if len(all_raw_candidates) > len(unique_candidates):
            status.write(f"✂️ 智能去重：移除 {len(all_raw_candidates) - len(unique_candidates)} 条重复内容")

        if not unique_candidates:
            status.update(label="未发现相关线索", state="error")
            st.warning("今日无相关更新。")
        else:
            status.write(f"🧠 AI 分级研判中 ({len(unique_candidates)} 条任务)... VIP 源优先保留。")
            result_container = st.container()
            
            # 3. 分析 (v25 逻辑：15线程并发 + v26 分级Prompt)
            with ThreadPoolExecutor(max_workers=15) as exe:
                futures = {exe.submit(analyze_article_final, item, target_date_objs): item for item in unique_candidates}
                found_count = 0
                
                for f in as_completed(futures):
                    res = f.result()
                    if res:
                        found_count += 1
                        st.session_state.results.append(res)
                        
                        # 渲染 (v26 逻辑：带 VIP 徽章)
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
                st.info("经 AI 核查，所有线索均为非目标日期旧闻或无关内容。")
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
