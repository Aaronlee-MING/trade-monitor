import streamlit as st
import time
import re
import io
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from datetime import timedelta, datetime
import warnings

# === 依赖库检查与导入 ===
# 核心逻辑依赖: requests, streamlit, beautifulsoup4
# 高级反爬依赖: curl_cffi (可选，自动降级)
# PDF处理依赖: pypdf (可选，自动忽略)

try:
    import pypdf
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

try:
    from curl_cffi import requests as c_requests
    HAS_CURL = True
except ImportError:
    import requests as c_requests # 占位
    HAS_CURL = False

import requests # 标准库兜底
from bs4 import BeautifulSoup

# 屏蔽 SSL 警告
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# ================= ⚙️ 核心配置 =================

MODEL_NAME = "deepseek-ai/DeepSeek-V3"
API_URL = "https://api.siliconflow.cn/v1/chat/completions"

# 媒体源专用关键词 (初筛用)
MEDIA_KEYWORDS = [
    "Sanction", "Export", "Entity List", "SDN", "Tariff", 
    "Semiconductor", "Chip", "Blacklist", "Laundering", "Ban", 
    "Restriction", "Investigation", "China", "Russia", "Iran",
    "Enforcement", "Violation", "Entity", "List", "Control",
    "制裁", "出口", "实体清单", "关税", "半导体", "芯片", "黑名单", "洗钱"
]

# 站点配置
DEFAULT_SITES = [
    # === VIP: 核心政府源 ===
    {"name": "🇺🇸 BIS News", "url": "https://www.bis.gov/news-updates", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 OFAC Actions", "url": "https://ofac.treasury.gov/recent-actions", "engine": "curl", "type": "vip"},
    {"name": "🇺🇸 DOJ Press", "url": "https://www.justice.gov/news/press-releases", "engine": "curl", "type": "vip"},
    {"name": "🇪🇺 EU Council", "url": "https://www.consilium.europa.eu/en/press/press-releases/", "engine": "curl", "type": "vip"},
    {"name": "🇨🇳 MOFCOM (管制局)", "url": "http://aqygzj.mofcom.gov.cn/article/glxd/", "engine": "standard", "type": "vip"},
    
    # === Media: 泛读源 ===
    {"name": "🇬🇧 Reuters (Defense)", "url": "https://www.reuters.com/business/aerospace-defense/", "engine": "curl", "type": "media"},
    {"name": "🏛️ CSIS Analysis", "url": "https://www.csis.org/analysis", "engine": "curl", "type": "media"},
    {"name": "📰 Foreign Policy", "url": "https://foreignpolicy.com/latest/", "engine": "curl", "type": "media"}
]

# ================= 🎨 UI 设计 (律师专业版) =================

st.set_page_config(page_title="Global Trade Compliance Monitor", page_icon="⚖️", layout="wide")

ST_STYLE = """
<style>
    /* 全局字体与背景 */
    .stApp { background-color: #f8f9fa; font-family: "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    
    /* 标题设计 */
    .main-title {
        font-family: "Merriweather", "Times New Roman", serif;
        color: #0f172a;
        font-size: 2.5rem;
        font-weight: 700;
        margin-bottom: 0;
        border-bottom: 3px solid #1e3a8a;
        padding-bottom: 10px;
    }
    .sub-title {
        font-family: "Segoe UI", sans-serif;
        color: #64748b;
        font-size: 1rem;
        margin-top: 5px;
        margin-bottom: 2rem;
    }

    /* 卡片设计 */
    .report-card {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-left: 4px solid #3b82f6; /* Default Blue */
        border-radius: 8px;
        padding: 20px;
        margin-bottom: 15px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        transition: transform 0.2s;
    }
    .report-card:hover {
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        transform: translateY(-2px);
    }
    
    /* 风险等级颜色 */
    .border-critical { border-left-color: #dc2626 !important; background-color: #fef2f2; } /* Red */
    .border-high { border-left-color: #ea580c !important; } /* Orange */
    .border-medium { border-left-color: #3b82f6 !important; } /* Blue */
    
    /* 标签 */
    .tag {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        margin-right: 8px;
    }
    .tag-vip { background-color: #1e3a8a; color: white; }
    .tag-media { background-color: #94a3b8; color: white; }
    
    /* 内容排版 */
    .card-title { font-size: 1.1rem; font-weight: 700; color: #1e293b; margin-bottom: 8px; }
    .card-date { font-size: 0.85rem; color: #64748b; margin-bottom: 12px; }
    .card-body { font-size: 0.95rem; color: #334155; line-height: 1.6; }
    .compliance-tip { 
        margin-top: 12px; 
        padding: 10px; 
        background-color: #f1f5f9; 
        border-radius: 4px; 
        font-size: 0.9rem; 
        color: #475569; 
        font-style: italic;
    }
    
    /* Markdown 预览区 */
    .md-preview {
        background-color: #1e1e1e;
        color: #d4d4d4;
        padding: 20px;
        border-radius: 8px;
        font-family: "Consolas", "Monaco", monospace;
        white-space: pre-wrap;
    }
</style>
"""
st.markdown(ST_STYLE, unsafe_allow_html=True)

# ================= 🛠️ 辅助函数 =================

def normalize_title(title):
    """去重指纹：仅保留字母数字，转小写"""
    if not title: return ""
    return re.sub(r'[^\w\s]', '', title).lower().strip()

def extract_pdf_text(content):
    """解析 PDF (前3页)"""
    if not HAS_PDF: return ""
    try:
        with io.BytesIO(content) as f:
            reader = pypdf.PdfReader(f)
            text = ""
            for i in range(min(3, len(reader.pages))): 
                text += reader.pages[i].extract_text() + "\n"
            return text
    except: return ""

def smart_request(url, engine="standard"):
    """智能网络请求 (自动切换 curl/standard)"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
    
    try:
        if engine == "curl" and HAS_CURL:
            return c_requests.get(url, impersonate="chrome120", timeout=20)
        else:
            # Standard requests fallback
            resp = requests.get(url, headers=headers, timeout=15, verify=False)
            # 中文乱码处理
            if "mofcom" in url: 
                resp.encoding = "gbk" if "gbk" in resp.text.lower() else "utf-8"
            return resp
    except Exception as e:
        return None

def fetch_links_step(site):
    """步骤1: 采集链接"""
    links = []
    try:
        resp = smart_request(site['url'], site['engine'])
        if not resp or resp.status_code != 200: return []
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        for a in soup.find_all('a'):
            t = a.get_text(strip=True)
            h = a.get('href')
            
            if not h or "javascript" in h or "mailto" in h: continue
            
            full_url = urljoin(site['url'], h)
            is_pdf = h.lower().endswith('.pdf')
            
            # 初筛逻辑
            should_keep = False
            if site['type'] == 'vip':
                # VIP: 只要有标题或PDF就保留，交给AI判断
                if len(t) > 4 or is_pdf: should_keep = True
            else:
                # Media: 必须命中关键词
                if len(t) > 10 and any(k.lower() in t.lower() for k in MEDIA_KEYWORDS):
                    should_keep = True
            
            if should_keep:
                links.append({
                    "source": site['name'],
                    "type": site['type'],
                    "title": t if t else "Document",
                    "url": full_url,
                    "engine": site['engine']
                })
        return links
    except:
        return []

def analyze_content_step(item, target_date_objs, api_key):
    """步骤2: 内容下载 + AI 深度清洗与分析"""
    try:
        # 1. 下载内容
        resp = smart_request(item['url'], item['engine'])
        if not resp: return None
        
        is_pdf = item['url'].lower().endswith(".pdf")
        if is_pdf:
            raw_text = extract_pdf_text(resp.content)
        else:
            soup = BeautifulSoup(resp.text, 'html.parser')
            # 移除无关元素
            for s in soup(["script", "style", "nav", "footer", "header"]): s.extract()
            raw_text = soup.get_text(separator="\n", strip=True)[:5000] # 截取前5000字
            
        if len(raw_text) < 50: return None

        # 2. AI 判决
        date_strs = [d.strftime("%Y-%m-%d") for d in target_date_objs]
        
        system_prompt = """你是一个极其严格的全球贸易合规情报官。你的任务是过滤噪音，仅保留高价值情报。
        
        【判断标准】
        1. 时间校验：必须包含用户指定日期范围内的事件。
        2. 相关性校验(核心)：
           - 保留：制裁名单变更(Entity List/SDN)、出口管制新规、贸易调查、供应链禁令、重大执法案件。
           - 剔除：人事任免、常规会议通知、节假日公告、网站维护通知、普通外交辞令（无实质行动）、无关的新闻。
        
        【输出格式】
        如果不符合标准，仅输出：MISMATCH
        如果符合，输出 JSON 格式：
        {
            "date": "YYYY-MM-DD",
            "title_cn": "中文标题(简练专业)",
            "summary": "核心事实摘要(不超过3点)",
            "risk_level": "Critical" | "High" | "Medium" | "Low",
            "compliance_tip": "针对企业的合规建议(一句话)"
        }
        """
        
        user_prompt = f"""
        目标日期范围: {date_strs}
        来源类型: {item['type']} ({item['source']})
        原文标题: {item['title']}
        原文内容片段: 
        {raw_text}
        """

        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }
        
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        
        # Retry mechanism
        for _ in range(2):
            try:
                res = requests.post(API_URL, json=payload, headers=headers, timeout=30)
                if res.status_code == 200:
                    content = res.json()['choices'][0]['message']['content']
                    if "MISMATCH" in content: return None
                    
                    data = json.loads(content)
                    data.update(item) # 合并元数据
                    return data
            except:
                time.sleep(1)
                continue
                
    except Exception as e:
        pass
    return None

# ================= 🖥️ 主程序 =================

def main():
    # Sidebar Configuration
    with st.sidebar:
        st.header("⚖️ 控制台")
        
        api_key = st.text_input("SiliconFlow API Key", type="password", help="请输入您的 API Key 以启动 AI 引擎")
        
        # 默认 Key (仅作演示，实际使用请留空让用户填)
        if not api_key:
            # 尝试从 User Code 获取，如果没有则提示
            default_key = "sk-lvnzrlhumujjhpzjkslhhuqjdukioscebcoeuawumtyqoqiz"
            api_key = default_key
            
        report_type = st.selectbox("报告周期", ["日报", "周报", "月报"])
        selected_date = st.date_input("基准日期", datetime.now())
        
        st.divider()
        st.caption("数据源状态")
        st.success(f"VIP 源: {len([s for s in DEFAULT_SITES if s['type']=='vip'])} 个")
        st.info(f"Media 源: {len([s for s in DEFAULT_SITES if s['type']=='media'])} 个")
        
        run_btn = st.button("🚀 生成情报日报", type="primary", use_container_width=True)
        
        st.divider()
        if not HAS_CURL:
            st.warning("⚠️ 未检测到 curl_cffi，正使用标准 requests 模式 (可能遗漏部分强反爬网站)")
        if not HAS_PDF:
            st.warning("⚠️ 未检测到 pypdf，无法解析 PDF 内容")

    # Main Content
    st.markdown('<div class="main-title">Trade Compliance Monitor</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">全球贸易与制裁情报日报生成系统 | AI Powered</div>', unsafe_allow_html=True)

    if "intelligence_data" not in st.session_state:
        st.session_state.intelligence_data = []

    if run_btn and api_key:
        st.session_state.intelligence_data = []
        
        # 计算日期范围
        target_dates = [selected_date]
        if report_type == "日报": target_dates.append(selected_date - timedelta(days=1))
        elif report_type == "周报": target_dates = [selected_date - timedelta(days=i) for i in range(8)]
        
        status_box = st.status("🕵️‍♂️ 情报搜集任务执行中...", expanded=True)
        
        # Phase 1: 广度扫描
        status_box.write("📡 正在扫描全球数据源...")
        all_links = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_links_step, site) for site in DEFAULT_SITES]
            for future in as_completed(futures):
                all_links.extend(future.result())
        
        status_box.write(f"✅ 扫描完成，捕获原始线索 {len(all_links)} 条")
        
        # Phase 2: 去重
        unique_items = []
        seen = set()
        for item in all_links:
            # 指纹: URL 或 标题(非PDF)
            fp = item['url']
            if "pdf" not in fp.lower():
                fp += normalize_title(item['title'])
                
            if fp not in seen:
                seen.add(fp)
                unique_items.append(item)
        
        status_box.write(f"✂️ 智能去重后剩余 {len(unique_items)} 条待核查线索")
        
        # Phase 3: 深度清洗与 AI 分析
        status_box.write("🧠 AI 正在逐条阅读并进行相关性校验 (VIP Relevance Check)...")
        progress_bar = status_box.progress(0)
        
        valid_results = []
        processed_count = 0
        
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(analyze_content_step, item, target_dates, api_key) for item in unique_items]
            for future in as_completed(futures):
                res = future.result()
                if res: valid_results.append(res)
                
                processed_count += 1
                progress_bar.progress(processed_count / len(unique_items))
        
        st.session_state.intelligence_data = valid_results
        status_box.update(label=f"🎉 完成！锁定 {len(valid_results)} 条核心高价值情报", state="complete", expanded=False)

    # === 结果展示区 ===
    if st.session_state.intelligence_data:
        data = st.session_state.intelligence_data
        
        # Tab 分区：可视化 vs Markdown源码
        tab1, tab2 = st.tabs(["📄 可视化日报", "📝 Markdown 源码 (可复制)"])
        
        with tab1:
            # 排序：Critical 先展示
            priority_map = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
            sorted_data = sorted(data, key=lambda x: priority_map.get(x.get("risk_level", "Low"), 3))
            
            for item in sorted_data:
                # 样式处理
                risk = item.get('risk_level', 'Low')
                border_class = f"border-{risk.lower()}" if risk in ["Critical", "High", "Medium"] else ""
                vip_tag = '<span class="tag tag-vip">VIP SOURCE</span>' if item['type'] == 'vip' else '<span class="tag tag-media">MEDIA</span>'
                
                st.markdown(f"""
                <div class="report-card {border_class}">
                    <div>
                        {vip_tag}
                        <span style="color:#64748b; font-size:0.8rem;">{item['source']} • {item.get('date', 'N/A')}</span>
                    </div>
                    <div class="card-title">{item['title_cn']}</div>
                    <div class="card-body">{item['summary']}</div>
                    <div class="compliance-tip">💡 <strong>合规提示：</strong>{item['compliance_tip']}</div>
                    <div style="margin-top:10px;">
                        <a href="{item['url']}" target="_blank" style="font-size:0.85rem; text-decoration:none; color:#3b82f6;">🔗 阅读原文 (Read Original)</a>
                    </div>
                </div>
                """, unsafe_allow_html=True)

        with tab2:
            # 生成纯净 Markdown
            date_str = datetime.now().strftime("%Y-%m-%d")
            md_lines = [f"# 🌍 全球贸易合规与制裁日报 ({date_str})", ""]
            
            # 分组生成
            for risk in ["Critical", "High", "Medium", "Low"]:
                items = [x for x in sorted_data if x.get("risk_level") == risk]
                if not items: continue
                
                icon_map = {"Critical": "🔴", "High": "🟠", "Medium": "🔵", "Low": "⚪"}
                md_lines.append(f"## {icon_map[risk]} {risk} Priority")
                
                for idx, item in enumerate(items, 1):
                    md_lines.append(f"### {idx}. {item['title_cn']}")
                    md_lines.append(f"- **来源**: {item['source']} ({item['date']})")
                    md_lines.append(f"- **摘要**: {item['summary']}")
                    md_lines.append(f"- **💡 合规建议**: {item['compliance_tip']}")
                    md_lines.append(f"- [🔗 原文链接]({item['url']})")
                    md_lines.append("")
            
            md_content = "\n".join(md_lines)
            st.text_area("结果预览 (Ctrl+A 全选复制)", value=md_content, height=600)
            
            st.download_button(
                label="📥 下载 .md 文件",
                data=md_content,
                file_name=f"Trade_Compliance_Report_{date_str}.md",
                mime="text/markdown"
            )

    elif run_btn:
        st.info("今日无相关重大合规情报更新。")

if __name__ == "__main__":
    main()
