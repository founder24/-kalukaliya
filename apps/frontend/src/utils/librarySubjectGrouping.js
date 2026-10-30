const CLASS_GROUPS = [
  ['hs 1st year', 'class 11'],
  ['hs 2nd year', 'class 12'],
  ['class 9'],
  ['class 10'],
  ['1st semester'],
  ['2nd semester'],
  ['3rd semester'],
  ['4th semester'],
  ['5th semester'],
  ['6th semester'],
  ['7th semester'],
  ['8th semester'],
];

function getSharedCourseKey(subject, streamMap) {
  const slug = String(subject.slug || subject.subject_slug || '').trim().toLowerCase();
  if (!slug) return null;
  const classId = streamMap.get(subject.stream_id)?.class_id
    || subject.class_id
    || subject.classId
    || String(subject.className || '').trim().toLowerCase();
  if (!classId) return null;
  return `${classId}:${slug}`;
}

function getStreamId(subject) {
  return String(subject.stream_id || subject.streamId || '');
}

function getStreamNames(copies, streamMap) {
  return [...new Set(copies.flatMap((copy) => {
    const existingNames = Array.isArray(copy.available_stream_names)
      ? copy.available_stream_names
      : [];
    const streamName = streamMap.get(copy.stream_id)?.name || copy.streamName;
    return [...existingNames, streamName].filter(Boolean);
  }))];
}

export function deduplicateSharedSubjects(subjects, streamMap, catalogSubjects = subjects) {
  const catalogByKey = new Map();
  for (const subject of catalogSubjects) {
    const key = getSharedCourseKey(subject, streamMap);
    if (!key) continue;
    if (!catalogByKey.has(key)) catalogByKey.set(key, []);
    catalogByKey.get(key).push(subject);
  }

  const subjectsByKey = new Map();
  for (const subject of subjects) {
    const key = getSharedCourseKey(subject, streamMap);
    if (!key) continue;
    if (!subjectsByKey.has(key)) subjectsByKey.set(key, []);
    subjectsByKey.get(key).push(subject);
  }

  const emitted = new Set();
  return subjects.flatMap((subject) => {
    const key = getSharedCourseKey(subject, streamMap);
    if (!key) return [subject];
    const copies = subjectsByKey.get(key) || [subject];
    const catalogCopies = catalogByKey.get(key) || copies;
    const streamIds = new Set(catalogCopies.map(getStreamId).filter(Boolean));
    if (streamIds.size < 2) return [subject];
    if (emitted.has(key)) return [];
    emitted.add(key);

    // Keep the active stream's record when filtering to one stream; otherwise
    // use B.A. as the stable representative when available.
    const primary = copies.length > 1
      ? copies.find((copy) => streamMap.get(copy.stream_id)?.slug === 'ba') || copies[0]
      : subject;
    const streamNames = getStreamNames(catalogCopies, streamMap);
    const duplicateIds = [...new Set(catalogCopies.flatMap((copy) => [
      ...(copy.duplicate_subject_ids || []),
      ...(copy.id && copy.id !== primary.id ? [copy.id] : []),
    ]))];

    return [{
      ...primary,
      available_stream_names: streamNames,
      duplicate_subject_ids: duplicateIds,
      is_saved: catalogCopies.some((copy) => copy.is_saved) || copies.some((copy) => copy.is_saved),
    }];
  });
}

export function groupLibrarySubjects(subjectsToGroup, streamMap, classMap, catalogSubjects = subjectsToGroup) {
  const uniqueSubjects = deduplicateSharedSubjects(subjectsToGroup, streamMap, catalogSubjects);
  return CLASS_GROUPS.flatMap((classNames) => {
    return uniqueSubjects.filter((subject) => {
      const stream = streamMap.get(subject.stream_id);
      const cls = classMap.get(stream?.class_id);
      return classNames.includes(String(cls?.name || '').trim().toLowerCase());
    });
  });
}
