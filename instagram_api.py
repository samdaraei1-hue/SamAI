from __future__ import annotations

import time

import requests

from config import config
from database import normalize_timestamp


class InstagramError(RuntimeError):
    def __init__(self, message, status=None, transient=False):
        super().__init__(message)
        self.status, self.transient = status, transient


class InstagramAPI:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.base = config.graph_base

    def _request(self, method, path, *, params=None, data=None, retry=True):
        if not config.instagram_token:
            raise InstagramError("Instagram access token is not configured")
        url = path if path.startswith("https://") else f"{self.base}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {config.instagram_token}"}
        tries = config.max_retries if retry else 0
        for attempt in range(tries + 1):
            try:
                response = self.session.request(method, url, params=params, data=data, headers=headers, timeout=30)
                if response.ok:
                    return response.json()
                try:
                    body = response.json().get("error", {})
                except ValueError:
                    body = {}
                code = response.status_code
                transient = code == 429 or code >= 500 or body.get("is_transient") is True
                # Avoid storing Graph error messages, which can contain request details.
                if transient and attempt < tries:
                    time.sleep(min(2 ** attempt, 12))
                    continue
                raise InstagramError(f"Instagram API request failed (HTTP {code}, code {body.get('code', 'unknown')})", code, transient)
            except requests.RequestException as exc:
                if attempt < tries:
                    time.sleep(min(2 ** attempt, 12)); continue
                raise InstagramError(f"Instagram network error: {type(exc).__name__}", transient=True) from None
        raise InstagramError("Instagram request retries exhausted", transient=True)

    def pages(self, path, params=None):
        params = dict(params or {})
        for _ in range(config.max_pages):
            payload = self._request("GET", path, params=params)
            yield from payload.get("data", [])
            next_url = payload.get("paging", {}).get("next")
            if not next_url:
                break
            path, params = next_url, {}

    def media(self):
        fields = "id,caption,media_type,timestamp,comments_count"
        for i, item in enumerate(self.pages(f"{config.instagram_user_id}/media", {"fields": fields, "limit": 50})):
            if i >= config.max_media:
                break
            yield item

    def comments(self, media_id):
        fields = "id,text,timestamp,from{id,username},username,parent_id,hidden,replies{id,text,timestamp,from{id,username},username,parent_id,hidden}"
        # Instagram returns comments newest-first by default. Since its comments
        # edge has no timestamp filter, stop paging once we cross the configured
        # start date instead of fetching the post's full history.
        comments_seen = 0
        old_comment_boundary = None
        for comment in self.pages(f"{media_id}/comments", {"fields": fields, "limit": 50}):
            comments_seen += 1
            timestamp = normalize_timestamp(comment.get("timestamp"))
            is_recent = bool(timestamp and timestamp >= config.comment_cutoff_utc)
            if is_recent:
                yield comment
            elif old_comment_boundary is None:
                # Check a bounded page of older parent threads too: someone may
                # have replied to an old comment today.
                old_comment_boundary = comments_seen
            # The media comments edge returns top-level comments. Their replies
            # are expanded separately and become individual work items.
            parent_id = str(comment["id"])
            parent_text = (comment.get("text") or "").strip()
            expanded_replies = comment.get("replies")
            replies = self.replies(parent_id, existing=expanded_replies) if isinstance(expanded_replies, dict) else []
            chronological = sorted(
                replies,
                key=lambda item: normalize_timestamp(item.get("timestamp")) or "",
            )
            for reply in chronological:
                reply_timestamp = normalize_timestamp(reply.get("timestamp"))
                if not reply_timestamp or reply_timestamp < config.comment_cutoff_utc:
                    continue
                if str(reply.get("id", "")) == parent_id:
                    continue
                reply["parent_id"] = parent_id
                history = [
                    item for item in chronological
                    if str(item.get("id", "")) != str(reply.get("id", ""))
                    and normalize_timestamp(item.get("timestamp"))
                    and normalize_timestamp(item.get("timestamp")) <= reply_timestamp
                ]
                history = history[-6:]
                thread_parts = [f"کامنت اصلی: {parent_text}"]
                if history:
                    thread_parts.append("پاسخ‌های قبلی در همین گفتگو:\n" + "\n".join(
                        f"{'سام' if self.is_own(item) else 'مخاطب'}: {(item.get('text') or '').strip()}"
                        for item in history
                    ))
                reply["thread_context"] = "\n".join(thread_parts)[:4000]
                yield reply
            if old_comment_boundary is not None and comments_seen >= old_comment_boundary + 49:
                break

    def replies(self, comment_id, existing=None):
        if isinstance(existing, dict) and isinstance(existing.get("data"), list):
            found = list(existing["data"])
            next_url = existing.get("paging", {}).get("next")
            if next_url:
                found.extend(self.pages(next_url))
            return found
        return list(self.pages(f"{comment_id}/replies", {"fields": "id,text,timestamp,from{id,username},username,parent_id,hidden", "limit": 50}))

    @staticmethod
    def is_own(item):
        author = item.get("from") or {}
        return (str(author.get("id", "")) == config.instagram_user_id and bool(config.instagram_user_id)) or (item.get("username") or author.get("username", "")).casefold() == "samienergy"

    def creator_replies(self, comment_id, existing=None):
        if isinstance(existing, dict) and isinstance(existing.get("data"), list):
            found = list(existing["data"])
            next_url = existing.get("paging", {}).get("next")
            if next_url:
                found.extend(self.pages(next_url))
        else:
            found = self.replies(comment_id)
        result = []
        for item in found:
            author = item.get("from") or {}
            own = (str(author.get("id", "")) == config.instagram_user_id and bool(config.instagram_user_id)) or (item.get("username") or author.get("username", "")).casefold() == "samienergy"
            if own: result.append(item)
        return result

    def already_replied(self, comment_id, reply_text=None, existing=None):
        replies = self.creator_replies(comment_id, existing)
        return any(not reply_text or (item.get("text") or "").strip() == reply_text.strip() for item in replies)

    def publish_reply(self, comment_id, message):
        return self._request("POST", f"{comment_id}/replies", data={"message": message}, retry=False)
