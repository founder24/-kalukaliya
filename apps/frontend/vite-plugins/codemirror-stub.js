/**
 * Task #362 — codemirror-stub plugin.
 *
 * Stubs every CodeMirror / @lezer / cm6-theme / `codemirror` umbrella
 * import with no-op shims at build time. The admin MDX editor uses a
 * custom textarea-based code-block descriptor (PlainCodeBlockEditor)
 * and never mounts MDXEditor's CodeMirror-backed editor, but
 * @mdxeditor/editor's main barrel and @codesandbox/sandpack-react
 * statically import dozens of CodeMirror APIs that drag in ~580 KB
 * of runtime + parsers. Tree-shaking can't drop them.
 *
 * This plugin scans every transformed module for matching imports and
 * rewrites them inline:
 *
 *   import { foo, bar as baz } from "@codemirror/view";
 *   import * as ns from "@codemirror/state";
 *   import cm from "codemirror";
 *
 * becomes:
 *
 *   const __cm_stub_xxx = (() => {
 *     const noop = () => {};
 *     return new Proxy({}, { get: () => noop });
 *   })();
 *   const { foo, bar: baz } = __cm_stub_xxx;
 *   const ns = __cm_stub_xxx;
 *   const cm = __cm_stub_xxx;
 *
 * Because the stub is an inline expression with a Proxy, it answers
 * any property access with a noop, including any new APIs added in
 * future CodeMirror releases — no whack-a-mole required.
 *
 * Caveats:
 * - All CodeMirror APIs become inert. Anything calling them at runtime
 *   crashes. We rely on the editor never being constructed.
 * - We skip ESM `export ... from` re-exports because rewriting them is
 *   tricky; instead we replace the source-side imports.
 */
const TARGETS = /^(@codemirror\/|codemirror$|cm6-theme-|@lezer\/(?!common$|highlight$|lr$))/;

const IMPORT_REGEX =
  /import\s+(?:(\*\s+as\s+\w+)|(\w+\s*,\s*\{[^}]*\})|(\w+)|(\{[^}]*\}))\s+from\s+(['"])([^'"]+)\5\s*;?/g;

const SIDE_EFFECT_IMPORT_REGEX = /import\s+(['"])([^'"]+)\1\s*;?/g;

let counter = 0;

function makeStubExpr() {
  return `((()=>{const n=()=>{};const c=class{};const f={of:n,from:n,computeN:n,compute:n};const o=new Proxy(function(){},{get(t,p){if(p==='of'||p==='from'||p==='computeN'||p==='compute'||p==='define'||p==='create'||p==='define'||p==='fromClass'||p==='allowMultipleSelections'||p==='readOnly'||p==='lineWrapping'||p==='editable'||p==='updateListener'||p==='domEventHandlers'||p==='theme'||p==='baseTheme'||p==='contentAttributes'||p==='editorAttributes'||p==='decorations'||p==='atomicRanges'||p==='styleModule'||p==='announce'||p==='findFromDOM'||p==='phrases'||p==='mark'||p==='widget'||p==='line'||p==='replace'||p==='none'){return f;}if(p==='prototype'){return Object.create(null);}if(p===Symbol.toPrimitive){return ()=>'';}if(p==='__esModule'){return true;}return n;},apply(){return undefined;},construct(){return Object.create(null);}});return o;})())`;
}

function rewriteImports(code) {
  let modified = false;
  let stubVar = null;

  // Side-effect-only imports: `import "@codemirror/view";`
  let result = code.replace(SIDE_EFFECT_IMPORT_REGEX, (m, _q, src) => {
    if (TARGETS.test(src)) {
      modified = true;
      return '';
    }
    return m;
  });

  result = result.replace(IMPORT_REGEX, (match, ns, defNamed, def, named, _q, src) => {
    if (!TARGETS.test(src)) return match;
    modified = true;
    if (!stubVar) stubVar = `__cm_stub_${counter++}`;

    const parts = [];
    if (ns) {
      const name = ns.replace(/^\*\s+as\s+/, '').trim();
      parts.push(`const ${name} = ${stubVar};`);
    } else if (defNamed) {
      const m2 = defNamed.match(/^(\w+)\s*,\s*\{([^}]*)\}$/);
      if (m2) {
        parts.push(`const ${m2[1]} = ${stubVar};`);
        const list = m2[2]
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean)
          .map((s) => s.replace(/\s+as\s+/, ': '))
          .join(', ');
        if (list) parts.push(`const { ${list} } = ${stubVar};`);
      }
    } else if (def) {
      parts.push(`const ${def.trim()} = ${stubVar};`);
    } else if (named) {
      const list = named
        .replace(/[{}]/g, '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
        .map((s) => s.replace(/\s+as\s+/, ': '))
        .join(', ');
      if (list) parts.push(`const { ${list} } = ${stubVar};`);
    }
    return parts.join(' ');
  });

  if (!modified) return null;

  const prelude = `const ${stubVar || `__cm_stub_${counter++}`} = ${makeStubExpr()};\n`;
  if (!stubVar) return null;
  return prelude + result;
}

export default function codemirrorStubPlugin() {
  return {
    name: 'task-362-codemirror-stub',
    enforce: 'pre',
    transform(code, id) {
      if (!/\.(m?js|jsx|ts|tsx)$/.test(id)) return null;
      if (!/@codemirror|codemirror|cm6-theme|@lezer/.test(code)) return null;
      const out = rewriteImports(code);
      if (!out) return null;
      return { code: out, map: null };
    },
    resolveId(id) {
      // Make sure the original CM packages still "resolve" so rollup
      // doesn't error before transform — it just never reads them.
      if (TARGETS.test(id)) {
        return { id: '\0task-362-cm-empty', moduleSideEffects: false };
      }
      return null;
    },
    load(id) {
      if (id === '\0task-362-cm-empty') return 'export {};';
      return null;
    },
  };
}
