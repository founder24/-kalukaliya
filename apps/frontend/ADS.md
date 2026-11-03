# Advertising policy

## Monetag learning pages

| Page | Monetag placement | AdSense placements |
| --- | --- | --- |
| Chapter and topic pages (`/:board/...`) | OnClick zone 12180636 and In-Page Push zone 12180642, using provider defaults | Replaced |
| Notes (`/learn/:slug`) | OnClick zone 12180636 and In-Page Push zone 12180642, using provider defaults | Replaced |
| PYQ (`/pyq/:slug`) | OnClick zone 12180636 and In-Page Push zone 12180642, using provider defaults | Replaced |
| Subject landing pages | OnClick zone 12180636 and In-Page Push zone 12180642, using provider defaults | Replaced |

The Monetag scripts load only on those learning routes, after the existing auth, ad-free-plan, and opt-out checks allow advertising. Leaving a Monetag route uses full-page navigation so provider script state does not persist on Library or Chat.

Zone IDs and script hosts are defined in `src/utils/monetagAds.js`. Release validation checks the route components and approved tags in `scripts/verify-required-ads.mjs`.

## Other routes

Ad behavior outside the learning pages above is unchanged. This change does not enable Dappier or Monetag on Chat, Library, or Browser.

## Verification boundary

Automated tests use stubbed tag responses and do not prove that Monetag serves an ad. Verify actual script loading and provider-side fill after the protected release; do not claim fill or earnings before that check.