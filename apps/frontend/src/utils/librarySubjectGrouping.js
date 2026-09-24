const CLASS_GROUPS = [
  ['hs 1st year', 'class 11'],
  ['hs 2nd year', 'class 12'],
  ['1st semester'],
  ['3rd semester'],
  ['5th semester'],
];

export function groupLibrarySubjects(subjectsToGroup, streamMap, classMap) {
  return CLASS_GROUPS.flatMap((classNames) =>
    subjectsToGroup.filter((subject) => {
      const stream = streamMap.get(subject.stream_id);
      const cls = classMap.get(stream?.class_id);
      return classNames.includes(String(cls?.name || '').trim().toLowerCase());
    }),
  );
}
