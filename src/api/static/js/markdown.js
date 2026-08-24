// 轻量安全 Markdown 渲染：原始输入先整体转义，再识别受限的块级和行内语法。

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function isSafeLink(url) {
  return /^(https?:\/\/|mailto:)/i.test(url) && !/["'`*<>]/.test(url);
}

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label, url) => (
      isSafeLink(url)
        ? `<a href="${url}" target="_blank" rel="noopener">${label}</a>`
        : label
    ));
}

function isUnorderedList(line) {
  return /^\s*[-*]\s+/.test(line);
}

function isOrderedList(line) {
  return /^\s*\d+\.\s+/.test(line);
}

function isQuote(line) {
  return /^\s*&gt;\s?/.test(line);
}

function isBlockStart(line) {
  return /^(#{1,4})\s/.test(line)
    || isUnorderedList(line)
    || isOrderedList(line)
    || isQuote(line)
    || line.trim().startsWith("```")
    || /^\s*\|/.test(line)
    || /^\s*(---+|\*\*\*+)\s*$/.test(line);
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
      const headers = tableHead[1].split("|").map((header) => header.trim());
      const rows = [];
      i += 2;
      while (i < lines.length) {
        const row = lines[i].match(/^\s*\|(.+)\|\s*$/);
        if (!row) break;
        rows.push(row[1].split("|").map((cell) => cell.trim()));
        i += 1;
      }
      const head = `<tr>${headers.map((header) => `<th>${inline(header)}</th>`).join("")}</tr>`;
      const body = rows
        .map((row) => `<tr>${row.map((cell) => `<td>${inline(cell)}</td>`).join("")}</tr>`)
        .join("");
      out.push(`<table><thead>${head}</thead><tbody>${body}</tbody></table>`);
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }

    if (isUnorderedList(line) || isOrderedList(line)) {
      const ordered = isOrderedList(line);
      const pattern = ordered ? /^\s*\d+\.\s+/ : /^\s*[-*]\s+/;
      const tag = ordered ? "ol" : "ul";
      const items = [];
      while (i < lines.length && (ordered ? isOrderedList(lines[i]) : isUnorderedList(lines[i]))) {
        items.push(`<li>${inline(lines[i].replace(pattern, ""))}</li>`);
        i += 1;
      }
      out.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }

    if (isQuote(line)) {
      const quote = [];
      while (i < lines.length && isQuote(lines[i])) {
        quote.push(lines[i].replace(/^\s*&gt;\s?/, ""));
        i += 1;
      }
      out.push(`<blockquote>${quote.map((item) => inline(item)).join("<br>")}</blockquote>`);
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

    const paragraph = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() !== "" && !isBlockStart(lines[i])) {
      paragraph.push(lines[i]);
      i += 1;
    }
    out.push(`<p>${inline(paragraph.join("<br>"))}</p>`);
  }
  return out.join("");
}
