import streamlit as st
import requests
import warnings
import time
import re
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

# --- 依赖库检查 ---
try:
    import pypdf
except ImportError:
    st.error("⚠️ 缺少依赖库：请在终端运行 `pip install pypdf` 以支持 PDF 解析。")

try:
    from curl_cffi import requests as c_requests
except ImportError:
    # 降级兼容
    import requests as c_requests

# 屏蔽 SSL 警告
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# ================= ⚙️ 核心配置 =================

SILICON_KEY = "sk-lvnzrlhumujjhpzjkslhhuqjdukioscebcoeuawumtyqoqiz" 
API_URL = "https://api.siliconflow.cn/v1/chat/completions"
MODEL_NAME = "deepseek-ai/DeepSeek-V3"

# 媒体源专用关键词 (VIP源全量抓取，不使用此表)
MEDIA_KEYWORDS = [
    "Sanction", "Export", "Entity List", "SDN", "Tariff", 
    "Semiconductor", "Chip", "Blacklist", "Laundering", "Ban", 
    "Restriction", "Investigation", "China", "Russia", "Iran",
    "Enforcement", "Violation", "Entity", "List",
    "制裁", "出口", "实体清单", "关税", "半导体", "芯片", "黑名单", "洗钱"
]

# 默认站点配置 (VIP与Media分级)
DEFAULT_SITES = [
    # === VIP 核心源 (策略：全抓，不漏任何公告) ===
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 DOJ Press", "url": "https://www.justice.gov/news/press-releases", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 Commerce Press", "url": "https://www.commerce.gov/news/press-releases", "engine": "curl", "type": "vip"},
    {"name": "🇪🇺 EU Council", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "engine": "curl", "type": "vip"},
    {"name": "🇨🇳 MOFCOM (管制局)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "engine": "standard", "type": "vip"},
    {"name": "🇨🇳 外交部发言人", "url": "https://www.fmprc.gov.cn/fyrbt_673021/", "engine": "standard", "type": "vip"},
    {"name": "🇺🇸 BIS Enforcement", "url": "https://www.bis.gov/enforcement/export-violations", "engine": "curl", "type": "vip"},

    # === Media 泛读源 (策略：关键词抓取) ===
    {"name": "🇬🇧 Reuters (Defense)", "url": "https://www.reuters.com/business/aerospace-defense/", "engine": "curl", "type": "media"},
    {"name": "🏛️ CSIS Analysis", "url": "https://www.csis.org/analysis", "engine": "curl", "type": "media"},
    {"name": "🇺🇸 US Congress", "url": "https://www.congress.gov/search?q=%7B%22source%22%3A%22legislation%22%2C%22congress%22%3A%22118%22%7D", "engine": "curl", "type": "media"},
    {"name": "🇭🇰 SCMP", "url": "https://www.scmp.com/news/china/diplomacy", "engine": "curl", "type": "media"},
    {"name": "📰 Foreign Policy", "url": "https://foreignpolicy.com/latest/", "engine": "curl", "type": "media"}
]

# ================= 🎨 UI 设计 (强制高对比度律所风) =================

st.set_page_config(page_title="Trade Compliance Monitor", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    /* 1. 强制全局样式 */
    .stApp { background-color: #ffffff; color: #1a202c; font-family: "Segoe UI", sans-serif; }
    
    /* 2. 标题设计 */
    .main-header {
        font-family: "Times New Roman", "Source Serif Pro", serif;
        color: #0F294D;
        font-size: 2.6rem;
        font-weight: 700;
        margin-bottom: 0.5rem;
        letter-spacing: -0.5px;
    }
    .sub-header {
        font-family: "Arial", sans-serif;
        color: #64748b;
        font-size: 1rem;
        margin-bottom: 2rem;
        border-bottom: 2px solid #f1f5f9;
        padding-bottom: 1rem;
    }

    /* 3. 情报卡片 (核心展示区) */
    .result-card {
        background-color: #F8FAFC !important; /* 极淡蓝灰 */
        border: 1px solid #E2E8F0;
        border-left: 5px solid #1E3A8A; /* 律所深蓝 */
        border-radius: 6px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.03);
        transition: all 0.2s ease;
    }
    .result-card:hover {
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        border-color: #CBD5E1;
    }
    
    /* 4. 标签系统 */
    .source-tag {
        background-color: #E2E8F0;
        color: #334155 !important;
        padding: 4px 10px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 700;
        text-transform: uppercase;
        display: inline-block;
        margin-bottom: 12px;
    }
    .vip-badge {
        background-color: #FEF3C7;
        color: #92400E !important;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.7rem;
        font-weight: 800;
        margin-left: 8px;
        vertical-align: middle;
        border: 1px solid #FDE68A;
    }

    /* 5. 内容排版 */
    .content-text {
        color: #1e293b !important; /* 强制深灰黑 */
        font-size: 15px;
        line-height: 1.7;
        white-space: pre-wrap;
        font-family: "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }

    /* 6. 链接按钮 */
    .link-btn {
        display: inline-block;
        margin-top: 16px;
        color: #2563eb !important;
        font-weight: 600;
        text-decoration: none;
        font-size: 0.9rem;
    }
    .link-btn:hover { text-decoration: underline; }
</style>
""", unsafe_allow_html=True)

# ================= 🧠 核心逻辑 =================

def get_target_dates(selected_date, report_type, include_tz):
    """生成目标日期对象列表"""
    dates = [selected_date]
    if report_type == "日报" and include_tz: dates.append(selected_date - timedelta(days=1))
    elif report_type == "周报": dates = [selected_date - timedelta(days=i) for i in range(8)]
    elif report_type == "月报": dates = [selected_date - timedelta(days=i) for i in range(31)]
    return dates

def get_date_keywords_list(target_dates):
    """生成用于 Python 初筛的日期字符串列表"""
    keywords = []
    for d in target_dates:
        y, m_full, m_abbr, d_str, d_pad = str(d.year), d.strftime("%B"), d.strftime("%b"), str(d.day), d.strftime("%d")
        # 覆盖所有常见格式
        keywords.extend([
            f"{m_full} {d_str}, {y}", f"{m_abbr} {d_str}, {y}", f"{m_abbr}. {d_str}, {y}", # Nov 21, 2025
            f"{y}-{m_abbr}-{d_pad}", f"{y}/{d.strftime('%m')}/{d_pad}", f"{y}-{d.strftime('%m')}-{d_pad}", # 2025-11-21
            f"{d_str} {m_full} {y}", d.strftime("%m/%d/%Y") # 21 November 2025, 11/21/2025
        ])
    return list(set(keywords))

def normalize_title(title):
    """去重指纹：只保留字母数字，转小写"""
    return re.sub(r'[^\w\s]', '', title).lower().strip()

def extract_pdf_text(content):
    """解析 PDF"""
    try:
        import pypdf
        with io.BytesIO(content) as f:
            reader = pypdf.PdfReader(f)
            text = ""
            for i in range(min(5, len(reader.pages))): # 只读前5页
                text += reader.pages[i].extract_text() + "\n"
            return text
    except: return ""

def fetch_links_step(site):
    """
    步骤1: 采集
    - VIP: 抓取所有看似新闻的链接 (标题长度>5 或 PDF)
    - Media: 仅抓取包含关键词的链接
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
        html = ""
        
        # 引擎选择
        if site['engine'] == "standard":
            resp = requests.get(site['url'], headers=headers, timeout=15, verify=False)
            if "mofcom" in site['url']: resp.encoding = "gbk" if "gbk" in resp.text.lower() else "utf-8"
            html = resp.text
        else:
            for fp in ["chrome120", "safari15_3"]:
                try:
                    resp = c_requests.get(site['url'], impersonate=fp, timeout=20)
                    html = resp.text
                    break
                except: continue
        
        if not html: return []
        soup = BeautifulSoup(html, 'html.parser')
        links = []
        
        for a in soup.find_all('a'):
            t, h = a.get_text(strip=True), a.get('href')
            
            if h and "javascript" not in h and "mailto" not in h:
                full = urljoin(site['url'], h)
                is_pdf = h.lower().endswith('.pdf')
                
                # === 核心分级抓取逻辑 ===
                if site['type'] == 'vip':
                    # VIP: 只要标题有长度，或者链接是PDF，就抓 (不漏OFAC)
                    if len(t) > 5 or is_pdf:
                        links.append((site['name'], t if t else "PDF Document", full, site['engine'], site['type']))
                else:
                    # Media: 必须包含关键词，且标题够长
                    if len(t) > 10 and any(k.lower() in t.lower() for k in MEDIA_KEYWORDS):
                        links.append((site['name'], t, full, site['engine'], site['type']))
        return links
    except: return []

def analyze_article_final(item, date_keywords, target_date_objs):
    """
    步骤2: 深度分析 (PDF + HTML)
    """
    site_name, title, url, engine, site_type = item
    try:
        raw_text = ""
        is_pdf = url.lower().endswith(".pdf")
        
        # --- 下载 ---
        if engine == "standard":
            r = requests.get(url, headers={"User-Agent": "Chrome/120.0"}, timeout=15, verify=False)
            if not is_pdf and "mofcom" in url: r.encoding = "gbk"
        else:
            r = c_requests.get(url, impersonate="chrome120", timeout=20)
        
        # --- 提取 ---
        if is_pdf:
            raw_text = extract_pdf_text(r.content)
        else:
            # Python日期初筛 (仅针对非PDF的网页，提高速度)
            # 如果网页里连个日期字符串都没有，直接跳过 (除非是VIP源，VIP源强制AI审)
            match_hit = False
            if site_type == 'vip': 
                match_hit = True # VIP不进行Python初筛，防止漏
            else:
                for dk in date_keywords:
                    if dk in r.text:
                        match_hit = True
                        break
            
            if not match_hit: return None

            soup = BeautifulSoup(r.text, 'html.parser')
            for s in soup(["script", "style", "nav", "footer", "aside", "header", "div.related"]): s.extract()
            raw_text = soup.get_text(separator="\n", strip=True)[:4500]

        if len(raw_text) < 50: return None

        # --- AI 审核 ---
        date_range_str = ", ".join([d.strftime("%Y-%m-%d") for d in target_date_objs])
        
        if site_type == 'vip':
            role_desc = "核心监管机构。只要日期符合，无论是法规更新、执法行动还是声明，必须保留。"
        else:
            role_desc = "媒体新闻。必须与【制裁、出口管制、实体清单】强相关。如果是普通外交/泛政治新闻，返回 MISMATCH。"

        prompt = f"""
        你是一名专业的贸易合规情报员。
        
        【任务一：日期核查】
        目标范围：{date_range_str}
        请在正文中寻找发布日期。⚠️ 忽略侧边栏推荐或页脚日期。
        如果正文日期不在目标范围内，返回 "MISMATCH"。
        
        【任务二：内容核查】
        {role_desc}
        如果不符，返回 "MISMATCH"。
        
        【任务三：输出简报】
        如果符合，请输出纯文本内容（严禁Markdown代码块，严禁输出Prompt本身）：
        
        【日期】YYYY-MM-DD
        【标题】(中文翻译)
        【核心事实】(3点摘要)
        【合规提示】(风险建议)
        
        原文片段：
        来源：{site_name}
        标题：{title}
        正文：{raw_text}
        """
        
        res = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {SILICON_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=35
        )
        
        if res.status_code == 200:
            content = res.json()['choices'][0]['message']['content']
            if "MISMATCH" in content: return None
            
            # 最终清洗：去掉 AI 可能带出的 markdown 标记
            clean_content = content.replace("```markdown", "").replace("```", "").strip()
            return {"source": site_name, "url": url, "content": clean_content, "type": site_type}
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
        with st.expander("➕ 添加数据源"):
            new_name = st.text_input("名称", placeholder="例: US Dept of State")
            new_url = st.text_input("URL", placeholder="https://...")
            new_type = st.selectbox("类型", ["vip (全量抓)", "media (严格抓)"])
            if st.button("添加"):
                if new_name and new_url:
                    st.session_state.custom_sites.append({
                        "name": new_name, "url": new_url, 
                        "engine": "curl", "type": "vip" if "vip" in new_type else "media"
                    })
                    st.success("已添加")
        
        st.divider()
        all_sites = DEFAULT_SITES + st.session_state.custom_sites
        site_names = [s['name'] for s in all_sites]
        sites_selected = st.multiselect("数据源", site_names, default=site_names)
        run = st.button("开始检索", type="primary", use_container_width=True)

    st.markdown('<div class="main-header">Trade Compliance Monitor</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-header">律师专业版 v35.0 (最终交付) | 模式：{report_type}</div>', unsafe_allow_html=True)

    if run:
        st.session_state.results = []
        target_date_objs = get_target_dates(selected_date, report_type, include_tz)
        date_keywords_list = get_date_keywords_list(target_date_objs)
        
        status = st.status("🚀 启动全能引擎...", expanded=True)
        status.write(f"📅 锁定日期: {[d.strftime('%Y-%m-%d') for d in target_date_objs]}")
        
        # 1. 采集 (15线程)
        all_raw = []
        target_sites_objs = [s for s in all_sites if s['name'] in sites_selected]
        
        with ThreadPoolExecutor(max_workers=15) as exe:
            futures = {exe.submit(fetch_links_step, s): s['name'] for s in target_sites_objs}
            for f in as_completed(futures):
                links = f.result()
                if links:
                    all_raw.extend(links)
                    status.write(f"✅ {futures[f]}: 捕获 {len(links)} 条线索")
        
        # 2. 去重 (URL + 标题指纹)
        unique = []
        seen_urls = set()
        seen_titles = set()
        
        for item in all_raw:
            title_clean = normalize_title(item[1])
            url = item[2]
            if url in seen_urls: continue
            # 如果标题不是 "PDF Document" (这种标题没法去重)，且重复了，则跳过
            if "pdf document" not in title_clean and len(title_clean) > 8 and title_clean in seen_titles: continue
            
            seen_urls.add(url)
            seen_titles.add(title_clean)
            unique.append(item)
        
        dup_cnt = len(all_raw) - len(unique)
        if dup_cnt > 0: status.write(f"✂️ 已剔除 {dup_cnt} 条重复内容")

        if not unique:
            status.update(label="无结果", state="error")
            st.warning("今日全网无相关更新。")
        else:
            status.write(f"🧠 AI 深度研判中 ({len(unique)} 条任务)... VIP全量审，Media去噪。")
            result_container = st.container()
            
            # 3. 分析 (15线程)
            with ThreadPoolExecutor(max_workers=15) as exe:
                futures = {exe.submit(analyze_article_final, item, date_keywords_list, target_date_objs): item for item in unique}
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
                st.info("经 AI 核查，所有线索均为非目标日期旧闻或无关内容。")
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
