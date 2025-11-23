import streamlit as st
import requests
import warnings
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from datetime import timedelta, datetime

# 引入抗指纹浏览器伪装库
try:
    from curl_cffi import requests as c_requests
except ImportError:
    import requests as c_requests

# 忽略 SSL 安全警告，保持界面整洁
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# ================= ⚙️ 用户配置区 (User Config) =================

# ⚠️ 请在此处填入你的 SiliconFlow (DeepSeek) API 密钥
# 获取地址: https://cloud.siliconflow.cn/
SILICON_KEY = "sk-lvnzrlhumujjhpzjkslhhuqjdukioscebcoeuawumtyqoqiz" 

API_URL = "https://api.siliconflow.cn/v1/chat/completions"
MODEL_NAME = "deepseek-ai/DeepSeek-V3"

# 关键词库 (既用于搜索，也用于高亮)
KEYWORDS = [
    "Sanction", "Entity List", "Tariff", "Export Control", "Supply Chain", 
    "Semiconductor", "Chip", "Ban", "Restriction", "UFLPA", "Bis", "Ofac",
    "制裁", "实体清单", "关税", "出口管制", "供应链", "芯片", "半导体", "黑名单"
]

# 数据源配置 (涵盖官方 VIP 源与权威媒体)
SITES = [
    # VIP: 官方监管源 (权威性最高)
    {"name": "🇺🇸 BIS News (美商务部)", "url": "https://www.bis.gov/news-updates", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 OFAC Actions (美财政部)", "url": "https://ofac.treasury.gov/recent-actions", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 DOJ Press (美司法部)", "url": "https://www.justice.gov/news/press-releases", "engine": "curl", "type": "vip"},
    {"name": "🇨🇳 MOFCOM (中国商务部)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "engine": "standard", "type": "vip"},
    {"name": "🇪🇺 EU Sanctions (欧盟)", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "engine": "curl", "type": "vip"},
    
    # Media: 法律与行业情报
    {"name": "🇬🇧 Reuters Defense", "url": "https://www.reuters.com/business/aerospace-defense/", "engine": "curl", "type": "media"},
    {"name": "🏛️ CSIS Analysis", "url": "https://www.csis.org/analysis", "engine": "curl", "type": "media"},
    {"name": "📰 Foreign Policy", "url": "https://foreignpolicy.com/latest/", "engine": "curl", "type": "media"},
]

# ================= 🎨 律所级 UI 设计 (CSS) =================

st.set_page_config(page_title="Global Trade Monitor", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    /* 1. 整体背景与字体 - 模拟纸质文件 */
    .stApp {
        background-color: #f8f9fa;
        font-family: "Times New Roman", "Source Serif Pro", serif;
    }
    
    /* 2. 顶部导航栏 - 海军蓝专业风 */
    header[data-testid="stHeader"] {
        background-color: #002b49;
    }
    
    /* 3. 标题样式 */
    .main-title {
        color: #002b49;
        font-family: "Georgia", serif;
        font-size: 2.8rem;
        font-weight: 700;
        border-bottom: 2px solid #bfa15f; /* 金色线条 */
        padding-bottom: 10px;
        margin-bottom: 20px;
    }
    .sub-title {
        color: #555;
        font-family: "Arial", sans-serif;
        font-size: 1rem;
        margin-top: -15px;
        margin-bottom: 30px;
    }

    /* 4. 结果卡片 - 类似彭博终端/法律简报的卡片 */
    .report-card {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        border-left: 6px solid #002b49; /* 左侧强强调色 */
        border-radius: 4px;
        padding: 25px;
        margin-bottom: 20px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        transition: transform 0.2s;
    }
    .report-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 5px 15px rgba(0,0,0,0.1);
    }

    /* 5. 标签体系 */
    .tag-vip {
        background-color: #002b49;
        color: #bfa15f; /* 金色文字 */
        padding: 4px 8px;
        font-size: 0.75rem;
        font-weight: bold;
        text-transform: uppercase;
        border-radius: 2px;
        letter-spacing: 1px;
    }
    .tag-media {
        background-color: #e2e8f0;
        color: #475569;
        padding: 4px 8px;
        font-size: 0.75rem;
        font-weight: bold;
        border-radius: 2px;
    }
    .date-tag {
        color: #888;
        font-family: "Arial", sans-serif;
        font-size: 0.85rem;
        margin-left: 10px;
    }

    /* 6. 内容排版 */
    .card-title {
        color: #1a1a1a;
        font-family: "Georgia", serif;
        font-size: 1.3rem;
        font-weight: 600;
        margin: 12px 0;
    }
    .card-body {
        color: #333;
        font-family: "Arial", sans-serif;
        font-size: 0.95rem;
        line-height: 1.6;
        white-space: pre-wrap;
    }
    
    /* 7. 链接按钮 */
    .source-link {
        display: inline-block;
        margin-top: 15px;
        color: #0056b3;
        text-decoration: none;
        font-size: 0.9rem;
        font-weight: 600;
        border-bottom: 1px dotted #0056b3;
    }
    .source-link:hover {
        border-bottom: 1px solid #0056b3;
    }
</style>
""", unsafe_allow_html=True)

# ================= 🧠 核心逻辑 (后端) =================

def fetch_site_links(site):
    """采集器：针对不同网站使用不同的伪装策略"""
    links = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        
        # 1. 获取网页源码
        if site['engine'] == "standard":
            # 普通模式：适合简单网站（如中国商务部）
            resp = requests.get(site['url'], headers=headers, timeout=10, verify=False)
            if "mofcom" in site['url']: resp.encoding = "gbk" # 解决中文乱码
            html = resp.text
        else:
            # 穿墙模式：模拟 Chrome 120 浏览器
            resp = c_requests.get(site['url'], impersonate="chrome120", timeout=15)
            html = resp.text

        # 2. 解析链接
        soup = BeautifulSoup(html, 'html.parser')
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            href = a['href']
            
            # 初步过滤：标题太短或无意义的不看
            if len(text) < 5: continue
            
            # VIP 源全量抓取，Media 源需包含关键词
            is_relevant = False
            if site['type'] == 'vip':
                is_relevant = True # 官方源全看，交给 AI 过滤
            else:
                if any(k.lower() in text.lower() for k in KEYWORDS):
                    is_relevant = True
            
            if is_relevant:
                full_url = urljoin(site['url'], href)
                links.append({
                    "source": site['name'],
                    "title": text,
                    "url": full_url,
                    "type": site['type'],
                    "engine": site['engine']
                })
    except Exception as e:
        print(f"Error fetching {site['name']}: {e}")
    return links

def analyze_with_ai(article, date_scope_str):
    """AI 分析师：DeepSeek-V3"""
    try:
        # 1. 获取正文
        txt = ""
        if article['engine'] == "standard":
            r = requests.get(article['url'], timeout=10, verify=False)
            if "mofcom" in article['url']: r.encoding = "gbk"
            txt = r.text
        else:
            r = c_requests.get(article['url'], impersonate="chrome120", timeout=10)
            txt = r.text
        
        soup = BeautifulSoup(txt, 'html.parser')
        # 移除干扰元素
        for tag in soup(['script', 'style', 'nav', 'footer']): tag.decompose()
        content_snippet = soup.get_text(separator="\n", strip=True)[:5000] # 限制长度

        # 2. 构造 AI 指令
        system_prompt = "你是一个专业的贸易合规律师。请严格按格式输出，不要废话。"
        user_prompt = f"""
        请分析以下新闻是否与“国际贸易、制裁、出口管制”相关，并检查日期。

        【目标日期范围】: {date_scope_str}
        （注意：如果文章未明确标日期，但内容明显是今日或昨日发生的重大事件，也请保留）。

        【判断标准】:
        1. 必须是实质性新闻（剔除网站维护、放假通知、普通人事任免）。
        2. 必须在目标日期范围内（或内容具有极高时效性）。

        如果【不符合】，仅输出: MISMATCH
        如果【符合】，请输出以下格式（不要使用 Markdown 代码块）：

        【摘要】一句话概括核心事实（专业口吻）。
        【风险】对企业的合规影响提示。
        【日期】文章发布日期 (YYYY-MM-DD)

        ---
        文章来源: {article['source']}
        文章标题: {article['title']}
        文章内容: {content_snippet}
        """

        payload = {
            "model": "deepseek-ai/DeepSeek-V3",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1, # 低温模式，减少胡说八道
            "max_tokens": 500
        }

        resp = requests.post(
            API_URL, 
            json=payload, 
            headers={"Authorization": f"Bearer {SILICON_KEY}", "Content-Type": "application/json"},
            timeout=30
        )
        
        result_text = resp.json()['choices'][0]['message']['content']
        
        if "MISMATCH" in result_text:
            return None
        
        # 格式化返回
        article['ai_analysis'] = result_text
        return article

    except Exception as e:
        return None

# ================= 🖥️ 前端界面 (Streamlit) =================

def main():
    # 侧边栏控制台
    with st.sidebar:
        st.header("🎛️ Control Panel")
        lookback_days = st.slider("回溯天数 (Lookback)", 1, 7, 1)
        
        st.subheader("数据源状态")
        for site in SITES:
            st.caption(f"✅ {site['name']}")
            
        st.divider()
        st.info("系统已就绪。\n点击右侧按钮开始扫描。")

    # 主界面
    st.markdown('<div class="main-title">Trade Compliance Monitor</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-title">Powered by DeepSeek-V3 | 律所专业版 | {datetime.now().strftime("%Y-%m-%d")}</div>', unsafe_allow_html=True)

    # 操作按钮区
    col1, col2 = st.columns([1, 4])
    with col1:
        start_btn = st.button("🚀 开始全网扫描", type="primary", use_container_width=True)

    if start_btn:
        status_box = st.status("正在执行合规扫描程序...", expanded=True)
        
        # 1. 计算日期范围
        dates = [datetime.now() - timedelta(days=i) for i in range(lookback_days + 1)]
        date_str = ", ".join([d.strftime("%Y-%m-%d") for d in dates])
        status_box.write(f"📅 锁定日期范围: {date_str}")

        # 2. 抓取链接 (多线程)
        all_links = []
        status_box.write("🔍 正在接入各国监管机构网站...")
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_site_links, site) for site in SITES]
            for future in as_completed(futures):
                links = future.result()
                all_links.extend(links)
        
        status_box.write(f"📦 初步捕获线索: {len(all_links)} 条")

        # 3. 去重
        unique_links = {link['url']: link for link in all_links}.values()
        status_box.write(f"✂️ 去重后剩余: {len(unique_links)} 条，正在排队进入 AI 审计...")

        # 4. AI 分析 (核心步骤)
        valid_results = []
        progress_bar = st.progress(0)
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            # 限制 AI 并发，防止 API 报错
            futures = {executor.submit(analyze_with_ai, link, date_str): link for link in unique_links}
            completed_count = 0
            
            for future in as_completed(futures):
                res = future.result()
                if res:
                    valid_results.append(res)
                
                completed_count += 1
                progress_bar.progress(completed_count / len(unique_links))
        
        status_box.update(label="扫描完成!", state="complete", expanded=False)
        progress_bar.empty()

        # 5. 渲染结果
        if not valid_results:
            st.warning("今日未发现实质性合规风险更新 (No material updates found).")
        else:
            st.success(f"✅ 发现 {len(valid_results)} 条高风险情报")
            
            for item in valid_results:
                # 渲染漂亮的卡片
                tag_class = "tag-vip" if item['type'] == 'vip' else "tag-media"
                tag_text = "OFFICIAL" if item['type'] == 'vip' else "MEDIA"
                
                # 解析 AI 返回的文本，尝试美化
                content_html = item['ai_analysis'].replace("\n", "<br>")
                
                st.markdown(f"""
                <div class="report-card">
                    <div>
                        <span class="{tag_class}">{tag_text}</span>
                        <span class="date-tag">来源: {item['source']}</span>
                    </div>
                    <div class="card-title">{item['title']}</div>
                    <div class="card-body">{content_html}</div>
                    <a href="{item['url']}" target="_blank" class="source-link">🔗 阅读全文 (Read Source) &rarr;</a>
                </div>
                """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
