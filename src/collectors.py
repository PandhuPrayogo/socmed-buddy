from __future__ import annotations

import hashlib
import re
from typing import Any
import httpx

from src.models import CanonicalItem


class CollectorError(Exception):
    def __init__(self, error_class: str, message: str):
        super().__init__(message)
        self.error_class = error_class


def compute_content_hash(title: str | None, text: str | None) -> str:
    raw = f"{title or ''}:{text or ''}".strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def parse_iso8601_duration(duration_str: str | None) -> int | None:
    if not duration_str:
        return None
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


class YouTubeConnector:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://www.googleapis.com/youtube/v3"

    async def resolve_channel(self, client: httpx.AsyncClient, handle: str) -> tuple[str, str, str]:
        """Resolves handle to (channel_id, uploads_playlist_id, title)."""
        clean_handle = handle.lstrip("@")
        params: dict[str, Any] = {
            "key": self.api_key,
            "part": "contentDetails,snippet",
            "forHandle": clean_handle if not clean_handle.startswith("UC") else None,
            "id": clean_handle if clean_handle.startswith("UC") else None,
        }
        params = {k: v for k, v in params.items() if v is not None}

        resp = await client.get(f"{self.base_url}/channels", params=params, timeout=10.0)
        if resp.status_code == 403:
            raise CollectorError("quotaExceeded", "YouTube API quota exceeded or key invalid.")
        if resp.status_code != 200:
            raise CollectorError("transientHttp", f"YouTube API returned {resp.status_code}")

        data = resp.json()
        items = data.get("items", [])
        if not items:
            raise CollectorError("notFound", f"YouTube channel '{handle}' not found.")

        channel = items[0]
        channel_id = channel["id"]
        title = channel["snippet"]["title"]
        uploads_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        return channel_id, uploads_id, title

    async def collect(
        self,
        target_id: int,
        handle: str,
        external_id: str | None,
        cursor: str | None,
        max_items: int = 50,
    ) -> tuple[list[CanonicalItem], str | None, float]:
        """Fetches latest uploads without search.list to preserve quota. Returns (items, new_cursor, cost_units)."""
        cost_units = 0.0
        async with httpx.AsyncClient() as client:
            if not external_id:
                channel_id, uploads_id, _ = await self.resolve_channel(client, handle)
                cost_units += 1.0
            else:
                uploads_id = external_id

            # Fetch playlist items
            resp = await client.get(
                f"{self.base_url}/playlistItems",
                params={
                    "key": self.api_key,
                    "part": "snippet,contentDetails",
                    "playlistId": uploads_id,
                    "maxResults": min(max_items, 50),
                },
                timeout=15.0,
            )
            cost_units += 1.0
            if resp.status_code != 200:
                raise CollectorError("httpError", f"Failed fetching playlist: {resp.text}")

            playlist_data = resp.json()
            raw_items = playlist_data.get("items", [])
            if not raw_items:
                return [], cursor, cost_units

            video_ids = [item["contentDetails"]["videoId"] for item in raw_items]

            # Stop at cursor if incremental
            if cursor and cursor in video_ids:
                idx = video_ids.index(cursor)
                video_ids = video_ids[:idx]
                raw_items = raw_items[:idx]

            if not video_ids:
                return [], cursor, cost_units

            # Fetch video details (metrics + duration)
            v_resp = await client.get(
                f"{self.base_url}/videos",
                params={
                    "key": self.api_key,
                    "part": "statistics,contentDetails,snippet",
                    "id": ",".join(video_ids[:50]),
                },
                timeout=15.0,
            )
            cost_units += 1.0
            v_map = {v["id"]: v for v in v_resp.json().get("items", [])}

            canonical_items: list[CanonicalItem] = []
            for raw in raw_items:
                vid = raw["contentDetails"]["videoId"]
                detail = v_map.get(vid, raw)
                snippet = detail.get("snippet", raw["snippet"])
                stats = detail.get("statistics", {})

                title = snippet.get("title")
                description = snippet.get("description")
                published_at = snippet.get("publishedAt", "")
                author = snippet.get("channelTitle", handle)
                url = f"https://www.youtube.com/watch?v={vid}"

                duration_s = parse_iso8601_duration(
                    detail.get("contentDetails", {}).get("duration")
                )
                metrics = {
                    "views": int(stats.get("viewCount", 0)),
                    "likes": int(stats.get("likeCount", 0)),
                    "comments": int(stats.get("commentCount", 0)),
                }

                canonical_items.append(
                    CanonicalItem(
                        target_id=target_id,
                        platform="youtube",
                        platform_item_id=vid,
                        url=url,
                        author=author,
                        published_at=published_at,
                        title=title,
                        text=description,
                        duration_s=duration_s,
                        metrics=metrics,
                        raw_json=detail,
                        content_hash=compute_content_hash(title, description),
                    )
                )

            new_cursor = video_ids[0] if video_ids else cursor
            return canonical_items, new_cursor, cost_units


class TikTokConnector:
    def __init__(self, apify_token: str):
        self.apify_token = apify_token
        self.base_url = "https://api.apify.com/v2"

    async def validate_target(self, client: httpx.AsyncClient, handle: str) -> bool:
        clean_handle = handle.lstrip("@")
        return len(clean_handle) >= 2

    async def collect(
        self,
        target_id: int,
        handle: str,
        cursor: str | None,
        max_items: int = 20,
    ) -> tuple[list[CanonicalItem], str | None, float]:
        clean_handle = handle.lstrip("@")
        run_input = {
            "profiles": [clean_handle],
            "resultsPerPage": max_items,
            "profileSorting": "latest",
            "shouldDownloadSubtitles": True,
            "shouldDownloadVideos": False,
        }

        async with httpx.AsyncClient() as client:
            # Run actor synchronously via Apify API
            resp = await client.post(
                f"{self.base_url}/acts/clockworks~tiktok-scraper/run-sync-get-dataset-items",
                params={"token": self.apify_token},
                json=run_input,
                timeout=120.0,
            )
            if resp.status_code == 401:
                raise CollectorError("authInvalid", "Apify token invalid or expired.")
            if resp.status_code != 200 and resp.status_code != 201:
                raise CollectorError("actorError", f"TikTok actor failed: {resp.status_code}")

            results = resp.json()
            if not isinstance(results, list):
                results = []

            items: list[CanonicalItem] = []
            new_cursor = cursor
            # Rate: ~$1.70 per 1000 items
            cost_units = round(len(results) * 0.0017, 4)

            for post in results:
                post_id = str(post.get("id") or post.get("webVideoUrl", "").split("/")[-1])
                if not post_id or post_id == cursor:
                    break

                text = post.get("text") or post.get("desc") or ""
                subtitles = post.get("subtitles") or []
                if subtitles and isinstance(subtitles, list):
                    sub_text = " ".join(s.get("text", "") for s in subtitles if isinstance(s, dict))
                    if sub_text:
                        text = f"{text}\n[Subtitles]: {sub_text}"

                url = post.get("webVideoUrl") or f"https://www.tiktok.com/@{clean_handle}/video/{post_id}"
                author = post.get("authorMeta", {}).get("name") or clean_handle
                published_at = post.get("createTimeISO") or ""

                metrics = {
                    "views": int(post.get("playCount", 0)),
                    "likes": int(post.get("diggCount", 0)),
                    "comments": int(post.get("commentCount", 0)),
                }

                items.append(
                    CanonicalItem(
                        target_id=target_id,
                        platform="tiktok",
                        platform_item_id=post_id,
                        url=url,
                        author=author,
                        published_at=published_at,
                        title=None,
                        text=text,
                        duration_s=post.get("videoMeta", {}).get("duration"),
                        metrics=metrics,
                        raw_json=post,
                        content_hash=compute_content_hash(None, text),
                    )
                )

            if items:
                new_cursor = items[0].platform_item_id

            return items, new_cursor, cost_units


class InstagramConnector:
    def __init__(self, access_token: str, ig_user_id: str):
        self.access_token = access_token
        self.ig_user_id = ig_user_id
        self.base_url = "https://graph.facebook.com/v21.0"

    async def validate_target(self, client: httpx.AsyncClient, username: str) -> bool:
        clean_username = username.lstrip("@")
        fields = f"business_discovery.username({clean_username}){{followers_count}}"
        resp = await client.get(
            f"{self.base_url}/{self.ig_user_id}",
            params={"fields": fields, "access_token": self.access_token},
            timeout=10.0,
        )
        if resp.status_code == 400:
            data = resp.json()
            err_msg = data.get("error", {}).get("message", "")
            if "business" in err_msg.lower() or "not found" in err_msg.lower():
                return False
        return resp.status_code == 200

    async def collect(
        self,
        target_id: int,
        username: str,
        cursor: str | None,
        max_items: int = 25,
    ) -> tuple[list[CanonicalItem], str | None, float]:
        clean_username = username.lstrip("@")
        fields = (
            f"business_discovery.username({clean_username}){{"
            f"followers_count,media_count,media.limit({min(max_items, 25)}){{"
            f"id,caption,media_type,permalink,timestamp,like_count,comments_count"
            f"}}"
            f"}}"
        )

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/{self.ig_user_id}",
                params={"fields": fields, "access_token": self.access_token},
                timeout=15.0,
            )
            if resp.status_code == 401:
                raise CollectorError("authInvalid", "Meta access token expired or invalid.")
            if resp.status_code == 400:
                err = resp.json().get("error", {})
                raise CollectorError("targetInvalid", f"Instagram target error: {err.get('message')}")
            if resp.status_code != 200:
                raise CollectorError("httpError", f"Instagram API returned {resp.status_code}")

            data = resp.json()
            b_disc = data.get("business_discovery", {})
            media_list = b_disc.get("media", {}).get("data", [])

            items: list[CanonicalItem] = []
            for media in media_list:
                media_id = str(media["id"])
                if media_id == cursor:
                    break

                caption = media.get("caption") or ""
                permalink = media.get("permalink") or f"https://www.instagram.com/p/{media_id}"
                timestamp = media.get("timestamp") or ""

                metrics = {
                    "views": 0,
                    "likes": int(media.get("like_count", 0)),
                    "comments": int(media.get("comments_count", 0)),
                }

                items.append(
                    CanonicalItem(
                        target_id=target_id,
                        platform="instagram",
                        platform_item_id=media_id,
                        url=permalink,
                        author=clean_username,
                        published_at=timestamp,
                        title=None,
                        text=caption,
                        duration_s=None,
                        metrics=metrics,
                        raw_json=media,
                        content_hash=compute_content_hash(None, caption),
                    )
                )

            new_cursor = items[0].platform_item_id if items else cursor
            return items, new_cursor, 0.0
