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

# 媒体源专用关键词 (VIP源全量抓取，不使用此表)
MEDIA_KEYWORDS = [
    "Sanction", "Export Control", "Entity List", "SDN", "Tariff", 
    "Semiconductor", "Chip", "Blacklist", "Laundering", "Ban", "Restriction",
    "制裁", "出口管制", "实体清单", "关税", "半导体", "芯片", "黑名单", "洗钱"
]

# 站点配置
DEFAULT_SITES = [
    # VIP: 核心政府源 (全抓 + 严审)
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 Commerce Press", "url": "https://www.commerce.gov/news/press-releases", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 DOJ Press", "url": "https://www.justice.gov/news/press-releases", "engine": "curl", "type": "vip"},
    {"name": "🇪🇺 EU Council", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "engine": "curl", "type": "vip"},
    {"name": "🇨🇳 MOFCOM (管制局)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "engine": "standard", "type": "vip"},
    {"name": "🇨🇳 外交部发言人", "url": "https://www.fmprc.gov.cn/fyrbt_673021/", "engine": "standard", "type": "vip"},
    {"name": "🇺🇸 BIS Enforcement", "url": "https://www.bis.gov/enforcement/export-violations", "engine": "curl", "type": "vip"},
    
    # Media: 泛读源 (关键词抓 + 严审)
    {"name": "🇬🇧 Reuters (Defense)", "url": "https://www.reuters.com/business/aerospace-defense/", "engine": "curl", "type": "media"},
    {"name": "🏛️ CSIS Analysis", "url": "https://www.csis.org/analysis", "engine": "curl", "type": "media"},
    {"name": "🇺🇸 US Congress", "url": "https://www.congress.gov/search?q=%7B%22source%22%3A%22legislation%22%2C%22congress%22%3A%22118%22%7D", "engine": "curl", "type": "media"},
    {"name": "🇭🇰 SCMP", "url": "https://www.scmp.com/news/china/diplomacy", "engine": "curl", "type": "media"},
    {"name": "📰 Foreign Policy", "url": "https://foreignpolicy.com/latest/", "engine": "curl", "type": "media"}
]

# ================= 🎨 UI 设计 (律所专业版 - 强制清晰) =================

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
    """标题去重指纹：去标点、转小写"""
    return re.sub(r'[^\w\s]', '', title).lower().strip()

def fetch_links_step(site):
    """采集阶段"""
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
                
                # VIP源：全抓
                if site['type'] == 'vip':
                    links.append((site['name'], t, full, site['engine'], site['type']))
                # 媒体源：必须包含强关键词
                else:
                    if any(k.lower() in t.lower() for k in MEDIA_KEYWORDS):
                        links.append((site['name'], t, full, site['engine'], site['type']))
        return links
    except: return []

def analyze_article_final(item, target_date_objs):
    """分析阶段：Prompt 防火墙"""
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
        # 彻底移除干扰项
        for s in soup(["script", "style", "nav", "footer", "aside", "header", "div.related", "div.sidebar"]): s.extract()
        raw_text = soup.get_text(separator="\n", strip=True)[:3500]
        if len(raw_text) < 50: return None

        # 2. 构造日期约束
        date_list_str = ", ".join([d.strftime("%Y-%m-%d") for d in target_date_objs])
        
        # 3. 构造 Prompt
        if site_type == 'vip':
            role_desc = "核心政府网站。只要日期符合，内容涉及政策/执法/声明，一律保留。"
        else:
            role_desc = "媒体新闻。必须与【制裁、实体清单、出口管制】强相关。普通外交/泛政治新闻直接剔除。"

        system_prompt = "你是一个只会输出简报的机器人。不要输出任何问候语，不要输出你的思考过程，不要输出Prompt本身。"
        
        user_prompt = f"""
        任务：贸易合规简报生成。
        
        【步骤1：日期核查】
        目标日期列表：[{date_list_str}]
        请在正文中寻找发布日期（忽略侧边栏/推荐阅读）。
        如果正文日期不在目标列表中，输出 "MISMATCH"。
        
        【步骤2：内容核查】
        {role_desc}
        如果不符，输出 "MISMATCH"。
        
        【步骤3：输出结果】
        如果符合，请严格按以下格式输出（纯文本，无Markdown标记）：
        
        【日期】YYYY-MM-DD
        【标题】(中文翻译)
        【核心事实】(3点摘要)
        【合规提示】(风险建议)
        
        ---
        待分析文本：
        来源：{site_name}
        标题：{title}
        内容：{raw_text}
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
                "temperature": 0.1
            },
            timeout=35
        )
        
        if res.status_code == 200:
            ai_reply = res.json()['choices'][0]['message']['content']
            
            # 后处理：防止 AI 还是输出了 Prompt 或 Markdown
            if "MISMATCH" in ai_reply: return None
            
            # 清洗：去掉 ```markdown, ```, 以及可能包含的 "Here is the report"
            clean_reply = ai_reply.replace("```markdown", "").replace("```", "").strip()
            # 再次确保开头是【日期】
            if "【日期】" not in clean_reply:
                # 如果 AI 没按格式输出，尝试强行提取或丢弃（这里选择返回原始内容，因为内容可能是好的）
                pass
                
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
        with st.expander("➕ 添加数据源"):
            new_name = st.text_input("名称", placeholder="例: US State Dept")
            new_url = st.text_input("URL", placeholder="https://...")
            if st.button("添加"):
                if new_name and new_url:
                    st.session_state.custom_sites.append({"name": new_name, "url": new_url, "engine": "curl", "type": "vip"})
                    st.success("已添加")
        
        st.divider()
        # 源选择
        all_sites = DEFAULT_SITES + st.session_state.custom_sites
        site_names = [s['name'] for s in all_sites]
        sites_selected = st.multiselect("数据源", site_names, default=site_names)
        run = st.button("开始检索", type="primary", use_container_width=True)

    st.markdown('<div class="main-header">Trade Compliance Monitor</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-header">律师专业版 v32.0 (最终交付) | 模式：{report_type}</div>', unsafe_allow_html=True)

    if run:
        st.session_state.results = []
        target_date_objs = get_target_dates(selected_date, report_type, include_tz)
        
        status = st.status("🚀 启动系统...", expanded=True)
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
        
        # 2. 严格去重 (URL + 标题指纹)
        unique = []
        seen_urls = set()
        seen_titles = set()
        
        for item in all_raw:
            title_clean = normalize_title(item[1])
            url = item[2]
            
            if url in seen_urls: continue # URL 重复
            if len(title_clean) > 8 and title_clean in seen_titles: continue # 标题重复
            
            seen_urls.add(url)
            seen_titles.add(title_clean)
            unique.append(item)
        
        dup_count = len(all_raw) - len(unique)
        if dup_count > 0: status.write(f"✂️ 已自动剔除 {dup_count} 条重复/相似内容")

        if not unique:
            status.update(label="无结果", state="error")
            st.warning("未发现线索。请检查日期或添加自定义源。")
        else:
            status.write(f"🧠 AI 深度审核中 ({len(unique)} 条任务)...")
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
                st.info("AI 判定抓取内容均为旧闻或无关。")
            else:
                status.update(label=f"✅ 检索完成：锁定 {found_count} 条情报", state="complete", expanded=False)

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
