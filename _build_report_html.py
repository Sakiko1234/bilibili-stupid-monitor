"""生成 report_status.html - 带分页和检索逻辑"""
import json, os
from datetime import datetime

BASE = "/root/bilibili-stupid-monitor"
TRACK_FILE = f"{BASE}/data/report_tracking.json"
OUT = f"{BASE}/report_status.html"

def build():
    tracking = {}
    if os.path.exists(TRACK_FILE):
        with open(TRACK_FILE, "r", encoding="utf-8") as f:
            tracking = json.load(f)

    items = []
    for rpid, info in tracking.items():
        items.append({
            "rpid": rpid,
            "user": str(info.get("user", "?")),
            "content": str(info.get("content", "")),
            "reason": str(info.get("reason", "")),
            "reported_at": str(info.get("reported_at", "")),
            "comment_time": str(info.get("comment_time", "")),
            "result": str(info.get("result", "pending")),
            "checked_at": str(info.get("checked_at") or ""),
        })

    items.sort(key=lambda x: x["reported_at"], reverse=True)

    total = len(items)
    removed_count = sum(1 for x in items if x["result"] == "removed")
    still_there = sum(1 for x in items if x["result"] == "still_there")
    pending = sum(1 for x in items if x["result"] == "pending")

    now = datetime.now().strftime("%m-%d %H:%M")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    css = """  :root {
    --bili-pink: #fb7299; --bili-blue: #00a1d6;
    --bg: #f1f2f3; --card: #ffffff; --border: #e3e5e7;
    --text: #18191c; --dim: #9499a0; --accent: var(--bili-pink);
    --accent-dim: rgba(251,114,153,0.10);
    --overlay: linear-gradient(rgba(241,242,243,0.65),rgba(241,242,243,0.65));
    --topbar-bg: #ffffff; --topbar-text: #18191c;
    --card-bg: #ffffff; --input-bg: #f1f2f5;
    --bg-image: url('bg.jpg');
  }
  [data-theme="dark"] {
    --bili-pink: #fb7299; --bili-blue: #00a1d6;
    --bg: #0f0f15; --card: #1a1a24; --border: #2a2a3a;
    --text: #e8e8ed; --dim: #8b8b9e; --accent: var(--bili-pink);
    --accent-dim: rgba(251,114,153,0.12);
    --overlay: linear-gradient(rgba(15,15,21,0.55),rgba(15,15,21,0.55));
    --topbar-bg: #1a1a24; --topbar-text: #e8e8ed;
    --card-bg: rgba(26,26,36,0.85); --input-bg: #1a1a24;
    --bg-image: url('bg.jpg');
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: -apple-system,BlinkMacSystemFont,"Helvetica Neue",Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif;
    background: var(--overlay), var(--bg-image) fixed center/cover;
    color: var(--text); min-height: 100vh;
  }
  .topbar {
    position: sticky; top: 0; z-index: 100;
    background: var(--topbar-bg); backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--border);
    padding: 0 20px; height: 48px;
    display: flex; align-items: center; justify-content: space-between;
  }
  .topbar .logo { display: flex; align-items: center; gap: 8px; color: var(--bili-pink); font-size: 16px; font-weight: 700; text-decoration: none; }
  .topbar .theme-toggle { background: transparent; border: 1px solid var(--border); border-radius: 16px; padding: 4px 12px; cursor: pointer; font-size: 15px; color: var(--dim); transition: all 0.2s; }
  .topbar .theme-toggle:hover { border-color: var(--bili-pink); color: var(--bili-pink); }
  .main { max-width: 650px; margin: 0 auto; padding: 16px 12px 24px; }
  .nav { display: flex; gap: 8px; margin-bottom: 14px; }
  .nav a { background: var(--card-bg); color: var(--text); text-decoration: none; font-size: 13px; padding: 6px 16px; border: 1px solid var(--border); border-radius: 6px; transition: all 0.2s; font-weight: 500; }
  .nav a:hover { border-color: var(--bili-pink); color: var(--bili-pink); }
  .nav a.active { background: var(--bili-pink); color: #fff; border-color: var(--bili-pink); }
  h1 { font-size: 18px; margin-bottom: 4px; color: var(--text); }
  h1 span { font-size: 12px; color: var(--dim); font-weight: 400; }
  .stats { display: flex; gap: 24px; margin: 8px 0 16px; font-size: 13px; color: var(--dim); background: var(--card-bg); padding: 10px 16px; border-radius: 8px; flex-wrap: wrap; }
  .stats b { color: var(--bili-pink); }
  .toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; font-size: 13px; color: var(--dim); }
  .toolbar select { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 28px 6px 10px; font-size: 13px; cursor: pointer; appearance: none; }
  .search-box { background: var(--input-bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 13px; width: 200px; outline: none; transition: border-color 0.2s; }
  .search-box:focus { border-color: var(--bili-pink); }
  .search-box::placeholder { color: var(--dim); }
  .search-result { color: var(--dim); font-size: 12px; }

  /* 卡片布局 */
  .report-item {
    display: flex; background: var(--card-bg); padding: 14px 16px;
    border-radius: 0; border-bottom: 1px solid var(--border);
    transition: background 0.15s; gap: 12px;
  }
  .report-item:hover { background: rgba(128,128,128,0.03); }
  .report-item:first-child { border-radius: 8px 8px 0 0; }
  .report-item:last-child { border-radius: 0 0 8px 8px; border-bottom: none; }
  .report-item:first-child:last-child { border-radius: 8px; }
  .report-status {
    display: flex; align-items: flex-start; padding-top: 2px; flex-shrink: 0;
  }
  .report-body { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .report-user-row { display: flex; align-items: center; margin-bottom: 4px; font-size: 13px; gap: 6px; flex-wrap: wrap; }
  .report-username { color: var(--dim); font-weight: 500; }
  .report-content { font-size: 15px; color: var(--text); line-height: 1.6; margin-bottom: 6px; white-space: pre-wrap; word-break: break-word; }
  .report-meta { display: flex; align-items: center; gap: 12px; color: var(--dim); font-size: 12px; flex-wrap: wrap; }
  .report-meta span { white-space: nowrap; }
  .reason-tag { background: var(--accent-dim); color: var(--bili-pink); font-size: 11px; padding: 1px 6px; border-radius: 8px; font-weight: 500; }
  .status-badge { font-size: 12px; padding: 2px 8px; border-radius: 10px; font-weight: 600; white-space: nowrap; }
  .status-removed { background: rgba(103,194,58,0.12); color: #67c23a; }
  .status-still { background: rgba(246,108,108,0.12); color: #f56c6c; }
  .status-pending { background: rgba(230,162,60,0.12); color: #e6a23c; }
  .status-other { background: rgba(128,128,128,0.1); color: var(--dim); }
  .empty { text-align: center; padding: 80px 0; color: var(--dim); font-size: 15px; }
  .pager { display: flex; justify-content: center; align-items: center; gap: 6px; margin-top: 24px; font-size: 14px; flex-wrap: wrap; }
  .pager button { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 7px 14px; cursor: pointer; font-size: 13px; min-width: 38px; text-align: center; transition: all 0.2s; }
  .pager button:hover { border-color: var(--bili-pink); color: var(--bili-pink); }
  .pager button:disabled { opacity: 0.3; cursor: default; }
  .pager button.active { background: var(--bili-pink); color: #fff; border-color: var(--bili-pink); font-weight: 600; }
  footer { text-align: center; padding: 24px; color: var(--dim); font-size: 12px; }
  @media (max-width: 600px) {
    .main { padding: 12px 8px; }
    .topbar { padding: 0 12px; height: 44px; }
    .topbar .logo { font-size: 14px; }
    .search-box { width: 130px; }
    .report-content { font-size: 14px; }
  }"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="referrer" content="no-referrer">
<script>var bgs=['bg.jpg','bg2.jpg','bg3.jpg','bg4.jpg'];document.documentElement.style.setProperty('--bg-image','url('+bgs[Math.floor(Math.random()*bgs.length)]+')');</script>
<title>举报追踪 · 社区评论观察台</title>
<style>
{css}
</style>
<link rel="stylesheet" href="dashboard.css">
</head>
<body class="page-reports">
<div class="topbar">
  <div class="logo">🚩 举报反馈</div>
  <button class="theme-toggle" onclick="toggleTheme()" title="切换主题">🌓</button>
</div>
<div class="main">
<div class="nav"><a href="./">评论</a><a href="users.html">名人堂</a><a href="report_status.html" class="active">举报反馈</a></div>
<h1>举报处理状态 <span>B站举报API反馈</span></h1>
<div class="stats">
  <div>总举报 <b>{total}</b> 条</div>
  <div>已删除 <b>{removed_count}</b> 条</div>
  <div>未删除 <b>{still_there}</b> 条</div>
  <div>处理中 <b>{pending}</b> 条</div>
  <div>更新于 <b>{now}</b></div>
</div>
<div class="toolbar">
  <div style="display:flex;align-items:center;gap:10px;">
    <select id="sortSelect" onchange="render()"><option value="newest">最新举报</option><option value="oldest">最早举报</option></select>
    <input type="text" id="searchInput" class="search-box" placeholder="搜索用户/内容/理由..." oninput="search()">
  </div>
  <div><span id="pageInfo">1/1</span><span id="searchInfo" class="search-result" style="margin-left:10px;display:none;"></span></div>
</div>
<div id="cardsContainer"></div>
<div class="pager" id="pager"></div>
<footer>DeepSeek AI · 举报反馈实时更新</footer>
</div>

<script>
(function() {{
  var saved = localStorage.getItem('theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
}})();
function toggleTheme() {{
  var current = document.documentElement.getAttribute('data-theme');
  var next = current === 'dark' ? '' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
}}

var ALL_REPORTS = {json.dumps(items, ensure_ascii=False)};

const PER_PAGE = 20;
let currentSort = 'newest';
let currentPage = 1;
let currentKeyword = '';

function getSorted() {{
  let list = [...ALL_REPORTS];
  if (currentSort === 'newest') return list;
  list.reverse();
  return list;
}}

function getFiltered() {{
  const sorted = getSorted();
  if (!currentKeyword) return sorted;
  const kw = currentKeyword.toLowerCase();
  return sorted.filter(r =>
    (r.user || '').toLowerCase().includes(kw) ||
    (r.content || '').toLowerCase().includes(kw) ||
    (r.reason || '').toLowerCase().includes(kw) ||
    (r.result || '').toLowerCase().includes(kw)
  );
}}

function esc(s) {{ if (!s) return ''; s = String(s); return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }}

function fmtTime(t) {{
  if (!t) return '';
  var s = t.substring(0, 16) || t;
  return s.replace('T', ' ');
}}

function statusInfo(r) {{
  if (r === 'removed') return {{ cls: 'status-removed', text: '已删除' }};
  if (r === 'still_there') return {{ cls: 'status-still', text: '未删除' }};
  if (r === 'pending' || !r) return {{ cls: 'status-pending', text: '处理中' }};
  if (r === 'check_failed') return {{ cls: 'status-other', text: '检查失败' }};
  return {{ cls: 'status-other', text: r.substring(0, 12) }};
}}

function render() {{
  currentSort = document.getElementById('sortSelect').value;
  const filtered = getFiltered();
  const totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  if (currentPage > totalPages) currentPage = totalPages;
  if (currentPage < 1) currentPage = 1;
  const start = (currentPage - 1) * PER_PAGE;
  const pageItems = filtered.slice(start, start + PER_PAGE);

  let html = '';
  if (pageItems.length === 0) {{
    html = '<div class="empty">' + (currentKeyword ? '没有匹配的结果' : '暂无举报记录') + '</div>';
  }} else {{
    for (const r of pageItems) {{
      var st = statusInfo(r.result);
      var timeStr = esc(fmtTime(r.comment_time)) || '?';
      var reportStr = esc(fmtTime(r.reported_at)) || '?';
      var checkStr = esc(fmtTime(r.checked_at));
      html += '<div class="report-item">'
        + '<div class="report-status"><span class="status-badge ' + st.cls + '">' + st.text + '</span></div>'
        + '<div class="report-body">'
        + '<div class="report-user-row">'
        + '<span class="report-username">' + esc(r.user) + '</span>'
        + '<span class="reason-tag">' + esc(r.reason) + '</span>'
        + '</div>'
        + '<div class="report-content">' + esc(r.content) + '</div>'
        + '<div class="report-meta">'
        + '<span>评论 ' + timeStr + '</span>'
        + '<span>举报 ' + reportStr + '</span>'
        + (checkStr ? '<span>检查 ' + checkStr + '</span>' : '')
        + '</div>'
        + '</div>'
        + '</div>';
    }}
  }}

  document.getElementById('cardsContainer').innerHTML = html;
  document.getElementById('pageInfo').textContent = currentPage + '/' + totalPages;

  var pagerHtml = '';
  pagerHtml += '<button onclick="goPage(' + (currentPage - 1) + ')"' + (currentPage <= 1 ? ' disabled' : '') + '>← 上一页</button>';
  var pages = [];
  if (totalPages <= 7) {{ for (var i = 1; i <= totalPages; i++) pages.push(i); }}
  else if (currentPage <= 4) {{ pages = [1, 2, 3, 4, 5, '...', totalPages]; }}
  else if (currentPage >= totalPages - 3) {{ pages = [1, '...', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages]; }}
  else {{ pages = [1, '...', currentPage - 1, currentPage, currentPage + 1, '...', totalPages]; }}
  for (var i = 0; i < pages.length; i++) {{
    if (pages[i] === '...') {{ pagerHtml += '<span style="padding: 7px 6px; color: var(--dim);">...</span>'; }}
    else {{ pagerHtml += '<button onclick="goPage(' + pages[i] + ')"' + (pages[i] === currentPage ? ' class="active"' : '') + '>' + pages[i] + '</button>'; }}
  }}
  pagerHtml += '<button onclick="goPage(' + (currentPage + 1) + ')"' + (currentPage >= totalPages ? ' disabled' : '') + '>下一页 →</button>';
  document.getElementById('pager').innerHTML = pagerHtml;

  var si = document.getElementById('searchInfo');
  if (currentKeyword) {{ si.textContent = '找到 ' + filtered.length + ' 条'; si.style.display = 'inline'; }}
  else {{ si.style.display = 'none'; }}
}}

function search() {{
  currentKeyword = document.getElementById('searchInput').value.trim();
  currentPage = 1;
  render();
}}

function goPage(p) {{
  const filtered = getFiltered();
  const totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  if (p < 1) p = 1; if (p > totalPages) p = totalPages;
  currentPage = p; render();
}}

render();
</script>
<!-- rendered at {ts} -->
</body>
</html>"""

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"report_status.html: {total} reports ({removed_count} removed, {still_there} still_there, {pending} pending)")

if __name__ == "__main__":
    build()
