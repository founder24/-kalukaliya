"""Safely catalog material from Syrabit's explicitly approved public libraries.

This is deliberately dry-run by default.  It is a bounded, allowlisted crawler,
not a general web spider: pass ``--apply`` to persist metadata/text and never
use it to fetch a URL which was not reached from one of the configured seeds.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
import shutil
import subprocess
import tempfile
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import fitz
import requests
from bs4 import BeautifulSoup
from pymongo import ASCENDING, MongoClient

PDF_LIMIT = 50 * 1024 * 1024
PDF_TEXT_LIMIT = 6 * 1024 * 1024
HTML_TEXT_LIMIT = 2 * 1024 * 1024
USER_AGENT = "SyrabitExternalLibraryIndexer/1.0 (+https://syrabit.ai; contact@syrabit.ai)"


@dataclass(frozen=True)
class Source:
    name: str
    root: str
    seed: str
    kind: str  # dspace | web
    institution: str
    reference_excerpt_chars: Optional[int] = None


# These are the only crawl entry points.  A source's graph is never joined to
# another source, even when a site happens to link out to it.
SOURCES = (
    Source("goalpara", "http://goalparacollege.bsmlib.com", "http://goalparacollege.bsmlib.com/handle/123456789/11", "dspace", "Goalpara College"),
    Source("ngc", "http://ngc.digitallibrary.co.in", "http://ngc.digitallibrary.co.in/handle/123456789/1", "dspace", "Nowgong Girls' College"),
    Source("bikali", "http://bikalicollege.digitallibrary.co.in", "http://bikalicollege.digitallibrary.co.in/handle/123456789/3", "dspace", "Bikali College"),
    Source("dynamic_tutorials", "https://www.dynamictutorialsandservices.org", "https://www.dynamictutorialsandservices.org/p/class-1112.html", "web", "Dynamic Tutorials"),
    # Its published content signal permits reference use but forbids AI training.
    # Keep catalog metadata and a citation-sized excerpt only.
    Source("dev_library", "https://devlibrary.in", "https://devlibrary.in/hs", "web", "Dev Library", 500),
    Source("assam_library", "https://www.assamlibrary.com", "https://www.assamlibrary.com/?m=1", "web", "Assam Library"),
    Source("roy_library", "https://roylibrary.in", "https://roylibrary.in/", "web", "Roy Library"),
)

EDUCATIONAL_RE = re.compile(
    r"\b(?:class\s*(?:11|12|xi|xii)|hs|ahsec|fyugp|semester|sem\.?|"
    r"notes?|questions?|solutions?|books?|syllabus)\b", re.I
)
DEV_REFERENCE_SCOPE_RE = re.compile(
    r"(?:class[-_/ ]?(?:11|12|xi|xii)|ahsec|higher[-_ ]secondary|"
    r"fyugp|semester|(?:^|[-_/])hs(?:[-_/]|$))",
    re.I,
)
SUBJECTS = ("physics", "chemistry", "mathematics", "maths", "biology", "english",
            "assamese", "economics", "history", "political science", "geography",
            "accountancy", "business studies", "computer science")


@dataclass
class Candidate:
    source: Source
    item_url: str
    content_url: Optional[str]
    title: str
    document_type: str = "library_document"
    content_format: str = "html"
    metadata: dict = field(default_factory=dict)


def canonical_url(url: str) -> str:
    """Drop fragments/tracking parameters for stable deduplication."""
    parsed = urlparse(url)
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                            if not k.lower().startswith(("utm_", "fbclid", "gclid"))
                            and k.lower() not in {"sequence", "isallowed"}))
    path = re.sub(r"/+", "/", parsed.path or "/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, ""))


def stable_key(source_name: str, item_url: str, content_url: Optional[str]) -> str:
    value = f"{source_name}\n{canonical_url(item_url)}\n{canonical_url(content_url or item_url)}"
    return hashlib.sha256(value.encode()).hexdigest()


def _same_origin(source: Source, url: str) -> bool:
    parsed, root = urlparse(url), urlparse(source.root)
    return parsed.scheme == root.scheme and parsed.netloc.lower() == root.netloc.lower()


def is_educational_url(source: Source, url: str, anchor_text: str = "") -> bool:
    """Web boundary filter: same approved host and an educational signal only."""
    if not _same_origin(source, url):
        return False
    parsed = urlparse(url)
    if parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".zip", ".mp3", ".mp4")):
        return False
    return bool(EDUCATIONAL_RE.search(f"{parsed.path} {anchor_text}"))


def is_dspace_url(source: Source, url: str) -> bool:
    """Restrict DSpace traversal to same-origin handle/item/bitstream routes."""
    if not _same_origin(source, url):
        return False
    parsed = urlparse(url)
    path = parsed.path.lower()
    query_keys = {key.lower() for key, _ in parse_qsl(parsed.query)}
    # Do not queue repository search, browse, statistics, authentication, or
    # administration endpoints. Offset query parameters on /handle are valid
    # DSpace pagination and deliberately remain allowed.
    handle = bool(re.search(r"(?:^|/)(?:jspui/)?handle/[^/]+/[^/]+/?$", path))
    allowed_pagination = query_keys <= {"offset", "rpp", "sort_by", "order"}
    return bool((handle and allowed_pagination) or
                "/bitstream/" in path or "/retrieve/" in path)


def _cap_text(text: str, limit: int) -> tuple[str, bool]:
    text = text.replace("\x00", "")
    return (text[:limit], len(text) > limit)


def is_pdf_response(content_type: str, data: bytes) -> bool:
    """Content type is advisory; file signature and parser are the authority."""
    return data.lstrip()[:5] == b"%PDF-"


def parse_hierarchy(text: str) -> dict:
    """Only emit fields supported by explicit signals, never guessed hierarchy."""
    result: dict = {}
    class_match = re.search(
        r"\b(?:class|h\.?\s*s\.?)\s*(11|12|xi|xii|1st\s+year|2nd\s+year)\b",
        text,
        re.I,
    )
    if class_match:
        value = class_match.group(1).lower()
        result["class_name"] = "HS 1st Year" if value in ("11", "xi", "1st year") else "HS 2nd Year"
        result["board"] = "AHSEC" if re.search(r"\b(?:ahsec|asseb)\b", text, re.I) else None
    sem = re.search(
        r"\b(?:(?:semester|sem\.?)\s*(?:-|:)?\s*"
        r"([1-9]|10|i{1,3}|iv|v|vi{0,3}|ix|x)|"
        r"([1-9]|10)(?:st|nd|rd|th)\s+(?:semester|sem\.?))\b",
        text,
        re.I,
    )
    if sem:
        token = (sem.group(1) or sem.group(2)).upper()
        roman = {
            "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
            "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
        }
        result["semester"] = int(token) if token.isdigit() else roman.get(token)
        result["course"] = "FYUGP" if re.search(r"\bfyugp\b", text, re.I) else None
    for subject in SUBJECTS:
        if re.search(rf"\b{re.escape(subject)}\b", text, re.I):
            result["subject"] = "Mathematics" if subject == "maths" else subject.title()
            break
    year = re.search(r"\b(20\d{2})\b", text)
    if year:
        result["year"] = int(year.group(1))
    return {key: value for key, value in result.items() if value is not None}


def _clean_html(data: bytes) -> tuple[str, bool]:
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()
    main = soup.select_one("article, main, .post, .entry-content, .content") or soup.body or soup
    return _cap_text(" ".join(main.get_text(" ", strip=True).split()), HTML_TEXT_LIMIT)


def _ocr_language(title: str) -> str:
    value = title.lower()
    return "asm+eng" if "assamese" in value else "ben+eng" if "bengali" in value else "eng"


def extract_pdf(
    data: bytes,
    title: str,
    *,
    max_ocr_pages: int = 3,
) -> tuple[str, int, bool, str]:
    if not is_pdf_response("", data):
        raise ValueError("response is not a PDF")
    with fitz.open(stream=data, filetype="pdf") as document:
        pages = [page.get_text("text") for page in document]
        method = "pymupdf"
        # OCR a representative prefix of image-only scans. College repositories
        # contain thousands of scans; unbounded page-by-page OCR would make a
        # refresh take days. The complete public PDF remains linked and hashed.
        ocr_pages = 0
        skipped_image_pages = 0
        if max_ocr_pages > 0 and shutil.which("tesseract"):
            with tempfile.TemporaryDirectory(prefix="syrabit-library-ocr-") as tmp:
                for number, (page, text) in enumerate(zip(document, pages)):
                    if text.strip():
                        continue
                    if ocr_pages >= max_ocr_pages:
                        skipped_image_pages += 1
                        continue
                    image = os.path.join(tmp, f"{number}.png")
                    page.get_pixmap(
                        matrix=fitz.Matrix(0.9, 0.9), alpha=False
                    ).save(image)
                    try:
                        output = subprocess.run(
                            [
                                "tesseract", image, "stdout", "-l",
                                _ocr_language(title), "--psm", "6",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=15,
                            check=False,
                        )
                        pages[number] = output.stdout if output.returncode == 0 else ""
                        method = "pymupdf+ocr"
                    except subprocess.TimeoutExpired:
                        pass
                    ocr_pages += 1
        text, truncated = _cap_text("\n".join(pages), PDF_TEXT_LIMIT)
        if skipped_image_pages:
            truncated = True
            method = "pymupdf+partial-ocr"
        elif any(not page.strip() for page in pages):
            truncated = True
            method = "pymupdf+image-pages-deferred"
        return text, len(document), truncated, method


class PoliteClient:
    def __init__(self, timeout: int):
        self.timeout = timeout
        self._local = threading.local()
        self._robots: dict[str, RobotFileParser] = {}
        self._robots_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._last_request: dict[str, float] = {}

    @property
    def session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
            self._local.session.headers["User-Agent"] = USER_AGENT
        return self._local.session

    def allowed(self, url: str) -> bool:
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        with self._robots_lock:
            if origin not in self._robots:
                robot = RobotFileParser()
                try:
                    response = self.session.get(urljoin(origin, "/robots.txt"), timeout=self.timeout)
                    robot.parse(response.text.splitlines() if response.ok else ["User-agent: *", "Disallow: /"])
                except requests.RequestException:
                    return False  # fail closed: do not crawl when robots cannot be read
                self._robots[origin] = robot
        return self._robots[origin].can_fetch(USER_AGENT, url)

    def get(self, url: str, *, stream: bool = False) -> requests.Response:
        if not self.allowed(url):
            raise PermissionError("robots.txt disallows this URL")
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                # A shared origin gate keeps concurrent extraction workers from
                # turning an approved crawl into an abusive burst.
                origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                with self._rate_lock:
                    delay = 0.25 - (time.monotonic() - self._last_request.get(origin, 0))
                    if delay > 0:
                        time.sleep(delay)
                    self._last_request[origin] = time.monotonic()
                response = self.session.get(url, timeout=self.timeout, stream=stream, allow_redirects=True)
                if urlparse(response.url).scheme != urlparse(url).scheme or urlparse(response.url).netloc.lower() != urlparse(url).netloc.lower():
                    raise PermissionError("redirect left approved source origin")
                if response.status_code in (403, 406):
                    raise PermissionError(f"source blocked request ({response.status_code})")
                if response.status_code >= 500:
                    raise requests.HTTPError(f"server error {response.status_code}")
                response.raise_for_status()
                return response
            except (requests.RequestException, PermissionError) as exc:
                last = exc
                if isinstance(exc, PermissionError) or attempt == 2:
                    break
                time.sleep(1.0 * (2 ** attempt))
        raise RuntimeError(str(last))


def _anchors(soup: BeautifulSoup, base: str) -> Iterable[tuple[str, str]]:
    for node in soup.select("a[href]"):
        yield canonical_url(urljoin(base, node["href"])), node.get_text(" ", strip=True)


def _item_title(source: Source, soup: BeautifulSoup, fallback: str) -> str:
    title = soup.title.get_text(" ", strip=True) if soup.title else fallback
    if source.kind == "dspace":
        # Repository installations commonly prepend their branding, e.g.
        # "NGC Digital Library: Paper title". It is not document metadata.
        title = re.sub(r"^\s*(?:dspace|[^:|]{1,100}(?:digital\s+library|repository))\s*[:|\-]\s*",
                       "", title, flags=re.I)
    return title.strip() or fallback


def _reference_sitemap_candidates(
    source: Source,
    client: PoliteClient,
    max_pages: int,
    max_documents: int,
) -> tuple[list[Candidate], Optional[str]]:
    """Catalog allowed reference URLs without copying page-body content."""
    index_url = canonical_url(urljoin(source.root, "/sitemap_index.xml"))
    queue, queued, seen = deque([index_url]), {index_url}, set()
    candidates: list[Candidate] = []
    try:
        while queue and len(seen) < max_pages and len(candidates) < max_documents:
            sitemap_url = queue.popleft()
            if sitemap_url in seen:
                continue
            seen.add(sitemap_url)
            response = client.get(sitemap_url)
            raw_locations = re.findall(
                rb"<loc>\s*(.*?)\s*</loc>", response.content, flags=re.I | re.S
            )
            for raw in raw_locations:
                location = canonical_url(
                    html.unescape(raw.decode("utf-8", errors="replace")).strip()
                )
                if not _same_origin(source, location):
                    continue
                if urlparse(location).path.lower().endswith(".xml"):
                    if location not in queued:
                        queued.add(location)
                        queue.append(location)
                    continue
                if not DEV_REFERENCE_SCOPE_RE.search(location):
                    continue
                slug = urlparse(location).path.strip("/").rsplit("/", 1)[-1]
                title = re.sub(r"[-_]+", " ", slug).strip().title() or location
                candidates.append(
                    Candidate(
                        source, location, location, title,
                        "study_material", "html",
                        {"content_policy": "reference-only"},
                    )
                )
                if len(candidates) >= max_documents:
                    break
        return candidates, None
    except Exception as exc:
        return candidates, str(exc)


def crawl_source(source: Source, client: PoliteClient, max_pages: int, max_documents: int) -> tuple[list[Candidate], Optional[str]]:
    """Discover only source-local documents from the approved seed graph."""
    if source.name == "dev_library":
        return _reference_sitemap_candidates(
            source, client, max_pages, max_documents
        )
    seed = canonical_url(source.seed)
    queue, queued, seen = deque([seed]), {seed}, set()
    candidates: list[Candidate] = []
    candidate_keys: set[str] = set()

    def enqueue(url: str) -> None:
        normalized = canonical_url(url)
        if normalized not in queued:
            queued.add(normalized)
            queue.append(normalized)

    def add_candidate(candidate: Candidate) -> None:
        key = stable_key(
            candidate.source.name, candidate.item_url, candidate.content_url
        )
        if key not in candidate_keys:
            candidate_keys.add(key)
            candidates.append(candidate)

    try:
        while queue and len(seen) < max_pages and len(candidates) < max_documents:
            page = queue.popleft()
            if page in seen:
                continue
            seen.add(page)
            if len(seen) % 250 == 0:
                print(
                    f"[{source.name}] discovery pages={len(seen)} "
                    f"queued={len(queue)} documents={len(candidates)}",
                    flush=True,
                )
            try:
                response = client.get(page)
            except Exception as exc:
                # A disallowed or stale child URL must not discard the rest of
                # an approved source graph. Only failure of the seed itself is
                # a source-level failure.
                if page == canonical_url(source.seed):
                    return candidates, str(exc)
                continue
            soup = BeautifulSoup(response.content, "html.parser")
            title = _item_title(source, soup, page)
            links = list(_anchors(soup, page))
            if source.kind == "dspace":
                files = bool(soup.find(string=re.compile(r"files in this item", re.I)))
                for link, label in links:
                    if is_dspace_url(source, link) and "/handle/" in urlparse(link).path.lower():
                        # The queue is a graph rooted at the supplied community;
                        # links are never synthesized or discovered elsewhere.
                        enqueue(link)
                    elif files and ("/bitstream/" in urlparse(link).path.lower() or "/retrieve/" in urlparse(link).path.lower()):
                        if link.lower().split("?", 1)[0].endswith(".pdf") or "pdf" in label.lower():
                            add_candidate(
                                Candidate(
                                    source, page, link, title,
                                    "question_paper", "pdf",
                                )
                            )
                if files and not any(c.item_url == page for c in candidates):
                    # Keep item metadata even where DSpace exposes no usable PDF.
                    add_candidate(
                        Candidate(
                            source, page, None, title, "library_item", "html"
                        )
                    )
            else:
                for link, label in links:
                    if not is_educational_url(source, link, label):
                        continue
                    if link.lower().split("?", 1)[0].endswith(".pdf"):
                        add_candidate(
                            Candidate(
                                source, page, link, title,
                                "library_document", "pdf",
                            )
                        )
                    else:
                        enqueue(link)
                if page != canonical_url(source.seed) and EDUCATIONAL_RE.search(f"{title} {page}"):
                    add_candidate(
                        Candidate(
                            source, page, page, title, "study_material", "html"
                        )
                    )
        return candidates[:max_documents], None
    except Exception as exc:
        return candidates, str(exc)


def _download_limited(client: PoliteClient, url: str) -> bytes:
    response = client.get(url, stream=True)
    length = int(response.headers.get("content-length", 0) or 0)
    if length > PDF_LIMIT:
        raise ValueError("PDF exceeds 50MB limit")
    data = bytearray()
    for chunk in response.iter_content(64 * 1024):
        data.extend(chunk)
        if len(data) > PDF_LIMIT:
            raise ValueError("PDF exceeds 50MB limit")
    return bytes(data)


def record_for(candidate: Candidate, client: PoliteClient, metadata_only: bool) -> dict:
    now = datetime.now(timezone.utc)
    hierarchy = parse_hierarchy(f"{candidate.title} {candidate.item_url}")
    record = {
        "stable_key": stable_key(candidate.source.name, candidate.item_url, candidate.content_url),
        "canonical_source_url": canonical_url(candidate.source.seed),
        "canonical_item_url": canonical_url(candidate.item_url),
        "content_url": canonical_url(candidate.content_url) if candidate.content_url else None,
        "source_root": candidate.source.root, "source_name": candidate.source.name,
        "title": candidate.title[:1000], "document_type": candidate.document_type,
        "content_format": candidate.content_format, "institution": candidate.source.institution,
        "status": "metadata_only" if metadata_only else "discovered", "metadata": candidate.metadata,
        "discovered_at": now, "updated_at": now, **hierarchy,
    }
    if (
        metadata_only
        or not candidate.content_url
        or candidate.source.reference_excerpt_chars
    ):
        if candidate.source.reference_excerpt_chars:
            record["status"] = "metadata_only"
            record["metadata"] = {
                **record["metadata"],
                "content_policy": "reference-only",
            }
        return record
    data = _download_limited(client, candidate.content_url)
    record["size_bytes"] = len(data)
    if is_pdf_response("", data):
        text, pages, truncated, method = extract_pdf(
            data,
            candidate.title,
            # DSpace question-paper archives are predominantly scans and can
            # contain thousands of files. Keep the complete validated source
            # linked for deferred OCR rather than blocking the catalog import.
            max_ocr_pages=0 if candidate.source.kind == "dspace" else 3,
        )
        if candidate.source.reference_excerpt_chars:
            text, was_truncated = _cap_text(text, candidate.source.reference_excerpt_chars)
            truncated = truncated or was_truncated
        record.update(extracted_text=text, page_count=pages, extracted_text_truncated=truncated,
                      extraction_method=method, checksum_sha256=hashlib.sha256(data).hexdigest(),
                      content_format="pdf", status="extracted")
    else:
        text, truncated = _clean_html(data)
        if candidate.source.reference_excerpt_chars:
            text, was_truncated = _cap_text(text, candidate.source.reference_excerpt_chars)
            truncated = truncated or was_truncated
        record.update(extracted_text=text, extracted_text_truncated=truncated,
                      extraction_method="beautifulsoup", content_format="html", status="extracted")
    return record


def _link_subjects(db, record: dict) -> list:
    """Return existing subject ids only after explicit hierarchy + subject match."""
    if not record.get("subject") or not (record.get("class_name") or record.get("semester")):
        return []
    query = {"$or": [{"name": {"$regex": f"^{re.escape(record['subject'])}$", "$options": "i"}},
                     {"slug": re.sub(r"[^a-z0-9]+", "-", record["subject"].lower()).strip("-")}]}
    expected_class = record.get("class_name")
    if not expected_class and record.get("semester"):
        ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(record["semester"], f"{record['semester']}th")
        expected_class = f"{ordinal} Semester"
    matches = []
    for subject in db.subjects.find(query, {"_id": 1, "stream_id": 1}).limit(25):
        if not subject.get("stream_id"):
            continue
        stream = db.streams.find_one({"_id": subject["stream_id"]}, {"class_id": 1, "name": 1})
        if not stream or (record.get("course") and record["course"].lower() not in stream.get("name", "").lower()):
            continue
        cls = db.classes.find_one({"_id": stream.get("class_id")}, {"name": 1})
        if cls and cls.get("name", "").lower() == expected_class.lower():
            matches.append(subject["_id"])
    return matches


def _database():
    uri = os.environ.get("MONGODB_URL") or os.environ.get("MONGODB_URI")
    if not uri:
        raise RuntimeError("MONGODB_URL / MONGODB_URI is required with --apply")
    return MongoClient(uri, serverSelectionTimeoutMS=15_000)[os.environ.get("MONGODB_DB_NAME", "syrabit_prod")]


def _upsert_record(db, record: dict) -> None:
    """Apply one record without allowing a transient failure to erase success."""
    existing = db.external_library_documents.find_one(
        {"stable_key": record["stable_key"]}, {"status": 1}
    )
    if existing and existing.get("status") == "extracted" and record["status"] in ("blocked", "error"):
        return
    record["subject_ids"] = _link_subjects(db, record)
    # discovered_at is insert-only and must not also occur in $set.
    values = {key: value for key, value in record.items() if key != "discovered_at"}
    db.external_library_documents.update_one(
        {"stable_key": record["stable_key"]},
        {"$set": values, "$setOnInsert": {"discovered_at": record["discovered_at"]}},
        upsert=True,
    )


def apply_records(records: Iterable[dict]) -> None:
    db = _database()
    db.external_library_documents.create_index([("stable_key", ASCENDING)], unique=True)
    for record in records:
        _upsert_record(db, record)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--source", choices=("all",) + tuple(s.name for s in SOURCES), default="all")
    parser.add_argument("--workers", type=int, default=2, help="Parallel extraction workers (1-8)")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--max-documents", type=int, default=200)
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    selected = [s for s in SOURCES if args.source in ("all", s.name)]
    client, records = PoliteClient(args.timeout), []
    workers = max(1, min(args.workers, 8))
    db = _database() if args.apply else None
    if db is not None:
        db.external_library_documents.create_index([("stable_key", ASCENDING)], unique=True)
    written = 0

    def consume(record: dict) -> None:
        nonlocal written
        written += 1
        if db is not None:
            # All Mongo writes stay on this coordinator thread; workers only
            # perform bounded fetch/extraction with their thread-local sessions.
            _upsert_record(db, record)
        else:
            records.append(record)

    def failed(candidate: Candidate, exc: Exception) -> dict:
        return {"stable_key": stable_key(candidate.source.name, candidate.item_url, candidate.content_url),
            "canonical_source_url": canonical_url(candidate.source.seed), "canonical_item_url": canonical_url(candidate.item_url),
            "content_url": candidate.content_url, "source_root": candidate.source.root, "source_name": candidate.source.name,
            "title": candidate.title[:1000], "document_type": candidate.document_type, "content_format": candidate.content_format,
            "institution": candidate.source.institution, "status": "blocked" if "blocked" in str(exc) or "robots" in str(exc) else "error",
            "error": str(exc)[:2000], "metadata": {}, "discovered_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}

    summaries = {}
    for source in selected:
        candidates, error = crawl_source(source, client, max(1, args.max_pages), max(1, args.max_documents))
        print(
            f"[{source.name}] discovery complete: {len(candidates)} documents"
            + (f"; {error}" if error else ""),
            flush=True,
        )
        source_written = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(record_for, candidate, client, args.metadata_only): candidate for candidate in candidates}
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    consume(future.result())
                except Exception as exc:
                    consume(failed(candidate, exc))
                source_written += 1
                if source_written % 50 == 0 or source_written == len(candidates):
                    print(
                        f"[{source.name}] processed {source_written}/{len(candidates)}",
                        flush=True,
                    )
        if error:
            consume({"stable_key": stable_key(source.name, source.seed, None), "canonical_source_url": canonical_url(source.seed),
                "canonical_item_url": canonical_url(source.seed), "source_root": source.root, "source_name": source.name,
                "title": f"{source.institution} source", "document_type": "source", "content_format": "none", "institution": source.institution,
                "status": "blocked" if "blocked" in error or "robots" in error else "error", "error": error[:2000], "metadata": {},
                "discovered_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)})
        summaries[source.name] = {"discovered": len(candidates), "error": error}
    print(f"External library ingest: {written} records; mode={'APPLY' if args.apply else 'DRY RUN'}")
    for name, summary in summaries.items():
        print(f"  {name}: {summary['discovered']} discovered" + (f"; {summary['error']}" if summary["error"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())