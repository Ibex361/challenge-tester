# Section notes

Each file here is extracted/summarized info from one quiz section's video.
When a file is selected (via the `SECTION_NOTES` env var / the workflow's
`section_notes` input), its full contents are attached to every question's
AI prompt for that run.

## Adding or updating notes (no code editing needed)

1. On GitHub, open this folder (`section_notes/`).
2. Click **Add file → Create new file**.
3. Name it `section_<N>.md` where `<N>` matches the section number (e.g.
   `section_5.md` for Section 5: Currency Pairs). Use the same name to
   *replace* existing notes for a section.
4. Paste in the extracted notes as Markdown (headings/bullets are fine —
   the file is sent to the AI as plain text).
5. Commit directly to `main`.

That's it — no other file needs to change. The next run just needs
`SECTION_NOTES` set to the filename without `.md` (e.g. `section_5`) to
use it.

## Current files

- `section_5.md` — Section 5: Currency Pairs (covers base/quote currency,
  bid/ask/spread, pip vs point, major/minor/exotic pairs, commodity pairs).
