import streamlit as st
import requests
import warnings
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

# 尝试引入穿墙库，如果环境不支持则降级
try:
    from curl_cffi import requests as c_requests
    HAS_CURL = True
except ImportError:
    HAS_CURL = False
    import requests as c_requests

# 忽略 SSL 安全警告
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# ================= ⚙️ 核心配置 =================

# ⚠️ 请确认你的 Key 有效且有余额
SILICON_KEY = "sk-lvnzrlhumujjhpzjkslhhuqjdukioscebcoeuawumtyqoqiz" 
API_URL = "https://api.siliconflow.cn/v1/chat/completions"

# 宽泛关键词：先尽可能把链接抓进来，再用 AI 过滤
# 注意：移除了一些过于具体的词，防止漏抓
KEYWORDS = [
    "Sanction", "Entity", "Trade", "Export", "Import", "Tariff", "Supply Chain", 
    "China", "Russia", "Technology", "Chip", "Ban", "Restriction", "Investment",
    "制裁", "实体", "贸易", "出口", "进口", "关税", "供应链", "芯片", "半导体"
]

SITES = [
    # VIP 源：必须全量抓取
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "type": "vip"},
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "type": "vip"},
    {"name": "🇨🇳 MOFCOM (管制局)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "type": "vip"},
    {"name": "🇪🇺 EU Press", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "type": "vip"},
    
    # 媒体源：需要关键词匹配
    {"name": "🇬🇧 Reuters Defense", "url": "https://www.reuters.com/business/aerospace-defense/", "type": "media"},
    {"name": "🏛️ CSIS Analysis", "url": "https://www.csis.org/analysis", "type": "media"},
]

# ================= 🎨 UI 设计 (修复版) =================

st.set_page_config(page_title="Trade Monitor Pro", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .main-header { color: #0a2540; font-family: serif; font-size: 2rem; font-weight: 700; border-bottom: 2px solid #cba6f7; padding-bottom: 10px; }
    
    /* 结果卡片 */
    .result-card { 
        background: white; 
        padding: 20px; 
        border-radius: 8px; 
        border-left: 5px solid #0a2540; 
        margin-bottom: 15px; 
        box-shadow: 0 2px 4px rgba(0,0,0,0.05); 
    }
    .tag { font-size: 12px; padding: 3px 8px; border-radius: 4px; font-weight: bold; color: white; }
    .tag-vip { background-color: #ad1d28; } /* 深红 */
    .tag-media { background-color: #0a2540; } /* 海军蓝 */
    
    .debug-log { font-size: 12px; color: #666; font-family: monospace; border-left: 2px solid #ddd; padding-left: 10px; margin-top: 5px; }
</style>
""", unsafe_allow_html=True)

# ================= 🕷️ 爬虫逻辑 (增强兼容性) =================

def get_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }

def fetch_site_links(site):
    """
    第一步：获取网页上的所有链接
    """
    links = []
    debug_info = []
    try:
        # 1. 发起请求
        if HAS_CURL and "mofcom" not in site['url']:
            # 国外网站用穿墙库，impersonate 参数改为较老的版本以提高稳定性
            resp = c_requests.get(site['url'], impersonate="chrome110", timeout=20)
        else:
            # 国内网站或环境不支持时，用普通库
            resp = requests.get(site['url'], headers=get_headers(), timeout=20, verify=False)
            if "mofcom" in site['url']: resp.encoding = "gbk" # 强制修复中文乱码
        
        html = resp.text
        debug_info.append(f"Status Code: {resp.status_code}, Length: {len(html)}")

        # 2. 解析链接
        soup = BeautifulSoup(html, 'html.parser')
        unique_urls = set()
        
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            url = urljoin(site['url'], a['href'])
            
            # 基础清洗
            if len(text) < 4 or "javascript" in url: continue
            if url in unique_urls: continue
            
            # 筛选逻辑
            is_hit = False
            if site['type'] == 'vip':
                is_hit = True # VIP 源全部保留，交给 AI
            else:
                # 媒体源：标题必须包含关键词
                if any(k.lower() in text.lower() for k in KEYWORDS):
                    is_hit = True
            
            if is_hit:
                unique_urls.add(url)
                links.append({"source": site['name'], "title": text, "url": url, "type": site['type']})
        
        debug_info.append(f"Found {len(links)} relevant links.")
        
    except Exception as e:
        debug_info.append(f"Error: {str(e)}")
    
    return links, debug_info

# ================= 🧠 AI 分析 (核心修正) =================

def analyze_link(item, target_date_str, debug_mode):
    """
    第二步：AI 逐条审核
    """
    try:
        # 1. 获取正文
        try:
            if HAS_CURL:
                r = c_requests.get(item['url'], impersonate="chrome110", timeout=15)
            else:
                r = requests.get(item['url'], headers=get_headers(), timeout=15, verify=False)
            
            if "mofcom" in item['url']: r.encoding = "gbk"
            text = BeautifulSoup(r.text, 'html.parser').get_text(separator="\n", strip=True)[:4000]
        except:
            return None, "Fetch Failed"

        if len(text) < 50: return None, "Content too short"

        # 2. AI 判断 (增加时区宽容度)
        prompt = f"""
        User Target Date: {target_date_str}
        
        Analyze this news article.
        
        Task 1: DATE CHECK
        Is this article published on {target_date_str}? 
        * ALLOW +/- 1 day difference due to time zones.
        * IF the article has NO explicit date, but the title implies a MAJOR recent event, assume YES.
        * IF the date is old (e.g. 2023), output "MISMATCH_DATE".

        Task 2: RELEVANCE CHECK
        Is it about Trade Sanctions, Export Controls, Entity Lists, or Tariffs?
        * IGNORE: Website maintenance, holiday notices, job postings.
        * IF irrelevant, output "MISMATCH_TOPIC".

        Output Format (JSON only):
        {{
            "status": "MATCH" or "MISMATCH_DATE" or "MISMATCH_TOPIC",
            "summary": "One sentence summary in Chinese",
            "risk": "One sentence risk warning in Chinese",
            "date_found": "YYYY-MM-DD or 'Unknown'"
        }}

        ---
        Title: {item['title']}
        Content Snippet: {text[:1000]}...
        """

        payload = {
            "model": "deepseek-ai/DeepSeek-V3",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"} # 强制 JSON 格式
        }
        
        res = requests.post(
            API_URL, 
            json=payload, 
            headers={"Authorization": f"Bearer {SILICON_KEY}", "Content-Type": "application/json"},
            timeout=30
        )
        
        ai_data = res.json()['choices'][0]['message']['content']
        result_json = json.loads(ai_data)
        
        if result_json.get("status") == "MATCH":
            return {
                "source": item['source'],
                "title": item['title'],
                "url": item['url'],
                "type": item['type'],
                "summary": result_json['summary'],
                "risk": result_json['risk'],
                "date": result_json['date_found']
            }, "MATCH"
        else:
            return None, f"AI Reject: {result_json.get('status')}"

    except Exception as e:
        return None, f"AI Error: {str(e)}"

# ================= 🖥️ 主程序 =================

def main():
    st.markdown('<div class="main-header">Trade Compliance Monitor (Github Edition)</div>', unsafe_allow_html=True)
    
    # 控制栏
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        # 关键修改：指定日期
        target_date = st.date_input("📅 目标日期 (Target Date)", datetime.now())
    with col2:
        debug_mode = st.checkbox("🐞 调试模式 (Debug)", value=True, help="勾选后可看到为什么抓不到数据")
    with col3:
        run_btn = st.button("开始检索", type="primary", use_container_width=True)

    if run_btn:
        st.session_state.logs = []
        results = []
        
        status = st.status("🚀 系统运行中...", expanded=True)
        
        # 1. 抓取阶段
        status.write("📡 正在连接各国数据源...")
        raw_items = []
        
        with ThreadPoolExecutor(max_workers=5) as exe:
            future_to_site = {exe.submit(fetch_site_links, site): site for site in SITES}
            for future in as_completed(future_to_site):
                site = future_to_site[future]
                links, logs = future.result()
                raw_items.extend(links)
                
                if debug_mode:
                    with st.expander(f"Log: {site['name']}"):
                        st.write(logs)
                        st.write(f"Found {len(links)} potential links")

        status.write(f"✅ 初步获取 {len(raw_items)} 条链接，开始 AI 智能去噪...")
        
        # 2. 分析阶段
        target_str = target_date.strftime("%Y-%m-%d")
        
        progress = st.progress(0)
        processed = 0
        
        with ThreadPoolExecutor(max_workers=8) as exe:
            # 去重
            unique_items = {x['url']: x for x in raw_items}.values()
            total = len(unique_items)
            
            futures = {exe.submit(analyze_link, item, target_str, debug_mode): item for item in unique_items}
            
            for i, future in enumerate(as_completed(futures)):
                item = futures[future]
                res, reason = future.result()
                
                processed += 1
                progress.progress(processed / total if total > 0 else 1.0)
                
                if res:
                    results.append(res)
                elif debug_mode:
                    # 在调试模式下，显示被 AI 拒绝的理由，方便排查
                    print(f"Reject [{item['source']}] {item['title']}: {reason}")

        status.update(label="检索完成", state="complete", expanded=False)
        
        # 3. 显示结果
        st.divider()
        if not results:
            st.error(f"❌ 在 {target_str} 未找到合规情报。")
            if debug_mode:
                st.info("建议：\n1. 检查上方的 'Log' 看看是否抓取到了链接。\n2. 如果 Log 里有链接但被 AI 拒绝，可能是因为日期不匹配（时差问题）或内容无关。")
        else:
            st.success(f"🎯 锁定 {len(results)} 条核心情报")
            for res in results:
                tag_cls = "tag-vip" if res['type'] == 'vip' else "tag-media"
                st.markdown(f"""
                <div class="result-card">
                    <span class="tag {tag_cls}">{res['source']}</span>
                    <span style="color:#999; font-size:0.9rem; margin-left:10px;">📅 {res['date']}</span>
                    <h3 style="margin: 10px 0; color: #333;">{res['title']}</h3>
                    <div style="background:#f8f9fa; padding:10px; border-radius:4px; font-size:0.95rem;">
                        <b>摘要：</b>{res['summary']}<br>
                        <b style="color:#d9534f;">风险：</b>{res['risk']}
                    </div>
                    <a href="{res['url']}" target="_blank" style="display:block; margin-top:10px; color:#0056b3;">🔗 原文链接</a>
                </div>
                """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
