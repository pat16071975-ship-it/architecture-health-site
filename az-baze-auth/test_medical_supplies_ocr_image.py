"""Synthetic pixel and source-provenance regressions; no private documents."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib
import cv2
import numpy as np
import medical_supplies_ocr as core
import medical_supplies_ocr_image as image


def words(text, confidence=95):
    result=[]
    x=10
    for token in text.split():
        result.append([x,10,max(5,len(token)*8),16,confidence,token])
        x+=len(token)*8+8
    return result


def header_cells(value):
    return [words("Артикул и наименование товара"),words("Ед. изм."),words("Кол-во"),words(value)]


def synthetic_image(with_price=False):
    canvas=np.full((1000,1500),255,np.uint8)
    xs=([80,850,1000,1140,1300,1450] if with_price else [80,920,1060,1220,1450])
    ys=[210,310,420,530,630]
    for x in xs:
        cv2.line(canvas,(x,ys[0]),(x,ys[-1]),0,2)
    for y in ys:
        cv2.line(canvas,(xs[0],y),(xs[-1],y),0,2)
    def text(value,x,y,scale=.8):
        cv2.putText(canvas,value,(x,y),cv2.FONT_HERSHEY_SIMPLEX,scale,0,2,cv2.LINE_AA)
    text("Example supplier - delivery A12345",80,110,1)
    labels=["Product","Unit","Qty","Price","Total"] if with_price else ["Product","Unit","Qty","Total"]
    for x,label in zip(xs,labels):
        text(label,x+18,270)
    values=[['100001 Gauze sterile 10 pcs','pcs','3','4.1133','12.34'],['100002 Gloves size 8','pcs','1','40.5000','40.50']]
    for baseline,row in zip([380,490],values):
        if not with_price:
            row=row[:3]+row[4:]
        for x,value in zip(xs,row):
            text(value,x+18,baseline)
    text('Total',xs[0]+18,590)
    text('52.84',xs[-2]+18,590)
    matrix=cv2.getRotationMatrix2D((750,500),-2.2,1)
    return cv2.warpAffine(canvas,matrix,(1500,1000),borderValue=255)


def image_pdf(gray):
    """Minimal raster-only PDF; exercises rendering, not a native text layer."""
    height,width=gray.shape
    packed=zlib.compress(gray.tobytes())
    stream=b'q 750 0 0 500 0 0 cm /Im0 Do Q'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 750 500] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>',
        b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream',
        b'<< /Type /XObject /Subtype /Image /Width '+str(width).encode()+b' /Height '+str(height).encode()+b' /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length '+str(len(packed)).encode()+b' >>\nstream\n'+packed+b'\nendstream']
    data=bytearray(b'%PDF-1.4\n')
    offsets=[0]
    for index,obj in enumerate(objects,1):
        offsets.append(len(data))
        data.extend(str(index).encode()+b' 0 obj\n'+obj+b'\nendobj\n')
    xref=len(data)
    data.extend(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets[1:]:
        data.extend(f'{offset:010d} 00000 n \n'.encode())
    data.extend(b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n')
    return bytes(data)


class CellSemanticsTests(unittest.TestCase):
    def test_numeric_requires_agreement_not_single_confident_wrong_digit(self):
        self.assertEqual(image._agree_cells({'first':words('5'),'second':words('??'),'third':words('2')},1000),(None,'conflict'))
        self.assertEqual(image._agree_cells({'first':words('5')},1000),(None,'single_read'))
        self.assertEqual(image._agree_cells({'first':words('5'),'second':words('2'),'third':words('2')},1000),('2',None))
        self.assertEqual(image._agree_cells({'first':words('5'),'second':words('5'),'third':words('2'),'fourth':words('2')},1000),(None,'conflict'))

    def test_money_keeps_printed_decimal_and_price_precision(self):
        self.assertEqual(image._numeric(words('1 234,56'),100,True),'1234.56')
        self.assertIsNone(image._numeric(words('1 234 56'),100,True))
        self.assertIsNone(image._numeric(words('1234.OO'),100,True))
        self.assertEqual(image._numeric(words('4.1133'),10000),'4.1133')
        self.assertIsNone(image._numeric(words('4.1133'),1000))

    def test_header_gross_never_overrides_an_explicit_net_read(self):
        good=header_cells('Стоимость с НДС')
        net=header_cells('Стоимость без НДС')
        mixed=[{'first':a,'second':b,'third':a} for a,b in zip(good,net)]
        self.assertIsNone(image._header_consensus(mixed)['value_basis'])
        good_only=[{'first':a,'second':a,'third':a} for a in good]
        self.assertEqual(image._header_consensus(good_only)['value_basis'],'printed_gross')

    def test_separate_vat_column_does_not_make_a_generic_total_gross(self):
        labels=header_cells('Стоимость')+[words('НДС')]
        cells=[{'first':w,'second':w,'third':w} for w in labels]
        self.assertIsNone(image._header_consensus(cells)['value_basis'])
        first=header_cells('НДС Сумма')
        third=header_cells('Сумма с НДС')
        cells=[{'first':a,'second':a,'third':b} for a,b in zip(first,third)]
        self.assertIsNone(image._header_consensus(cells)['value_basis'])

    def test_conflicting_units_are_not_silently_selected(self):
        self.assertEqual(image._agree_unit({'first':words('РУЛ'),'second':words('УПАК'),'third':words('ШТ')}),'')
        self.assertEqual(image._agree_unit({'first':words('РУЛ'),'second':words('УПАК'),'third':words('УПАК')}),'УПАК')

    def test_subtotal_keeps_following_goods_and_uses_final_total(self):
        xs=[10,600,730,850,1100]
        ys=[10,90,170,250,330,410]
        labels=['Наименование','Ед.','Количество','Стоимость с НДС']
        rows=[labels,['Товар А','Шт','1','100.00'],['Итого по группе','','','100.00'],
              ['Товар Б','Шт','1','200.00'],['Итого','','','300.00']]
        raw=[]
        mapping=[]
        for r,row in enumerate(rows):
            for col,value in enumerate(row):
                x0,y0,x1,y1=xs[col],ys[r],xs[col+1],ys[r+1]
                for w in words(value):
                    raw.append([w[0]+x0,y0+24,*w[2:]])
                mapping.append({'grid':0,'row':r,'col':col,'source':(x0,y0,x1,y1),
                                'target':(x0,y0,x1,y1),'scale':1})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            path=root/'page.png'
            canvas=np.full((500,1200),255,np.uint8)
            cv2.imwrite(str(path),canvas)
            with patch.object(image,'_rotation',return_value=(canvas,np.array([[1.,0.,0.],[0.,1.,0.]]),0)), \
                 patch.object(image,'_grids',return_value=([{'xs':xs,'ys':ys}],canvas)), \
                 patch.object(image,'_montage',return_value=(canvas,mapping)), \
                 patch.object(image,'_ocr',return_value=raw):
                result=image.enhance_page(path,root,None,{'words':[],'lines':[],'candidates':[]})
        self.assertEqual([row['name'] for row in result['candidates']],['Товар А','Товар Б'])
        self.assertEqual(result['printed_total'],'300.00')


    def test_tax_amount_and_damaged_qualifier_are_not_goods_gross(self):
        for text in ['Сумма НДС','Стоимость без НДС','Стоимость с HOC','Стоимость не включая НДС']:
            with self.subTest(text=text):
                self.assertIsNone(image._header(header_cells(text))['value_basis'])

    def test_grid_finds_rows_without_any_initial_ocr_words(self):
        rotated,_,angle=image._rotation(synthetic_image(),cv2,np)
        grids,_=image._grids(rotated,cv2,np)
        self.assertTrue(grids)
        self.assertAlmostEqual(angle,2.2,delta=.25)
        self.assertEqual(len(grids[0]['ys'])-3,2)
        self.assertEqual(len(grids[0]['xs'])-1,4)

    def test_temporary_ocr_failure_remains_retryable_and_preserves_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            path=root/'page.png'
            cv2.imwrite(str(path),synthetic_image())
            original={'words':[], 'lines':[], 'candidates':[{'name':'Existing draft'}]}
            def failed(*args):
                raise core.OCRUnavailable('timeout')
            out=image.enhance_page(path,root,failed,original)
            self.assertTrue(out['recognition_retryable'])
            self.assertEqual(out['candidates'],original['candidates'])
            self.assertNotIn('recognition_retryable',original)

    def test_blank_page_fallback_does_not_retry_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            path=root/'page.png'
            cv2.imwrite(str(path),np.full((600,800),255,np.uint8))
            original={'words':[], 'lines':[], 'candidates':[]}
            def unexpected(*args):
                self.fail('No OCR process is needed for an absent table')
            out=image.enhance_page(path,root,unexpected,original)
            self.assertFalse(out.get('recognition_retryable',False))

    def test_inverse_mapping_retains_printed_word_inside_source(self):
        inv=cv2.invertAffineTransform(cv2.getRotationMatrix2D((750,500),2.2,1))
        mapped=image._source_word([100,250,100,30,95,'12.34'],inv,(1000,1500))
        self.assertEqual(mapped[-2:],[95,'12.34'])
        self.assertTrue(0<=mapped[0]<1500 and 0<=mapped[1]<1000)
        self.assertLessEqual(mapped[0]+mapped[2],1500)
        self.assertLessEqual(mapped[1]+mapped[3],1000)


@unittest.skipUnless(os.environ.get('AZ_TEST_REAL_OCR')=='1','Set AZ_TEST_REAL_OCR=1 for raster-PDF/Tesseract acceptance')
class RealImageTests(unittest.TestCase):
    def test_raster_pdf_reconstructs_goods_price_and_source_evidence(self):
        original_command=core._command
        tessdata=os.environ.get('AZ_OCR_TESSDATA') or os.environ.get('TESSDATA_PREFIX')
        def bounded(argv,directory,output,seconds,max_size):
            if argv[0]=='tesseract' and tessdata:
                argv=[*argv[:3],'--tessdata-dir',tessdata,*argv[3:]]
            return original_command(argv,directory,output,seconds,max_size)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(core,'_command',side_effect=bounded):
                count,result=core.recognize_pdf_page(image_pdf(synthetic_image(with_price=True)),1,Path(tmp))
            self.assertEqual(count,1)
            self.assertEqual(result.get('image_evidence',{}).get('method'),'deskewed_grid_cells',result.get('recognition_issues'))
            rows=result['candidates']
            self.assertEqual(len(rows),2)
            self.assertEqual([r['quantity'] for r in rows],['3','1'])
            self.assertEqual([r['value'] for r in rows],['12.34','40.50'])
            self.assertEqual([r['price'] for r in rows],['4.1133','40.5000'])
            self.assertEqual(result['printed_total'],'52.84')
            self.assertTrue(all(r['unit']=='pcs' for r in rows))
            self.assertTrue(all(r['name'] for r in rows))
            ids={line['id'] for line in result['lines']}
            self.assertTrue(all(r['source_line'] in ids for r in rows))
            self.assertEqual(len({r['source_line'] for r in rows}),2)
            source=' '.join(line['text'] for line in result['lines'])
            for amount in ('12.34','40.50','4.1133'):
                self.assertIn(amount,source)
            self.assertNotIn('rows',result)


if __name__=='__main__':
    unittest.main()
