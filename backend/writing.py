"""Small, deterministic helpers for Xiaohongshu-style reference selection and QA."""

import math
import re


def parse_engagement(value):
    """Parse visible card engagement such as 455, 1.2万, or 3k."""
    if value is None:
        return 0
    text = str(value).strip().lower().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([万wk]?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    multiplier = {"万": 10000, "w": 10000, "k": 1000}.get(match.group(2), 1)
    return int(number * multiplier)


def select_style_references(records, task, limit=5):
    """Pick relevant, high-visible-interaction notes for style analysis, not fact transfer."""
    anchors = [
        task.get("store", ""), task.get("city", ""), task.get("area", ""),
        *re.split(r"[\s,，/]+", task.get("product", "")),
    ]
    anchors = [x.lower() for x in anchors if len(x.strip()) >= 2]
    ranked = []
    for row in records:
        if row.get("content_type") != "note" or not row.get("text"):
            continue
        haystack = (row.get("title", "") + " " + row["text"]).lower()
        groups = row.get("research_groups", [])
        relevance = sum(1 for word in anchors if word in haystack)
        group_score = max(({"market": 1, "segment": 3, "brand": 5}.get(g, 0) for g in groups), default=0)
        engagement = parse_engagement(row.get("visible_engagement"))
        score = group_score * 12 + relevance * 8 + math.log10(engagement + 1) * 5
        ranked.append((score, engagement, row))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [{
        "source_id": row["source_id"],
        "title": row.get("title", "").strip(),
        "text": row["text"][:1600],
        "visible_engagement": row.get("visible_engagement", ""),
        "research_groups": row.get("research_groups", []),
        "usage_boundary": "只分析标题钩子、语气和正文节奏；不得把其他门店事实写成本店事实。",
    } for _, _, row in ranked[:limit]]


def _compact_length(text):
    return len(re.sub(r"\s+", "", text or ""))


def content_quality_issues(plan, task):
    """Reject common low-quality drafts before they are presented as ready to publish."""
    issues = []
    concrete = [
        task.get("store", ""), task.get("city", ""), task.get("area", ""),
        *re.split(r"[\s,，/]+", task.get("product", "")),
        "抹茶", "咖啡", "甜品", "soft serve", "软冰", "冰淇淋", "下午", "插座", "环境", "口味",
    ]
    concrete = [x.lower() for x in concrete if len(x.strip()) >= 2]
    vague = ("随便看看", "顺便看看", "记录一下", "是什么感觉", "慢一点", "松弛一下", "当天氛围观察")
    process_terms = ("这篇想", "这篇不", "这篇只", "这一篇只", "本篇", "发布前", "拍摄前", "拍摄当天", "资料显示", "资料尚未", "下次拍到", "没有确认的信息", "创作思路")
    for index, article in enumerate(plan.get("articles", []), 1):
        titles = article.get("titles", [])
        if len(set(t.strip() for t in titles if t.strip())) != len(titles):
            issues.append(f"第{index}篇存在重复标题备选")
        for title_index, title in enumerate(titles, 1):
            normalized = title.lower().strip()
            length = _compact_length(title)
            if length < 8 or length > 24:
                issues.append(f"第{index}篇标题{title_index}长度应控制在8—24字")
            if not any(word in normalized for word in concrete):
                issues.append(f"第{index}篇标题{title_index}没有一眼说明产品、地点、人群或具体场景")
            if any(word in title for word in vague):
                issues.append(f"第{index}篇标题{title_index}过于空泛")
        body = article.get("body", "")
        body_length = _compact_length(body)
        if body_length < 100 or body_length > 360:
            issues.append(f"第{index}篇正文建议控制在100—360字")
        used = [word for word in process_terms if word in body]
        if used:
            issues.append(f"第{index}篇正文出现创作过程说明：{'、'.join(used)}")
        if article.get("cta") and article["cta"].strip() in body:
            issues.append(f"第{index}篇正文与互动收尾重复")
    return list(dict.fromkeys(issues))
