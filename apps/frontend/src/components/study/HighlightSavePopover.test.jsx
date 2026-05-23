import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import React from 'react';

vi.mock('@/utils/studyApi', () => ({
  studyApi: { createNote: vi.fn() },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('./QuizModal', () => ({
  QuizModal: () => null,
}));

import { HighlightSavePopover } from './HighlightSavePopover.jsx';

let savableEl;
let getSelectionSpy;
let rafSpy;

function makeFakeSelection(text = 'this is long enough text') {
  const range = {
    commonAncestorContainer: savableEl,
    getBoundingClientRect: () => ({ left: 120, top: 80, width: 60, height: 18 }),
  };
  return {
    isCollapsed: false,
    toString: () => text,
    getRangeAt: () => range,
  };
}

async function triggerSelectionChange(text) {
  getSelectionSpy.mockReturnValue(makeFakeSelection(text));
  await act(async () => {
    document.dispatchEvent(new Event('selectionchange'));
    await new Promise((r) => setTimeout(r, 0));
  });
}

beforeEach(() => {
  savableEl = document.createElement('div');
  savableEl.setAttribute('data-savable', 'true');
  document.body.appendChild(savableEl);

  getSelectionSpy = vi.spyOn(window, 'getSelection');

  rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
    cb(0);
    return 0;
  });
});

afterEach(() => {
  getSelectionSpy.mockRestore();
  rafSpy.mockRestore();
  if (savableEl && savableEl.parentNode) {
    savableEl.parentNode.removeChild(savableEl);
  }
  vi.clearAllMocks();
});

describe('HighlightSavePopover — hideSave prop', () => {
  it('hides the Save button and divider but keeps Quiz me when hideSave={true}', async () => {
    render(<HighlightSavePopover hideSave={true} />);

    await triggerSelectionChange('enough text to show popover');

    expect(screen.queryByText(/save/i)).toBeNull();
    expect(document.querySelector('.w-px.h-5')).toBeNull();
    expect(screen.getByText(/quiz me/i)).toBeInTheDocument();
  });

  it('shows the Save button when hideSave={false}', async () => {
    render(<HighlightSavePopover hideSave={false} />);

    await triggerSelectionChange('enough text to show popover');

    expect(screen.getByText(/save/i)).toBeInTheDocument();
    expect(screen.getByText(/quiz me/i)).toBeInTheDocument();
    expect(document.querySelector('.w-px.h-5')).toBeInTheDocument();
  });

  it('does not render the popover at all when hideSave={true} and hideQuiz={true}', async () => {
    render(<HighlightSavePopover hideSave hideQuiz />);

    await triggerSelectionChange('enough text to show popover');

    expect(screen.queryByText(/save/i)).toBeNull();
    expect(screen.queryByText(/quiz me/i)).toBeNull();
    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeNull();
  });
});

describe('HighlightSavePopover — hideQuiz prop', () => {
  it('hides the Quiz me button and divider when hideQuiz={true}', async () => {
    render(<HighlightSavePopover hideQuiz={true} />);

    await triggerSelectionChange('enough text to show popover');

    expect(screen.queryByText(/quiz me/i)).toBeNull();
    expect(screen.getByText(/save/i)).toBeInTheDocument();
    expect(document.querySelector('.w-px.h-5')).toBeNull();
  });

  it('shows the Quiz me button when hideQuiz={false}', async () => {
    render(<HighlightSavePopover hideQuiz={false} />);

    await triggerSelectionChange('enough text to show popover');

    expect(screen.getByText(/quiz me/i)).toBeInTheDocument();
    expect(screen.getByText(/save/i)).toBeInTheDocument();
    expect(document.querySelector('.w-px.h-5')).toBeInTheDocument();
  });

  it('does not show the popover at all when text is too short', async () => {
    render(<HighlightSavePopover hideQuiz={false} />);

    await triggerSelectionChange('hi');

    expect(screen.queryByText(/quiz me/i)).toBeNull();
    expect(screen.queryByText(/save/i)).toBeNull();
  });
});

describe('HighlightSavePopover — edge cases', () => {
  it('does not show the popover for text of exactly 5 chars (below minimum)', async () => {
    render(<HighlightSavePopover />);

    await triggerSelectionChange('abcde');

    expect(screen.queryByText(/save/i)).toBeNull();
    expect(screen.queryByText(/quiz me/i)).toBeNull();
    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeNull();
  });

  it('does not show the popover for text of exactly 4001 chars (above maximum)', async () => {
    render(<HighlightSavePopover />);

    await triggerSelectionChange('a'.repeat(4001));

    expect(screen.queryByText(/save/i)).toBeNull();
    expect(screen.queryByText(/quiz me/i)).toBeNull();
    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeNull();
  });

  it('shows the popover for text of exactly 6 chars (minimum valid length)', async () => {
    render(<HighlightSavePopover />);

    await triggerSelectionChange('abcdef');

    expect(screen.getByText(/save/i)).toBeInTheDocument();
    expect(screen.getByText(/quiz me/i)).toBeInTheDocument();
    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeInTheDocument();
  });

  it('shows the popover for text of exactly 4000 chars (maximum valid length)', async () => {
    render(<HighlightSavePopover />);

    await triggerSelectionChange('a'.repeat(4000));

    expect(screen.getByText(/save/i)).toBeInTheDocument();
    expect(screen.getByText(/quiz me/i)).toBeInTheDocument();
    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeInTheDocument();
  });

  it('does not show the popover when the selection is collapsed', async () => {
    render(<HighlightSavePopover />);

    getSelectionSpy.mockReturnValue({ isCollapsed: true, toString: () => 'enough text here', getRangeAt: () => ({}) });
    await act(async () => {
      document.dispatchEvent(new Event('selectionchange'));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(screen.queryByText(/save/i)).toBeNull();
    expect(screen.queryByText(/quiz me/i)).toBeNull();
    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeNull();
  });

  it('does not show the popover when the commonAncestorContainer is a text node outside any savable container', async () => {
    render(<HighlightSavePopover />);

    const plainDiv = document.createElement('div');
    document.body.appendChild(plainDiv);
    const outsideTextNode = document.createTextNode('enough text to show popover');
    plainDiv.appendChild(outsideTextNode);

    const outsideTextNodeRange = {
      commonAncestorContainer: outsideTextNode,
      getBoundingClientRect: () => ({ left: 50, top: 50, width: 80, height: 18 }),
    };
    getSelectionSpy.mockReturnValue({
      isCollapsed: false,
      toString: () => 'enough text to show popover',
      getRangeAt: () => outsideTextNodeRange,
    });

    await act(async () => {
      document.dispatchEvent(new Event('selectionchange'));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(screen.queryByText(/save/i)).toBeNull();
    expect(screen.queryByText(/quiz me/i)).toBeNull();
    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeNull();

    plainDiv.parentNode.removeChild(plainDiv);
  });

  it('shows the popover when the commonAncestorContainer is a text node inside a savable element', async () => {
    render(<HighlightSavePopover />);

    const textNode = document.createTextNode('highlighted content inside savable');
    savableEl.appendChild(textNode);

    const textNodeRange = {
      commonAncestorContainer: textNode,
      getBoundingClientRect: () => ({ left: 100, top: 60, width: 80, height: 18 }),
    };
    getSelectionSpy.mockReturnValue({
      isCollapsed: false,
      toString: () => 'highlighted content inside savable',
      getRangeAt: () => textNodeRange,
    });

    await act(async () => {
      document.dispatchEvent(new Event('selectionchange'));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeInTheDocument();
  });

  it('shows the popover when data-savable is on a distant ancestor (deeply nested text node)', async () => {
    render(<HighlightSavePopover />);

    const section = document.createElement('section');
    const p = document.createElement('p');
    const span = document.createElement('span');
    const deepTextNode = document.createTextNode('deeply nested highlighted content');
    span.appendChild(deepTextNode);
    p.appendChild(span);
    section.appendChild(p);
    savableEl.appendChild(section);

    const deepRange = {
      commonAncestorContainer: deepTextNode,
      getBoundingClientRect: () => ({ left: 100, top: 60, width: 80, height: 18 }),
    };
    getSelectionSpy.mockReturnValue({
      isCollapsed: false,
      toString: () => 'deeply nested highlighted content',
      getRangeAt: () => deepRange,
    });

    await act(async () => {
      document.dispatchEvent(new Event('selectionchange'));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeInTheDocument();
    expect(screen.getByText(/save/i)).toBeInTheDocument();
    expect(screen.getByText(/quiz me/i)).toBeInTheDocument();
  });

  it('does not show the popover for a selection inside a data-savable="false" div nested within a data-savable="true" container', async () => {
    render(<HighlightSavePopover />);

    const outerSavable = document.createElement('div');
    outerSavable.setAttribute('data-savable', 'true');
    const section = document.createElement('section');
    const optOutDiv = document.createElement('div');
    optOutDiv.setAttribute('data-savable', 'false');
    const textNode = document.createTextNode('text inside opt-out zone');
    optOutDiv.appendChild(textNode);
    section.appendChild(optOutDiv);
    outerSavable.appendChild(section);
    document.body.appendChild(outerSavable);

    const optOutRange = {
      commonAncestorContainer: textNode,
      getBoundingClientRect: () => ({ left: 100, top: 60, width: 80, height: 18 }),
    };
    getSelectionSpy.mockReturnValue({
      isCollapsed: false,
      toString: () => 'text inside opt-out zone',
      getRangeAt: () => optOutRange,
    });

    await act(async () => {
      document.dispatchEvent(new Event('selectionchange'));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(screen.queryByText(/save/i)).toBeNull();
    expect(screen.queryByText(/quiz me/i)).toBeNull();
    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeNull();

    outerSavable.parentNode.removeChild(outerSavable);
  });

  it('shows the popover for a selection in a plain sibling div next to a data-savable="false" opt-out zone', async () => {
    render(<HighlightSavePopover />);

    const outerSavable = document.createElement('div');
    outerSavable.setAttribute('data-savable', 'true');

    const optOutDiv = document.createElement('div');
    optOutDiv.setAttribute('data-savable', 'false');
    optOutDiv.appendChild(document.createTextNode('opt-out content'));

    const plainDiv = document.createElement('div');
    const plainTextNode = document.createTextNode('valid highlighted content here');
    plainDiv.appendChild(plainTextNode);

    outerSavable.appendChild(optOutDiv);
    outerSavable.appendChild(plainDiv);
    document.body.appendChild(outerSavable);

    const plainRange = {
      commonAncestorContainer: plainTextNode,
      getBoundingClientRect: () => ({ left: 100, top: 60, width: 80, height: 18 }),
    };
    getSelectionSpy.mockReturnValue({
      isCollapsed: false,
      toString: () => 'valid highlighted content here',
      getRangeAt: () => plainRange,
    });

    await act(async () => {
      document.dispatchEvent(new Event('selectionchange'));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeInTheDocument();
    expect(screen.getByText(/save/i)).toBeInTheDocument();
    expect(screen.getByText(/quiz me/i)).toBeInTheDocument();

    outerSavable.parentNode.removeChild(outerSavable);
  });

  it('shows the popover when a selection spans across a data-savable="false" opt-out zone into a plain sibling (commonAncestorContainer is the outer savable div)', async () => {
    render(<HighlightSavePopover />);

    const outerSavable = document.createElement('div');
    outerSavable.setAttribute('data-savable', 'true');

    const optOutDiv = document.createElement('div');
    optOutDiv.setAttribute('data-savable', 'false');
    optOutDiv.appendChild(document.createTextNode('opt-out content'));

    const plainDiv = document.createElement('div');
    plainDiv.appendChild(document.createTextNode('plain sibling content'));

    outerSavable.appendChild(optOutDiv);
    outerSavable.appendChild(plainDiv);
    document.body.appendChild(outerSavable);

    const crossBoundaryRange = {
      commonAncestorContainer: outerSavable,
      getBoundingClientRect: () => ({ left: 100, top: 60, width: 80, height: 18 }),
    };
    getSelectionSpy.mockReturnValue({
      isCollapsed: false,
      toString: () => 'opt-out content plain sibling content',
      getRangeAt: () => crossBoundaryRange,
    });

    await act(async () => {
      document.dispatchEvent(new Event('selectionchange'));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeInTheDocument();
    expect(screen.getByText(/save/i)).toBeInTheDocument();
    expect(screen.getByText(/quiz me/i)).toBeInTheDocument();

    outerSavable.parentNode.removeChild(outerSavable);
  });

  it('does not show the popover when a selection spans from inside a savable area to completely outside it (commonAncestorContainer is non-savable)', async () => {
    render(<HighlightSavePopover />);

    const wrapperDiv = document.createElement('div');
    const innerSavable = document.createElement('div');
    innerSavable.setAttribute('data-savable', 'true');
    innerSavable.appendChild(document.createTextNode('text inside savable'));
    const outerSibling = document.createElement('div');
    outerSibling.appendChild(document.createTextNode('text completely outside savable'));
    wrapperDiv.appendChild(innerSavable);
    wrapperDiv.appendChild(outerSibling);
    document.body.appendChild(wrapperDiv);

    const crossOutsideRange = {
      commonAncestorContainer: wrapperDiv,
      getBoundingClientRect: () => ({ left: 50, top: 50, width: 80, height: 18 }),
    };
    getSelectionSpy.mockReturnValue({
      isCollapsed: false,
      toString: () => 'text inside savable text completely outside savable',
      getRangeAt: () => crossOutsideRange,
    });

    await act(async () => {
      document.dispatchEvent(new Event('selectionchange'));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(screen.queryByText(/save/i)).toBeNull();
    expect(screen.queryByText(/quiz me/i)).toBeNull();
    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeNull();

    wrapperDiv.parentNode.removeChild(wrapperDiv);
  });

  it('does not show the popover when the commonAncestorContainer is document.body (selection escaped all containers)', async () => {
    render(<HighlightSavePopover />);

    const childInsideSavable = document.createElement('span');
    childInsideSavable.appendChild(document.createTextNode('text inside savable'));
    savableEl.appendChild(childInsideSavable);

    const childOutsideSavable = document.createElement('span');
    childOutsideSavable.appendChild(document.createTextNode('text outside any savable container'));
    document.body.appendChild(childOutsideSavable);

    const bodyRange = {
      commonAncestorContainer: document.body,
      getBoundingClientRect: () => ({ left: 50, top: 50, width: 200, height: 18 }),
    };
    getSelectionSpy.mockReturnValue({
      isCollapsed: false,
      toString: () => 'text inside savable text outside any savable container',
      getRangeAt: () => bodyRange,
    });

    await act(async () => {
      document.dispatchEvent(new Event('selectionchange'));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(screen.queryByText(/save/i)).toBeNull();
    expect(screen.queryByText(/quiz me/i)).toBeNull();
    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeNull();

    childOutsideSavable.parentNode.removeChild(childOutsideSavable);
  });

  it('does not show the popover and throws no error when getSelection() returns null', async () => {
    render(<HighlightSavePopover />);

    getSelectionSpy.mockReturnValue(null);

    await expect(
      act(async () => {
        document.dispatchEvent(new Event('selectionchange'));
        await new Promise((r) => setTimeout(r, 0));
      }),
    ).resolves.not.toThrow();

    expect(screen.queryByText(/save/i)).toBeNull();
    expect(screen.queryByText(/quiz me/i)).toBeNull();
    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeNull();
  });

  it('does not show the popover when the selection is outside any savable container', async () => {
    render(<HighlightSavePopover />);

    const plainDiv = document.createElement('div');
    document.body.appendChild(plainDiv);

    const outsideRange = {
      commonAncestorContainer: plainDiv,
      getBoundingClientRect: () => ({ left: 50, top: 50, width: 80, height: 18 }),
    };
    getSelectionSpy.mockReturnValue({
      isCollapsed: false,
      toString: () => 'enough text to show popover',
      getRangeAt: () => outsideRange,
    });

    await act(async () => {
      document.dispatchEvent(new Event('selectionchange'));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(screen.queryByText(/save/i)).toBeNull();
    expect(screen.queryByText(/quiz me/i)).toBeNull();
    expect(document.querySelector('.fixed.z-\\[110\\]')).toBeNull();

    plainDiv.parentNode.removeChild(plainDiv);
  });
});
