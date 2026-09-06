"""Build category-aware Xiaohongshu research plans and verify cited findings."""
import math
import re

GROUPS = {"market": "第一层 · 大市场", "segment": "第二层 · 细分场景", "brand": "第三层 · 本店与品牌"}


def product_profile(task):
    raw = " ".join([task.get("store", ""), task.get("product", ""), task.get("facts", "")]).lower()
    if any(x in raw for x in ["soft serve", "soft-serve", "软冰淇淋", "软雪糕"]):
        product_type, strict_terms = "soft serve 软冰淇淋", ["soft serve", "软冰淇淋", "软雪糕"]
    elif any(x in raw for x in ["gelato", "意式冰淇淋"]):
        product_type, strict_terms = "gelato 意式冰淇淋", ["gelato", "意式冰淇淋"]
    elif "咖啡" in raw:
        product_type, strict_terms = "咖啡", ["咖啡"]
    else:
        product_type, strict_terms = task["product"].strip(), [task["product"].strip()]
    if any(x in raw for x in ["抹茶", "matcha", "green tea"]):
        store_type, store_query = "抹茶甜品店", "抹茶店 抹茶甜品"
    elif any(x in raw for x in ["咖啡店", "咖啡馆", "cafe"]):
        store_type, store_query = "咖啡店", "咖啡店"
    else:
        store_type, store_query = "甜品店", "甜品店"
    return {"product_type": product_type, "strict_product_terms": strict_terms, "store_type": store_type,
            "store_query": store_query,
            "excluded_as_same_category": ["gelato", "球状冰淇淋"] if "soft serve" in product_type else []}


def research_plan(task):
    city, area = task["city"].strip(), task.get("area", "").strip()
    location = " ".join(x for x in [city, area] if x)
    brand = re.sub(r"\s+", " ", task["store"]).replace(city, "").strip()
    profile = product_profile(task)
    product_type = profile["product_type"]
    segment_location=location if area else city
    specs = [
        ("market", f"{city} 咖啡店 甜品店", "发现大市场里用户普遍关注的维度：口味、价格、位置、品类、环境、停留条件等。"),
        ("market", f"{city} 咖啡店 探店 推荐", "补充高互动的自然分享主题和用户提问，不预设所有人都关注同一件事。"),
        ("segment", f"{segment_location} {profile['store_query']}", "缩到同店型与商圈，判断哪些大市场关注点仍然成立。"),
        ("segment", f"{segment_location} {product_type}", "只在产品层研究相同形态；soft serve 与 gelato 不算同类产品。"),
        ("segment", f"{segment_location} {product_type} 推荐", "研究细分品类的选择理由、负面体验与分享语气。"),
        ("brand", f"{city} {brand}", "最后看本品牌评价；找不到也不阻断，上传资料仍可定义本店能提供什么。"),
    ]
    budget = max(2, min(int(task.get("limit", 12)), 18))
    queries = [{"id": f"Q{i + 1}", "group": group, "label": GROUPS[group], "query": query,
                "question": question, "scope": "area_query_unverified" if area else "city_query_unverified"}
               for i, (group, query, question) in enumerate(specs)]
    extra = task.get("query", "").strip()
    if extra:
        queries[-1]["query"] = extra
        queries[-1]["question"] += " 使用用户补充搜索词。"
    return {"profile": profile, "queries": queries, "note_budget": budget,
            "per_query_limit": min(4, max(1, math.ceil(budget / len(queries)))), "location": location,
            "area_confirmed": bool(area),
            "ranking": "按大市场→细分场景→本店逐层收窄；每层先看相关性，再参考搜索卡片可见互动数。平台未提供浏览量时不冒充浏览量。",
            "limitations": ["大市场材料只用来发现关注维度，不能直接成为 soft serve 的产品卖点。",
                            "用户上传资料用于定义策划对象和本店可用事实；小红书样本用于发现关注点与内容语气。",
                            "本品牌评价是可选增强项，找不到不阻断同类研究与内容策划。",
                            "搜索区域和互动数都需逐条核实；互动高不等于消费转化高。"]}


def scoped_retrieval(records, query, retrieve_fn, k=20):
    selected, seen = [], set()
    for group in GROUPS:
        subset = [r for r in records if group in r.get("research_groups", [])]
        for row in retrieve_fn(query, subset, 5):
            if row["source_id"] not in seen:
                selected.append(row); seen.add(row["source_id"])
    for row in retrieve_fn(query, records, k):
        if len(selected) >= k: break
        if row["source_id"] not in seen:
            selected.append(row); seen.add(row["source_id"])
    return selected[:k]


def check_findings(findings, rows):
    known, valid = {r["source_id"]: r for r in rows}, []
    for finding in findings:
        item = finding.model_dump() if hasattr(finding, "model_dump") else dict(finding)
        ids, quotes = item.get("evidence_ids", []), item.get("excerpts", [])
        if not ids or len(ids) != len(quotes): continue
        if any(i not in known or not q or q not in known[i]["text"] for i, q in zip(ids, quotes)): continue
        item["evidence_status"] = "sample_observation"; valid.append(item)
    return valid


def angle_priority(angle, rows, focus="auto"):
    known = {row["source_id"]: row for row in rows}
    evidence = [known[i] for i in angle.get("evidence_ids", []) if i in known]
    text = " ".join([angle.get("title", ""), angle.get("reason", ""), *angle.get("excerpts", [])]).lower()
    score = sum("brand" in row.get("research_groups", []) for row in evidence) * 8
    score += sum("segment" in row.get("research_groups", []) for row in evidence) * 4
    if any(x in text for x in ["抹茶", "matcha", "green tea"]): score += 4 if focus in ["auto", "matcha", "balanced"] else 1
    if any(x in text for x in ["自习", "舒服", "隔间", "插座", "氛围", "待一下午", "停留"]): score += 4 if focus in ["auto", "scene", "balanced"] else 1
    if focus not in ["auto", "matcha", "scene", "balanced"]:
        terms = [x for x in re.split(r"[\s,，、；;]+", focus.lower()) if len(x) >= 2]
        score += sum(3 for term in terms if term in text)
    if any(x in text for x in ["gelato", "意式冰淇淋"]): score -= 6
    return score
