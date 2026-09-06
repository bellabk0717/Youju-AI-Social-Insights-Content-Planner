"""Synthetic fixtures test logic only; never used as product/research data."""
import unittest
from unittest.mock import AsyncMock, patch
from backend.app import record, retrieve, validate_angles, Analysis, check_plan, clean_api_key, normalize_plan_payload
from backend.xhs_mcp import valid

class CoreTests(unittest.TestCase):
    def test_key_validation_does_not_echo_secret(self):
        for bad in ['密钥名称sk-test', 'sk-test\u200b', 'sk-te st', 'Bearer sk-test']:
            with self.assertRaises(ValueError) as caught:clean_api_key(bad)
            self.assertNotIn(bad,str(caught.exception))
        self.assertEqual(clean_api_key('  sk-test\n'),'sk-test')

    def test_record_id_ignores_access_token_and_normalizes_whitespace(self):
        a=record('comment','测试 文本','https://www.xiaohongshu.com/explore/abc?xsec_token=a')
        b=record('comment','测试  文本','https://www.xiaohongshu.com/explore/abc?xsec_token=b')
        self.assertEqual(a['source_id'],b['source_id'])
        self.assertIn('xsec_token=a',a['url'])
    def test_external_url_rejected(self):
        for url in ['https://evil.com/explore/abc','file:///tmp/test','https://www.xiaohongshu.com.evil.com/explore/abc']:
            with self.assertRaises(ValueError):valid(url)
    def test_real_note_url_preserved(self):
        u='https://www.xiaohongshu.com/explore/abcdef123?xsec_token=example';self.assertEqual(valid(u),u)
    def test_false_quote_and_unknown_fact_are_rejected(self):
        rows=[record('comment','喜欢咖啡','https://www.xiaohongshu.com/explore/abc')]
        a={'title':'角度','reason':'说明','evidence_ids':[rows[0]['source_id']],'excerpts':['不存在的摘录'],'matched_fact':'有咖啡','limitations':'测试'}
        result=Analysis(summary='测试',angles=[a]);self.assertEqual(validate_angles(result,rows,'有咖啡'),[])
        a['excerpts']=['喜欢咖啡'];a['matched_fact']='有插座'
        self.assertEqual(validate_angles(Analysis(summary='测试',angles=[a]),rows,'有咖啡'),[])
    def test_one_thread_not_sufficient(self):
        r=record('comment','喜欢咖啡','https://www.xiaohongshu.com/explore/abc')
        a={'title':'角度','reason':'说明','evidence_ids':[r['source_id']],'excerpts':['喜欢咖啡'],'matched_fact':'有咖啡','limitations':'测试'}
        out=validate_angles(Analysis(summary='测试',angles=[a]),[r],'有咖啡')
        self.assertEqual(out[0]['evidence_status'],'limited')
    def test_angle_can_align_to_exact_user_fact_sentence(self):
        r=record('note','喜欢抹茶的有福了','https://www.xiaohongshu.com/explore/brand')
        r['research_groups']=['brand']
        a={'title':'抹茶爱好者来这里','reason':'用抹茶承接 soft serve','evidence_ids':[r['source_id']],'excerpts':['喜欢抹茶的有福了'],'matched_fact':'用户说这是抹茶店','limitations':'单篇样本'}
        facts='用户提供：抹茶是核心品类，本次推广店内 soft serve 系列。'
        out=validate_angles(Analysis(summary='测试',angles=[a]),[r],facts)
        self.assertIn(out[0]['matched_fact'],facts)
    def test_retrieval_has_no_invented_records(self):
        rows=[record('note',s,'https://www.xiaohongshu.com/explore/'+str(i)) for i,s in enumerate(['咖啡新品拿铁','地铁通勤','宠物玩具'])]
        out=retrieve('咖啡新品',rows,2)
        self.assertEqual(len(out),2);self.assertEqual(out[0]['text'],'咖啡新品拿铁')
        self.assertTrue(all(x['source_id'] in {r['source_id'] for r in rows} for x in out))

    def test_nested_model_sections_are_normalized_to_readable_text(self):
        raw={'series_strategy':{'positioning':'抹茶优先','boundary':'不写未知价格'},'test_suggestion':['标题A/B','比较收藏'],'limitations':['口味待确认']}
        out=normalize_plan_payload(raw)
        self.assertEqual(out['series_strategy'],'positioning：抹茶优先；boundary：不写未知价格')
        self.assertEqual(out['test_suggestion'],'标题A/B；比较收藏')

class GuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_number_guard_overrides_model_pass(self):
        with patch('backend.app.llm',new=AsyncMock(return_value={'passed':True,'issues':[]})):
            out=await check_plan({'articles':[{'titles':['特价9.9元'],'body':'','cta':''}]},{'facts':'售价20元','product':'拿铁'})
        self.assertFalse(out['passed']);self.assertTrue(out['issues'])
    async def test_issues_override_inconsistent_pass_flag(self):
        with patch('backend.app.llm',new=AsyncMock(return_value={'passed':True,'issues':['编造插座']})):
            out=await check_plan({'articles':[{'titles':['新品'],'body':'','cta':''}]},{'facts':'有拿铁','product':'拿铁'})
        self.assertFalse(out['passed'])

if __name__=='__main__':unittest.main()
