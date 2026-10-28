"""Offline, allowlisted snapshot archiving and crash-safe publication."""

from __future__ import annotations

import datetime as _dt
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TWEET_SUFFIX = re.compile(r" - ([0-9]+)\.md$")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


class BookmarkWikiError(Exception):
    code = "IO_ERROR"


class InvalidInput(BookmarkWikiError):
    code = "INVALID_INPUT"


class IntegrityError(BookmarkWikiError):
    code = "INTEGRITY_ERROR"


class Locked(BookmarkWikiError):
    code = "LOCKED"


class CorruptManifest(BookmarkWikiError):
    code = "CORRUPT_MANIFEST"


class SimulatedFault(RuntimeError):
    """Private test hook; no CLI flag exposes it."""


class MissingManifest(BookmarkWikiError):
    code = "MISSING_MANIFEST"


class StaleIndex(BookmarkWikiError):
    code = "STALE_INDEX"


class NotInitialized(BookmarkWikiError):
    code = "NOT_INITIALIZED"


class NotFound(BookmarkWikiError):
    code = "NOT_FOUND"



def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_bytes(value: object) -> bytes:
    return canonical(value).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_json_bytes(path: Path, error_type=InvalidInput):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise error_type("invalid json") from exc


def _check_no_symlinks(path: Path, stop: Path) -> None:
    """Reject symlinks in every lexical component from filesystem root to path."""
    stop = stop.absolute()
    path = path.absolute()
    try:
        path.relative_to(stop)
    except ValueError as exc:
        raise InvalidInput("path outside vault") from exc
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            # macOS exposes temporary directories through these stable system aliases;
            # user-controlled aliases remain rejected below.
            system_alias = {"/var": "/private/var", "/tmp": "/private/tmp"}.get(str(current))
            if system_alias is None or os.path.realpath(current) != system_alias:
                raise InvalidInput("symlink path")


def _safe_vault_relative(vault: Path, relative: str, *, require_exists=True) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise InvalidInput("invalid relative path")
    if relative.startswith("/") or relative.startswith("../") or "/../" in relative or relative.endswith("/.."):
        raise InvalidInput("invalid relative path")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidInput("invalid relative path")
    path = vault / Path(*parts)
    _check_no_symlinks(path, vault)
    if require_exists and not path.is_file():
        raise InvalidInput("missing referenced path")
    return path


def _input_path(vault: Path, path_value: str, expected_parent: str) -> Path:
    path = Path(path_value).absolute()
    if ".." in path.parts:
        raise InvalidInput("traversal path")
    base = (vault / expected_parent).absolute()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise InvalidInput("input outside evidence") from exc
    _check_no_symlinks(path, vault)
    if not path.is_file():
        raise InvalidInput("missing input")
    return path


def _require_string(value, name: str, *, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value):
        raise InvalidInput("invalid field")
    return value


def _parse_iso(value, nullable=True):
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise InvalidInput("invalid timestamp")
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidInput("invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidInput("timestamp must be timezone aware")
    return value


def _encoded_object(value, warning_code: str):
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, warning_code
    try:
        parsed = json.loads(value)
    except (UnicodeError, json.JSONDecodeError):
        return None, warning_code
    return parsed, None


def _absolute_http_url(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and bool(hostname)


def _validate_snapshot(vault: Path, snapshot_path: Path, hashes_path: Path):
    snapshot_bytes = snapshot_path.read_bytes()
    snapshot = _parse_json_bytes(snapshot_path)
    if not isinstance(snapshot, dict) or set(("captured_at", "tables")) - snapshot.keys():
        raise InvalidInput("invalid snapshot")
    _parse_iso(snapshot["captured_at"], nullable=False)
    tables = snapshot["tables"]
    if not isinstance(tables, dict):
        raise InvalidInput("invalid tables")
    for table in ("bookmarks", "media", "categories", "assignments"):
        if not isinstance(tables.get(table), list):
            raise InvalidInput("invalid table")

    capture_manifest = snapshot_path.parent / "capture-manifest.json"
    _check_no_symlinks(capture_manifest, vault)
    if not capture_manifest.is_file():
        raise InvalidInput("missing capture manifest")
    capture = _parse_json_bytes(capture_manifest)
    if not isinstance(capture, dict) or not isinstance(capture.get("evidence_sha256"), str) or not _SHA256.fullmatch(capture["evidence_sha256"]):
        raise InvalidInput("invalid capture manifest")
    actual_evidence = sha256_bytes(snapshot_bytes)
    if capture["evidence_sha256"] != actual_evidence:
        raise IntegrityError("evidence digest mismatch")

    raw_hashes = _parse_json_bytes(hashes_path)
    if not isinstance(raw_hashes, dict):
        raise InvalidInput("invalid raw hashes")
    digest_paths = {}
    native_dirs = set()
    raw_candidates = []
    for relative, digest in raw_hashes.items():
        if not isinstance(relative, str) or not relative.startswith("raw/") or "\\" in relative or relative.startswith("/"):
            raise InvalidInput("invalid digest path")
        parts = relative.split("/")
        if any(part in {"", ".", ".."} for part in parts) or not relative.endswith(".md"):
            raise InvalidInput("invalid digest path")
        native_indexes = [index for index, part in enumerate(parts) if part == "Twitter Bookmarks"]
        if len(native_indexes) != 1 or native_indexes[0] < 1:
            raise InvalidInput("invalid digest path")
        native_end = native_indexes[0] + 1
        native_rel = "/".join(parts[:native_end])
        tail = parts[native_end:]
        is_source = len(tail) == 1
        is_index = len(tail) >= 2 and tail[0] == "_index"
        if not (is_source or is_index):
            raise InvalidInput("invalid digest path")
        path = _safe_vault_relative(vault, relative)
        if not path.is_file():
            raise InvalidInput("missing digest file")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise InvalidInput("invalid digest")
        if sha256_bytes(path.read_bytes()) != digest:
            raise IntegrityError("raw digest mismatch")
        digest_paths[relative] = path
        native_dirs.add(native_rel)
        if is_source:
            match = _TWEET_SUFFIX.search(path.name)
            if not match:
                raise InvalidInput("invalid source note name")
            raw_candidates.append((match.group(1), relative, path))

    bookmarks = tables["bookmarks"]
    bookmark_ids = set()
    tweet_ids = set()
    normalized = []
    for row in bookmarks:
        if not isinstance(row, dict):
            raise InvalidInput("invalid bookmark")
        required = ("id", "tweetId", "text", "authorHandle", "authorName", "tweetCreatedAt", "importedAt", "semanticTags", "entities", "enrichedAt")
        if any(key not in row for key in required):
            raise InvalidInput("missing bookmark field")
        bookmark_id = _require_string(row["id"], "id", nonempty=True)
        tweet_id = _require_string(row["tweetId"], "tweetId", nonempty=True)
        if not tweet_id.isascii() or not tweet_id.isdigit():
            raise InvalidInput("invalid tweet id")
        if bookmark_id in bookmark_ids or tweet_id in tweet_ids:
            raise InvalidInput("duplicate bookmark")
        bookmark_ids.add(bookmark_id)
        tweet_ids.add(tweet_id)
        text = _require_string(row["text"], "text")
        author_handle = _require_string(row["authorHandle"], "authorHandle")
        author_name = _require_string(row["authorName"], "authorName")
        tweet_created = _parse_iso(row["tweetCreatedAt"])
        imported = _parse_iso(row["importedAt"])
        enriched = row["enrichedAt"]
        if enriched is not None and not isinstance(enriched, str):
            raise InvalidInput("invalid enrichedAt")

        tags, tag_warning = _encoded_object(row["semanticTags"], "malformed_hints")
        warnings = set()
        if tag_warning or (tags is not None and (not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags))):
            semantic = []
            warnings.add("malformed_hints")
        else:
            semantic = sorted(set(tags or []))
        entities, url_warning = _encoded_object(row["entities"], "malformed_urls")
        urls = []
        if url_warning or (entities is not None and not isinstance(entities, dict)):
            warnings.add("malformed_urls")
        elif entities is None or not isinstance(entities.get("urls"), list) or not all(isinstance(url, str) for url in entities.get("urls", [])):
            warnings.add("malformed_urls")
        else:
            valid = []
            for url in entities["urls"]:
                if _absolute_http_url(url):
                    valid.append(url)
                else:
                    warnings.add("malformed_urls")
            urls = sorted(set(valid))

        normalized.append({
            "bookmark_id": bookmark_id,
            "tweet_id": tweet_id,
            "text": text,
            "author_handle": author_handle,
            "author_name": author_name,
            "tweet_created_at": tweet_created,
            "imported_at": imported,
            "enriched_at": enriched,
            "semantic_hints": semantic,
            "expanded_urls": urls,
            "warnings": warnings,
        })

    media_ids = set()
    media_by_bookmark = {row["bookmark_id"]: [] for row in normalized}
    for row in tables["media"]:
        if not isinstance(row, dict) or any(key not in row for key in ("id", "bookmarkId", "type", "url", "thumbnailUrl", "imageTags")):
            raise InvalidInput("invalid media")
        media_id = _require_string(row["id"], "media id", nonempty=True)
        bookmark_id = _require_string(row["bookmarkId"], "media bookmark", nonempty=True)
        media_type = _require_string(row["type"], "media type")
        media_url = _require_string(row["url"], "media url")
        thumb = row["thumbnailUrl"]
        if thumb is not None and not isinstance(thumb, str):
            raise InvalidInput("invalid thumbnail")
        if media_id in media_ids or bookmark_id not in media_by_bookmark:
            raise InvalidInput("invalid media identity")
        media_ids.add(media_id)
        image_tags, image_warning = _encoded_object(row["imageTags"], "malformed_image_tags")
        if image_warning or (image_tags is not None and not isinstance(image_tags, dict)):
            vision_status = "malformed"
            for normalized_row in normalized:
                if normalized_row["bookmark_id"] == bookmark_id:
                    normalized_row["warnings"].add("malformed_image_tags")
        elif image_tags is None:
            vision_status = "missing"
        elif image_tags == {}:
            vision_status = "empty"
        else:
            vision_status = "present"
        media_by_bookmark[bookmark_id].append({"id": media_id, "type": media_type, "url": media_url, "thumbnail_url": thumb, "_vision": vision_status})

    category_ids = set()
    categories = {}
    slugs = set()
    for row in tables["categories"]:
        if not isinstance(row, dict) or any(key not in row for key in ("id", "name", "slug")):
            raise InvalidInput("invalid category")
        category_id = _require_string(row["id"], "category id", nonempty=True)
        name = _require_string(row["name"], "category name")
        slug = _require_string(row["slug"], "category slug")
        if category_id in category_ids or slug in slugs:
            raise InvalidInput("duplicate category")
        category_ids.add(category_id)
        slugs.add(slug)
        categories[category_id] = {"id": category_id, "name": name, "slug": slug}

    assignment_pairs = set()
    category_by_bookmark = {row["bookmark_id"]: [] for row in normalized}
    for row in tables["assignments"]:
        if not isinstance(row, dict) or any(key not in row for key in ("bookmarkId", "categoryId")):
            raise InvalidInput("invalid assignment")
        bookmark_id = _require_string(row["bookmarkId"], "assignment bookmark", nonempty=True)
        category_id = _require_string(row["categoryId"], "assignment category", nonempty=True)
        pair = (bookmark_id, category_id)
        if pair in assignment_pairs or pair[0] not in category_by_bookmark or pair[1] not in categories:
            raise InvalidInput("invalid assignment")
        assignment_pairs.add(pair)
        category_by_bookmark[pair[0]].append(categories[pair[1]])
    if any(not category_by_bookmark[row["bookmark_id"]] for row in normalized):
        raise InvalidInput("bookmark without category")

    by_tweet = {tweet_id: [] for tweet_id in tweet_ids}
    seen_source_tweets = set()
    for tweet_id, relative, path in raw_candidates:
        if tweet_id not in by_tweet or tweet_id in seen_source_tweets:
            raise InvalidInput("orphan or duplicate source note")
        seen_source_tweets.add(tweet_id)
        by_tweet[tweet_id].append((relative, path))
    if seen_source_tweets != tweet_ids:
        raise InvalidInput("missing or orphan source note")
    for native_rel in native_dirs:
        native_dir = vault / Path(*native_rel.split("/"))
        actual = set()
        for candidate in native_dir.rglob("*"):
            if candidate.is_symlink():
                raise InvalidInput("symlink path")
            if candidate.is_file() and candidate.suffix == ".md":
                actual.add(candidate.relative_to(vault).as_posix())
        listed = {
            rel for rel in digest_paths
            if rel == native_rel or rel.startswith(native_rel + "/")
        }
        if actual != listed:
            raise InvalidInput("incomplete native digest coverage")

    records = []
    for row in normalized:
        relative_raw, raw_path = by_tweet[row["tweet_id"]][0]
        content = {
            "bookmark_id": row["bookmark_id"],
            "tweet_id": row["tweet_id"],
            "text": row["text"],
            "author_handle": row["author_handle"],
            "author_name": row["author_name"],
            "tweet_created_at": row["tweet_created_at"],
            "imported_at": row["imported_at"],
            "source_url": "https://x.com/" + row["author_handle"] + "/status/" + row["tweet_id"],
            "expanded_urls": row["expanded_urls"],
            "media": sorted(({k: v for k, v in media.items() if k != "_vision"} for media in media_by_bookmark[row["bookmark_id"]]), key=lambda item: item["id"]),
        }
        metadata = {
            "categories": sorted(category_by_bookmark[row["bookmark_id"]], key=lambda item: item["id"]),
            "semantic_hints": row["semantic_hints"],
            "enriched_at": row["enriched_at"],
            "vision": sorted(({"id": media["id"], "status": media["_vision"]} for media in media_by_bookmark[row["bookmark_id"]]), key=lambda item: item["id"]),
            "warnings": sorted(row["warnings"]),
        }
        records.append({
            "source_id": "x:" + row["tweet_id"],
            "tweet_id": row["tweet_id"],
            "content": content,
            "metadata": metadata,
            "evidence_path": snapshot_path.relative_to(vault).as_posix(),
            "evidence_sha256": actual_evidence,
            "raw_path": relative_raw,
            "raw_sha256": raw_hashes[relative_raw],
        })
    return records


def _revision_for(record):
    content_hash = sha256_bytes(canonical_bytes(record["content"]))
    metadata_hash = sha256_bytes(canonical_bytes(record["metadata"]))
    revision_hash = sha256_bytes(canonical_bytes({"content_hash": content_hash, "metadata_hash": metadata_hash, "raw_sha256": record["raw_sha256"]}))
    return {
        "revision_hash": revision_hash,
        "content_hash": content_hash,
        "metadata_hash": metadata_hash,
        "archived_at": _utc_now(),
        "evidence_path": record["evidence_path"],
        "evidence_sha256": record["evidence_sha256"],
        "raw_path": record["raw_path"],
        "raw_sha256": record["raw_sha256"],
        "content": record["content"],
        "metadata": record["metadata"],
    }


_SOURCE_KEYS = {"source_id", "tweet_id", "current_revision", "revisions", "context_status", "integration_status", "wiki_pages", "removal_eligible"}
_REVISION_KEYS = {"revision_hash", "content_hash", "metadata_hash", "archived_at", "evidence_path", "evidence_sha256", "raw_path", "raw_sha256", "content", "metadata"}
_CONTENT_KEYS = {"bookmark_id", "tweet_id", "text", "author_handle", "author_name", "tweet_created_at", "imported_at", "source_url", "expanded_urls", "media"}
_METADATA_KEYS = {"categories", "semantic_hints", "enriched_at", "vision", "warnings"}


def _validate_manifest_revision_shape(source_id: str, source_tweet_id: str, revision_hash: str, revision: dict):
    if set(revision) != _REVISION_KEYS or revision.get("revision_hash") != revision_hash:
        raise CorruptManifest("invalid revision")
    for field in ("revision_hash", "content_hash", "metadata_hash", "evidence_sha256", "raw_sha256"):
        if not isinstance(revision.get(field), str) or not _SHA256.fullmatch(revision[field]):
            raise CorruptManifest("invalid revision digest")
    try:
        _parse_iso(revision["archived_at"], nullable=False)
    except InvalidInput as exc:
        raise CorruptManifest("invalid revision timestamp") from exc
    for field in ("evidence_path", "raw_path"):
        if not isinstance(revision.get(field), str):
            raise CorruptManifest("invalid revision path")

    content = revision["content"]
    if not isinstance(content, dict) or set(content) != _CONTENT_KEYS:
        raise CorruptManifest("invalid revision content")
    for field in ("bookmark_id", "tweet_id", "text", "author_handle", "author_name", "source_url"):
        if not isinstance(content[field], str):
            raise CorruptManifest("invalid revision content field")
    if content["tweet_id"] != source_tweet_id or source_id != "x:" + content["tweet_id"]:
        raise CorruptManifest("source identity mismatch")
    for field in ("tweet_created_at", "imported_at"):
        try:
            _parse_iso(content[field])
        except InvalidInput as exc:
            raise CorruptManifest("invalid revision timestamp") from exc
    expected_url = "https://x.com/" + content["author_handle"] + "/status/" + content["tweet_id"]
    if content["source_url"] != expected_url or not _absolute_http_url(content["source_url"]):
        raise CorruptManifest("invalid source url")
    if not isinstance(content["expanded_urls"], list) or not all(_absolute_http_url(url) for url in content["expanded_urls"]):
        raise CorruptManifest("invalid expanded urls")
    if not isinstance(content["media"], list):
        raise CorruptManifest("invalid media")
    for media in content["media"]:
        if not isinstance(media, dict) or set(media) != {"id", "type", "url", "thumbnail_url"}:
            raise CorruptManifest("invalid media")
        if not all(isinstance(media[field], str) for field in ("id", "type", "url")):
            raise CorruptManifest("invalid media field")
        if media["thumbnail_url"] is not None and not isinstance(media["thumbnail_url"], str):
            raise CorruptManifest("invalid media thumbnail")

    metadata = revision["metadata"]
    if not isinstance(metadata, dict) or set(metadata) != _METADATA_KEYS:
        raise CorruptManifest("invalid revision metadata")
    if not isinstance(metadata["categories"], list) or not isinstance(metadata["semantic_hints"], list) or not isinstance(metadata["vision"], list) or not isinstance(metadata["warnings"], list):
        raise CorruptManifest("invalid revision metadata field")
    if not all(isinstance(value, str) for value in metadata["semantic_hints"] + metadata["warnings"]):
        raise CorruptManifest("invalid revision metadata strings")
    if metadata["enriched_at"] is not None and not isinstance(metadata["enriched_at"], str):
        raise CorruptManifest("invalid enriched timestamp")
    for category in metadata["categories"]:
        if not isinstance(category, dict) or set(category) != {"id", "name", "slug"} or not all(isinstance(category[field], str) for field in ("id", "name", "slug")):
            raise CorruptManifest("invalid category")
    for vision in metadata["vision"]:
        if not isinstance(vision, dict) or set(vision) != {"id", "status"} or not isinstance(vision["id"], str) or not isinstance(vision["status"], str):
            raise CorruptManifest("invalid vision")


def _check_revision_integrity(vault: Path, revision: dict, digest_cache=None):
    digest_cache = {} if digest_cache is None else digest_cache

    def verified_digest(path: Path, expected: str) -> None:
        cache_key = path.as_posix()
        actual = digest_cache.get(cache_key)
        if actual is None:
            actual = sha256_bytes(path.read_bytes())
            digest_cache[cache_key] = actual
        if actual != expected:
            raise IntegrityError("historical digest mismatch")

    try:
        evidence = _safe_vault_relative(vault, revision["evidence_path"])
        raw = _safe_vault_relative(vault, revision["raw_path"])
        verified_digest(evidence, revision["evidence_sha256"])
        verified_digest(raw, revision["raw_sha256"])
        if sha256_bytes(canonical_bytes(revision["content"])) != revision["content_hash"]:
            raise CorruptManifest("historical content mismatch")
        if sha256_bytes(canonical_bytes(revision["metadata"])) != revision["metadata_hash"]:
            raise CorruptManifest("historical metadata mismatch")
        expected = sha256_bytes(canonical_bytes({"content_hash": revision["content_hash"], "metadata_hash": revision["metadata_hash"], "raw_sha256": revision["raw_sha256"]}))
        if expected != revision["revision_hash"]:
            raise CorruptManifest("historical revision mismatch")
    except (InvalidInput, OSError, TypeError, KeyError) as exc:
        raise CorruptManifest("incomplete revision") from exc


def _validate_manifest(vault: Path, manifest: dict):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION or not isinstance(manifest.get("sources"), dict) or not isinstance(manifest.get("updated_at"), str):
        raise CorruptManifest("invalid manifest")
    try:
        _parse_iso(manifest["updated_at"], nullable=False)
    except InvalidInput as exc:
        raise CorruptManifest("invalid manifest timestamp") from exc
    digest_cache = {}
    for source_id, source in manifest["sources"].items():
        if not isinstance(source_id, str) or not source_id.startswith("x:") or not source_id[2:].isascii() or not source_id[2:].isdigit():
            raise CorruptManifest("invalid source id")
        if not isinstance(source, dict) or set(source) != _SOURCE_KEYS or source.get("source_id") != source_id or not isinstance(source.get("tweet_id"), str) or source.get("tweet_id") != source_id[2:] or not source["tweet_id"].isascii() or not source["tweet_id"].isdigit() or not isinstance(source.get("revisions"), dict) or not source["revisions"] or not isinstance(source.get("current_revision"), str) or source.get("current_revision") not in source["revisions"] or not isinstance(source.get("context_status"), str) or not isinstance(source.get("integration_status"), str) or not isinstance(source.get("wiki_pages"), list) or not all(isinstance(page, str) for page in source["wiki_pages"]) or not isinstance(source.get("removal_eligible"), bool):
            raise CorruptManifest("invalid source")
        for revision_hash, revision in source["revisions"].items():
            if not isinstance(revision_hash, str) or not isinstance(revision, dict):
                raise CorruptManifest("invalid revision")
            _validate_manifest_revision_shape(source_id, source["tweet_id"], revision_hash, revision)
            _check_revision_integrity(vault, revision, digest_cache)


def _load_prior(vault: Path):
    manifest_path = vault / "manifest.json"
    index_path = vault / "derived" / "search.sqlite3"
    _check_no_symlinks(manifest_path, vault)
    _check_no_symlinks(index_path, vault)
    if not manifest_path.exists() and not index_path.exists():
        return None, None
    if not manifest_path.exists() and index_path.exists():
        raise MissingManifest("manifest missing")
    _check_no_symlinks(manifest_path, vault)
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorruptManifest("invalid manifest") from exc
    _validate_manifest(vault, manifest)
    return manifest, manifest_bytes


_DOCUMENT_COLUMNS = "source_id,tweet_id,author_handle,tweet_created_at,source_url,expanded_urls_json,content_json,metadata_json,evidence_path,raw_path,context_status,integration_status,wiki_pages_json,removal_eligible,warnings_json,current_revision"
_FTS_COLUMNS = "source_id,tweet_id,text,author,urls,categories,semantic_hints"


def _expected_index_rows(manifest: dict):
    """Project authoritative current revisions into deterministic index rows."""
    documents = []
    fts = []
    for source_id in sorted(manifest["sources"]):
        source = manifest["sources"][source_id]
        revision = source["revisions"][source["current_revision"]]
        content, metadata = revision["content"], revision["metadata"]
        warnings = _warnings(source, metadata)
        documents.append(
            (
                source_id, source["tweet_id"], content["author_handle"], content["tweet_created_at"], content["source_url"],
                canonical(content["expanded_urls"]), canonical(content), canonical(metadata), revision["evidence_path"], revision["raw_path"],
                source["context_status"], source["integration_status"], canonical(source["wiki_pages"]), int(source["removal_eligible"]),
                canonical(warnings), source["current_revision"],
            )
        )
        fts.append(
            (
                source_id, source["tweet_id"], content["text"], content["author_handle"] + " " + content["author_name"],
                content["source_url"] + " " + " ".join(content["expanded_urls"]),
                " ".join(category["name"] + " " + category["slug"] for category in metadata["categories"]),
                " ".join(metadata["semantic_hints"]),
            )
        )
    return {"documents": tuple(documents), "fts": tuple(fts)}


def _validate_index_connection(connection, manifest: dict, manifest_bytes: bytes) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("integrity")
    meta = dict(connection.execute("SELECT key,value FROM meta").fetchall())
    if meta.get("schema_version") != str(SCHEMA_VERSION) or meta.get("manifest_sha256") != sha256_bytes(manifest_bytes):
        raise ValueError("generation")
    expected = _expected_index_rows(manifest)
    documents = tuple(connection.execute("SELECT " + _DOCUMENT_COLUMNS + " FROM documents ORDER BY source_id").fetchall())
    if documents != expected["documents"]:
        raise ValueError("documents")
    fts = tuple(connection.execute("SELECT " + _FTS_COLUMNS + " FROM search_fts ORDER BY source_id").fetchall())
    if fts != expected["fts"]:
        raise ValueError("fts")


def _index_is_fresh(vault: Path, manifest_bytes: bytes, manifest: dict) -> bool:
    index_path = vault / "derived" / "search.sqlite3"
    _check_no_symlinks(index_path, vault)
    if not index_path.is_file():
        return False
    connection = None
    try:
        _check_no_symlinks(index_path, vault)
        connection = sqlite3.connect(index_path.as_uri() + "?mode=ro", uri=True)
        _validate_index_connection(connection, manifest, manifest_bytes)
        return True
    except (sqlite3.Error, OSError, ValueError):
        return False
    finally:
        if connection is not None:
            connection.close()


def _build_index(vault: Path, manifest_bytes: bytes, manifest: dict) -> Path:
    derived = vault / "derived"
    _check_no_symlinks(derived, vault)
    derived.mkdir(parents=True, exist_ok=True)
    temp = derived / f".search.sqlite3.tmp-{uuid.uuid4().hex}"
    connection = sqlite3.connect(temp)
    try:
        connection.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE documents (
                source_id TEXT PRIMARY KEY, tweet_id TEXT NOT NULL, author_handle TEXT NOT NULL,
                tweet_created_at TEXT, source_url TEXT NOT NULL, expanded_urls_json TEXT NOT NULL,
                content_json TEXT NOT NULL, metadata_json TEXT NOT NULL, evidence_path TEXT NOT NULL,
                raw_path TEXT NOT NULL, context_status TEXT NOT NULL, integration_status TEXT NOT NULL,
                wiki_pages_json TEXT NOT NULL, removal_eligible INTEGER NOT NULL, warnings_json TEXT NOT NULL,
                current_revision TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE search_fts USING fts5(
                source_id UNINDEXED, tweet_id UNINDEXED, text, author, urls, categories, semantic_hints,
                tokenize='unicode61'
            );
        """)
        connection.execute("INSERT INTO meta(key,value) VALUES (?,?)", ("schema_version", str(SCHEMA_VERSION)))
        connection.execute("INSERT INTO meta(key,value) VALUES (?,?)", ("manifest_sha256", sha256_bytes(manifest_bytes)))
        expected = _expected_index_rows(manifest)
        connection.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", expected["documents"])
        connection.executemany("INSERT INTO search_fts VALUES (?,?,?,?,?,?,?)", expected["fts"])
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise IntegrityError("index integrity check failed")
    except BaseException:
        if temp.exists():
            temp.unlink()
        raise
    finally:
        connection.close()
    with open(temp, "rb") as file:
        os.fsync(file.fileno())
    return temp


def _warnings(source, metadata):
    values = set(metadata.get("warnings", []))
    values.update({"unverified_source_claims", "bookmarks_not_preferences"})
    if source.get("context_status") == "context_pending":
        values.add("context_pending")
    return sorted(values)


@contextmanager
def _update_lock(vault: Path):
    state = vault / "state"
    _check_no_symlinks(vault, Path(vault.anchor))
    _check_no_symlinks(state, vault)
    state.mkdir(parents=True, exist_ok=True)
    lock_path = state / "update.lock"
    _check_no_symlinks(lock_path, vault)
    handle = open(lock_path, "a+b")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Locked("update locked") from exc
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def update(vault: Path | str, snapshot_path: Path | str, hashes_path: Path | str, *, fault_at=None):
    vault = Path(vault).absolute()
    if not vault.is_dir() or vault.is_symlink():
        raise InvalidInput("invalid vault")
    _check_no_symlinks(vault, Path(vault.anchor))
    for output_path in (vault / "manifest.json", vault / "derived", vault / "derived" / "search.sqlite3", vault / "state", vault / "state" / "update.lock"):
        _check_no_symlinks(output_path, vault)
    snapshot_path = _input_path(vault, str(snapshot_path), "source-evidence")
    hashes_path = _input_path(vault, str(hashes_path), "source-evidence")
    with _update_lock(vault):
        records = _validate_snapshot(vault, snapshot_path, hashes_path)
        prior, prior_bytes = _load_prior(vault)
        incoming = {record["source_id"]: record for record in records}
        if prior is None:
            candidate = {"schema_version": SCHEMA_VERSION, "updated_at": _utc_now(), "sources": {}}
        else:
            candidate = json.loads(canonical(prior))
        counts = {"added": 0, "changed": 0, "unchanged": 0}
        for source_id in sorted(incoming):
            record = incoming[source_id]
            new_revision = _revision_for(record)
            if source_id not in candidate["sources"]:
                counts["added"] += 1
                candidate["sources"][source_id] = {
                    "source_id": source_id, "tweet_id": record["tweet_id"], "current_revision": new_revision["revision_hash"], "revisions": {new_revision["revision_hash"]: new_revision},
                    "context_status": "context_pending", "integration_status": "captured", "wiki_pages": [], "removal_eligible": False,
                }
                continue
            source = candidate["sources"][source_id]
            if new_revision["revision_hash"] not in source["revisions"]:
                source["revisions"][new_revision["revision_hash"]] = new_revision
            if source["current_revision"] != new_revision["revision_hash"]:
                counts["changed"] += 1
                source["current_revision"] = new_revision["revision_hash"]
                source["context_status"] = "context_pending"
                source["integration_status"] = "provisional"
                source["removal_eligible"] = False
            else:
                counts["unchanged"] += 1
        retained = 0
        if prior is not None:
            retained = sum(1 for source_id in prior["sources"] if source_id not in incoming)
        counts["unchanged"] = len(incoming) - counts["added"] - counts["changed"]
        source_changed = prior is None or canonical(candidate["sources"]) != canonical(prior["sources"])
        if source_changed:
            candidate["updated_at"] = _utc_now()
        candidate_bytes = (canonical(candidate) + "\n").encode("utf-8")
        manifest_changed = prior_bytes != candidate_bytes
        need_index = manifest_changed or not _index_is_fresh(vault, candidate_bytes, candidate)
        temp_index = None
        if need_index:
            temp_index = _build_index(vault, candidate_bytes, candidate)
        if fault_at == "after_staging":
            if temp_index and temp_index.exists():
                temp_index.unlink()
            raise SimulatedFault("after staging")
        if manifest_changed:
            manifest_path = vault / "manifest.json"
            _check_no_symlinks(manifest_path, vault)
            fd, temp_name = tempfile.mkstemp(prefix=".manifest.tmp-", dir=vault)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(candidate_bytes)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp_name, manifest_path)
                dir_fd = os.open(vault, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        if fault_at == "after_manifest_replace":
            if temp_index and temp_index.exists():
                temp_index.unlink()
            raise SimulatedFault("after manifest replacement")
        if temp_index:
            index_path = vault / "derived" / "search.sqlite3"
            _check_no_symlinks(index_path, vault)
            os.replace(temp_index, index_path)
            dir_fd = os.open(vault / "derived", os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        if fault_at == "after_index_replace":
            raise SimulatedFault("after index replacement")
        total_revisions = sum(len(source["revisions"]) for source in candidate["sources"].values())
        return {
            "total_sources": len(candidate["sources"]), "added": counts["added"], "changed": counts["changed"], "unchanged": counts["unchanged"],
            "retained": retained, "revision_count": total_revisions, "index_rebuilt": bool(temp_index), "model_calls": 0, "network_calls": 0,
            "manifest_sha256": sha256_bytes(candidate_bytes),
        }


def load_manifest_for_reader(vault: Path | str):
    vault = Path(vault).absolute()
    _check_no_symlinks(vault, Path(vault.anchor))
    manifest_path = vault / "manifest.json"
    index_path = vault / "derived" / "search.sqlite3"
    _check_no_symlinks(manifest_path, vault)
    _check_no_symlinks(index_path, vault)
    if not manifest_path.exists() and not index_path.exists():
        raise NotInitialized("not initialized")
    if not manifest_path.exists() and index_path.exists():
        raise MissingManifest("manifest missing")
    _check_no_symlinks(manifest_path, vault)
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorruptManifest("invalid manifest") from exc
    try:
        _validate_manifest(vault, manifest)
    except CorruptManifest:
        raise
    if not _index_is_fresh(vault, manifest_bytes, manifest):
        raise StaleIndex("index stale")
    return vault, manifest, manifest_bytes
