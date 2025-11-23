import streamlit as st
import re
import json
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

# 忽略 SSL 警告
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# ================= 📦 依赖库检查 =================
try:
    from curl_cffi import requests as c_requests
    HAS_CURL = True
except ImportError:
    st.error("严重错误：缺少 curl_cffi 库。请在终端运行: pip install curl_cffi")
    st.stop()

# ================= ⚙️ 核心配置 =================

SILICON_KEY = "sk-lvnzrlhumujjhpzjkslhhuqjdukioscebcoeuawumtyqoqiz" 
API_URL = "https://api.siliconflow.cn/v1/chat/completions"

# 关键词库
KEYWORDS = [
    "Sanction", "Entity", "Trade", "Export", "Import", "Tariff", "Supply Chain", 
    "China", "Russia", "Technology", "Chip", "Ban", "Restriction", 
    "Designation", "Blocked", "SDN", "WRO",
    "制裁", "实体", "贸易", "出口", "进口", "关税", "供应链", "芯片"
]

SITES = [
    # 重点：OFAC 配置了特殊的伪装标记
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "type": "vip", "engine": "chrome110"},
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "type": "vip", "engine": "chrome110"},
    {"name": "🇺🇸 DOJ Press", "url": "https://www.justice.gov/news/press-releases", "type": "vip", "engine": "chrome110"},
    {"name": "🇨🇳 MOFCOM (管制局)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "type": "vip", "engine": "standard"},
    {"name": "🇬🇧 Reuters Defense", "url": "https://www.reuters.com/business/aerospace-defense/", "type": "media", "engine": "chrome110"},
]

# ================= 🎨 UI 设计 (高对比度纯黑版) =================

st.set_page_config(page_title="Trade Monitor Pro", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    /* 强制黑白高对比度 */
    .stApp { background-color: #ffffff !important; color: #000000 !important; }
    h1, h2, h3, p, div, span, label { color: #000000 !important; font-family: 'Arial', sans-serif; }
    
    /* 标题栏 */
    .main-header { 
        font-family: "Arial Black", sans-serif; 
        font-size: 2.2rem; 
        border-bottom: 5px solid #000000; 
        padding-bottom: 10px; 
        margin-bottom: 20px;
    }
    
    /* 结果卡片 */
    .result-card { 
        border: 3px solid #000000; 
        padding: 20px; 
        margin-bottom: 20px; 
        box-shadow: 6px 6px 0px #000000; 
    }
    
    /* 状态标签 */
    .tag { font-weight: 900; background: #000; color: #fff !important; padding: 4px 8px; font-size: 0.8rem; }
    .tag-media { background: #555; }
    
    /* 链接 */
    a { color: #0000EE !important; font-weight: bold; text-decoration: underline; }
</style>
""", unsafe_allow_html=True)

# ================= 🛠️ 核心功能函数 =================

def extract_date_from_url(url):
    """从 URL 中提取日期 (YYYY-MM-DD)"""
    # 匹配 /2024/11/19/ 或 /2024-11-19/ 或 -2024-11-19
    match = re.search(r'[\/-](\d{4})[\/-](\d{2})[\/-](\d{2})', url)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None

def fetch_links(site):
    """第一步：抓取所有链接"""
    links = []
    log_msg = ""
    try:
        # 1. 发起请求
        if site['engine'] == "chrome110":
            # 关键：使用刚才测试成功的伪装参数
            resp = c_requests.get(site['url'], impersonate="chrome110", timeout=15)
        else:
            resp = c_requests.get(site['url'], timeout=15)
            if "mofcom" in site['url']: resp.encoding = "gbk"
            
        # 2. 验证数据量
        if len(resp.text) < 1000:
            return [], f"❌ {site['name']} 内容过短，可能被拦截"
        
        # 3. 解析 HTML
        soup = BeautifulSoup(resp.text, 'html.parser')
        seen_urls = set()
        
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            url = urljoin(site['url'], a['href'])
            
            # 基础过滤
            if len(text) < 5 or "javascript" in url: continue
            if url in seen_urls: continue
            
            # 命中策略
            is_hit = False
            if site['type'] == 'vip': 
                is_hit = True # 官方源全量抓取
            elif any(k.lower() in text.lower() for k in KEYWORDS):
                is_hit = True
                
            if is_hit:
                # 尝试从 URL 提取日期
                url_date = extract_date_from_url(url)
                
                links.append({
                    "source": site['name'],
                    "title": text,
                    "url": url,
                    "type": site['type'],
                    "engine": site['engine'],
                    "url_date": url_date # 传递日期指纹
                })
                seen_urls.add(url)
                
        log_msg = f"✅ {site['name']}: 成功捕获 {len(links)} 条"
        return links, log_msg
        
    except Exception as e:
        return [], f"☠️ {site['name']} 连接失败: {str(e)}"

def ai_analyze(item, target_date_str):
    """第二步：AI 深度分析"""
    try:
        # 1. 再次伪装获取正文
        if item['engine'] == "chrome110":
            resp = c_requests.get(item['url'], impersonate="chrome110", timeout=10)
        else:
            resp = c_requests.get(item['url'], timeout=10)
            if "mofcom" in item['url']: resp.encoding = "gbk"
            
        raw_text = BeautifulSoup(resp.text, 'html.parser').get_text(separator="\n", strip=True)[:5000]
        
        # 2. AI 判断
        prompt = f"""
        User Target Date: {target_date_str}
        
        Article Info:
        - URL Date Clue: {item['url_date']} (Strong Evidence)
        - Title: {item['title']}
        - Content Snippet: {raw_text[:2000]}
        
        Your Job:
        1. TIME CHECK: Does this article belong to the User Target Date?
           - Allow +/- 2 days tolerance for timezones.
           - IF URL Date Clue matches, IT IS A MATCH.
           - IF content explicitly mentions the date, IT IS A MATCH.
        2. RELEVANCE CHECK: Is it about Sanctions/Trade/Export/Entity List?
        
        Output JSON Only:
        {{
            "is_match": true/false,
            "summary": "简要中文摘要 (1句话)",
            "risk_tip": "企业合规警示 (1句话)"
        }}
        """
        
        payload = {
            "model": "deepseek-ai/DeepSeek-V3",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}
        }
        
        res = c_requests.post(
            API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {SILICON_KEY}", "Content-Type": "application/json"},
            timeout=30
        )
        
        result = json.loads(res.json()['choices'][0]['message']['content'])
        
        if result.get('is_match'):
            item['summary'] = result.get('summary')
            item['risk_tip'] = result.get('risk_tip')
            return item
            
    except Exception:
        pass
    return None

# ================= 🖥️ 主程序逻辑 =================

def main():
    # 侧边栏：状态监控
    with st.sidebar:
        st.header("🕵️‍♂️ 监控台")
        target_date = st.date_input("选择基准日期", datetime.now() - timedelta(days=1)) # 默认为昨天
        target_str = target_date.strftime("%Y-%m-%d")
        
        st.divider()
        st.write("📡 **数据源连通性测试**")
        status_placeholder = st.empty()
    
    st.markdown('<div class="main-header">TRADE SANCTION MONITOR</div>', unsafe_allow_html=True)
    st.info(f"📅 当前检索日期：**{target_str}** (系统将自动扫描前后 2 天的数据以覆盖时差)")

    if st.button("🚀 开始检索 (START SCAN)", type="primary", use_container_width=True):
        
        # 1. 抓取阶段
        status_placeholder.text("⏳ 正在连接全球服务器...")
        all_items = []
        logs = []
        
        with ThreadPoolExecutor(max_workers=5) as exe:
            futures = {exe.submit(fetch_links, s): s for s in SITES}
            for f in as_completed(futures):
                links, msg = f.result()
                all_items.extend(links)
                logs.append(msg)
        
        # 更新侧边栏状态
        with st.sidebar:
            for log in logs:
                color = "green" if "✅" in log else "red"
                st.markdown(f":{color}[{log}]")
        
        # 2. 筛选阶段
        st.write(f"🔍 捕获 {len(all_items)} 条原始线索，正在进行 AI 智能鉴别...")
        progress_bar = st.progress(0)
        final_results = []
        
        # 简单去重
        unique_items = {i['url']: i for i in all_items}.values()
        total = len(unique_items)
        
        with ThreadPoolExecutor(max_workers=10) as exe:
            futures = [exe.submit(ai_analyze, item, target_str) for item in unique_items]
            for i, f in enumerate(as_completed(futures)):
                res = f.result()
                if res:
                    final_results.append(res)
                progress_bar.progress((i + 1) / total)
        
        progress_bar.empty()
        
        # 3. 结果渲染
        if not final_results:
            st.error("❌ 该日期范围内未发现核心合规风险事件。")
            st.markdown("""
            **排查建议：**
            1. 请查看左侧侧边栏，确认 **OFAC Actions** 是否显示 ✅。
            2. 尝试将日期调整为 **前一天** 或 **后一天**（OFAC 发布时间可能有延迟）。
            """)
        else:
            st.success(f"✅ 成功锁定 {len(final_results)} 条高风险情报")
            for item in final_results:
                tag_cls = "tag" if item['type'] == 'vip' else "tag-media"
                
                st.markdown(f"""
                <div class="result-card">
                    <span class="{tag_cls}">{item['source']}</span>
                    <h3 style="margin-top:10px; font-weight:900">{item['title']}</h3>
                    <p><b>📝 摘要：</b>{item['summary']}</p>
                    <div style="background:#eee; padding:10px; border-left:5px solid #000;">
                        <b>⚡ 合规警示：</b>{item['risk_tip']}
                    </div>
                    <br>
                    <a href="{item['url']}" target="_blank">🔗 点击查看原文</a>
                </div>
                """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
