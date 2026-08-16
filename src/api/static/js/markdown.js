// 轻量 markdown 渲染：标题/列表/表格/代码块/加粗/行内代码/链接（输入先整体转义）

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, url) => {
      // 仅放行 http(s)/mailto 且不含引号——javascript: 协议与属性注入防护
      const safe = /^(https?:\/\/|mailto:)/i.test(url) && !/["']/.test(url);
      return safe
        ? `<a href="${url}" target="_blank" rel="noopener">${text}</a>`
        : text;
    });
}

export function renderMarkdown(text) {
  if (!text) return "";
  const lines = esc(String(text)).split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim().startsWith("```")) {
      const buf = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        buf.push(lines[i]);
        i += 1;
      }
      out.push(`<pre><code>${buf.join("\n")}</code></pre>`);
      i += 1;
      continue;
    }
    const tableHead = line.match(/^\s*\|(.+)\|\s*$/);
    if (tableHead && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const headers = tableHead[1].split("|").map((h) => h.trim());
      const rows = [];
      i += 2;
      while (i < lines.length) {
        const m = lines[i].match(/^\s*\|(.+)\|\s*$/);
        if (!m) break;
        rows.push(m[1].split("|").map((c) => c.trim()));
        i += 1;
      }
      const thead = `<tr>${headers.map((h) => `<th>${inline(h)}</th>`).join("")}</tr>`;
      const tbody = rows
        .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`)
        .join("");
      out.push(`<table><thead>${thead}</thead><tbody>${tbody}</tbody></table>`);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ""))}</li>`);
        i += 1;
      }
      out.push(`<ul>${items.join("")}</ul>`);
      continue;
    }
    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) {
      out.push("<hr>");
      i += 1;
      continue;
    }
    if (line.trim() === "") {
      i += 1;
      continue;
    }
    const para = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() !== ""
           && !/^(#{1,4})\s/.test(lines[i])
           && !/^\s*[-*]\s+/.test(lines[i])
           && !lines[i].trim().startsWith("```")
           && !/^\s*\|/.test(lines[i])) {
      para.push(lines[i]);
      i += 1;
    }
    out.push(`<p>${inline(para.join("<br>"))}</p>`);
  }
  return out.join("");
}
