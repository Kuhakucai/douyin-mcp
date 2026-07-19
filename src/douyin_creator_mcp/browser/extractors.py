"""Conservative page extractors for the browser-login channel."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit
from zoneinfo import ZoneInfo


LOGIN_REQUIRED = "login_required"
VERIFICATION_REQUIRED = "verification_required"
LOGGED_IN = "logged_in"
UNKNOWN = "unknown"

_LOGIN_KEYWORDS = (
    "扫码登录",
    "登录抖音",
    "手机登录",
    "验证码登录",
    "密码登录",
)
_VERIFICATION_KEYWORDS = (
    "安全验证",
    "身份验证",
    "请完成验证",
    "滑块",
    "风险",
)
_CREATOR_KEYWORDS = (
    "创作者中心",
    "内容管理",
    "作品管理",
    "视频管理",
    "数据中心",
    "发布作品",
)
_VIDEO_HINT_KEYWORDS = (
    "播放",
    "点赞",
    "评论",
    "分享",
    "收藏",
    "完播",
    "公开视频",
    "私密",
    "审核",
    "发布时间",
)
_NOISE_LINE_KEYWORDS = (
    "首页",
    "消息",
    "设置",
    "帮助",
    "退出登录",
    "隐私政策",
)

_PAGE_STATE_SCRIPT = r"""
() => {
  const dateRe = /20\d{2}.*\d{2}:\d{2}/;
  const all = [...document.querySelectorAll('body *')];
  const dateLeaves = all.filter(el => {
    const text = (el.innerText || '').trim();
    return dateRe.test(text) && ![...el.children].some(child =>
      dateRe.test((child.innerText || '').trim())
    );
  });
  const totalMatch = (document.body.innerText || '').match(/共\s*(\d+)\s*个作品/);
  return {
    card_count: dateLeaves.length,
    total_count: totalMatch ? Number(totalMatch[1]) : null,
    scroll_height: document.documentElement.scrollHeight
  };
}
"""

_EXTRACT_VIDEO_CARDS_SCRIPT = r"""
() => {
  const dateRe = /20\d{2}.*\d{2}:\d{2}/;
  const durationRe = /^\d{2}:\d{2}(?::\d{2})?$/;
  const all = [...document.querySelectorAll('body *')];
  const dateLeaves = all.filter(el => {
    const text = (el.innerText || '').trim();
    return dateRe.test(text) && ![...el.children].some(child =>
      dateRe.test((child.innerText || '').trim())
    );
  });

  const findCard = dateNode => {
    const namedCard = dateNode.closest('[class*="video-card-content-"]');
    if (namedCard) return namedCard;
    let current = dateNode.parentElement;
    for (let depth = 0; current && depth < 6; depth++, current = current.parentElement) {
      const labels = current.querySelectorAll('[class*="metric-label-"]');
      const title = current.querySelector('[class*="info-title-text-"]');
      if (title && labels.length >= 2) return current;
    }
    return null;
  };

  const cards = [];
  const seen = new Set();
  for (const dateNode of dateLeaves) {
    const card = findCard(dateNode);
    if (!card || seen.has(card)) continue;
    seen.add(card);

    const titleNode = card.querySelector('[class*="info-title-text-"]');
    const statusNode = card.querySelector('[class*="info-status-"]');
    const anchors = [...card.querySelectorAll('a[href]')];
    const detailAnchor = anchors.find(anchor =>
      /数据|详情|分析/.test((anchor.innerText || '').trim())
    ) || anchors.find(anchor =>
      /data|detail|analysis/.test(anchor.getAttribute('href') || '')
    ) || anchors[0] || null;
    const leafNodes = [...card.querySelectorAll('*')].filter(el => el.children.length === 0);
    const durationNode = leafNodes.find(el => durationRe.test((el.innerText || '').trim()));
    const metrics = {};
    for (const labelNode of card.querySelectorAll('[class*="metric-label-"]')) {
      const item = labelNode.parentElement;
      const valueNode = item && item.querySelector('[class*="metric-value-"]');
      const label = (labelNode.innerText || '').trim();
      if (label && valueNode) metrics[label] = (valueNode.innerText || '').trim();
    }

    let coverUrl = null;
    const image = card.querySelector('img[src]');
    if (image) coverUrl = image.src;
    if (!coverUrl) {
      const backgroundNode = [...card.querySelectorAll('*')].find(el =>
        (el.style && el.style.backgroundImage || '').includes('url(')
      );
      if (backgroundNode) {
        const match = backgroundNode.style.backgroundImage.match(/url\(["']?(.*?)["']?\)/);
        coverUrl = match ? match[1] : null;
      }
    }

    cards.push({
      title: titleNode ? (titleNode.innerText || '').trim() : '',
      publish_time: (dateNode.innerText || '').trim(),
      duration: durationNode ? (durationNode.innerText || '').trim() : null,
      status: statusNode ? (statusNode.innerText || '').trim() : null,
      cover_url: coverUrl,
      detail_url: detailAnchor ? detailAnchor.href : null,
      platform_item_id: detailAnchor ? (
        new URL(detailAnchor.href, window.location.href).searchParams.get('item_id') ||
        new URL(detailAnchor.href, window.location.href).searchParams.get('video_id') ||
        ((detailAnchor.href || '').match(/(?:video|item)[\/-](\d{6,})/) || [])[1] ||
        null
      ) : null,
      metrics
    });
  }
  return cards;
}
"""

_EXTRACT_DETAIL_METRICS_SCRIPT = r"""
() => {
  const detailMetrics = {};
  const aliases = [
    '曝光量', '曝光次数', '播放量', '播放次数', '5秒完播率', '5 秒完播率',
    '5s完播率', '5S完播率',
    '整体完播率', '完播率', '平均播放时长', '平均观看时长', '点赞量', '点赞数',
    '收藏量', '收藏数', '评论量', '评论数', '分享量', '分享数', '涨粉量', '新增粉丝'
  ];
  const valueRe = /^[-+]?\d[\d,.]*(?:\.\d+)?\s*(?:%|万|亿|秒)?$/;
  const timeRe = /^\d{1,2}:\d{2}(?::\d{2})?$/;
  const units = new Set(['%', '万', '亿', '秒']);
  const leaves = [...document.querySelectorAll('body *')].filter(el => el.children.length === 0);
  for (const node of leaves) {
    const label = (node.innerText || '').trim();
    if (!aliases.includes(label)) continue;
    let container = node.parentElement;
    let value = null;
    for (let depth = 0; container && depth < 4 && !value; depth++, container = container.parentElement) {
      const candidates = [...container.querySelectorAll('*')]
        .filter(el => el.children.length === 0)
        .map(el => (el.innerText || '').trim())
        .filter(text => text && text !== label);
      for (let index = 0; index < candidates.length; index++) {
        const text = candidates[index];
        if (!valueRe.test(text) && !timeRe.test(text)) continue;
        const next = candidates[index + 1];
        value = units.has(next) && !text.endsWith(next) ? `${text}${next}` : text;
        break;
      }
    }
    if (value) detailMetrics[label] = value;
  }
  return detailMetrics;
}
"""

_OPEN_TRAFFIC_TAB_SCRIPT = r"""
() => {
  const exactText = element => (element.innerText || '').trim() === '流量分析';
  const roleTab = [...document.querySelectorAll('[role="tab"]')].find(exactText);
  const leaf = [...document.querySelectorAll('body *')]
    .filter(element => element.children.length === 0)
    .find(exactText);
  const target = roleTab || (leaf && (leaf.closest('[role="tab"]') || leaf));
  if (!target) return false;
  target.click();
  return true;
}
"""

DETAIL_METRIC_FIELDS = (
    "exposure_count",
    "play_count",
    "five_second_completion_rate",
    "completion_rate",
    "average_watch_duration_seconds",
    "like_count",
    "collect_count",
    "comment_count",
    "share_count",
    "follower_gain",
)

_DETAIL_ALIASES = {
    "曝光量": "exposure_count",
    "曝光次数": "exposure_count",
    "播放量": "play_count",
    "播放次数": "play_count",
    "5秒完播率": "five_second_completion_rate",
    "5 秒完播率": "five_second_completion_rate",
    "5s完播率": "five_second_completion_rate",
    "5S完播率": "five_second_completion_rate",
    "整体完播率": "completion_rate",
    "完播率": "completion_rate",
    "平均播放时长": "average_watch_duration_seconds",
    "平均观看时长": "average_watch_duration_seconds",
    "点赞量": "like_count",
    "点赞数": "like_count",
    "收藏量": "collect_count",
    "收藏数": "collect_count",
    "评论量": "comment_count",
    "评论数": "comment_count",
    "分享量": "share_count",
    "分享数": "share_count",
    "涨粉量": "follower_gain",
    "新增粉丝": "follower_gain",
}


def detect_login_status(text: str, url: str = "", title: str = "") -> str:
    haystack = f"{title}\n{url}\n{text}"
    if any(keyword in haystack for keyword in _VERIFICATION_KEYWORDS):
        return VERIFICATION_REQUIRED
    if any(keyword in haystack for keyword in _LOGIN_KEYWORDS):
        return LOGIN_REQUIRED
    if "creator.douyin.com" in url and any(
        keyword in haystack for keyword in _CREATOR_KEYWORDS
    ):
        return LOGGED_IN
    return UNKNOWN


def extract_text_lines(text: str, limit: int = 80) -> list[str]:
    normalized = re.sub(r"[ \t\r\f\v]+", " ", text)
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def extract_video_candidates(lines: list[str], limit: int = 30) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for line in lines:
        if len(line) < 4 or len(line) > 180:
            continue
        if line in _NOISE_LINE_KEYWORDS:
            continue
        if any(keyword in line for keyword in _VIDEO_HINT_KEYWORDS) or re.search(
            r"\d+\s*(播放|点赞|评论|分享|收藏)",
            line,
        ):
            candidates.append({"text": line})
        if len(candidates) >= limit:
            break
    return candidates


def parse_metric_count(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([万亿]?)", text)
    if not match:
        return None
    multiplier = {"": 1, "万": 10_000, "亿": 100_000_000}[match.group(2)]
    try:
        return int(Decimal(match.group(1)) * multiplier)
    except (InvalidOperation, ValueError):
        return None


def parse_duration_seconds(value: Any) -> int | None:
    if value is None:
        return None
    parts = str(value).strip().split(":")
    if len(parts) not in {2, 3} or not all(part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if numbers[-1] >= 60 or (len(numbers) == 3 and numbers[-2] >= 60):
        return None
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]


def parse_metric_rate(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    is_percent = text.endswith("%")
    if is_percent:
        text = text[:-1].strip()
    try:
        number = float(Decimal(text))
    except (InvalidOperation, ValueError):
        return None
    result = number / 100 if is_percent else number
    return result if 0 <= result <= 1 else None


def parse_watch_duration_seconds(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    parsed = parse_duration_seconds(text)
    if parsed is not None:
        return float(parsed)
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*秒", text)
    return float(match.group(1)) if match else None


def parse_publish_time(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = datetime.strptime(str(value).strip(), "%Y年%m月%d日 %H:%M")
    except ValueError:
        return None
    return int(parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


def sanitize_public_url(value: Any) -> str | None:
    if not value:
        return None
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _url_video_ids(value: Any) -> set[str]:
    if not value:
        return set()
    parsed = urlsplit(str(value).strip())
    query = parse_qs(parsed.query)
    candidates = {
        item
        for key in ("item_id", "video_id", "itemId", "videoId")
        for item in query.get(key, [])
        if item
    }
    candidates.update(
        re.findall(r"(?:video|item|work-detail)[/-](\d{6,})", parsed.path)
    )
    return candidates


def detail_video_id_from_url(value: Any) -> str | None:
    """Return one stable platform id only when the detail URL is unambiguous."""
    candidates = _url_video_ids(value)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _detail_url_matches(expected: Any, current: Any) -> bool:
    if not expected or not current:
        return False
    expected_url = urlsplit(str(expected).strip())
    current_url = urlsplit(str(current).strip())
    if (
        expected_url.scheme,
        expected_url.netloc,
        expected_url.path.rstrip("/"),
    ) != (
        current_url.scheme,
        current_url.netloc,
        current_url.path.rstrip("/"),
    ):
        return False
    expected_ids = _url_video_ids(expected)
    current_ids = _url_video_ids(current)
    if expected_ids or current_ids:
        return bool(expected_ids & current_ids)
    expected_query = parse_qs(expected_url.query)
    current_query = parse_qs(current_url.query)
    return expected_query == current_query


def normalize_video_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    title = str(raw.get("title") or "").strip()
    publish_time = parse_publish_time(raw.get("publish_time"))
    if not title or publish_time is None:
        return None
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    video_url = sanitize_public_url(raw.get("detail_url") or raw.get("video_url"))
    platform_item_id = str(raw.get("platform_item_id") or "").strip() or None
    fingerprint_source = "|".join(
        [
            platform_item_id or "",
            str(publish_time),
            title,
            str(parse_duration_seconds(raw.get("duration")) or ""),
        ]
    )
    status = str(raw.get("status") or "").strip() or None
    raw_kind = str(raw.get("content_kind") or raw.get("type") or "").strip().lower()
    if status and "私密" in status:
        visibility = "private"
    elif status and any(marker in status for marker in ("公开", "已发布", "发布成功")):
        visibility = "public"
    else:
        visibility = "unknown"
    if raw_kind in {"video", "视频"} or (status and "视频" in status):
        content_kind = "video"
    elif raw_kind in {"image", "images", "图文", "图片"} or (
        status and any(marker in status for marker in ("图文", "图片"))
    ):
        content_kind = "image"
    else:
        # The creator video-management extractor only emits playable cards. Keep
        # unknown when the page exposes an explicit but unfamiliar kind.
        content_kind = "video" if not raw_kind else "unknown"
    return {
        "title": title,
        "publish_time": publish_time,
        "duration": parse_duration_seconds(raw.get("duration")),
        "status": status,
        "visibility": visibility,
        "content_kind": content_kind,
        "classification_source": "creator_card_status_v1",
        "cover_url": sanitize_public_url(raw.get("cover_url")),
        "video_url": video_url,
        "platform_item_id": platform_item_id,
        "source_fingerprint": hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest(),
        "play_count": parse_metric_count(metrics.get("播放")),
        "like_count": parse_metric_count(metrics.get("点赞")),
        "comment_count": parse_metric_count(metrics.get("评论")),
        "share_count": parse_metric_count(metrics.get("分享")),
        "collect_count": parse_metric_count(metrics.get("收藏")),
    }


def load_all_video_cards(
    page: Any,
    max_scrolls: int = 30,
    stable_rounds: int = 3,
    wait_ms: int = 1000,
) -> dict[str, Any]:
    state = page.evaluate(_PAGE_STATE_SCRIPT)
    initial_count = int(state.get("card_count") or 0)
    current_count = initial_count
    total_count = state.get("total_count")
    stable_count = 0
    scroll_rounds = 0
    stop_reason = "max_scrolls"

    if total_count is not None and current_count >= int(total_count):
        stop_reason = "total_reached"
    else:
        for scroll_rounds in range(1, max_scrolls + 1):
            page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(wait_ms)
            next_state = page.evaluate(_PAGE_STATE_SCRIPT)
            next_count = int(next_state.get("card_count") or 0)
            if next_state.get("total_count") is not None:
                total_count = int(next_state["total_count"])
            stable_count = stable_count + 1 if next_count <= current_count else 0
            current_count = max(current_count, next_count)
            if total_count is not None and current_count >= total_count:
                stop_reason = "total_reached"
                break
            if stable_count >= stable_rounds:
                stop_reason = "stable"
                break

    return {
        "initial_card_count": initial_count,
        "loaded_card_count": current_count,
        "page_total_video_count": total_count,
        "scroll_rounds": scroll_rounds,
        "stop_reason": stop_reason,
    }


def extract_structured_videos(page: Any) -> list[dict[str, Any]]:
    raw_records = page.evaluate(_EXTRACT_VIDEO_CARDS_SCRIPT)
    records: list[dict[str, Any]] = []
    for raw in raw_records if isinstance(raw_records, list) else []:
        if not isinstance(raw, dict):
            continue
        normalized = normalize_video_record(raw)
        if normalized is not None:
            records.append(normalized)
    return records


def collect_all_video_cards(
    page: Any,
    max_scrolls: int = 30,
    stable_rounds: int = 3,
    wait_ms: int = 1000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect unique cards during scrolling so virtualized lists cannot discard history."""
    state = page.evaluate(_PAGE_STATE_SCRIPT)
    total_count = state.get("total_count")
    initial_dom_count = int(state.get("card_count") or 0)
    current_dom_count = initial_dom_count
    collected: dict[str, dict[str, Any]] = {}
    stable_count = 0
    scroll_rounds = 0
    stop_reason = "max_scrolls"

    for round_index in range(max_scrolls + 1):
        before = len(collected)
        for record in extract_structured_videos(page):
            key = str(record.get("platform_item_id") or record["source_fingerprint"])
            collected[key] = record
        stable_count = stable_count + 1 if len(collected) == before else 0
        if total_count is not None and len(collected) >= int(total_count):
            stop_reason = "total_reached"
            break
        if round_index > 0 and stable_count >= stable_rounds:
            stop_reason = "stable"
            break
        if round_index >= max_scrolls:
            break
        scroll_rounds = round_index + 1
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(wait_ms)
        state = page.evaluate(_PAGE_STATE_SCRIPT)
        current_dom_count = int(state.get("card_count") or 0)
        if state.get("total_count") is not None:
            total_count = int(state["total_count"])

    records = list(collected.values())
    return records, {
        "initial_card_count": initial_dom_count,
        "current_dom_card_count": current_dom_count,
        "loaded_card_count": len(records),
        "page_total_video_count": total_count,
        "scroll_rounds": scroll_rounds,
        "stop_reason": stop_reason,
    }


def extract_detail_metrics(
    page: Any,
    expected_video: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title = _read_page_title(page)
    url = str(getattr(page, "url", "") or "")
    body_text = _read_body_text(page)
    login_status = detect_login_status(body_text, url=url, title=title or "")
    raw, collected_sections = _collect_detail_sections(page, login_status)

    metrics: dict[str, Any] = {field: None for field in DETAIL_METRIC_FIELDS}
    raw_by_field: dict[str, str] = {}
    for label, raw_value in raw.items():
        field = _DETAIL_ALIASES.get(str(label).strip())
        if not field:
            continue
        raw_by_field[field] = str(raw_value).strip()
        if field in {"five_second_completion_rate", "completion_rate"}:
            metrics[field] = parse_metric_rate(raw_value)
        elif field == "average_watch_duration_seconds":
            metrics[field] = parse_watch_duration_seconds(raw_value)
        else:
            metrics[field] = parse_metric_count(raw_value)

    identity_confirmed = expected_video is None
    if expected_video is not None:
        expected_title = str(expected_video.get("title") or "").strip()
        expected_id = str(
            expected_video.get("platform_item_id")
            or expected_video.get("item_id")
            or expected_video.get("video_id")
            or ""
        ).strip()
        current_ids = _url_video_ids(url)
        if expected_id and current_ids:
            identity_confirmed = expected_id in current_ids
        else:
            identity_confirmed = bool(
                _detail_url_matches(expected_video.get("video_url"), url)
                or (expected_id and expected_id in url)
                or (expected_title and expected_title in body_text)
            )

    valid_count = sum(value is not None for value in metrics.values())
    missing_reason = "not_displayed" if raw else "parser_not_matched"
    missing = {field: missing_reason for field, value in metrics.items() if value is None}
    visible_field_count = len(raw_by_field)
    if valid_count == 0:
        quality = "parser_degraded"
    elif valid_count == visible_field_count:
        quality = "complete"
    else:
        quality = "partial"
    return {
        "title": title,
        "source_url": url,
        "login_status": login_status,
        "identity_confirmed": identity_confirmed,
        "raw_metrics": raw_by_field,
        "metrics": metrics,
        "missing_reasons": missing,
        "quality": quality,
        "valid_metric_count": valid_count,
        "visible_metric_count": visible_field_count,
        "collected_sections": collected_sections,
    }


def _collect_detail_sections(
    page: Any,
    login_status: str,
) -> tuple[dict[str, Any], list[str]]:
    raw: dict[str, Any] = {}
    collected_sections: list[str] = []
    try:
        overview = page.evaluate(_EXTRACT_DETAIL_METRICS_SCRIPT)
    except Exception:
        overview = {}
    if isinstance(overview, dict):
        raw.update(overview)
        collected_sections.append("overview")

    if login_status != LOGGED_IN:
        return raw, collected_sections
    try:
        traffic_opened = page.evaluate(_OPEN_TRAFFIC_TAB_SCRIPT)
    except Exception:
        traffic_opened = False
    if not traffic_opened:
        return raw, collected_sections
    wait_for_timeout = getattr(page, "wait_for_timeout", None)
    if callable(wait_for_timeout):
        wait_for_timeout(800)
    try:
        traffic = page.evaluate(_EXTRACT_DETAIL_METRICS_SCRIPT)
    except Exception:
        traffic = {}
    if isinstance(traffic, dict):
        raw.update(traffic)
        collected_sections.append("traffic")
    return raw, collected_sections


def extract_page_snapshot(
    page: Any,
    structured_videos: list[dict[str, Any]] | None = None,
    load_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title = _read_page_title(page)
    url = str(getattr(page, "url", "") or "")
    body_text = _read_body_text(page)
    text_lines = extract_text_lines(body_text)
    login_status = detect_login_status(body_text, url=url, title=title)
    video_candidates = extract_video_candidates(text_lines)
    result = {
        "title": title,
        "source_url": url,
        "login_status": login_status,
        "text_lines": text_lines,
        "video_candidates": video_candidates,
        "diagnostics": {
            "text_line_count": len(text_lines),
            "video_candidate_count": len(video_candidates),
            "structured_video_count": len(structured_videos or []),
        },
    }
    if structured_videos is not None:
        result["structured_videos"] = structured_videos
    if load_stats is not None:
        result["load_stats"] = load_stats
    return result


def _read_page_title(page: Any) -> str | None:
    title_attr = getattr(page, "title", None)
    try:
        if callable(title_attr):
            return str(title_attr())
        if title_attr:
            return str(title_attr)
    except Exception:
        return None
    return None


def _read_body_text(page: Any) -> str:
    inner_text = getattr(page, "inner_text", None)
    if callable(inner_text):
        try:
            return str(inner_text("body", timeout=3000))
        except TypeError:
            return str(inner_text("body"))
        except Exception:
            return ""
    locator = getattr(page, "locator", None)
    if callable(locator):
        try:
            return str(locator("body").inner_text(timeout=3000))
        except TypeError:
            return str(locator("body").inner_text())
        except Exception:
            return ""
    return ""
