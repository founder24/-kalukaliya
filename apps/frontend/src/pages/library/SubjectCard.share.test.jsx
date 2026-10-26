import { describe, expect, it } from 'vitest';

import {
  getSubjectCardChapterTitle,
  getSubjectCardDescription,
  getSubjectCardHierarchyLabel,
  getSubjectCardName,
  getSubjectCardPyqTitle,
  getSubjectLandingPath,
} from './SubjectCard';

describe('SubjectCard share destination', () => {
  it('uses the canonical subject page represented by normalized card data', () => {
    expect(getSubjectLandingPath({
      id: 'physics-id',
      boardSlug: 'ahsec',
      classSlug: 'hs-1st-year',
      slug: 'physics',
    })).toBe('/ahsec/hs-1st-year/physics');
  });

  it('uses the same canonical page for raw API snake_case data', () => {
    expect(getSubjectLandingPath({
      id: 'physics-id',
      board_slug: 'ahsec',
      class_slug: 'hs-1st-year',
      subject_slug: 'physics',
    })).toBe('/ahsec/hs-1st-year/physics');
  });
});

describe('SubjectCard Assamese localization', () => {
  const subject = {
    name: 'Makers of Modern Assam (MDC-01013)',
    slug: 'makers-of-modern-assam-mdc-01013',
    boardName: 'Degree',
    className: '1st Semester',
    streamName: 'B.A.',
  };

  it('uses saved Assamese subject metadata or the course-specific fallback', () => {
    expect(getSubjectCardName(subject, true)).toBe('আধুনিক অসমৰ নিৰ্মাতা সকল');
    expect(getSubjectCardDescription({ ...subject, description_as: 'FYUGP English text' }, true))
      .toBe('গুৱাহাটী বিশ্ববিদ্যালয় · NEP 2020-ৰ চাৰি বছৰীয়া FYUGP · প্ৰথম ষাণ্মাসিক · MDC-01013। কি পঢ়িব? আধুনিক অসমীয়া ভাষা, সাহিত্য, সংগীত, ৰাজনৈতিক ভিত্তি, জনজাতীয় অধিকাৰ আৰু নাৰী শিক্ষা।');
    expect(getSubjectCardName({ ...subject, name_as: 'D1 নাম' }, true)).toBe('D1 নাম');
    expect(getSubjectCardDescription({ ...subject, description_as: 'D1 বৰ্ণনা' }, true)).toBe('D1 বৰ্ণনা');
    expect(getSubjectCardName(subject, false)).toBe(subject.name);
  });

  it('localizes the degree, semester, stream, and unit labels', () => {
    expect(getSubjectCardHierarchyLabel(subject.boardName, 'board', true)).toBe('স্নাতক');
    expect(getSubjectCardHierarchyLabel(subject.className, 'class', true)).toBe('প্ৰথম ষাণ্মাসিক');
    expect(getSubjectCardHierarchyLabel('1st', 'class', true)).toBe('প্ৰথম ষাণ্মাসিক');
    expect(getSubjectCardHierarchyLabel(subject.streamName, 'stream', true)).toBe('বি.এ.');
    expect(getSubjectCardChapterTitle({
      title: 'Unit I: Language, Literature & Culture',
      slug: 'unit-1-language-literature-culture',
    }, true)).toBe('গোট ১: ভাষা, সাহিত্য আৰু সংস্কৃতি');
    expect(getSubjectCardChapterTitle({
      title: 'Unit II: Framers of the Political Foundations',
    }, true)).toBe('গোট ২: ৰাজনৈতিক ভিত্তিপ্ৰতিষ্ঠাকাৰীসকল');
  });

  it('prefers stored Assamese unit titles and preserves English mode', () => {
    expect(getSubjectCardChapterTitle({
      title: 'Unit II: Framers of the Political Foundations',
      title_as: 'D1 শিৰোনাম',
      slug: 'unit-2-political-foundations',
    }, true)).toBe('D1 শিৰোনাম');
    expect(getSubjectCardChapterTitle({
      title: 'Unit II: Framers of the Political Foundations',
      slug: 'unit-2-political-foundations',
    }, false)).toBe('Unit II: Framers of the Political Foundations');
  });

  it('localizes the question-bank title when a stream copy has no saved Assamese title', () => {
    expect(getSubjectCardChapterTitle({
      title: 'Question Bank — Units I–III',
      slug: 'question-bank-units-1-3',
    }, true)).toBe('প্ৰশ্নসম্ভাৰ — গোট ১–৩');
    expect(getSubjectCardChapterTitle({
      title: 'Question Bank — Units I–III',
      slug: 'question-bank-units-1-3',
    }, false)).toBe('Question Bank — Units I–III');
  });

  it('localizes previous-year paper labels without changing English mode', () => {
    expect(getSubjectCardPyqTitle({ name: '2024 Exam Paper', year: 2024 }, 0, true))
      .toBe('বিগত বৰ্ষৰ প্ৰশ্নসমূহ — 2024');
    expect(getSubjectCardPyqTitle({ name: '2024 Exam Paper', name_as: '২০২৪ৰ প্ৰশ্নকাকত', year: 2024 }, 0, true))
      .toBe('২০২৪ৰ প্ৰশ্নকাকত');
    expect(getSubjectCardPyqTitle({ name: '2024 Exam Paper', year: 2024 }, 0, false))
      .toBe('2024 Exam Paper');
  });
});