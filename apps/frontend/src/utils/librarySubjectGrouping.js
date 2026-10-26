const CLASS_GROUPS = [
  ['hs 1st year', 'class 11'],
  ['hs 2nd year', 'class 12'],
  ['1st semester'],
  ['3rd semester'],
  ['5th semester'],
];

const MAKERS_SUBJECT_SLUG = 'makers-of-modern-assam-mdc-01013';

export function deduplicateMakersSubjects(subjects, streamMap) {
  const emitted = new Set();
  return subjects.flatMap((subject) => {
    if (subject.slug !== MAKERS_SUBJECT_SLUG) return [subject];
    const stream = streamMap.get(subject.stream_id);
    const classId = stream?.class_id || '';
    const key = `${classId}:${subject.slug}`;
    if (emitted.has(key)) return [];
    emitted.add(key);

    const copies = subjects.filter((candidate) => {
      const candidateStream = streamMap.get(candidate.stream_id);
      return candidate.slug === subject.slug && (candidateStream?.class_id || '') === classId;
    });
    const primary = copies.find((copy) => streamMap.get(copy.stream_id)?.slug === 'ba') || copies[0];
    const streamNames = [...new Set(copies.flatMap((copy) =>
      copy.available_stream_names?.length
        ? copy.available_stream_names
        : [streamMap.get(copy.stream_id)?.name],
    ).filter(Boolean))];
    const duplicateIds = [...new Set(copies.flatMap((copy) => [
      ...(copy.duplicate_subject_ids || []),
      ...(copy.id && copy.id !== primary.id ? [copy.id] : []),
    ]))];

    return [{
      ...primary,
      available_stream_names: streamNames,
      duplicate_subject_ids: duplicateIds,
      is_saved: copies.some((copy) => copy.is_saved),
    }];
  });
}

export function groupLibrarySubjects(subjectsToGroup, streamMap, classMap) {
  const uniqueSubjects = deduplicateMakersSubjects(subjectsToGroup, streamMap);
  return CLASS_GROUPS.flatMap((classNames) => {
    return uniqueSubjects.filter((subject) => {
      const stream = streamMap.get(subject.stream_id);
      const cls = classMap.get(stream?.class_id);
      return classNames.includes(String(cls?.name || '').trim().toLowerCase());
    });
  });
}
