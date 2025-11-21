import streamlit as st
import requests
import warnings
import time
import re  # ✅ 补全了这个关键库
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

# 媒体源专用关键词 (VIP源不使用此表，全量抓取)
MEDIA_KEYWORDS = [
    "Sanction", "Export Control", "Entity List", "SDN", "Tariff", 
    "Semiconductor", "Chip", "Blacklist", "Laundering", "Ban",
    "制裁", "出口管制", "实体清单", "关税", "半导体", "芯片", "黑名单"
]

# 站点配置
SITES = [
    # === VIP 核心源 (策略：不过滤关键词，只要日期对全抓) ===
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 Commerce Press", "url": "https://www.commerce.gov/news/press-releases", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 DOJ Press", "url": "https://www.justice.gov/news/press-releases", "engine": "curl", "type": "vip"},
    {"name": "🇪🇺 EU Council", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "engine": "curl", "type": "vip"},
    {"name": "🇨🇳 MOFCOM (管制局)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "engine": "standard", "type": "vip"},
    {"name": "🇨🇳 外交部发言人", "url": "https://www.fmprc.gov.cn/fyrbt_673021/", "engine": "standard", "type": "vip"},
    {"name": "🇺🇸 BIS Enforcement", "url": "https://www.bis.gov/enforcement/export-violations", "engine": "curl", "type": "vip"},

    # === General 媒体源 (策略：严格关键词过滤 + AI去噪) ===
    {"name": "🇬🇧 Reuters (Defense)", "url": "https://www.reuters.com/business/aerospace-defense/", "engine": "curl", "type": "media"},
    {"name": "🏛️ CSIS Analysis", "url": "https://www.csis.org/analysis", "engine": "curl", "type": "media"},
    {"name": "🇺🇸 US Congress", "url": "https://www.congress.gov/search?q=%7B%22source%22%3A%22legislation%22%2C%22congress%22%3A%22118%22%7D", "engine": "curl", "type": "media"},
    {"name": "🇭🇰 SCMP", "url": "https://www.scmp.com/news/china/diplomacy", "engine": "curl", "type": "media"},
    {"name": "📰 Foreign Policy", "url": "https://foreignpolicy.com/latest/", "engine": "curl", "type": "media"}
]

# ================= 🎨 UI 设计 (极简专业版) =================

st.set_page_config(page_title="Trade Compliance Monitor", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #ffffff; color: #1a202c; }
    .main-header { font-family: "Source Serif Pro", "Georgia", serif; color: #0F294D; font-size: 2.4rem; font-weight: 700; margin-bottom: 0.5rem; }
    .sub-header { font-family: "Helvetica Neue", sans-serif; color: #64748b; font-size: 1rem; margin-bottom: 2.5rem; border-bottom: 1px solid #e2e8f0; padding-bottom: 1rem; }
    .result-card { background-color: #F7F9FB !important; border: 1px solid #E2E8F0; border-left: 5px solid #0F294D; border-radius: 8px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.02); }
    .source-tag { background-color: #E2E8F0; color: #1e293b !important; padding: 4px 10px; border-radius: 4px; font-size: 0.75rem; font-weight: 700; display: inline-block; margin-bottom: 14px; }
    .vip-badge { background-color: #FEF3C7; color: #92400E !important; padding: 2px 6px; border-radius: 4px; font-size: 0.7rem; font-weight: 800; margin-left: 8px; vertical-align: middle; }
    .content-text { color: #1A202C !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 15px; line-height: 1.7; white-space: pre-wrap; }
    .link-btn { display: inline-block; margin-top: 18px; color: #2563eb !important; font-weight: 600; text-decoration: none; font-size: 0.9rem; border-bottom: 1px dotted #2563eb; }
</style>
""", unsafe_allow_html=True)

# ================= 🧠 智能双标逻辑 =================

def get_date_keywords(selected_date, report_type, include_tz):
    """生成日期匹配字符串"""
    dates = [selected_date]
    if report_type == "日报" and include_tz: dates.append(selected_date - timedelta(days=1))
    elif report_type == "周报": dates = [selected_date - timedelta(days=i) for i in range(8)]
    elif report_type == "月报": dates = [selected_date - timedelta(days=i) for i in range(31)]
    
    keywords = []
    for d in dates:
        y, m_full, m_abbr, d_str, d_pad = str(d.year), d.strftime("%B"), d.strftime("%b"), str(d.day), d.strftime("%d")
        keywords.extend([
            f"{m_full} {d_str}, {y}", f"{m_abbr} {d_str}, {y}", f"{m_abbr}. {d_str}, {y}",
            f"{y}-{m_abbr}-{d_pad}", f"{y}/{d.strftime('%m')}/{d_pad}", f"{y}-{d.strftime('%m')}-{d_pad}",
            f"{d_str} {m_full} {y}", d.strftime("%m/%d/%Y")
        ])
    return list(set(keywords)), dates

def normalize_title(title):
    return re.sub(r'[^\w\s]', '', title).lower().strip()

def fetch_links_smart(site):
    """
    智能采集：
    - VIP源：无视关键词，抓取所有长标题链接
    - 媒体源：必须包含MEDIA_KEYWORDS才抓取
    """
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
            if t and len(t)>10 and h and "javascript" not in h:
                full = urljoin(site['url'], h)
                
                # === 核心分级逻辑 ===
                if site['type'] == 'vip':
                    # VIP: 只要标题够长，全抓
                    links.append((site['name'], t, full, site['engine'], site['type']))
                else:
                    # Media: 必须包含关键词
                    if any(k.lower() in t.lower() for k in MEDIA_KEYWORDS):
                        links.append((site['name'], t, full, site['engine'], site['type']))
        return links
    except: return []

def analyze_article_smart(item, date_keywords, target_date_objs):
    """
    智能分析：Python 日期初筛 + AI 终极生成
    """
    site_name, title, url, engine, site_type = item
    try:
        # 1. 获取正文
        txt = ""
        if engine == "standard":
            r = requests.get(url, headers={"User-Agent": "Chrome/120.0"}, timeout=10, verify=False)
            if "mofcom" in url: r.encoding = "gbk"
            txt = r.text
        else:
            r = c_requests.get(url, impersonate="chrome120", timeout=10)
            txt = r.text
        
        # 2. Python 日期初筛
        match_hit = False
        for dk in date_keywords:
            if dk in txt:
                match_hit = True
                break
        if not match_hit: return None

        # 3. 提取纯文本
        soup = BeautifulSoup(txt, 'html.parser')
        for s in soup(["script", "style", "nav", "footer", "aside"]): s.extract()
        raw_text = soup.get_text(separator="\n", strip=True)[:3500]
        if len(raw_text) < 50: return None

        # 4. AI 终极生成
        date_range_str = ", ".join([d.strftime("%Y-%m-%d") for d in target_date_objs])
        
        if site_type == 'vip':
            context_instruction = "这是核心监管机构信息。只要日期符合，无论是法规、声明还是执法行动，一律保留。"
        else:
            context_instruction = "这是媒体新闻。必须与'制裁/出口管制/实体清单'强相关。如果是普通外交访问，请返回 MISMATCH。"

        prompt = f"""
        你是一名贸易合规情报专家。
        
        【核查任务】
        1. 再次核对日期：文章必须发布于 {date_range_str} 范围内（包含美国时差）。如果是旧闻，返回 "MISMATCH"。
        2. 内容核对：{context_instruction}
        
        【输出任务】
        如果符合，请输出简报（不要任何Markdown标记，不要代码块）：
        **【标题】**：(中文翻译)
        **【核心事实】**：(3点摘要，客观陈述)
        **【合规提示】**：(针对中企的风险提示)
        
        原文：
        标题：{title}
        来源：{site_name}
        正文：{raw_text}
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
            clean_reply = ai_reply.replace("```markdown", "").replace("```", "").strip()
            return {"source": site_name, "url": url, "content": clean_reply, "type": site_type}
    except: pass
    return None

# ================= 🖥️ 主界面 =================

def main():
    if 'results' not in st.session_state: st.session_state.results = []

    with st.sidebar:
        st.header("⚖️ 控制台")
        report_type = st.selectbox("报告周期", ["日报", "周报", "月报"])
        selected_date = st.date_input("基准日期", datetime.now())
        include_tz = st.checkbox("包含时差 (推荐)", value=True)
        st.divider()
        sites_selected = st.multiselect("数据源", [s['name'] for s in SITES], default=[s['name'] for s in SITES])
        run = st.button("开始检索", type="primary", use_container_width=True)

    st.markdown('<div class="main-header">Trade Compliance Monitor</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-header">律师专业版 v30.1 | 模式：{report_type}</div>', unsafe_allow_html=True)

    if run:
        st.session_state.results = []
        date_keys, target_date_objs = get_date_keywords(selected_date, report_type, include_tz)
        
        status = st.status("🚀 启动全自动引擎...", expanded=True)
        status.write(f"📅 锁定日期: {[d.strftime('%Y-%m-%d') for d in target_date_objs]}")
        
        # 1. 采集阶段
        all_raw = []
        target_sites = [s for s in SITES if s['name'] in sites_selected]
        
        with ThreadPoolExecutor(max_workers=15) as exe:
            futures = {exe.submit(fetch_links_smart, s): s['name'] for s in target_sites}
            for f in as_completed(futures):
                links = f.result()
                if links:
                    all_raw.extend(links)
                    status.write(f"✅ {futures[f]}: 捕获 {len(links)} 条线索")
        
        # 2. 去重阶段
        unique = []
        seen = set()
        for item in all_raw:
            fingerprint = normalize_title(item[1])
            if len(fingerprint) > 5 and fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(item)
        
        if not unique:
            status.update(label="无结果", state="error")
            st.warning("今日全网无相关更新。")
        else:
            status.write(f"🧠 AI 深度研判中 ({len(unique)} 条任务)... VIP源全量审核，媒体源严格去噪。")
            result_container = st.container()
            
            # 3. 分析阶段
            with ThreadPoolExecutor(max_workers=15) as exe:
                futures = {exe.submit(analyze_article_smart, item, date_keys, target_date_objs): item for item in unique}
                found_count = 0
                
                for f in as_completed(futures):
                    res = f.result()
                    if res:
                        found_count += 1
                        st.session_state.results.append(res)
                        
                        vip_badge = '<span class="vip-badge">CORE</span>' if res['type'] == 'vip' else ''
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
                st.info("已完成全网扫描。AI 判定今日抓取的链接均为旧闻或非实质性内容。")
            else:
                status.update(label=f"✅ 检索完成：锁定 {found_count} 条核心情报", state="complete", expanded=False)

    elif st.session_state.results:
        for res in st.session_state.results:
            vip_badge = '<span class="vip-badge">CORE</span>' if res.get('type') == 'vip' else ''
            st.markdown(f"""
            <div class="result-card">
                <div class="source-tag">{res['source']} {vip_badge}</div>
                <div class="content-text">{res['content']}</div>
                <a href="{res['url']}" target="_blank" class="link-btn">🔗 查看法律原文 (Source) &rarr;</a>
            </div>
            """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
