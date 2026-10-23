export function countPrerenderHydrationKinds(pages) {
  let subjects = 0;
  let chapters = 0;

  for (const page of pages) {
    if (/data-hydrate="subject"/.test(page.body)) subjects++;
    if (!/data-hydrate="chapter"/.test(page.body)) continue;

    const routeSegments = page.rel
      .replace(/\/index\.html$/, "")
      .split("/")
      .filter(Boolean);
    // Topic pages hydrate with ChapterPage, but are not chapter routes in the
    // prerender manifest and must not inflate the chapter count.
    if (routeSegments.includes("topic")) continue;
    chapters++;
  }

  return { subjects, chapters };
}
