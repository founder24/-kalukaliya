import { beforeEach, describe, expect, it } from 'vitest';
import { injectAdScript, removeAdScript } from './adScriptRegistry';

const ADSENSE_SRC = 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-test';

describe('ad script registry', () => {
  beforeEach(() => {
    document.head.innerHTML = '';
    removeAdScript(ADSENSE_SRC);
  });

  it('creates one marked script when the same URL is requested repeatedly', () => {
    injectAdScript(ADSENSE_SRC, { crossorigin: 'anonymous' });
    injectAdScript(ADSENSE_SRC, { crossorigin: 'anonymous' });

    const scripts = document.head.querySelectorAll(
      `script[data-syrabit-ad][src="${ADSENSE_SRC}"]`,
    );
    expect(scripts).toHaveLength(1);
  });

  it('adopts an existing matching script instead of adding another', () => {
    const existing = document.createElement('script');
    existing.src = ADSENSE_SRC;
    document.head.appendChild(existing);

    injectAdScript(ADSENSE_SRC);

    expect(document.head.querySelectorAll(`script[src="${ADSENSE_SRC}"]`)).toHaveLength(1);
  });
});