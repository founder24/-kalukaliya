/**
 * Preserve stored study text without HTML injection or changing its facts.
 * Legacy generators use TeX delimiters and unfenced box/arrow diagrams.
 */
export function normalizeStudyMarkdown(value) {
  const lines = String(value ?? '').replace(/\r\n?/g, '\n').split('\n');
  const output = [];
  let fence = null;
  const isDiagramLine = (line) => /^[\s┌┐└┘├┤┬┴┼─│┏┓┗┛━┃+|=\-<>]+$/u.test(line)
    && /[┌┐└┘├┤┬┴┼─│┏┓┗┛━┃]|[-=]{2,}/u.test(line);
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const marker = /^\s*(`{3,}|~{3,})/.exec(line)?.[1];
    if (marker) {
      if (!fence) fence = marker;
      else if (marker[0] === fence[0] && marker.length >= fence.length) fence = null;
      output.push(line);
      continue;
    }
    if (fence) {
      output.push(line);
      continue;
    }
    // Legacy notes put labels above/below figures as indented text. Preserve
    // the whole contiguous region, not just its border, without converting
    // ordinary indented lists or Markdown tables into code.
    if (/^\s{2,}\S/.test(line) && !/^\s*(?:[-*+] |\d+\.\s)/.test(line)) {
      let end = index;
      while (end < lines.length && /^\s{2,}\S/.test(lines[end])) end += 1;
      const block = lines.slice(index, end);
      if (block.length > 1
        && !block.some(part => /^\s*\|(?:\s*:?-+:?\s*\|)+\s*$/.test(part))
        && block.some(part => isDiagramLine(part)
        || /^\s*\+(?:[-=]{2,}\+)+\s*$/.test(part)
        || /^\s{2,}[\\/].*(?: {3,}|_{3,})/.test(part)
        || /\S\s{2,}(?:-{2,}>|<[-=]{2,}>)/.test(part))) {
        output.push('', '```text', ...block, '```', '');
        index = end - 1;
        continue;
      }
    }
    // Only a structural border starts an unindented diagram. An inline box
    // glyph in prose or a table cell must not change that content's semantics.
    if (isDiagramLine(line)) {
      let end = index + 1;
      while (end < lines.length && lines[end].trim()
        && !/^\s{0,3}\|.*\|/.test(lines[end])
        && (/[┌┐└┘├┤┬┴┼─│┏┓┗┛━┃]/u.test(lines[end]) || isDiagramLine(lines[end]))) end += 1;
      if (end - index > 1) {
        output.push('', '```text', ...lines.slice(index, end), '```', '');
        index = end - 1;
        continue;
      }
    }
    // Inline code must remain literal, including any TeX examples in it.
    output.push(line.split(/(`+[^`]*`+)/g).map((part, index) => index % 2 ? part : part
      .replace(/\\\[/g, () => '\n$$\n').replace(/\\\]/g, () => '\n$$\n')
      .replace(/\\\(/g, '$').replace(/\\\)/g, '$')).join(''));
  }
  return output.join('\n');
}
