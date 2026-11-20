import json
import unittest
from modlingo.provider import APIConfig,Translator
from modlingo.batching import wire_batches,output_cost

class CompactMarkerTests(unittest.TestCase):
    def translator(self,provider='deepseek'):
        return Translator(APIConfig('http://127.0.0.1:3333/v1','fixture',provider=provider))

    def test_compact_roundtrip_keeps_order_parameters_and_linebreaks(self):
        source='Hello {{PlayerName}}!#Welcome.$h\nBye!'
        translator=self.translator();items=[('field',source)]
        _,prefix,records,restores=translator.prepare(items,{})
        self.assertEqual(prefix,'__ML_')
        result=records['0']['text'].replace('Hello','你好').replace('Welcome','欢迎').replace('Bye','再见')
        decoded,errors=translator.decode(json.dumps({'0':result}),items,prefix,records,restores,partial=True)
        self.assertEqual(errors,{})
        self.assertEqual(decoded[0][2],'你好 {{PlayerName}}!#欢迎.$h\n再见!')

    def test_reserved_namespace_in_input_falls_back_to_nonce(self):
        translator=self.translator();source='Keep __ML_original and {{PlayerName}}'
        _,prefix,records,restores=translator.prepare([('a',source)],{})
        self.assertNotEqual(prefix,'__ML_');self.assertNotIn(prefix,source)
        self.assertEqual(output_cost(source,8),output_cost(source,22))

    def test_unknown_or_reordered_compact_markers_are_rejected(self):
        translator=self.translator();items=[('a','Hello {{Name}}!#Welcome')]
        _,prefix,records,restores=translator.prepare(items,{})
        source=records['0']['text'].replace('Hello','你好')
        for target in (source+'__ML_unknown__',source.replace('__ML_0_0__','__ML_0_1__')):
            rows,errors=translator.decode(json.dumps({'0':target}),items,prefix,records,restores,partial=True)
            self.assertEqual(rows,[]);self.assertTrue(errors)

    def test_unverified_vendors_retain_nonce_protocol(self):
        _,prefix,_,_=self.translator('custom').prepare([('a','Hello {{Name}}')],{})
        self.assertNotEqual(prefix,'__ML_')

    def test_compact_budgeting_reduces_overhead_without_exceeding_envelope(self):
        items=[(str(i),'Hello '+('{{Name}}#'*6)) for i in range(20)]
        compact=list(wire_batches(items,20,6000,2048,8))
        legacy=list(wire_batches(items,20,6000,2048,22))
        self.assertLess(len(compact),len(legacy))
        self.assertEqual(sum(map(len,compact)),20)
