import streamlit as st
import requests
import warnings
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse
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

# 媒体源专用关键词
MEDIA_KEYWORDS = [
    "Sanction", "Export Control", "Entity List", "SDN", "Tariff", 
    "Semiconductor", "Chip", "Blacklist", "Laundering", "Ban",
    "制裁", "出口管制", "实体清单", "关税", "半导体", "芯片", "黑名单"
]

# 默认预设站点
DEFAULT_SITES = [
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 Commerce Press", "url": "https://www.commerce.gov/news/press-releases", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 DOJ Press", "url": "https://www.justice.gov/news/press-releases", "engine": "curl", "type": "vip"},
    {"name": "🇪🇺 EU Council", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "engine": "curl", "type": "vip"},
    {"name": "🇨🇳 MOFCOM (管制局)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "engine": "standard", "type": "vip"},
    {"name": "🇨🇳 外交部发言人", "url": "https://www.fmprc.gov.cn/fyrbt_673021/", "engine": "standard", "type": "vip"},
    {"name": "🇺🇸 BIS Enforcement", "url": "https://www.bis.gov/enforcement/export-violations", "engine": "curl", "type": "vip"},
    {"name": "🇬🇧 Reuters (Defense)", "url": "https://www.reuters.com/business/aerospace-defense/", "engine": "curl", "type": "media"},
    {"name": "🏛️ CSIS Analysis", "url": "https://www.csis.org/analysis", "engine": "curl", "type": "media"},
    {"name": "🇺🇸 US Congress", "url": "https://www.congress.gov/search?q=%7B%22source%22%3A%22legislation%22%2C%22congress%22%3A%22118%22%7D", "engine": "curl", "type": "media"},
    {"name": "🇭🇰 SCMP", "url": "https://www.scmp.com/news/china/diplomacy", "engine": "curl", "type": "media"},
    {"name": "📰 Foreign Policy", "url": "https://foreignpolicy.com/latest/", "engine": "curl", "type": "media"}
]

# ================= 🎨 UI 设计 (律所专业版) =================

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

# ================= 🧠 逻辑层 =================

def get_date_keywords(selected_date, report_type, include_tz):
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
    """智能采集：根据站点类型决定抓取策略"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
        html = ""
        
        # 引擎选择
        if site['engine'] == "standard":
            resp = requests.get(site['url'], headers=headers, timeout=12, verify=False)
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
            if t and len(t)>8 and h and "javascript" not in h:
                full = urljoin(site['url'], h)
                
                # VIP源：全抓，不做关键词过滤
                if site['type'] == 'vip':
                    links.append((site['name'], t, full, site['engine'], site['type']))
                # 媒体源：必须包含强关键词
                else:
                    if any(k.lower() in t.lower() for k in MEDIA_KEYWORDS):
                        links.append((site['name'], t, full, site['engine'], site['type']))
        return links
    except: return []

def analyze_article_smart(item, date_keywords, target_date_objs):
    """智能分析：AI 终审"""
    site_name, title, url, engine, site_type = item
    try:
        txt = ""
        if engine == "standard":
            r = requests.get(url, headers={"User-Agent": "Chrome/120.0"}, timeout=10, verify=False)
            if "mofcom" in url: r.encoding = "gbk"
            txt = r.text
        else:
            r = c_requests.get(url, impersonate="chrome120", timeout=10)
            txt = r.text
        
        # 1. Python 日期初筛 (提高效率)
        match_hit = False
        for dk in date_keywords:
            if dk in txt:
                match_hit = True
                break
        if not match_hit: return None

        # 2. 提取正文
        soup = BeautifulSoup(txt, 'html.parser')
        for s in soup(["script", "style", "nav", "footer", "aside"]): s.extract()
        raw_text = soup.get_text(separator="\n", strip=True)[:4000]
        if len(raw_text) < 50: return None

        # 3. AI 终审
        date_range_str = ", ".join([d.strftime("%Y-%m-%d") for d in target_date_objs])
        
        # 针对 VIP 源，Prompt 更加宽容
        if site_type == 'vip':
            context_instruction = "这是核心监管机构。只要日期符合，且内容不是放假通知或招聘，**一律保留**。请特别留意那些标题不含'制裁'但内容涉及规则修订的公告。"
        else:
            context_instruction = "这是媒体新闻。必须与'制裁/出口管制/实体清单'强相关。如果是普通外交访问，返回 MISMATCH。"

        prompt = f"""
        你是一名贸易合规情报专家。
        
        【任务一：日期核查】
        目标范围：{date_range_str}
        请在正文中寻找发布日期。⚠️忽略侧边栏或推荐阅读的日期。
        如果正文日期不在范围内，返回 "MISMATCH"。
        
        【任务二：内容核查】
        文章标题：{title}
        {context_instruction}
        
        【任务三：生成简报】
        如果符合，请输出简报（不要Markdown标记）：
        **【标题】**：(中文翻译)
        **【核心事实】**：(3点摘要)
        **【合规提示】**：(风险建议)
        
        正文摘要：{raw_text}
        """
        
        res = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {SILICON_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=35
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
    # 初始化 Session State
    if 'results' not in st.session_state: st.session_state.results = []
    # 这里的 custom_sites 用于存储用户添加的源
    if 'custom_sites' not in st.session_state: st.session_state.custom_sites = []

    with st.sidebar:
        st.header("⚖️ 控制台")
        report_type = st.selectbox("报告周期", ["日报", "周报", "月报"])
        selected_date = st.date_input("基准日期", datetime.now())
        include_tz = st.checkbox("包含时差 (推荐)", value=True)
        
        st.divider()
        
        # === 新增：自定义数据源功能 ===
        with st.expander("➕ 添加数据源 (Custom Source)"):
            new_name = st.text_input("网站名称", placeholder="例如: US State Dept")
            new_url = st.text_input("网址 URL", placeholder="https://...")
            new_type = st.selectbox("类型", ["vip (全量抓取)", "media (关键词过滤)"], index=0)
            
            if st.button("添加源"):
                if new_name and new_url:
                    # 解析类型
                    site_type_val = "vip" if "vip" in new_type else "media"
                    # 默认使用 curl 引擎 (更强)
                    new_site = {"name": new_name, "url": new_url, "engine": "curl", "type": site_type_val}
                    st.session_state.custom_sites.append(new_site)
                    st.success(f"已添加: {new_name}")
                else:
                    st.error("请填写名称和 URL")
            
            # 显示已添加的源
            if st.session_state.custom_sites:
                st.caption(f"已添加 {len(st.session_state.custom_sites)} 个自定义源")
                if st.button("清除自定义源"):
                    st.session_state.custom_sites = []
                    st.rerun()

        st.divider()
        
        # 合并默认源和自定义源
        all_sites = DEFAULT_SITES + st.session_state.custom_sites
        site_names = [s['name'] for s in all_sites]
        
        sites_selected = st.multiselect("数据源选择", site_names, default=site_names)
        run = st.button("开始检索", type="primary", use_container_width=True)

    st.markdown('<div class="main-header">Trade Compliance Monitor</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-header">律师专业版 v31.0 (开放生态) | 模式：{report_type}</div>', unsafe_allow_html=True)

    if run:
        st.session_state.results = []
        date_keys, target_date_objs = get_date_keywords(selected_date, report_type, include_tz)
        
        status = st.status("🚀 启动全网扫描...", expanded=True)
        status.write(f"📅 锁定日期: {[d.strftime('%Y-%m-%d') for d in target_date_objs]}")
        
        all_raw = []
        # 根据用户选择过滤站点对象
        target_sites_objs = [s for s in all_sites if s['name'] in sites_selected]
        
        # 1. 并发采集
        with ThreadPoolExecutor(max_workers=15) as exe:
            futures = {exe.submit(fetch_links_smart, s): s['name'] for s in target_sites_objs}
            for f in as_completed(futures):
                links = f.result()
                if links:
                    all_raw.extend(links)
                    status.write(f"✅ {futures[f]}: 捕获 {len(links)} 条线索")
        
        # 2. 智能去重
        unique = []
        seen = set()
        for item in all_raw:
            fingerprint = normalize_title(item[1])
            if len(fingerprint) > 5 and fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(item)
        
        if not unique:
            status.update(label="无结果", state="error")
            st.warning("今日无相关更新。如果确认有遗漏，请尝试在左侧【添加数据源】中手动输入具体网址。")
        else:
            status.write(f"🧠 AI 深度研判中 ({len(unique)} 条任务)... VIP源全量审核，媒体源去噪。")
            result_container = st.container()
            
            # 3. 并发分析
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
                st.info("已完成扫描。AI 判定今日抓取的链接均为旧闻或非实质性内容。")
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
