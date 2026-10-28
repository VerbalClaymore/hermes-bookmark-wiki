"""Fail-closed local retrieval over the published SQLite generation."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .archive import (
    BookmarkWikiError,
    _validate_index_connection,
    load_manifest_for_reader,
)


class InvalidArgument(BookmarkWikiError):
    code = "INVALID_ARGUMENT"


_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _open_reader(vault: Path, manifest: dict, manifest_bytes: bytes):
    index_path = vault / "derived" / "search.sqlite3"
    connection = None
    try:
        connection = sqlite3.connect(index_path.as_uri() + "?mode=ro", uri=True)
        _validate_index_connection(connection, manifest, manifest_bytes)
        return connection
    except (sqlite3.Error, OSError, ValueError) as exc:
        try:
            connection.close()
        except Exception:
            pass
        from .archive import StaleIndex
        raise StaleIndex("index stale") from exc


def status(vault):
    vault, manifest, manifest_bytes = load_manifest_for_reader(vault)
    connection = _open_reader(vault, manifest, manifest_bytes)
    try:
        sources = list(manifest["sources"].values())
        return {
            "total_sources": len(sources),
            "revision_count": sum(len(source["revisions"]) for source in sources),
            "context_pending": sum(source["context_status"] == "context_pending" for source in sources),
            "captured": sum(source["integration_status"] == "captured" for source in sources),
            "provisional": sum(source["integration_status"] == "provisional" for source in sources),
            "index_fresh": True,
            "manifest_sha256": __import__("hashlib").sha256(manifest_bytes).hexdigest(),
        }
    finally:
        connection.close()


def _validate_search_args(query, limit, excerpt_chars):
    if not isinstance(query, str) or len(query) > 2000:
        raise InvalidArgument("invalid query")
    if not isinstance(limit, int) or not 1 <= limit <= 20:
        raise InvalidArgument("invalid limit")
    if not isinstance(excerpt_chars, int) or not 100 <= excerpt_chars <= 1200:
        raise InvalidArgument("invalid excerpt")
    tokens = []
    seen = set()
    for token in _TOKEN_RE.findall(query):
        lowered = token.lower()
        if lowered not in seen:
            seen.add(lowered)
            tokens.append(lowered)
            if len(tokens) == 32:
                break
    if not tokens:
        raise InvalidArgument("blank query")
    return tokens


def _match_sql(tokens, operator):
    quoted = ['"' + token.replace('"', '""') + '"' for token in tokens]
    return (" {} ".format(operator)).join(quoted)


def _doc_result(row, *, identity=False, query_tokens=()):
    source_id, tweet_id, author_handle, tweet_created_at, source_url, expanded_json, content_json, metadata_json, evidence_path, raw_path, context_status, integration_status, wiki_pages_json, removal, warnings_json, _ = row
    content = json.loads(content_json)
    metadata = json.loads(metadata_json)
    expanded_urls = json.loads(expanded_json)
    matched_fields = []
    fields = {
        "text": content["text"],
        "author": content["author_handle"] + " " + content["author_name"],
        "urls": source_url + " " + " ".join(expanded_urls),
        "categories": " ".join(item["name"] + " " + item["slug"] for item in metadata["categories"]),
        "semantic_hints": " ".join(metadata["semantic_hints"]),
    }
    for field in ("text", "author", "urls", "categories", "semantic_hints"):
        value = fields[field].casefold()
        if any(token.casefold() in value for token in query_tokens):
            matched_fields.append(field)
    if identity:
        matched_fields.append("identity")
    matched_fields = [field for field in ("text", "author", "urls", "categories", "semantic_hints", "identity") if field in matched_fields]
    excerpt = _excerpt(content["text"], query_tokens, 600)
    warnings = json.loads(warnings_json)
    if "semantic_hints" in matched_fields and "generated_search_hints" not in warnings:
        warnings.append("generated_search_hints")
    warnings.sort()
    return {
        "source_id": source_id,
        "tweet_id": tweet_id,
        "author_handle": author_handle,
        "tweet_created_at": tweet_created_at,
        "source_url": source_url,
        "expanded_urls": expanded_urls,
        "evidence_path": evidence_path,
        "raw_path": raw_path,
        "excerpt": excerpt,
        "matched_fields": matched_fields,
        "context_status": context_status,
        "integration_status": integration_status,
        "wiki_pages": json.loads(wiki_pages_json),
        "removal_eligible": bool(removal),
        "warnings": warnings,
    }


def _excerpt(text, tokens, limit):
    if len(text) <= limit:
        return text
    position = None
    folded = text.casefold()
    for token in tokens:
        found = folded.find(token.casefold())
        if found >= 0 and (position is None or found < position):
            position = found
    if position is None:
        return text[:limit]
    start = max(0, position - limit // 2)
    if start + limit > len(text):
        start = max(0, len(text) - limit)
    return text[start:start + limit]


def search(vault, query, limit=8, excerpt_chars=600):
    tokens = _validate_search_args(query, limit, excerpt_chars)
    vault, manifest, manifest_bytes = load_manifest_for_reader(vault)
    connection = _open_reader(vault, manifest, manifest_bytes)
    try:
        stripped = query.strip()
        exact_ids = set()
        for source_id, source in manifest["sources"].items():
            revision = source["revisions"][source["current_revision"]]
            content = revision["content"]
            if stripped in {source_id, source["tweet_id"], content["source_url"]} or stripped in content["expanded_urls"]:
                exact_ids.add(source_id)
        exact_rows = []
        if exact_ids:
            placeholders = ",".join("?" for _ in exact_ids)
            exact_rows = connection.execute(
                "SELECT source_id,tweet_id,author_handle,tweet_created_at,source_url,expanded_urls_json,content_json,metadata_json,evidence_path,raw_path,context_status,integration_status,wiki_pages_json,removal_eligible,warnings_json,current_revision FROM documents WHERE source_id IN (" + placeholders + ") ORDER BY source_id",
                tuple(sorted(exact_ids)),
            ).fetchall()
        remaining = limit - len(exact_rows)
        normal_rows = []
        all_rows = []
        or_added = False
        if remaining > 0:
            all_query = _match_sql(tokens, "AND")
            all_candidates = connection.execute(
                "SELECT d.source_id,d.tweet_id,d.author_handle,d.tweet_created_at,d.source_url,d.expanded_urls_json,d.content_json,d.metadata_json,d.evidence_path,d.raw_path,d.context_status,d.integration_status,d.wiki_pages_json,d.removal_eligible,d.warnings_json,d.current_revision FROM search_fts f JOIN documents d ON d.source_id=f.source_id WHERE search_fts MATCH ? ORDER BY bm25(search_fts,0,0,10,4,3,2,0.5), d.source_id",
                (all_query,),
            ).fetchall()
            all_rows = [row for row in all_candidates if row[0] not in exact_ids][:remaining]
            normal_rows.extend(all_rows)
            if len(normal_rows) < remaining:
                or_query = _match_sql(tokens, "OR")
                excluded = {row[0] for row in exact_rows + normal_rows}
                extra = connection.execute(
                    "SELECT d.source_id,d.tweet_id,d.author_handle,d.tweet_created_at,d.source_url,d.expanded_urls_json,d.content_json,d.metadata_json,d.evidence_path,d.raw_path,d.context_status,d.integration_status,d.wiki_pages_json,d.removal_eligible,d.warnings_json,d.current_revision FROM search_fts f JOIN documents d ON d.source_id=f.source_id WHERE search_fts MATCH ? ORDER BY bm25(search_fts,0,0,10,4,3,2,0.5), d.source_id",
                    (or_query,),
                ).fetchall()
                additions = [row for row in extra if row[0] not in excluded]
                or_added = bool(additions)
                normal_rows.extend(additions)
                normal_rows = normal_rows[:remaining]
        rows = exact_rows + normal_rows
        if exact_rows:
            mode = "exact"
        elif len(all_rows) >= remaining and remaining > 0:
            mode = "all"
        elif or_added:
            mode = "any"
        elif all_rows:
            mode = "all"
        else:
            mode = "none"
        results = []
        for row in rows[:limit]:
            item = _doc_result(row, identity=row[0] in exact_ids, query_tokens=tokens)
            item["excerpt"] = _excerpt(json.loads(row[6])["text"], tokens, excerpt_chars)
            results.append(item)
        return {"query": query, "limit": limit, "excerpt_chars": excerpt_chars, "match_mode": mode, "results": results}
    finally:
        connection.close()


def show(vault, source_id):
    if not isinstance(source_id, str):
        raise InvalidArgument("invalid id")
    if source_id.isascii() and source_id.isdigit():
        source_id = "x:" + source_id
    elif not (source_id.startswith("x:") and source_id[2:].isascii() and source_id[2:].isdigit()):
        raise InvalidArgument("invalid id")
    vault, manifest, manifest_bytes = load_manifest_for_reader(vault)
    source = manifest["sources"].get(source_id)
    if source is None:
        from .archive import NotFound
        raise NotFound("source not found")
    revision = source["revisions"][source["current_revision"]]
    return {
        "source_id": source_id,
        "tweet_id": source["tweet_id"],
        "current_revision": source["current_revision"],
        "revision_count": len(source["revisions"]),
        "content": revision["content"],
        "metadata": revision["metadata"],
        "evidence_path": revision["evidence_path"],
        "raw_path": revision["raw_path"],
        "context_status": source["context_status"],
        "integration_status": source["integration_status"],
        "wiki_pages": source["wiki_pages"],
        "removal_eligible": source["removal_eligible"],
        "warnings": _warnings_for_show(source, revision["metadata"]),
    }


def _warnings_for_show(source, metadata):
    values = set(metadata.get("warnings", []))
    values.update({"unverified_source_claims", "bookmarks_not_preferences"})
    if source.get("context_status") == "context_pending":
        values.add("context_pending")
    return sorted(values)
