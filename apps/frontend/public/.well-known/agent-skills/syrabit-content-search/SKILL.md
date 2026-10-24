# Search Syrabit published curriculum

Use Syrabit's public, read-only content search API to find published curriculum
material for a student's question.

## Search

Send a `GET` request to:

`https://api.syrabit.ai/api/v1/content/search?q={URL-encoded-query}`

The endpoint returns matching published content as JSON. Consult
`https://syrabit.ai/.well-known/openapi.json` for the current request parameters
and response schema.

## Use results responsibly

- Prefer the result's canonical page URL when citing material.
- Do not infer a fact that is absent from the returned content.
- Make clear when the search returns no suitable published result.
- The endpoint is read-only; do not send student, staff, or account data.
