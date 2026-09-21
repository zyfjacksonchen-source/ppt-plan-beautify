"""Focused regressions using the bundled original templates; no system writes."""
import copy
import tempfile
import unittest
import struct
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

import stratx100 as s
from stratx100_images import object_blocks, replace_image


class ProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path,_=s.source(s.catalog(),'morning-bay-haze')
        cls.original=s.read_parts(path)
        cls.logo=s.ROOT/'assets/ecore-logo.png'

    def test_unknown_parameters_are_not_ignored(self):
        for plan in [
            {'mode':'adaptation','slides':[{'id':'002'}],'images':[]},
            {'mode':'adaptation','slides':[{'id':'002','chart_values':[]}]},
            {'mode':'adaptation','slides':[{'id':'002','text_runs':[{'index':0,'before':'x','after':'y','font_size':12}]}]},
            {'mode':'reproduction','slides':[]},
            {'mode':'adaptation','slides':[{'id':'002','text_runs':[{'index':.5,'before':'x','after':'y'}]}]},
        ]:
            with self.subTest(plan=plan),self.assertRaises(ValueError):s.validate_plan(plan)

    def test_palette_rejects_invalid_slots_and_colors(self):
        for palette in [{},{'primary':'123456'},{'accent1':'red'},{'accent1':'#fff'},{'accent1':123456}]:
            with self.subTest(palette=palette),self.assertRaises(ValueError):
                s.validate_plan({'mode':'adaptation','slides':[{'id':'005'}],'palette':palette})

    def test_palette_preserves_objects_media_and_gradient_transforms(self):
        parts=s.subset(self.original,['ppt/slides/slide17.xml']);before=dict(parts)
        changes=s.replace_palette(parts,{'accent1':'#6D4AFF','dk2':'352375'})
        self.assertEqual(len(changes),2)
        themes=s.slide_themes(parts)
        self.assertEqual(themes,['ppt/theme/theme1.xml'])
        for name in parts:
            if name not in themes:self.assertEqual(parts[name],before[name])
        colors=s.theme_colors(parts)[themes[0]]
        self.assertEqual(colors['accent1']['val'],'6D4AFF')
        self.assertEqual(colors['dk2']['val'],'352375')
        original=s.theme_colors(before)[themes[0]]
        for role in original:
            if role not in ('accent1','dk2'):self.assertEqual(colors[role],original[role])

    def test_unchanged_axis_cache_is_checked(self):
        part='ppt/slides/slide33.xml'
        d=s.inspect_page(self.original,part)
        binding=next(b for b in d['chart_values'] if b['part']=='ppt/charts/chart24.xml' and '$B$' in b['formula'])
        binding={**binding,'after':[v+1 for v in binding['before']]}
        parts=s.subset(self.original,[part]);before=dict(parts)
        with self.assertRaisesRegex(ValueError,'A2.*45292.*45658'):s.replace_chart(parts,binding)
        self.assertEqual(parts,before)

    def test_audit_catches_unmodified_source_chart_conflict(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source013.pptx'
            parts=s.subset(self.original,['ppt/slides/slide33.xml'])
            with zipfile.ZipFile(path,'w') as z:
                for name,raw in parts.items():z.writestr(name,raw)
            before=path.read_bytes();report=s.audit_file(path)
            self.assertEqual(report['structural_data_status'],'failed')
            self.assertTrue(any('45292' in f.get('detail','') for f in report['findings']))
            self.assertEqual(path.read_bytes(),before)

    def test_audit_detects_missing_media_without_claiming_visual_pass(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'broken.pptx'
            parts=s.subset(self.original,['ppt/slides/slide11.xml'])
            media=next(n for n in parts if n.startswith('ppt/media/'))
            del parts[media]
            with zipfile.ZipFile(path,'w') as z:
                for name,raw in parts.items():z.writestr(name,raw)
            report=s.audit_file(path)
            self.assertEqual(report['structural_data_status'],'failed')
            self.assertEqual(report['delivery_status'],'pending_visual_and_content_review')
            self.assertTrue(any(f.get('target')==media for f in report['findings']))

    def image_binding(self,part,oid,fit='contain'):
        slot=next(x for x in s.inspect_page(self.original,part)['image_slots'] if x['shape_id']==oid)
        return {'shape_id':oid,'before_sha256':slot['before_sha256'],'path':str(self.logo),'fit':fit,'alt':'Synthetic image regression'}

    def test_picture_isolation_and_aspect_ratio(self):
        part='ppt/slides/slide11.xml';parts=s.subset(self.original,[part])
        before={oid:m[0] for oid,m,_ in object_blocks(parts[part].decode())}
        binding=self.image_binding(part,'163')
        receipt=replace_image(parts,part,binding)
        after={oid:m[0] for oid,m,_ in object_blocks(parts[part].decode())}
        self.assertEqual(set(before),set(after))
        for oid in before:
            if oid!='163':self.assertEqual(before[oid],after[oid])
        for n in self.original:
            if n.startswith('ppt/media/') and n in parts:self.assertEqual(parts[n],self.original[n])
        slot=next(x for x in s.inspect_page(parts,part)['image_slots'] if x['shape_id']=='163')
        self.assertAlmostEqual(slot['box_emu'][2]/slot['box_emu'][3],602/88,places=4)
        self.assertFalse(receipt['shared_source_media_overwritten'])
        self.assertNotIn('svgBlip',after['163'])

    def test_empty_frame_is_preserved_and_cover_crop_is_correct(self):
        part='ppt/slides/slide193.xml';parts=s.subset(self.original,[part])
        before={oid:m[0] for oid,m,_ in object_blocks(parts[part].decode())}
        receipt=replace_image(parts,part,self.image_binding(part,'11','cover'))
        after={oid:m[0] for oid,m,_ in object_blocks(parts[part].decode())}
        for oid in before:self.assertEqual(before[oid],after[oid])
        self.assertEqual(len(after),len(before)+1)
        oid=receipt['inserted_picture_id']
        pic=next(node for sid,_,node in object_blocks(parts[part].decode()) if sid==oid)
        crop=pic.find('p:blipFill/a:srcRect',s.NS)
        width=602*(1-(int(crop.attrib.get('l',0))+int(crop.attrib.get('r',0)))/100000)
        height=88*(1-(int(crop.attrib.get('t',0))+int(crop.attrib.get('b',0)))/100000)
        box=receipt['original_box_emu']
        self.assertAlmostEqual(width/height,box[2]/box[3],places=4)

    def test_hidden_stale_and_corrupt_images_are_rejected(self):
        part='ppt/slides/slide11.xml';binding=self.image_binding(part,'163')
        bad=copy.deepcopy(binding);bad['before_sha256']='bad'
        with self.assertRaisesRegex(ValueError,'Stale'):replace_image(dict(self.original),part,bad)
        bad={**binding,'shape_id':'4'}
        with self.assertRaisesRegex(ValueError,'visible'):replace_image(dict(self.original),part,bad)
        with tempfile.TemporaryDirectory() as tmp:
            badfile=Path(tmp)/'bad.png';data=bytearray(self.logo.read_bytes());data[40]^=1;badfile.write_bytes(data)
            bad={**binding,'path':str(badfile)}
            with self.assertRaises(ValueError):replace_image(dict(self.original),part,bad)

    def test_valid_crc_does_not_hide_corrupt_image_stream(self):
        def chunk(kind,payload):
            return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
        header=chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))
        for payload in [b'not-a-zlib-image',zlib.compress(b'\x00'),zlib.compress(b'\x05\x00\x00\x00\x00')]:
            data=b'\x89PNG\r\n\x1a\n'+header+chunk(b'IDAT',payload)+chunk(b'IEND',b'')
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'corrupt.png';path.write_bytes(data)
                part='ppt/slides/slide11.xml';binding=self.image_binding(part,'163');binding['path']=str(path)
                with self.assertRaises(ValueError):replace_image(dict(self.original),part,binding)


if __name__=='__main__':
    unittest.main()
