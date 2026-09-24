# Eddie

Small, read-only Ed Discussion exporter for TA workflows. It downloads full question threads—including answers and nested comments—to regular JSON files.

The exporter intentionally leaves out author names, email addresses, and user IDs. Keep the remaining course content private and follow your institution's course-data policy.

## Setup

Python 3.10+ is enough; there are no third-party dependencies.

Create `.env` (already ignored by Git):

```dotenv
ED_API_TOKEN=your-token-here
```

Check which course codes and IDs Ed exposes:

```powershell
python .\eddie.py courses
```

Export CIS 3200 and CIS 4500 for Fall 2026:

```powershell
python .\eddie.py export --course CIS3200 --course CIS4500 --term fa26
```

Results go to `exports/`, one JSON file per course plus `manifest.json`. By default only threads whose Ed type is `question` are included. Add `--all-types` to include regular posts and announcements too.

For a quick, low-volume check before a full export:

```powershell
python .\eddie.py export --course CIS3200 --term fa26 --limit 3 --output-dir sample-export
```

Use `--course-id 12345` if a code matches more than one enrollment. Run `python .\eddie.py export --help` for all options.

## Download slides and notes from a thread

Eddie can convert Google Slides and Google Docs linked in an Ed thread to local PDFs. It intentionally ignores recording links:

```powershell
python .\eddie.py download-thread-files --thread-id 8210743 --output-dir "C:\Users\you\Documents\course\notes"
```

Add `--dry-run` to preview filenames, `--kind slides` or `--kind notes` to select one type, and `--force` to replace existing files. Existing files are otherwise skipped.

Repeated links are downloaded only once. Direct CLI downloads work for link-accessible files; domain-restricted Google files require an authenticated Google Drive connection.

## JSON shape

Each course file contains course metadata and a `threads` array. A thread includes its title/body, category, status flags, `answers`, top-level `comments`, and recursively nested comments. Both Ed's `content` and rich `document` representations are retained so a later search or AI layer can choose the appropriate input.

This uses Ed's currently available JSON API, which is not formally documented and may change. Requests are GET-only, retry transient failures, and respect server `Retry-After` guidance.
