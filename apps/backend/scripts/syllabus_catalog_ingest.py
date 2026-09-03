"""Crawl official Assam syllabus PDFs and upsert the curriculum hierarchy.

Sources:
  * AHSEC / ASSEB Division II syllabus catalog
  * Gauhati University syllabus archive

The importer is dry-run by default. Pass ``--apply`` to write. It never
overwrites chapter content; it only upserts Board -> Class/Semester -> Course
(Stream) -> Subject records and the ``syllabus_documents`` source catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import fitz
import requests
import urllib3
from bs4 import BeautifulSoup
from pymongo import ASCENDING, MongoClient

AHSEC_CATALOG = "https://ahsec.assam.gov.in/index.php/syllabus-2"
GU_CATALOG = "https://gauhati.ac.in/syllabus/"
ALLOWED_PDF_HOSTS = {"ahsec.assam.gov.in", "gauhati.ac.in"}
ROMAN = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
}
ORDINAL = {
    1: "1st",
    2: "2nd",
    3: "3rd",
    4: "4th",
    5: "5th",
    6: "6th",
    7: "7th",
    8: "8th",
    9: "9th",
    10: "10th",
}


@dataclass(frozen=True)
class CatalogItem:
    institution: str
    source_page_url: str
    source_url: str
    source_title: str
    programme: str
    faculty: str
    subject_name: str
    session: str


@dataclass
class ParsedItem:
    item: CatalogItem
    checksum: str
    text: str
    page_count: int
    semesters: list[int]
    course_codes: list[str]
    course_names: list[str]


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("all", "ahsec", "gu"), default="all")
    parser.add_argument("--apply", action="store_true", help="Write to MongoDB")
    parser.add_argument("--max-pdfs", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-text-chars", type=int, default=1_500_000)
    return parser.parse_args()


def _slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value or "subject"


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "SyrabitSyllabusIndexer/1.0 (+https://syrabit.ai)"
    return session


def _get_html(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    # Both official Assam sites currently serve incomplete certificate chains.
    # The host allowlist below prevents this compatibility exception from
    # becoming a general-purpose insecure fetch.
    response = session.get(url, timeout=timeout, verify=False)
    response.raise_for_status()
    return BeautifulSoup(response.content, "html.parser")


def crawl_ahsec(session: requests.Session, timeout: int) -> list[CatalogItem]:
    soup = _get_html(session, AHSEC_CATALOG, timeout)
    seen: set[str] = set()
    items: list[CatalogItem] = []
    for anchor in soup.find_all("a", href=True):
        url = urljoin(AHSEC_CATALOG, anchor["href"])
        if ".pdf" not in url.lower() or url in seen:
            continue
        seen.add(url)
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title:
            title = Path(url.split("?", 1)[0]).stem.replace("-", " ")
        class_match = re.search(r"\bClass\s+(XI{1,2})\b", title, re.I)
        class_name = (
            "HS 1st Year"
            if class_match and class_match.group(1).upper() == "XI"
            else "HS 2nd Year"
            if class_match
            else ""
        )
        subject = re.sub(
            r"^(?:Revised\s+)?Syllabus\s+of\s+(?:New\s+Subject\s+)?",
            "",
            title,
            flags=re.I,
        )
        subject = re.split(r"\s+for\s+Class\s+XI{1,2}\b", subject, maxsplit=1, flags=re.I)[0]
        subject = re.sub(r"\s*\(\d{4}[-–]\d{2}\s+onwards\).*$", "", subject).strip(" .–-")
        if title.lower().startswith("notification"):
            subject = "Curriculum Notification"
        items.append(
            CatalogItem(
                institution="AHSEC / ASSEB Division II",
                source_page_url=AHSEC_CATALOG,
                source_url=url,
                source_title=title,
                programme=class_name,
                faculty="",
                subject_name=subject or "General Studies",
                session="2026-27",
            )
        )
    return items


def crawl_gu(session: requests.Session, timeout: int) -> list[CatalogItem]:
    items: list[CatalogItem] = []
    seen: set[str] = set()
    for page in range(1, 30):
        page_url = GU_CATALOG if page == 1 else urljoin(GU_CATALOG, f"page/{page}/")
        soup = _get_html(session, page_url, timeout)
        rows = soup.select(".gu-syllabus-row")
        if not rows:
            break
        added = 0
        for row in rows:
            pdf = row.select_one("a.gu-iqac-doc__dl[href]")
            title_node = row.select_one(".gu-iqac-doc__title")
            tags = [" ".join(x.get_text(" ", strip=True).split()) for x in row.select(".gu-iqac-tag")]
            if not pdf or len(tags) < 2:
                continue
            url = urljoin(page_url, pdf["href"])
            if url in seen:
                continue
            seen.add(url)
            added += 1
            source_title = (
                " ".join(title_node.get_text(" ", strip=True).replace("📄", "").split())
                if title_node
                else tags[1]
            )
            # Most cards expose programme, faculty, subject, session. A few
            # current cards omit the redundant subject tag and expose only
            # programme, faculty, session; derive the subject from the title.
            has_session_tag = bool(re.fullmatch(r"20\d{2}(?:-\d{2})?", tags[-1]))
            session_name = tags[-1] if has_session_tag else ""
            subject_tag_index = 2
            subject_name = (
                tags[subject_tag_index]
                if len(tags) >= 4 or (len(tags) >= 3 and not has_session_tag)
                else source_title.split(" — ", 1)[0].strip()
            )
            items.append(
                CatalogItem(
                    institution="Gauhati University",
                    source_page_url=page_url,
                    source_url=url,
                    source_title=source_title,
                    programme=tags[0],
                    faculty=tags[1],
                    subject_name=subject_name,
                    session=session_name,
                )
            )
        if added == 0:
            break
    return items


def _allowed_pdf(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_PDF_HOSTS


def _extract_semesters(text: str, programme: str) -> list[int]:
    found: set[int] = set()
    for match in re.finditer(
        r"\b(?:semester|sem\.?)\s*[-:–]?\s*(10|[1-9]|VIII|VII|VI|IV|V|III|II|IX|I)\b",
        text,
        re.I,
    ):
        token = match.group(1).upper()
        found.add(int(token) if token.isdigit() else ROMAN[token])
    for match in re.finditer(r"\b(1st|2nd|3rd|[4-9]th|10th)\s+semester\b", text, re.I):
        found.add(int(re.match(r"\d+", match.group(1)).group()))
    upper = programme.upper()
    if "FYIMP" in upper:
        return list(range(1, 11))
    if "FYUGP" in upper:
        return list(range(1, 9))
    if re.search(r"\bPG\b", upper):
        return list(range(1, 5))
    return sorted(found)


def _course_codes(text: str) -> list[str]:
    candidates = re.findall(
        r"\b[A-Z]{2,8}[-/]?(?:0?[1-9]|10)[A-Z0-9/-]{2,14}\b",
        text,
    )
    return sorted(dict.fromkeys(candidates))[:500]


def _ocr_languages(item: CatalogItem) -> str:
    value = f"{item.subject_name} {item.source_title}".lower()
    if "assamese" in value:
        return "asm+eng"
    if "bengali" in value:
        return "ben+eng"
    if "nepali" in value or "napali" in value:
        return "nep+eng"
    if "manipuri" in value:
        return "ben+eng"
    if "bodo" in value:
        return "hin+eng"
    return "eng"


def _degree_course(programme: str, faculty: str, subject: str) -> str:
    p = programme.upper()
    f = faculty.lower()
    s = subject.lower()
    if "FYUGP" in p:
        if "commerce" in f and "business administration" in s:
            return "BBA"
        if "commerce" in f:
            return "B.Com"
        if "science" in f:
            return "B.Sc"
        if "technology" in f:
            return "B.Voc"
        return "B.A."
    if "FYIMP" in p:
        if "science" in f:
            return "Integrated M.Sc."
        if "commerce" in f:
            return "Integrated M.Com"
        if "technology" in f:
            return "Integrated M.Voc"
        return "Integrated M.A."
    if re.search(r"\bPG\b", p):
        if "commerce" in f:
            return "M.Com"
        if "science" in f:
            return "M.Sc."
        if "technology" in f:
            return "M.Tech."
        return "M.A."
    return programme.split("(", 1)[0].strip() or "Degree"


def _ahsec_stream(subject: str) -> str:
    name = subject.lower()
    science = {"physics", "chemistry", "biology", "mathematics", "computer science"}
    commerce = {"accountancy", "business", "finance", "costing", "taxation", "economics"}
    if any(token in name for token in science):
        return "Science"
    if any(token in name for token in commerce):
        return "Commerce"
    return "General"


def _parse_pdf(
    item: CatalogItem,
    *,
    timeout: int,
    max_text_chars: int,
) -> ParsedItem:
    if not _allowed_pdf(item.source_url):
        raise ValueError(f"PDF host is not allowlisted: {item.source_url}")
    session = _session()
    response = session.get(item.source_url, timeout=timeout, verify=False)
    response.raise_for_status()
    data = response.content
    if not data.startswith(b"%PDF"):
        raise ValueError("response is not a PDF")
    checksum = hashlib.sha256(data).hexdigest()
    with fitz.open(stream=data, filetype="pdf") as document:
        pages = [page.get_text("text") for page in document]
        if not any(page.strip() for page in pages) and shutil.which("tesseract"):
            # A small number of official AHSEC notifications are image-only
            # scans. OCR them locally so every catalog PDF has searchable text.
            ocr_pages: list[str] = []
            with tempfile.TemporaryDirectory(prefix="syrabit-syllabus-ocr-") as tmp:
                for page_number, page in enumerate(document):
                    image_path = Path(tmp) / f"page-{page_number + 1}.png"
                    scale = min(1.5, 1800 / max(page.rect.width, 1))
                    page.get_pixmap(
                        matrix=fitz.Matrix(scale, scale), alpha=False
                    ).save(image_path)
                    try:
                        completed = subprocess.run(
                            [
                                "tesseract",
                                str(image_path),
                                "stdout",
                                "-l",
                                _ocr_languages(item),
                                "--psm",
                                "6",
                            ],
                            check=False,
                            capture_output=True,
                            text=True,
                            timeout=60,
                        )
                        ocr_pages.append(
                            completed.stdout if completed.returncode == 0 else ""
                        )
                    except subprocess.TimeoutExpired:
                        ocr_pages.append("")
            pages = ocr_pages
        page_count = len(document)
    text = "\n".join(pages)
    text = re.sub(r"\x00", "", text)
    if not text.strip():
        # Preserve searchable source metadata even when an image-only page
        # cannot be OCRed. The structured hierarchy still comes from the
        # official catalog card.
        text = (
            f"{item.source_title}\nInstitution: {item.institution}\n"
            f"Programme: {item.programme}\nSubject: {item.subject_name}"
        )
    semesters = (
        []
        if item.institution.startswith("AHSEC")
        else _extract_semesters(text[:max_text_chars], item.programme)
    )
    courses = (
        [_ahsec_stream(item.subject_name)]
        if item.institution.startswith("AHSEC")
        else [_degree_course(item.programme, item.faculty, item.subject_name)]
    )
    return ParsedItem(
        item=item,
        checksum=checksum,
        text=text[:max_text_chars],
        page_count=page_count,
        semesters=semesters,
        course_codes=_course_codes(text),
        course_names=courses,
    )


def _object_id_list(values: Iterable) -> list:
    return list(dict.fromkeys(values))


def apply_records(parsed: list[ParsedItem]) -> dict[str, int]:
    mongo_url = os.environ.get("MONGODB_URL") or os.environ.get("MONGODB_URI")
    if not mongo_url:
        raise RuntimeError("MONGODB_URL / MONGODB_URI is required with --apply")
    client = MongoClient(mongo_url, serverSelectionTimeoutMS=15_000)
    db = client[os.environ.get("MONGODB_DB_NAME", "syrabit_prod")]
    now = datetime.now(timezone.utc)
    counts = {
        "documents_upserted": 0,
        "classes_created": 0,
        "streams_created": 0,
        "subjects_created": 0,
        "subjects_linked": 0,
    }

    db.syllabus_documents.create_index([("source_url", ASCENDING)], unique=True)
    for record in parsed:
        item = record.item
        board_slug = "ahsec" if item.institution.startswith("AHSEC") else "degree"
        board = db.boards.find_one({"slug": board_slug})
        if not board:
            raise RuntimeError(f"Required board {board_slug!r} does not exist")

        class_names = (
            [item.programme]
            if board_slug == "ahsec" and item.programme
            else [f"{ORDINAL[n]} Semester" for n in record.semesters]
        )
        class_ids = []
        stream_ids = []
        subject_ids = []
        source_metadata = {
            "title": item.source_title,
            "url": item.source_url,
            "institution": item.institution,
            "programme": item.programme,
            "faculty": item.faculty,
            "session": item.session,
            "semesters": record.semesters,
            "checksum_sha256": record.checksum,
        }

        for class_name in class_names:
            cls = db.classes.find_one({"board_id": board["_id"], "name": class_name})
            if not cls:
                class_id = db.classes.insert_one(
                    {
                        "name": class_name,
                        "board_id": board["_id"],
                        "status": "active",
                        "created_at": now,
                        "updated_at": now,
                    }
                ).inserted_id
                cls = {"_id": class_id}
                counts["classes_created"] += 1
            class_ids.append(cls["_id"])

            for course_name in record.course_names:
                stream = db.streams.find_one(
                    {"class_id": cls["_id"], "name": course_name}
                )
                if not stream:
                    stream_id = db.streams.insert_one(
                        {
                            "name": course_name,
                            "class_id": cls["_id"],
                            "status": "active",
                            "created_at": now,
                            "updated_at": now,
                        }
                    ).inserted_id
                    stream = {"_id": stream_id}
                    counts["streams_created"] += 1
                stream_ids.append(stream["_id"])

                subject = db.subjects.find_one(
                    {
                        "stream_id": stream["_id"],
                        "$or": [
                            {"slug": _slug(item.subject_name)},
                            {"name": {"$regex": f"^{re.escape(item.subject_name)}$", "$options": "i"}},
                        ],
                    }
                )
                if not subject:
                    subject_id = db.subjects.insert_one(
                        {
                            "name": item.subject_name,
                            "slug": _slug(item.subject_name),
                            "stream_id": stream["_id"],
                            "status": "active",
                            "description": (
                                f"Official {item.programme or class_name} syllabus "
                                f"from {item.institution}."
                            ),
                            "tags": [
                                tag
                                for tag in ["syllabus", item.programme, item.faculty, item.session]
                                if tag
                            ],
                            "has_document": True,
                            "syllabus_sources": [source_metadata],
                            "created_at": now,
                            "updated_at": now,
                        }
                    ).inserted_id
                    subject = {"_id": subject_id}
                    counts["subjects_created"] += 1
                else:
                    result = db.subjects.update_one(
                        {
                            "_id": subject["_id"],
                            "syllabus_sources.url": {"$ne": item.source_url},
                        },
                        {
                            "$push": {"syllabus_sources": source_metadata},
                            "$set": {"has_document": True, "updated_at": now},
                        },
                    )
                    counts["subjects_linked"] += result.modified_count
                subject_ids.append(subject["_id"])

        db.syllabus_documents.update_one(
            {"source_url": item.source_url},
            {
                "$set": {
                    "source_page_url": item.source_page_url,
                    "source_title": item.source_title,
                    "institution": item.institution,
                    "programme": item.programme or None,
                    "faculty": item.faculty or None,
                    "session": item.session or None,
                    "subject_name": item.subject_name,
                    "course_names": record.course_names,
                    "semesters": record.semesters,
                    "course_codes": record.course_codes,
                    "board_id": board["_id"],
                    "class_ids": _object_id_list(class_ids),
                    "stream_ids": _object_id_list(stream_ids),
                    "subject_ids": _object_id_list(subject_ids),
                    "checksum_sha256": record.checksum,
                    "extracted_text": record.text,
                    "page_count": record.page_count,
                    "status": "active",
                    "crawled_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        counts["documents_upserted"] += 1
    return counts


def main() -> int:
    args = _args()
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")
    session = _session()
    items: list[CatalogItem] = []
    if args.source in ("all", "ahsec"):
        items.extend(crawl_ahsec(session, args.timeout))
    if args.source in ("all", "gu"):
        items.extend(crawl_gu(session, args.timeout))
    items = list({item.source_url: item for item in items}.values())
    if args.max_pdfs is not None:
        items = items[: max(0, args.max_pdfs)]
    print(f"Discovered {len(items)} unique official syllabus PDFs")

    parsed: list[ParsedItem] = []
    failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
        jobs = {
            pool.submit(
                _parse_pdf,
                item,
                timeout=args.timeout,
                max_text_chars=args.max_text_chars,
            ): item
            for item in items
        }
        for index, future in enumerate(as_completed(jobs), 1):
            item = jobs[future]
            try:
                result = future.result()
                parsed.append(result)
                print(
                    f"[{index}/{len(items)}] OK {item.subject_name} "
                    f"({result.page_count} pages; semesters={result.semesters or '-'})"
                )
            except Exception as exc:
                failures.append((item.source_url, str(exc)))
                print(f"[{index}/{len(items)}] FAILED {item.source_url}: {exc}", file=sys.stderr)

    parsed.sort(key=lambda x: (x.item.institution, x.item.subject_name, x.item.source_url))
    print(
        f"Parsed {len(parsed)}/{len(items)} PDFs; "
        f"{len(failures)} failed; mode={'APPLY' if args.apply else 'DRY RUN'}"
    )
    if args.apply:
        counts = apply_records(parsed)
        print("Database update:", counts)
    else:
        hierarchy = {
            (
                p.item.institution,
                p.item.programme,
                tuple(p.semesters),
                tuple(p.course_names),
                p.item.subject_name,
            )
            for p in parsed
        }
        print(f"Would upsert {len(hierarchy)} unique syllabus hierarchy entries")
    if failures:
        print("Failures:")
        for url, error in failures:
            print(f"  {url}: {error}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())