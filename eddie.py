#!/usr/bin/env python3
"""Export Ed Discussion questions and answers to privacy-conscious JSON."""

from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://us.edstem.org/api/"
PAGE_SIZE = 100
SCHEMA_VERSION = 1


class EddieError(Exception):
    """An error that can be shown directly to the user."""


def load_dotenv(path: Path) -> None:
    """Load a small, conventional .env file without overriding real env vars."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def clean_base_url(value: str) -> str:
    value = value.strip()
    if not value.startswith("https://"):
        raise EddieError("EDSTEM_BASE_URL must be an https:// URL.")
    return value.rstrip("/") + "/"


class EdClient:
    def __init__(self, token: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 20.0):
        if not token.strip():
            raise EddieError("ED_API_TOKEN is empty.")
        self._token = token.strip()
        self._base_url = clean_base_url(base_url)
        self._timeout = timeout

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
        url = self._base_url + path.lstrip("/")
        if query:
            url += "?" + query

        for attempt in range(5):
            request = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._token}",
                    "User-Agent": "eddie-edstem-exporter/0.1",
                },
                method="GET",
            )
            try:
                with urlopen(request, timeout=self._timeout) as response:
                    raw = response.read().decode("utf-8")
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise EddieError(f"Ed returned unexpected JSON for {path}.")
                    return value
            except HTTPError as error:
                if error.code in {401, 403}:
                    raise EddieError(
                        "Ed rejected the token. Create a current API token and set ED_API_TOKEN."
                    ) from None
                if error.code == 404:
                    raise EddieError(f"Ed resource not found: {path}") from None
                if error.code == 429 or 500 <= error.code < 600:
                    if attempt < 4:
                        time.sleep(_retry_delay(error.headers.get("Retry-After"), attempt))
                        continue
                detail = _http_error_message(error)
                raise EddieError(f"Ed API request failed (HTTP {error.code}): {detail}") from None
            except URLError as error:
                if attempt < 4:
                    time.sleep(min(2**attempt, 8) + random.random() / 4)
                    continue
                raise EddieError(f"Could not reach Ed: {error.reason}") from None
            except (TimeoutError, json.JSONDecodeError) as error:
                raise EddieError(f"Ed returned an unusable response for {path}: {error}") from None
        raise EddieError(f"Ed API request failed after retries: {path}")

    def courses(self) -> list[dict[str, Any]]:
        payload = self.get("user")
        courses: list[dict[str, Any]] = []
        for enrollment in _list(payload.get("courses")):
            course = _dict(enrollment.get("course"))
            role = _dict(enrollment.get("role")).get("role")
            if course:
                courses.append(
                    {
                        "id": course.get("id"),
                        "code": course.get("code") or "",
                        "name": course.get("name") or "",
                        "year": str(course.get("year") or ""),
                        "session": course.get("session") or "",
                        "status": course.get("status") or "",
                        "role": role or "",
                    }
                )
        return courses

    def thread_summaries(self, course_id: int, limit: int | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen: set[int] = set()
        offset = 0
        while limit is None or len(results) < limit:
            requested = min(PAGE_SIZE, limit - len(results)) if limit is not None else PAGE_SIZE
            payload = self.get(
                f"courses/{course_id}/threads",
                {"limit": requested, "offset": offset, "sort": "new"},
            )
            page = [_dict(item) for item in _list(payload.get("threads"))]
            if not page:
                break
            added = 0
            for thread in page:
                thread_id = _int(thread.get("id"))
                if thread_id and thread_id not in seen:
                    results.append(thread)
                    seen.add(thread_id)
                    added += 1
                    if limit is not None and len(results) >= limit:
                        break
            if len(page) < requested or added == 0:
                break
            offset += len(page)
        return results

    def thread(self, thread_id: int) -> dict[str, Any]:
        payload = self.get(f"threads/{thread_id}")
        return _dict(payload.get("thread") or payload)


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 60.0))
        except ValueError:
            try:
                when = parsedate_to_datetime(retry_after)
                return max(0.0, min((when - datetime.now(timezone.utc)).total_seconds(), 60.0))
            except (TypeError, ValueError):
                pass
    return min(2**attempt, 16) + random.random() / 2


def _http_error_message(error: HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
        return str(payload.get("message") or payload.get("code") or error.reason)
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return str(error.reason)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[dict[str, Any]]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


@dataclass(frozen=True)
class Term:
    year: str
    sessions: tuple[str, ...]


@dataclass(frozen=True)
class ThreadAsset:
    kind: str
    label: str
    lecture: str
    date: str
    source_url: str
    export_url: str
    filename: str


def parse_term(value: str | None) -> Term | None:
    if not value:
        return None
    match = re.fullmatch(r"(?i)(fa|fall|au|autumn|sp|spring|su|summer|wi|winter)[-_ ]?(\d{2}|\d{4})", value.strip())
    if not match:
        raise EddieError("Term must look like fa26, fall2026, sp27, or summer-2027.")
    season, year = match.groups()
    if len(year) == 2:
        year = "20" + year
    season = season.lower()
    aliases = {
        "fa": ("fall", "autumn", "fa"),
        "fall": ("fall", "autumn", "fa"),
        "au": ("fall", "autumn", "au"),
        "autumn": ("fall", "autumn", "au"),
        "sp": ("spring", "sp"),
        "spring": ("spring", "sp"),
        "su": ("summer", "su"),
        "summer": ("summer", "su"),
        "wi": ("winter", "wi"),
        "winter": ("winter", "wi"),
    }
    return Term(year, aliases[season])


def select_courses(
    courses: list[dict[str, Any]], selectors: Iterable[str], course_ids: Iterable[int], term: Term | None
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    wanted_ids = set(course_ids)
    for course_id in wanted_ids:
        matches = [course for course in courses if _int(course.get("id")) == course_id]
        if not matches:
            raise EddieError(f"You are not enrolled in Ed course ID {course_id}.")
        selected.extend(matches)

    for selector in selectors:
        needle = _norm(selector)
        matches = [
            course
            for course in courses
            if needle
            and (
                needle in _norm(course.get("code"))
                or needle in _norm(course.get("name"))
            )
        ]
        if term:
            matches = [course for course in matches if _course_in_term(course, term)]
        if not matches:
            suffix = f" in {term.year}" if term else ""
            raise EddieError(f"No enrolled course matches {selector!r}{suffix}. Run `courses` to inspect codes.")
        if len(matches) > 1:
            choices = ", ".join(_course_label(course) for course in matches)
            raise EddieError(f"{selector!r} is ambiguous: {choices}. Use --course-id.")
        selected.append(matches[0])

    unique: dict[int, dict[str, Any]] = {}
    for course in selected:
        unique[_int(course.get("id"))] = course
    return list(unique.values())


def _course_in_term(course: dict[str, Any], term: Term) -> bool:
    if term.year not in str(course.get("year") or ""):
        return False
    session = _norm(course.get("session"))
    return not session or any(_norm(alias) in session for alias in term.sessions)


def _course_label(course: dict[str, Any]) -> str:
    return f"{course.get('code') or course.get('name')} ({course.get('session')} {course.get('year')}, id={course.get('id')})"


def export_comment(comment: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _int(comment.get("id")),
        "type": comment.get("type") or "",
        "content": comment.get("content") or "",
        "document": comment.get("document") or "",
        "created_at": comment.get("created_at") or "",
        "is_endorsed": bool(comment.get("is_endorsed")),
        "is_resolved": bool(comment.get("is_resolved")),
        "is_anonymous": bool(comment.get("is_anonymous")),
        "vote_count": _int(comment.get("vote_count")),
        "comments": [export_comment(_dict(child)) for child in _list(comment.get("comments"))],
    }


def export_thread(thread: dict[str, Any], course_id: int) -> dict[str, Any]:
    thread_id = _int(thread.get("id"))
    return {
        "id": thread_id,
        "number": _int(thread.get("number")),
        "url": f"https://edstem.org/us/courses/{course_id}/discussion/{thread_id}",
        "title": thread.get("title") or "",
        "type": thread.get("type") or "",
        "category": thread.get("category") or "",
        "subcategory": thread.get("subcategory") or "",
        "subsubcategory": thread.get("subsubcategory") or "",
        "content": thread.get("content") or "",
        "document": thread.get("document") or "",
        "created_at": thread.get("created_at") or "",
        "updated_at": thread.get("updated_at") or "",
        "is_answered": bool(thread.get("is_answered")),
        "is_endorsed": bool(thread.get("is_endorsed")),
        "is_private": bool(thread.get("is_private")),
        "is_anonymous": bool(thread.get("is_anonymous")),
        "answers": [export_comment(_dict(answer)) for answer in _list(thread.get("answers"))],
        "comments": [export_comment(_dict(comment)) for comment in _list(thread.get("comments"))],
    }


def extract_thread_assets(content: str) -> list[ThreadAsset]:
    """Find Google Docs/Slides links in Ed's XML content; ignore recordings."""
    if not content.strip():
        return []
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise EddieError(f"The Ed thread's rich content could not be parsed: {error}") from None

    parent = {child: node for node in root.iter() for child in node}
    assets: list[ThreadAsset] = []
    seen_exports: set[str] = set()
    used_names: dict[str, int] = {}
    for node in root.iter():
        source_url = html.unescape(node.attrib.get("href") or node.attrib.get("url") or "")
        google = google_pdf_url(source_url)
        if not google:
            continue
        kind, export_url = google
        if export_url in seen_exports:
            continue
        seen_exports.add(export_url)
        label = _collapse_text("".join(node.itertext())) or kind
        row = node
        while row in parent and _local_tag(row.tag) not in {"paragraph", "list-item"}:
            row = parent[row]
        row_text = _collapse_text("".join(row.itertext()))
        date, lecture = _lecture_metadata(row_text)
        stem_parts = [date, lecture, label]
        stem = "-".join(_slug(part) for part in stem_parts if part)
        if not stem:
            stem = f"thread-file-{len(assets) + 1:02d}-{kind}"
        stem = stem[:180].rstrip("-_")
        occurrence = used_names.get(stem, 0) + 1
        used_names[stem] = occurrence
        filename = f"{stem}{f'-{occurrence}' if occurrence > 1 else ''}.pdf"
        assets.append(
            ThreadAsset(
                kind=kind,
                label=label,
                lecture=lecture,
                date=date,
                source_url=source_url,
                export_url=export_url,
                filename=filename,
            )
        )
    return assets


def google_pdf_url(value: str) -> tuple[str, str] | None:
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname != "docs.google.com":
        return None
    match = re.match(r"^/(presentation|document)/d/([A-Za-z0-9_-]+)(?:/|$)", parsed.path)
    if not match:
        return None
    google_type, file_id = match.groups()
    if google_type == "presentation":
        return "slides", f"https://docs.google.com/presentation/d/{file_id}/export/pdf"
    return "notes", f"https://docs.google.com/document/d/{file_id}/export?format=pdf"


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _collapse_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _lecture_metadata(row_text: str) -> tuple[str, str]:
    match = re.match(
        r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"(\d{1,2}),\s*(\d{4})\s*[-–—]\s*(.*?)(?:\s*(?:·|Â·)\s*|$)",
        row_text,
        flags=re.IGNORECASE,
    )
    if not match:
        return "", ""
    month, day, year, lecture = match.groups()
    parsed = datetime.strptime(f"{month} {day} {year}", "%B %d %Y")
    return parsed.strftime("%Y-%m-%d"), lecture.strip()


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")


def download_pdf(asset: ThreadAsset, destination: Path, force: bool = False) -> str:
    if destination.exists() and not force:
        return "skipped"
    request = Request(
        asset.export_url,
        headers={"Accept": "application/pdf", "User-Agent": "eddie-edstem-exporter/0.1"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=120) as response:
            data = response.read()
            content_type = response.headers.get("Content-Type", "").lower()
    except HTTPError as error:
        if error.code in {401, 403}:
            raise EddieError(
                f"Google requires a signed-in account for {asset.filename}; use an authenticated Drive connection."
            ) from None
        raise EddieError(f"Google rejected {asset.filename} (HTTP {error.code}).") from None
    except (URLError, TimeoutError) as error:
        reason = getattr(error, "reason", error)
        raise EddieError(f"Could not download {asset.filename}: {reason}") from None
    if not data.startswith(b"%PDF-"):
        detail = f"content type {content_type or 'unknown'}"
        raise EddieError(
            f"Google did not return a PDF for {asset.filename} ({detail}); the file may require a signed-in browser."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(data)
    temporary.replace(destination)
    return "downloaded"


def safe_filename(course: dict[str, Any]) -> str:
    stem = "-".join(
        part for part in [_norm(course.get("code")), _norm(course.get("session")), _norm(course.get("year"))] if part
    )
    return (stem or f"course-{course.get('id')}") + ".json"


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_courses(client: EdClient, _args: argparse.Namespace) -> int:
    courses = client.courses()
    if not courses:
        print("No Ed courses found.")
        return 0
    for course in courses:
        print(_course_label(course))
    return 0


def run_export(client: EdClient, args: argparse.Namespace) -> int:
    term = parse_term(args.term)
    courses = select_courses(client.courses(), args.course, args.course_id, term)
    if not courses:
        raise EddieError("Choose at least one course with --course or --course-id.")

    output_dir = Path(args.output_dir)
    manifest: list[dict[str, Any]] = []
    for course in courses:
        course_id = _int(course.get("id"))
        summaries = client.thread_summaries(course_id, args.limit)
        if not args.all_types:
            summaries = [thread for thread in summaries if str(thread.get("type") or "").lower() == "question"]
        print(f"Fetching {len(summaries)} thread(s) from {_course_label(course)}...", file=sys.stderr)
        threads: list[dict[str, Any]] = []
        for index, summary in enumerate(summaries, start=1):
            thread_id = _int(summary.get("id"))
            if not thread_id:
                continue
            threads.append(export_thread(client.thread(thread_id), course_id))
            if index % 25 == 0:
                print(f"  {index}/{len(summaries)}", file=sys.stderr)

        exported_at = datetime.now(timezone.utc).isoformat()
        document = {
            "schema_version": SCHEMA_VERSION,
            "exported_at": exported_at,
            "source": "Ed Discussion",
            "privacy": "Author names, email addresses, and user IDs are intentionally omitted.",
            "course": course,
            "thread_count": len(threads),
            "threads": threads,
        }
        path = output_dir / safe_filename(course)
        write_json_atomic(path, document)
        manifest.append({"course": course, "path": str(path), "thread_count": len(threads)})
        print(f"Wrote {len(threads)} thread(s) to {path}")

    write_json_atomic(
        output_dir / "manifest.json",
        {"schema_version": SCHEMA_VERSION, "exported_at": datetime.now(timezone.utc).isoformat(), "exports": manifest},
    )
    return 0


def run_download_thread_files(client: EdClient, args: argparse.Namespace) -> int:
    thread = client.thread(args.thread_id)
    assets = extract_thread_assets(str(thread.get("content") or ""))
    if args.kind:
        allowed = set(args.kind)
        assets = [asset for asset in assets if asset.kind in allowed]
    if not assets:
        raise EddieError("No matching Google Slides or Google Docs links were found in that thread.")

    output_dir = Path(args.output_dir)
    print(
        f"Found {len(assets)} PDF asset(s) in thread #{_int(thread.get('number'))}: "
        f"{thread.get('title') or ''}"
    )
    if args.dry_run:
        for asset in assets:
            print(f"[{asset.kind}] {asset.filename}")
        return 0

    downloaded = 0
    skipped = 0
    for asset in assets:
        status = download_pdf(asset, output_dir / asset.filename, force=args.force)
        if status == "downloaded":
            downloaded += 1
        else:
            skipped += 1
        print(f"{status}: {asset.filename}")
    print(f"Done: {downloaded} downloaded, {skipped} already present; recordings ignored.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eddie",
        description="Export Ed Discussion questions and answers to JSON without exporting identity fields.",
    )
    parser.add_argument("--env-file", default=".env", help="env file to read (default: .env)")
    parser.add_argument("--base-url", help="Ed API base URL (default: EDSTEM_BASE_URL or us.edstem.org/api)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    courses_parser = subparsers.add_parser("courses", help="list the courses visible to the token")
    courses_parser.set_defaults(handler=run_courses)

    export_parser = subparsers.add_parser("export", help="export full question and answer threads")
    export_parser.add_argument("--course", action="append", default=[], help="course code/name; repeatable")
    export_parser.add_argument("--course-id", action="append", type=int, default=[], help="numeric Ed course ID; repeatable")
    export_parser.add_argument("--term", help="term filter such as fa26 or spring2027")
    export_parser.add_argument("--output-dir", default="exports", help="destination directory (default: exports)")
    export_parser.add_argument("--limit", type=int, help="maximum thread-list entries per course (useful for testing)")
    export_parser.add_argument("--all-types", action="store_true", help="include posts and announcements, not just questions")
    export_parser.set_defaults(handler=run_export)

    files_parser = subparsers.add_parser(
        "download-thread-files",
        help="download Google Slides and Docs linked from a thread as PDFs (recordings are ignored)",
    )
    files_parser.add_argument("--thread-id", required=True, type=int, help="numeric Ed thread ID")
    files_parser.add_argument("--output-dir", required=True, help="destination directory")
    files_parser.add_argument(
        "--kind",
        action="append",
        choices=("slides", "notes"),
        help="download only this kind; repeatable (default: both)",
    )
    files_parser.add_argument("--dry-run", action="store_true", help="show planned filenames without downloading")
    files_parser.add_argument("--force", action="store_true", help="replace files that already exist")
    files_parser.set_defaults(handler=run_download_thread_files)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_dotenv(Path(args.env_file))
    token = os.environ.get("ED_API_TOKEN", "")
    if not token:
        parser.error("ED_API_TOKEN is not set (put it in .env or the environment)")
    base_url = args.base_url or os.environ.get("EDSTEM_BASE_URL", DEFAULT_BASE_URL)
    try:
        client = EdClient(token=token, base_url=base_url)
        return args.handler(client, args)
    except EddieError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
