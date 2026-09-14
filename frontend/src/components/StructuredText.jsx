import React from 'react';

function inlineParts(value) {
  return value.split(/(`[^`]+`|\[[^\]]+\]\([^)]+\))/g).map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`')) {
      return <code key={index} className="inline-code">{part.slice(1, -1)}</code>;
    }
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link) {
      return <a key={index} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    }
    return <React.Fragment key={index}>{part}</React.Fragment>;
  });
}

function tableBlock(lines, start) {
  const header = lines[start].split('|').slice(1, -1).map(cell => cell.trim());
  const rows = [];
  let cursor = start + 2;
  while (cursor < lines.length && lines[cursor].trim().startsWith('|')) {
    rows.push(lines[cursor].split('|').slice(1, -1).map(cell => cell.trim()));
    cursor += 1;
  }
  return {
    element: (
      <table className="message-table">
        <thead><tr>{header.map((cell, index) => <th key={index}>{inlineParts(cell)}</th>)}</tr></thead>
        <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, index) => <td key={index}>{inlineParts(cell)}</td>)}</tr>)}</tbody>
      </table>
    ),
    next: cursor
  };
}

export default function StructuredText({ text }) {
  const lines = String(text || '').split('\n');
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.startsWith('```')) {
      const language = line.slice(3).trim();
      const code = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith('```')) {
        code.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push(<pre key={blocks.length} className="message-code"><code data-language={language}>{code.join('\n')}</code></pre>);
      continue;
    }
    if (line.trim().startsWith('|') && index + 1 < lines.length && /\|?\s*:?-{3,}/.test(lines[index + 1])) {
      const block = tableBlock(lines, index);
      blocks.push(<React.Fragment key={blocks.length}>{block.element}</React.Fragment>);
      index = block.next;
      continue;
    }
    if (/^#{1,4}\s/.test(line)) {
      const level = line.match(/^#+/)[0].length;
      const Heading = `h${level}`;
      blocks.push(<Heading key={blocks.length}>{inlineParts(line.replace(/^#{1,4}\s/, ''))}</Heading>);
    } else if (/^\s*[-*]\s+/.test(line)) {
      blocks.push(<li key={blocks.length}>{inlineParts(line.replace(/^\s*[-*]\s+/, ''))}</li>);
    } else if (/^\s*\d+\.\s+/.test(line)) {
      blocks.push(<li key={blocks.length}>{inlineParts(line.replace(/^\s*\d+\.\s+/, ''))}</li>);
    } else if (line.trim()) {
      blocks.push(<p key={blocks.length}>{inlineParts(line)}</p>);
    }
    index += 1;
  }
  return <div className="structured-text">{blocks}</div>;
}
