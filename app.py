import streamlit as st
import requests
import warnings
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

# 尝试引入穿墙库
try:
    from curl_cffi import requests as c_requests
    HAS_CURL = True
except ImportError:
    HAS_CURL = False
    import requests as c_requests

# 忽略 SSL 警告
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# ================= ⚙️ 核心配置 (保持不变) =================

SILICON_KEY = "sk-lvnzrlhumujjhpzjkslhhuqjdukioscebcoeuawumtyqoqiz" 
API_URL = "https://api.siliconflow.cn/v1/chat/completions"

# 关键词库
KEYWORDS = [
    "Sanction", "Entity", "Trade", "Export", "Import", "Tariff", "Supply Chain", 
    "China", "Russia", "Technology", "Chip", "Ban", "Restriction", "Investment",
    "Designation", "Blocked", "SDN", "WRO", # 增加 OFAC 特有词汇
    "制裁", "实体", "贸易", "出口", "进口", "关税", "供应链", "芯片", "半导体"
]

SITES = [
    # VIP 源：必须全量抓取
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "type": "vip"},
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "type": "vip"}, # 重点关注
    {"name": "🇨🇳 MOFCOM (管制局)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "type": "vip"},
    {"name": "🇺🇸 DOJ Press", "url": "https://www.justice.gov/news/press-releases", "type": "vip"},
    
    # 媒体源
    {"name": "🇬🇧 Reuters Defense", "url": "https://www.reuters.com/business/aerospace-defense/", "type": "media"},
    {"name": "🏛️ CSIS Analysis", "url": "https://www.csis.org/analysis", "type": "media"},
]

# ================= 🎨 UI 设计 (高对比度纯黑版) =================

st.set_page_config(page_title="Trade Monitor Pro", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    /* 1. 强制全局纯白背景，纯黑字体 */
    .stApp { background-color: #ffffff !important; color: #000000 !important; }
    p, div, span, label, h1, h2, h3 { color: #000000 !important; }
    
    /* 2. 标题加粗加黑 */
    .main-header { 
        font-family: "Arial Black", sans-serif; 
        font-size: 2.2rem; 
        border-bottom: 4px solid #000000; 
        padding-bottom: 10px; 
        margin-bottom: 20px;
    }
    
    /* 3. 结果卡片：高对比度黑白边框 */
    .result-card { 
        background: #ffffff; 
        padding: 24px; 
        border: 2px solid #000000; /* 纯黑边框 */
        border-radius: 0px; /* 直角，更严谨 */
        margin-bottom: 20px; 
        box-shadow: 4px 4px 0px #000000; /* 复古黑影风格 */
    }
    
    /* 4. 标签体系 */
    .tag { 
        font-size: 14px; 
        padding: 4px 8px; 
        font-weight: 900; 
        color: #ffffff !important; 
        display: inline-block;
        margin-right: 10px;
    }
    .tag-vip { background-color: #000000; } /* 黑底白字 */
    .tag-media { background-color: #555555; } /* 灰底白字 */
    
    .date-badge {
        font-weight: bold;
        border-bottom: 2px solid #000000;
        margin-left: 10px;
    }

    /* 5. 链接 */
    a { color: #0000EE !important; text-decoration: underline; font-weight: bold; }
    
    /* 6. 调试日志 */
    .debug-log { font-family: "Courier New", monospace; font-size: 12px; color: #333 !important; border-left: 3px solid #000; padding-left: 10px; }
</style>
""", unsafe_allow_html=True)

# ================= 🛠️ 工具函数 =================

def extract_date_from_url(url):
    """
    策略修正：URL 往往比网页内容更诚实。
    OFAC 和 DOJ 的 URL 通常包含 YYYY-MM-DD 或 YYYY/MM/DD。
    """
    patterns = [
        r'/(\d{4})-(\d{2})-(\d{2})/', # /2024-11-19/
        r'/(\d{4})/(\d{2})/(\d{2})/', # /2024/11/19/
        r'-(\d{4})-(\d{2})-(\d{2})',  # -2024-11-19
    ]
    for p in patterns:
        match = re.search(p, url)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None

def get_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

# ================= 🕷️ 抓取逻辑 (OFAC 增强版) =================

def fetch_site_links(site):
    links = []
    debug_info = []
    try:
        # 针对 OFAC 使用更强的伪装
        impersonate_ver = "chrome110"
        
        if HAS_CURL and "mofcom" not in site['url']:
            resp = c_requests.get(site['url'], impersonate=impersonate_ver, timeout=20)
        else:
            resp = requests.get(site['url'], headers=get_headers(), timeout=20, verify=False)
            if "mofcom" in site['url']: resp.encoding = "gbk"
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # 链接提取逻辑
        seen = set()
        count = 0
        
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            href = a['href']
            url = urljoin(site['url'], href)
            
            # 基础过滤
            if len(text) < 5 or "javascript" in href: continue
            if url in seen: continue
            
            # 命中判断
            is_hit = False
            if site['type'] == 'vip':
                is_hit = True # VIP 全都要
            elif any(k.lower() in text.lower() for k in KEYWORDS):
                is_hit = True
            
            if is_hit:
                # 🛠️ 补丁：如果 URL 里包含 target date，直接加分
                url_date = extract_date_from_url(url)
                
                links.append({
                    "source": site['name'], 
                    "title": text, 
                    "url": url, 
                    "type": site['type'],
                    "url_date": url_date # 将 URL 里的日期传递给下一步
                })
                seen.add(url)
                count += 1
                
        debug_info.append(f"✅ Success: Found {count} links")
    except Exception as e:
        debug_info.append(f"❌ Error: {str(e)}")
    
    return links, debug_info

# ================= 🧠 AI 分析 (时区宽松版) =================

def analyze_link(item, target_date_obj):
    try:
        # 1. 获取正文
        try:
            if HAS_CURL:
                r = c_requests.get(item['url'], impersonate="chrome110", timeout=15)
            else:
                r = requests.get(item['url'], headers=get_headers(), timeout=15, verify=False)
            
            if "mofcom" in item['url']: r.encoding = "gbk"
            
            # 扩大抓取范围到 6000 字符，防止漏掉底部的日期
            text = BeautifulSoup(r.text, 'html.parser').get_text(separator="\n", strip=True)[:6000]
        except:
            return None

        # 2. 构造 Prompt
        target_str = target_date_obj.strftime("%Y-%m-%d")
        
        # 强力 Prompt：结合 URL 日期 + 内容日期 + 宽松时区
        prompt = f"""
        Target Date: {target_str}
        Current URL Date Clue: {item.get('url_date', 'None')}
        
        Task: Determine if this news is relevant to the Target Date (+/- 2 days buffer for timezone).
        
        CRITICAL RULES:
        1. IF the URL contains the date "{target_str}" (or close), IT IS A MATCH. TRUST THE URL.
        2. IF the text mentions "{target_date_obj.strftime('%B %d')}" (e.g. November 19) or "{target_date_obj.strftime('%m/%d')}", IT IS A MATCH.
        3. IGNORE website footers (copyright 2024). Look for "Press Release", "For Immediate Release", or dates near the title.
        4. TOPIC: Must be Trade/Sanctions/Export/Tariff related.

        Respond in JSON ONLY:
        {{
            "match": true/false,
            "reason": "Found date in URL/Found date in text/Date mismatch",
            "summary": "Chinese summary of the event",
            "action_required": "Key takeaway for compliance officer"
        }}
        
        Title: {item['title']}
        Text: {text[:2000]}...
        """

        res = requests.post(
            API_URL, 
            json={
                "model": "deepseek-ai/DeepSeek-V3",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}
            }, 
            headers={"Authorization": f"Bearer {SILICON_KEY}"},
            timeout=30
        )
        
        ai_data = json.loads(res.json()['choices'][0]['message']['content'])
        
        if ai_data.get("match"):
            return {
                "source": item['source'],
                "title": item['title'],
                "url": item['url'],
                "type": item['type'],
                "summary": ai_data.get("summary", "No summary"),
                "risk": ai_data.get("action_required", "Check details")
            }
    except:
        pass
    return None

# ================= 🖥️ 主程序 =================

def main():
    st.markdown('<div class="main-header">TRADE SANCTION MONITOR (HIGH CONTRAST)</div>', unsafe_allow_html=True)
    
    col1, col2 = st.columns([3, 1])
    with col1:
        # 默认日期设为昨天，因为 OFAC 往往有延迟
        target_date = st.date_input("📅 检索日期 (Select Date)", datetime.now() - timedelta(days=1))
    with col2:
        run_btn = st.button("开始检索 (RUN)", type="primary", use_container_width=True)
    
    st.info(f"💡 提示：正在检索 **{target_date}** (含前后2天)。OFAC 11月19日的数据因为时差可能显示为20日，系统会自动匹配。")

    if run_btn:
        results = []
        status = st.status("🚀 正在强力扫描...", expanded=True)
        
        # 1. 并发抓取
        all_links = []
        with ThreadPoolExecutor(max_workers=5) as exe:
            futures = {exe.submit(fetch_site_links, s): s for s in SITES}
            for f in as_completed(futures):
                site = futures[f]
                links, logs = f.result()
                all_links.extend(links)
                status.write(f"🔎 {site['name']}: 发现 {len(links)} 条原始链接")
                
                # 打印前3条链接方便调试（仅在 UI 上显示）
                # if links: st.code(str([l['url'] for l in links[:2]]))

        # 2. AI 并发清洗
        unique_links = {l['url']: l for l in all_links}.values() # 去重
        status.write(f"🧠 AI 正在深度审计 {len(unique_links)} 条内容 (包含 URL 日期指纹分析)...")
        
        processed_count = 0
        progress = st.progress(0)
        
        with ThreadPoolExecutor(max_workers=10) as exe:
            futures = [exe.submit(analyze_link, item, target_date) for item in unique_links]
            for f in as_completed(futures):
                res = f.result()
                processed_count += 1
                progress.progress(processed_count / len(unique_links))
                if res:
                    results.append(res)
        
        status.update(label="✅ 扫描完成", state="complete", expanded=False)
        progress.empty()

        # 3. 结果展示
        if not results:
            st.warning("⚠️ 未发现匹配该日期的核心合规事件。")
            st.write("如果是 OFAC 11月19日的数据，请尝试将日期选为 **11月20日** 再试一次（因为中美时差）。")
        else:
            st.success(f"🎯 锁定 {len(results)} 条关键情报")
            for item in results:
                tag_cls = "tag-vip" if item['type'] == 'vip' else "tag-media"
                st.markdown(f"""
                <div class="result-card">
                    <div>
                        <span class="tag {tag_cls}">{item['source']}</span>
                        <span class="date-badge">相关度: HIGH</span>
                    </div>
                    <h3 style="margin-top:15px; font-weight:900;">{item['title']}</h3>
                    <p style="font-size:16px; font-weight:bold;">摘要：{item['summary']}</p>
                    <p style="font-size:15px; background:#eeeeee; padding:10px; border-left:4px solid black;">
                        ⚡ <b>合规提示：</b>{item['risk']}
                    </p>
                    <a href="{item['url']}" target="_blank">🔗 点击阅读官方原文</a>
                </div>
                """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
