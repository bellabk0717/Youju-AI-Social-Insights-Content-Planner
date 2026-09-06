import unittest
from backend.research import research_plan, scoped_retrieval, check_findings

class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.task={'store':"nana's green tea",'city':'新加坡','product':'所有 soft serve 产品','limit':12}
    def test_market_first_and_brand_is_not_every_query(self):
        plan=research_plan(self.task)
        self.assertNotEqual(plan['queries'][0]['group'],'brand')
        self.assertEqual({q['group'] for q in plan['queries']},{'market','segment','brand'})
        self.assertEqual(sum('nana' in q['query'] for q in plan['queries']),1)
    def test_does_not_invent_neighborhood_or_study_need(self):
        p=research_plan(self.task)
        self.assertFalse(p['area_confirmed'])
        self.assertTrue(all('Tanjong' not in q['query'] and '自习' not in q['query'] for q in p['queries']))
        self.task['area']='Tanjong Pagar'
        p=research_plan(self.task)
        self.assertTrue(p['area_confirmed'])
        self.assertNotIn('Tanjong Pagar',p['queries'][0]['query'])
        self.assertTrue(any(q['group']=='segment' and 'Tanjong Pagar' in q['query'] for q in p['queries']))
    def test_soft_serve_profile_does_not_treat_gelato_as_same_product(self):
        p=research_plan(self.task)
        self.assertIn('soft serve',p['profile']['product_type'])
        self.assertIn('gelato',p['profile']['excluded_as_same_category'])
    def test_custom_brand_keywords_preserve_market_questions(self):
        self.task['query']='新加坡 nana soft serve'
        p=research_plan(self.task)
        self.assertEqual(p['queries'][-1]['query'],self.task['query'])
        self.assertTrue(any(q['group']=='segment' for q in p['queries']))
    def test_balanced_retrieval_prevents_brand_crowding(self):
        records=[{'source_id':str(i),'research_groups':['brand']} for i in range(40)]
        records += [{'source_id':g,'research_groups':[g]} for g in ['market','segment']]
        def ranking(q,rs,k):return rs[:k]
        result=scoped_retrieval(records,'test',ranking,20)
        self.assertEqual(len(result),20)
        self.assertTrue({'market','segment'}.issubset({r['source_id'] for r in result}))
    def test_finding_must_quote_actual_source(self):
        rows=[{'source_id':'N1','text':'测试夹具：这款太甜'}]
        good={'evidence_ids':['N1'],'excerpts':['这款太甜']}
        bad={'evidence_ids':['N1'],'excerpts':['适合自习']}
        out=check_findings([good,bad],rows)
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]['excerpts'],['这款太甜'])

if __name__=='__main__':unittest.main()
