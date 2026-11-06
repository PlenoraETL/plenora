/**
 * Una guida d'uso (src/utils/guides) come Markdown del repository
 * (docs/guide/<slug>.md). Il file si rigenera con
 *   UPDATE_GUIDES=1 npx vitest run tests/shared/userGuides.contract.test.jsx
 * e il test lo confronta col contenuto mostrato nell'app.
 */
export function guideToMarkdown(guide, sourcePath, where = 'dal nodo «Input di progetto» (Guida completa)') {
  const out = [
    `# ${guide.title}`,
    '',
    `<!-- Generato da ${sourcePath}: è la stessa guida che si apre nell'app ${where}. Modifica quel file e rigenera; non modificare questo a mano. -->`,
    '',
  ];
  if (guide.intro) out.push(guide.intro, '');
  for (const section of guide.sections) {
    out.push(`## ${section.title}`, '');
    for (const block of section.blocks) {
      if (block.p) out.push(block.p, '');
      else if (block.note) out.push(`> **Attenzione:** ${block.note}`, '');
      else if (block.code) out.push('```text', block.code, '```', '');
      else if (block.ul) out.push(...block.ul.map(item => `- ${item}`), '');
      else if (block.ol) out.push(...block.ol.map((item, i) => `${i + 1}. ${item}`), '');
    }
  }
  return `${out.join('\n').trimEnd()}\n`;
}
