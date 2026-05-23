import {
  UndoRedo, BoldItalicUnderlineToggles, BlockTypeSelect,
  CreateLink, CodeToggle, InsertTable, InsertThematicBreak,
  ListsToggle, Separator, DiffSourceToggleWrapper, InsertCodeBlock,
} from '@mdxeditor/editor';

export default function MdxToolbar({ onAiParse, aiParsing }) {
  return (
    <DiffSourceToggleWrapper>
      <UndoRedo />
      <Separator />
      <BoldItalicUnderlineToggles />
      <CodeToggle />
      <Separator />
      <ListsToggle />
      <Separator />
      <BlockTypeSelect />
      <Separator />
      <CreateLink />
      <InsertTable />
      <InsertThematicBreak />
      <InsertCodeBlock />
      <Separator />
      <button
        type="button"
        onClick={onAiParse}
        disabled={aiParsing}
        title="AI Structure Content"
        style={{
          display: 'flex', alignItems: 'center', gap: 4, padding: '2px 6px',
          borderRadius: 4, fontSize: 11, fontWeight: 600, color: '#a78bfa',
          background: 'rgba(167,139,250,0.10)', border: '1px solid rgba(167,139,250,0.20)',
          cursor: aiParsing ? 'not-allowed' : 'pointer', opacity: aiParsing ? 0.5 : 1,
        }}
      >
        {aiParsing
          ? <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style={{ animation: 'spin 1s linear infinite' }}><path d="M12 22C17.5228 22 22 17.5228 22 12H20C20 16.4183 16.4183 20 12 20V22Z"/></svg>
          : <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L13.09 8.26L19 7L14.74 11.74L20 14L13.74 14.91L14 21L9.26 16.74L7 21L7.91 14.74L2 14L7.26 11.26L3 7L8.91 8.09L12 2Z"/></svg>}
        AI
      </button>
    </DiffSourceToggleWrapper>
  );
}
