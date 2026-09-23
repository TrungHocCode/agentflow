import React from 'react';

const INLINE_MARKDOWN = /(`[^`]+`|\*\*.+?\*\*|__.+?__|\*[^*\n]+\*|_[^_\n]+_|\[[^\]]+\]\([^)]+\))/g;

function inlineParts(value) {
  const text = String(value || '');
  const parts = [];
  let cursor = 0;

  for (const match of text.matchAll(INLINE_MARKDOWN)) {
    const token = match[0];
    const start = match.index ?? cursor;
    if (start > cursor) parts.push(<React.Fragment key={`text-${cursor}`}>{text.slice(cursor, start)}</React.Fragment>);

    if (token.startsWith('`')) {
      parts.push(<code key={`code-${start}`} className="inline-code">{token.slice(1, -1)}</code>);
    } else if (token.startsWith('**') || token.startsWith('__')) {
      parts.push(<strong key={`strong-${start}`}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('*') || token.startsWith('_')) {
      parts.push(<em key={`em-${start}`}>{token.slice(1, -1)}</em>);
    } else {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const href = link?.[2]?.trim();
      const isSafeLink = href && /^(https?:\/\/|\/(?!\/)|#)/i.test(href);
      parts.push(isSafeLink
        ? <a key={`link-${start}`} href={href} target="_blank" rel="noreferrer">{link[1]}</a>
        : <React.Fragment key={`text-${start}`}>{link?.[1] || token}</React.Fragment>);
    }
    cursor = start + token.length;
  }

  if (cursor < text.length) parts.push(<React.Fragment key={`text-${cursor}`}>{text.slice(cursor)}</React.Fragment>);
  return parts;
}

function splitTableRow(line) {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  return trimmed.split('|').map((cell) => cell.trim());
}

function isTableDelimiter(line) {
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function tableBlock(lines, start) {
  const header = splitTableRow(lines[start]);
  const rows = [];
  let cursor = start + 2;
  while (cursor < lines.length && lines[cursor].trim().startsWith('|')) {
    rows.push(splitTableRow(lines[cursor]));
    cursor += 1;
  }

  return {
    element: (
      <div className="markdown-table-wrap">
        <table className="message-table">
          <thead><tr>{header.map((cell, index) => <th key={index}>{inlineParts(cell)}</th>)}</tr></thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {header.map((_, index) => <td key={index}>{inlineParts(row[index] || '')}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ),
    next: cursor
  };
}

function isUnorderedItem(line) {
  return /^\s*[-*+]\s+/.test(line);
}

function isOrderedItem(line) {
  return /^\s*\d+[.)]\s+/.test(line);
}

function isBlockStart(lines, index) {
  const line = lines[index] || '';
  return line.startsWith('```') ||
    /^#{1,6}\s/.test(line) ||
    /^\s*>/.test(line) ||
    isUnorderedItem(line) ||
    isOrderedItem(line) ||
    /^\s*(?:-{3,}|_{3,}|\*(?:\s*\*){2,})\s*$/.test(line) ||
    (line.trim().startsWith('|') && index + 1 < lines.length && isTableDelimiter(lines[index + 1]));
}

export default function StructuredText({ text }) {
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (line.startsWith('```')) {
      const language = line.slice(3).trim();
      const code = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith('```')) {
        code.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(<pre key={`code-block-${blocks.length}`} className="message-code"><code data-language={language}>{code.join('\n')}</code></pre>);
      continue;
    }

    if (line.trim().startsWith('|') && index + 1 < lines.length && isTableDelimiter(lines[index + 1])) {
      const block = tableBlock(lines, index);
      blocks.push(<React.Fragment key={`table-${blocks.length}`}>{block.element}</React.Fragment>);
      index = block.next;
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const Heading = `h${heading[1].length}`;
      blocks.push(<Heading key={`heading-${blocks.length}`}>{inlineParts(heading[2])}</Heading>);
      index += 1;
      continue;
    }

    if (/^\s*(?:-{3,}|_{3,}|\*(?:\s*\*){2,})\s*$/.test(line)) {
      blocks.push(<hr key={`rule-${blocks.length}`} />);
      index += 1;
      continue;
    }

    if (/^\s*>/.test(line)) {
      const quote = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) {
        quote.push(lines[index].replace(/^\s*>\s?/, ''));
        index += 1;
      }
      blocks.push(<blockquote key={`quote-${blocks.length}`}>{quote.map((part, quoteIndex) => <p key={quoteIndex}>{inlineParts(part)}</p>)}</blockquote>);
      continue;
    }

    if (isUnorderedItem(line)) {
      const items = [];
      while (index < lines.length && isUnorderedItem(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*+]\s+/, ''));
        index += 1;
      }
      blocks.push(<ul key={`list-${blocks.length}`}>{items.map((item, itemIndex) => <li key={itemIndex}>{inlineParts(item)}</li>)}</ul>);
      continue;
    }

    if (isOrderedItem(line)) {
      const items = [];
      const firstNumber = Number(line.match(/^\s*(\d+)/)?.[1] || 1);
      while (index < lines.length && isOrderedItem(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+[.)]\s+/, ''));
        index += 1;
      }
      blocks.push(<ol key={`ordered-list-${blocks.length}`} start={firstNumber}>{items.map((item, itemIndex) => <li key={itemIndex}>{inlineParts(item)}</li>)}</ol>);
      continue;
    }

    const paragraph = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(<p key={`paragraph-${blocks.length}`}>{inlineParts(paragraph.join(' '))}</p>);
  }

  return <div className="structured-text">{blocks}</div>;
}
