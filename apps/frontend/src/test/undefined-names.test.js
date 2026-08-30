import { ESLint } from 'eslint';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const configFile = path.join(frontendRoot, 'eslint.config.js');

async function lintSnippet(code) {
  const eslint = new ESLint({
    cwd: frontendRoot,
    overrideConfigFile: configFile,
  });
  const [result] = await eslint.lintText(code, {
    filePath: path.join(frontendRoot, 'src/test/fixtures/undefined-name-regression.jsx'),
  });
  return result.messages;
}

describe('undefined frontend names check', () => {
  it('rejects JSX components that are not imported or declared', async () => {
    const messages = await lintSnippet(`
      export default function BrokenComponent() {
        return <MissingStatusIcon />;
      }
    `);

    expect(messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          ruleId: 'react/jsx-no-undef',
          message: "'MissingStatusIcon' is not defined.",
        }),
      ]),
    );
  });

  it('rejects handlers that are not destructured props or declared names', async () => {
    const messages = await lintSnippet(`
      export default function BrokenButton({ onSave }) {
        return <button onClick={onMissingHandler}>Save</button>;
      }
    `);

    expect(messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          ruleId: 'no-undef',
          message: "'onMissingHandler' is not defined.",
        }),
      ]),
    );
  });
});