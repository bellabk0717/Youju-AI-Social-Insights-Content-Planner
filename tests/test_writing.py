import unittest

from backend.writing import content_quality_issues, parse_engagement, select_style_references


class WritingTests(unittest.TestCase):
    def setUp(self):
        self.task = {"store": "nana's green tea", "city": "新加坡", "area": "Tanjong Pagar", "product": "matcha soft serve"}

    def test_engagement_parser(self):
        self.assertEqual(parse_engagement("1.2万"), 12000)
        self.assertEqual(parse_engagement("3k"), 3000)
        self.assertEqual(parse_engagement("455"), 455)

    def test_style_reference_prefers_relevance_and_brand(self):
        rows = [
            {"source_id": "N1", "content_type": "note", "title": "旅行", "text": "新加坡路线", "visible_engagement": "2万", "research_groups": ["market"]},
            {"source_id": "N2", "content_type": "note", "title": "喜欢抹茶的有福了", "text": "nana's green tea matcha soft serve", "visible_engagement": "88", "research_groups": ["brand"]},
        ]
        self.assertEqual(select_style_references(rows, self.task, 1)[0]["source_id"], "N2")

    def test_quality_gate_rejects_vague_title_and_process_language(self):
        plan = {"articles": [{
            "titles": ["当天氛围观察", "顺便看看", "今天慢一点"],
            "body": "这篇想记录一下门店。" * 12,
            "cta": "你会来吗？",
        }]}
        issues = content_quality_issues(plan, self.task)
        self.assertTrue(any("一眼说明" in issue for issue in issues))
        self.assertTrue(any("创作过程" in issue for issue in issues))

    def test_quality_gate_accepts_concrete_titles(self):
        plan = {"articles": [{
            "titles": ["抹茶控来新加坡吃这杯", "Tanjong Pagar抹茶下午", "soft serve抹茶怎么选"],
            "body": "抹茶的清苦先出来，随后是奶香和柔软的口感。坐下来慢慢吃，味道不会只剩甜。店里这次想把不同款 soft serve 放在一起介绍，方便抹茶控按自己的偏好选择。第一次来可以先从最想尝的口味开始，也欢迎告诉我们更喜欢清苦还是奶香。",
            "cta": "你更喜欢清苦还是奶香？",
        }]}
        self.assertEqual(content_quality_issues(plan, self.task), [])


if __name__ == "__main__":
    unittest.main()
