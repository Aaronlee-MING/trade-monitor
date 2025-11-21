import streamlit as st
import requests
import warnings
import time
import re
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse
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

# --- 策略调整：扩充媒体关键词（不再过于严格，防止漏抓） ---
MEDIA_KEYWORDS = [
    "Sanction", "Export", "Import", "Trade", "Entity List", "Tariff", 
    "Semiconductor", "Chip", "Tech", "Supply Chain", "Investment", 
    "Blacklist", "Laundering", "Ban", "Restriction", "Investigation",
    "Enforcement", "Violation", "China", "Russia", "Policy",
    "制裁", "出口", "进口", "贸易", "清单", "关税", "芯片", "半导体", "供应链"
]

# 站点配置
DEFAULT_SITES = [
    # VIP: 核心政府源 (全量抓取 + AI 去除非业务信息)
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 Commerce Press", "url": "https://www.commerce.gov/news/press-releases", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 DOJ Press", "url": "https://www.justice.gov/news/press-releases", "engine": "curl", "type": "vip"},
    {"name": "🇪🇺 EU Council", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "engine": "curl", "type": "vip"},
    {"name": "🇨🇳 MOFCOM (管制局)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "engine": "standard", "type": "vip"},
    {"name": "🇨🇳 外交部发言人", "url": "https://www.fmprc.gov.cn/fyrbt_673021/", "engine": "standard", "type": "vip"},
    {"name": "🇺🇸 BIS Enforcement", "url": "https://www.bis.gov/enforcement/export-violations", "engine": "curl", "type": "vip"},
    
    # Media: 泛读源 (扩充关键词抓取 + AI 严格关联度审核)
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
    /* 1. 全局 - 强制白底黑字 */
    .stApp { background-color: #ffffff; color: #1a202c; }
    
    /* 2. 标题 - 衬线体 */
    .main-header { font-family: "Source Serif Pro", "Georgia", serif; color: #0F294D; font-size: 2.4rem; font-weight: 700; margin-bottom: 0.5rem; }
    .sub-header { font-family: "Helvetica Neue", sans-serif; color: #64748b; font-size: 1rem; margin-bottom: 2.5rem; border-bottom: 1px solid #e2e8f0; padding-bottom: 1rem; }

    /* 3. 卡片 - 强制配色 */
    .result-card { background-color: #F7F9FB !important; border: 1px solid #E2E8F0; border-left: 5px solid #0F294D; border-radius: 8px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.02); }
    
    /* 4. 标签 */
    .source-tag { background-color: #E2E8F0; color: #1e293b !important; padding: 4px 10px; border-radius: 4px; font-size: 0.75rem; font-weight: 700; display: inline-block; margin-bottom: 14px; }
    .vip-badge { background-color: #FEF3C7; color: #92400E !important; padding: 2px 6px; border-radius: 4px; font-size: 0.7rem; font-weight: 800; margin-left: 8px; vertical-align: middle; }

    /* 5. 正文 - 强制深黑 */
    .content-text { color: #1A202C !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 15px; line-height: 1.7; white-space: pre-wrap; }

    /* 6. 链接 */
    .link-btn { display: inline-block; margin-top: 18px; color: #2563eb !important; font-weight: 600; text-decoration: none; font-size: 0.9rem; border-bottom: 1px dotted #2563eb; }
    .link-btn:hover { border-bottom: 1px solid #2563eb; }
</style>
""", unsafe_allow_html=True)

# ================= 🧠 核心逻辑 =================

def get_target_dates(selected_date, report_type, include_tz):
    dates = [selected_date]
    if report_type == "日报" and include_tz: dates.append(selected_date - timedelta(days=1))
    elif report_type == "周报": dates = [selected_date - timedelta(days=i) for i in range(8)]
    elif report_type == "月报": dates = [selected_date - timedelta(days=i) for i in range(31)]
    return dates

def normalize_title(title):
    """标题指纹去重：去除标点、转小写、去除首尾空格"""
    return re.sub(r'[^\w\s]', '', title).lower().strip()

def fetch_links_step(site):
    """
    步骤1: 采集
    VIP: 抓取所有链接 (不漏)
    Media: 抓取包含扩充关键词的链接 (不过度严格)
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
        html = ""
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
                
                # VIP源：全量抓取，后续由 AI 判断是否为“行政噪音”
                if site['type'] == 'vip':
                    links.append((site['name'], t, full, site['engine'], site['type']))
                # Media源：使用扩充后的关键词库
                else:
                    if any(k.lower() in t.lower() for k in MEDIA_KEYWORDS):
                        links.append((site['name'], t, full, site['engine'], site['type']))
        return links
    except: return []

def analyze_article_final(item, target_date_objs):
    """
    步骤2: AI 深度审核
    功能：日期核查 + 内容相关性核查 + 格式化输出
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
        
        soup = BeautifulSoup(txt, 'html.parser')
        # 强力清洗：去除侧边栏、推荐阅读、页脚，防止日期混淆
        for s in soup(["script", "style", "nav", "footer", "aside", "header", "div.related", "div.sidebar", "div.menu"]): s.extract()
        raw_text = soup.get_text(separator="\n", strip=True)[:4000]
        if len(raw_text) < 50: return None

        # 2. 构造日期约束
        date_range_str = ", ".join([d.strftime("%Y-%m-%d") for d in target_date_objs])
        
        # 3. 构造分级 Prompt
        if site_type == 'vip':
            # VIP 策略：去除行政噪音
            role_desc = """
            这是核心政府/监管网站。
            【保留】：法规更新、制裁行动、官方声明、政策解读。
            【剔除】：网站维护通知、休假通知、招聘信息、无实质内容的会议议程。
            只要是实质性内容，必须保留。
            """
        else:
            # Media 策略：相关性清洗
            role_desc = """
            这是大众媒体新闻。
            【保留】：涉及“制裁、实体清单、出口管制、关税、供应链禁令”的实质性报道。
            【剔除】：普通的外交辞令、泛政治新闻、无贸易背景的军事冲突。
            必须强相关。
            """

        system_prompt = "你是一个只输出最终简报的机器人。严禁输出思考过程，严禁输出Prompt本身，严禁输出Markdown代码块标记。"
        
        user_prompt = f"""
        任务：贸易合规简报生成。
        
        【步骤1：日期核查 (Critical)】
        目标日期列表：[{date_range_str}]
        请仔细在正文中寻找发布日期 (Published/Updated Date)。
        ⚠️ 忽略“推荐阅读”或页脚版权信息中的日期。
        如果正文日期不在目标列表中，直接输出 "MISMATCH"。
        
        【步骤2：内容核查】
        文章标题：{title}
        {role_desc}
        如果内容属于【剔除】类别，直接输出 "MISMATCH"。
        
        【步骤3：输出结果】
        如果符合要求，请严格按以下格式输出纯文本：
        
        【日期】YYYY-MM-DD
        【标题】(中文翻译)
        【核心事实】(3点摘要，客观陈述)
        【合规提示】(针对企业的风险建议)
        
        ---
        待分析文本：
        来源：{site_name}
        标题：{title}
        内容摘要：{raw_text}
        """
        
        res = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {SILICON_KEY}", "Content-Type": "application/json"},
            json={
                "model": MODEL_NAME, 
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ], 
                "temperature": 0.1 # 低温，保证严谨
            },
            timeout=35
        )
        
        if res.status_code == 200:
            ai_reply = res.json()['choices'][0]['message']['content']
            
            # 后处理：防止 AI 幻觉或格式错误
            if "MISMATCH" in ai_reply or len(ai_reply) < 10: return None
            
            # 清洗：去掉 ```markdown, ```, 以及可能的 "Here is the summary"
            # 使用正则只保留 【日期】... 之后的内容
            clean_reply = ai_reply.replace("```markdown", "").replace("```", "").strip()
            
            return {"source": site_name, "url": url, "content": clean_reply, "type": site_type}
    except: pass
    return None

# ================= 🖥️ 主界面 =================

def main():
    if 'results' not in st.session_state: st.session_state.results = []
    if 'custom_sites' not in st.session_state: st.session_state.custom_sites = []

    with st.sidebar:
        st.header("⚖️ 控制台")
        report_type = st.selectbox("报告周期", ["日报", "周报", "月报"])
        selected_date = st.date_input("基准日期", datetime.now())
        include_tz = st.checkbox("包含时差 (推荐)", value=True)
        
        st.divider()
        # 自定义源
        with st.expander("➕ 添加自定义源"):
            new_name = st.text_input("名称", placeholder="例: EU Commission")
            new_url = st.text_input("URL", placeholder="https://...")
            new_type = st.selectbox("类型", ["vip (全量审)", "media (关键词审)"])
            if st.button("添加"):
                if new_name and new_url:
                    st.session_state.custom_sites.append({
                        "name": new_name, "url": new_url, 
                        "engine": "curl", "type": "vip" if "vip" in new_type else "media"
                    })
                    st.success("已添加")
        
        st.divider()
        # 源选择
        all_sites = DEFAULT_SITES + st.session_state.custom_sites
        site_names = [s['name'] for s in all_sites]
        sites_selected = st.multiselect("数据源", site_names, default=site_names)
        run = st.button("开始检索", type="primary", use_container_width=True)

    st.markdown('<div class="main-header">Trade Compliance Monitor</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-header">律师专业版 v33.0 (平衡智能版) | 模式：{report_type}</div>', unsafe_allow_html=True)

    if run:
        st.session_state.results = []
        target_date_objs = get_target_dates(selected_date, report_type, include_tz)
        
        status = st.status("🚀 启动系统...", expanded=True)
        status.write(f"📅 锁定日期: {[d.strftime('%Y-%m-%d') for d in target_date_objs]}")
        
        # 1. 采集 (15线程高并发)
        all_raw = []
        target_sites_objs = [s for s in all_sites if s['name'] in sites_selected]
        
        with ThreadPoolExecutor(max_workers=15) as exe:
            futures = {exe.submit(fetch_links_step, s): s['name'] for s in target_sites_objs}
            for f in as_completed(futures):
                links = f.result()
                if links:
                    all_raw.extend(links)
                    status.write(f"✅ {futures[f]}: 捕获 {len(links)} 条潜在线索")
        
        # 2. 全局去重 (URL + 标题指纹)
        unique = []
        seen_urls = set()
        seen_titles = set()
        
        for item in all_raw:
            # item: (site, title, url, engine, type)
            title_clean = normalize_title(item[1])
            url = item[2]
            
            if url in seen_urls: continue
            # 标题指纹去重：防止 DOJ 这种同一内容发多条链接的情况
            if len(title_clean) > 8 and title_clean in seen_titles: continue
            
            seen_urls.add(url)
            seen_titles.add(title_clean)
            unique.append(item)
        
        dup_count = len(all_raw) - len(unique)
        if dup_count > 0: status.write(f"✂️ 已剔除 {dup_count} 条重复内容")

        if not unique:
            status.update(label="无结果", state="error")
            st.warning("今日无相关更新。")
        else:
            status.write(f"🧠 AI 深度核查中 ({len(unique)} 条任务)...")
            result_container = st.container()
            
            # 3. 分析 (15线程)
            with ThreadPoolExecutor(max_workers=15) as exe:
                futures = {exe.submit(analyze_article_final, item, target_date_objs): item for item in unique}
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
                st.info("经 AI 核查，线索均为旧闻、重复或非实质性内容（如放假通知/无关外交新闻）。")
            else:
                status.update(label=f"✅ 检索完成：锁定 {found_count} 条高质量情报", state="complete", expanded=False)

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
