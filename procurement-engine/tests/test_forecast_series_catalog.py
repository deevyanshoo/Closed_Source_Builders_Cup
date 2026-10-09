"""Generated WB/BIS fixtures. No observed body, network, model or child execution."""
import csv
import hashlib
import io
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from procure.evaluation import observed_admission as a


def months():
    return [f'{n // 12:04d}-{n % 12 + 1:02d}' for n in range(2016*12+8,2026*12+8)]


def synthetic_sources(wb, bis):
    return {key: {'file': {'path': '/synthetic/'+key, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                    'identity':{'dev':1,'ino':2 if key=='wb' else 3,'mode':0o100600,'uid':501,'nlink':1,'size':len(raw),'mtime_ns':1,'ctime_ns':1},
                    'ancestors':[{'path':p,'identity':{'dev':1,'ino':i+1,'mode':0o40700,'uid':501}}for i,p in enumerate(['/','/synthetic'])]},
                  'provider': 'World Bank' if key=='wb' else 'BIS', 'source_id': 'generated-'+key,
                  'source_url': 'synthetic:owned-'+key, 'retrieved_utc': '2026-10-09T00:00:00.000000Z',
                  'rights_disposition_sha256': '7'*64, 'source_disposition_sha256': '8'*64}
            for key,raw in [('wb',wb),('bis',bis)]}


def generated_wb(*, missing=False, no_index_base=False, date_fault=False):
    book=Workbook();s=book.active;s.title='Monthly Prices'
    for col,label,unit in [(2,'Cotton A Index','($/kg)'),(3,'Unlisted grain','($/mt)'),
                           (4,'Unlisted gas index','(2010=100)'),(5,'Iron Ore','($/dmtu)'),(6,'Mystery','unrecognised')]:
        s.cell(5,col,label);s.cell(6,col,unit)
    t=book.create_sheet('Monthly Indices');t.cell(2,1,'Indices (2010=100)' if not no_index_base else 'Indices')
    t.cell(6,2,'Composite');t.cell(7,2,'nonfuel');t.cell(8,2,'benchmark')
    for i,m in enumerate(months()):
        compact=m.replace('-','M');s.cell(i+7,1,compact);t.cell(i+9,1,compact)
        for col in range(2,7):
            if not (missing and col==3 and i==119):s.cell(i+7,col,float(100+col+i))
        t.cell(i+9,2,float(200+i))
    if date_fault:s.cell(8,1,'2016M09')
    out=io.BytesIO();book.save(out);book.close();return out.getvalue()


BIS_HEADER=['FREQ','REF_AREA','CURRENCY','COLLECTION','TIME_PERIOD','OBS_VALUE','OBS_STATUS','UNIT_MULT']

def generated_bis(*, bad_status=False, regime=False, duplicate=False, extra_rows=0):
    out=io.StringIO();w=csv.writer(out);w.writerow(BIS_HEADER+(['TITLE'] if regime else []))
    for area,code in [('AA','ZZZ'),('ZZ','ZZZ'),('AT','EUR'),('DE','EUR'),('US','USD'),('XM','XDR')]:
        for i,m in enumerate(months()):
            value='1' if code=='USD' else str(2+i/100)
            row=['M',area,code,'A',m,value,'M' if bad_status and area=='AA' and i==119 else 'A','0']
            if regime:row+=['currency change' if area=='AA' and i>=60 else 'stable']
            w.writerow(row)
            if duplicate and area=='AA' and i==0:w.writerow(row)
    for i in range(extra_rows):
        # Unique out-of-window years, padding metadata; valid bounded SDMX rows.
        w.writerow(['M','PX','PAD','A',f'{i//12+1:04d}-{i%12+1:02d}','2','A','0']+(['stable'] if regime else []))
    return out.getvalue().encode()


def build(wb,bis,**kwargs):
    # Before implementation this exercises the existing public parsing behavior,
    # rather than reporting a missing import as a behavioral RED.
    try:
        from procure.evaluation.series_catalog import build_catalog
    except ModuleNotFoundError:
        return a._parse_native(wb)
    return build_catalog(wb,bis,sources=synthetic_sources(wb,bis),profile='generated-fixture-test-v1',**kwargs)


def test_catalog_arbitrary_native_price_index_fx_without_material_allowlist():
    snapshot=build(generated_wb(),generated_bis()); records=snapshot.records
    prices=[r for r in records if r['measure_type']=='price' and r['forecast_eligible']]
    assert {r['label'] for r in prices}=={'Cotton A Index','Unlisted grain'}
    indices=[r for r in records if r['measure_type']=='index' and r['forecast_eligible']]
    assert len(indices)==2 and all(r['quoted_unit']=='2010=100' for r in indices)
    assert all(len(r['required_window_mask'])==120 for r in records)
    assert len(records)==12  # six WB named columns + six area/currency lines
    eur=[r for r in records if r.get('currency')=='EUR' and r['forecast_eligible']]
    assert len(eur)==1 and eur[0]['locator']['REF_AREA']=='DE'
    assert all(r['series_id'].startswith(('WB:','BIS:')) for r in records)
    assert not any(r['currency']=='USD' and r['series_kind']=='fx' and r['forecast_eligible'] for r in records)


def test_catalog_missing_one_series_preserves_independent_complete_series():
    snapshot=build(generated_wb(missing=True),generated_bis(bad_status=True))
    grain=next(r for r in snapshot.records if r['label']=='Unlisted grain')
    assert grain['missing_count']==1 and not grain['forecast_eligible']
    assert any(r['label']=='Cotton A Index' and r['forecast_eligible'] for r in snapshot.records)
    zzz=next(r for r in snapshot.records if r['currency']=='ZZZ' and r['forecast_eligible'])
    assert zzz['locator']['REF_AREA']=='ZZ' and zzz['alternative_areas']==['AA','ZZ']

@pytest.mark.parametrize('fault',['index_base','duplicate_month'])
def test_catalog_sheet_specific_evidence_and_compact_calendar(fault):
    wb=generated_wb(no_index_base=fault=='index_base',date_fault=fault=='duplicate_month')
    if fault=='duplicate_month':
        with pytest.raises(ValueError,match='calendar|consecutive|duplicate'):build(wb,generated_bis())
    else:
        s=build(wb,generated_bis())
        index=next(r for r in s.records if r['locator'].get('sheet')=='Monthly Indices')
        assert not index['forecast_eligible'] and index['eligibility_reason']=='index_base_evidence_absent'
        assert index['locator']['header_rows']==[6,7,8]
        assert any(r['label']=='Unlisted grain' and r['forecast_eligible'] for r in s.records)

@pytest.mark.parametrize('fault',['regime','duplicate'])
def test_catalog_regime_or_duplicate_refuses_affected_line_without_splicing(fault):
    s=build(generated_wb(),generated_bis(regime=fault=='regime',duplicate=fault=='duplicate'))
    aa=next(r for r in s.records if r['locator'].get('REF_AREA')=='AA')
    assert not aa['forecast_eligible']
    assert aa['eligibility_reason']==('currency_regime_disposition_required' if fault=='regime' else 'duplicate_source_key')
    zz=next(r for r in s.records if r['locator'].get('REF_AREA')=='ZZ')
    assert zz['forecast_eligible'] and s.values[zz['series_id']]['2016-09']==2.


def test_catalog_finite_queue_covers_more_than_one_two_series_batch():
    from procure.evaluation.series_catalog import finite_batches
    s=build(generated_wb(),generated_bis())
    q=finite_batches(s.records);flat=[sid for batch in q for sid in batch]
    assert len(q)==4 and len(flat)==7 and all(1<=len(batch)<=2 for batch in q)
    assert flat==sorted(r['series_id'] for r in s.records if r['forecast_eligible'])


def test_catalog_native_basis_and_retrospective_calendar_not_pit():
    s=build(generated_wb(),generated_bis())
    r=next(r for r in s.records if r['label']=='Cotton A Index')
    assert r['analysis_as_of']=='2026-10-09' and r['quoted_unit']=='$/kg'
    assert s.values[r['series_id']]['2016-09']==102.0
    assert r['source_last_period']=='2026-08'  # no September requirement
    assert 'not PIT' in r['publication_availability_policy']
    iron=next(r for r in s.records if r['label']=='Iron Ore')
    assert not iron['forecast_eligible'] and iron['quoted_unit']=='$/dmtu'
    basket=next(r for r in s.records if r['currency']=='XDR')
    assert basket['category']=='reserve_basket'


def test_catalog_large_bis_bound_does_not_weaken_legacy_reader(tmp_path):
    from procure.evaluation.series_catalog import MAX_BIS_BYTES
    # 90000 unique pre-window monthly rows with bounded source metadata. The
    # generated body is >12MiB and <32MiB; no real source download is involved.
    out=io.StringIO();w=csv.writer(out);w.writerow(BIS_HEADER+['TITLE'])
    for i in range(90000):
        w.writerow(['M','PP','PAD','A',f'{i//12+1:04d}-{i%12+1:02d}','2','A','0','x'*128])
    raw=out.getvalue().encode()
    assert 12*1024*1024<len(raw)<32*1024*1024
    s=build(generated_wb(),raw)
    assert any(r['currency']=='PAD' and r['constant'] for r in s.records)
    path=tmp_path.resolve()/'large-generated.csv';path.write_bytes(raw);life=a.Lifetime()
    try:
        with pytest.raises(ValueError,match='bounded'):life.open(path)
    finally:life.close()
    with pytest.raises(ValueError,match='byte bound'):build(generated_wb(),b'x'*(MAX_BIS_BYTES+1))


def test_catalog_bis_row_and_field_limits_and_fixed_schema():
    base=generated_bis()
    with pytest.raises(ValueError,match='schema'):build(generated_wb(),base.replace(b'UNIT_MULT',b'UNKNOWN'))
    with pytest.raises(ValueError,match='field width'):build(generated_wb(),base.replace(b',ZZZ,',b','+b'Z'*129+b',',1))
    # Identical keys are visible unavailable until the hard transport row ceiling.
    row=b'M,AA,ZZZ,A,2016-09,2,A,0\n'
    body=(','.join(BIS_HEADER)+'\n').encode()+row*200001
    with pytest.raises(ValueError,match='row bound'):build(generated_wb(),body)


@pytest.mark.parametrize('unit',['($/cubic meter)','(cents/sheet)','($/troy oz)'])
def test_catalog_all_bound_native_price_unit_forms_remain_prices_without_conversion(unit):
    book=Workbook();s=book.active;s.title='Monthly Prices';s.cell(5,2,'Arbitrary supported benchmark');s.cell(6,2,unit)
    t=book.create_sheet('Monthly Indices');t.cell(2,1,'2010=100');t.cell(6,2,'Arbitrary index')
    for i,m in enumerate(months()):
        s.cell(i+7,1,m.replace('-','M'));s.cell(i+7,2,100.+i)
        t.cell(i+9,1,m.replace('-','M'));t.cell(i+9,2,100.+i)
    out=io.BytesIO();book.save(out);book.close();snapshot=build(out.getvalue(),generated_bis())
    r=next(r for r in snapshot.records if r['label']=='Arbitrary supported benchmark')
    assert r['forecast_eligible'] and r['measure_type']=='price'
    assert r['quoted_unit']==unit.strip('()') and snapshot.values[r['series_id']]['2016-09']==100.


def test_catalog_full_82_wb_and192_bis_lines_are_visible_without_universe_cap():
    book=Workbook();s=book.active;s.title='Monthly Prices';t=book.create_sheet('Monthly Indices');t.cell(2,1,'2010=100')
    for col in range(2,73):s.cell(5,col,f'Unlisted commodity {col}');s.cell(6,col,'($/kg)')
    for col in range(2,13):t.cell(6,col,'Unlisted');t.cell(7,col,f'index {col}')
    for i,m in enumerate(months()):
        s.cell(i+7,1,m.replace('-','M'));t.cell(i+9,1,m.replace('-','M'))
        for col in range(2,73):s.cell(i+7,col,100.+i+col)
        for col in range(2,13):t.cell(i+9,col,100.+i+col)
    wb=io.BytesIO();book.save(wb);book.close()
    out=io.StringIO();w=csv.writer(out);w.writerow(BIS_HEADER)
    for j in range(192):
        area=chr(65+j//26)+chr(65+j%26);currency='Q'+area
        for i,m in enumerate(months()):w.writerow(['M',area,currency,'A',m,str(100+i+j),'A','0'])
    snap=build(wb.getvalue(),out.getvalue().encode())
    assert len(snap.records)==274
    assert sum(r['series_kind']=='commodity' for r in snap.records)==82
    assert sum(r['series_kind']=='fx' for r in snap.records)==192
    from procure.evaluation.series_catalog import finite_batches
    queue=finite_batches(snap.records)
    assert len(queue)==137 and sum(len(b)for b in queue)==274
    assert all(r['forecast_eligible'] for r in snap.records)


def test_catalog_regime_disposition_binds_exact_evidence_not_approval_text():
    from procure.evaluation.series_catalog import build_catalog
    wb,bis=generated_wb(),generated_bis(regime=True);sources=synthetic_sources(wb,bis)
    initial=build(wb,bis);r=next(r for r in initial.records if r['locator'].get('REF_AREA')=='AA')
    evidence_sha=hashlib.sha256(a.canonical(r['header_unit_evidence'])).hexdigest()
    disposition={r['series_id']:{'decision':'retain_single_source_regime','evidence_sha256':evidence_sha,'disposition_sha256':'9'*64}}
    final=build_catalog(wb,bis,sources=sources,profile='generated-fixture-test-v1',regime_dispositions=disposition)
    chosen=next(x for x in final.records if x['series_id']==r['series_id'])
    assert chosen['forecast_eligible'] and chosen['regime_disposition']==disposition[r['series_id']]
    disposition[r['series_id']]['evidence_sha256']='0'*64
    refused=build_catalog(wb,bis,sources=sources,profile='generated-fixture-test-v1',regime_dispositions=disposition)
    assert not next(x for x in refused.records if x['series_id']==r['series_id'])['forecast_eligible']


def test_catalog_series_spec_requires_complete_original_identity_not_hash_alone():
    from procure.evaluation.series_catalog import build_catalog
    wb,bis=generated_wb(),generated_bis();sources=synthetic_sources(wb,bis)
    sources['bis']['file'].pop('identity',None)
    with pytest.raises(ValueError,match='schema|identity'):
        build_catalog(wb,bis,sources=sources,profile='generated-fixture-test-v1')


def test_catalog_wb_unit_field_bound_is_checked_before_unit_classification():
    book=Workbook();s=book.active;s.title='Monthly Prices';s.cell(5,2,'Unlisted');s.cell(6,2,'x'*513)
    t=book.create_sheet('Monthly Indices');t.cell(2,1,'2010=100');t.cell(6,2,'Index')
    for i,m in enumerate(months()):
        s.cell(i+7,1,m.replace('-','M'));s.cell(i+7,2,100.+i);t.cell(i+9,1,m.replace('-','M'));t.cell(i+9,2,100.+i)
    wb=io.BytesIO();book.save(wb);book.close()
    with pytest.raises(ValueError,match='field'):build(wb.getvalue(),generated_bis())


def test_catalog_stable_fx_ids_distinguish_pinned_regime_evidence():
    stable=build(generated_wb(),generated_bis())
    changed=build(generated_wb(),generated_bis(regime=True))
    first=next(r for r in stable.records if r['locator'].get('REF_AREA')=='AA')
    second=next(r for r in changed.records if r['locator'].get('REF_AREA')=='AA')
    assert first['series_id']!=second['series_id']
    assert 'historical authenticity not inferred' in first['regime_decision']
    assert second['regime_decision']=='unavailable_pending_explicit_regime_disposition'


# Source-shape regressions: generated bytes only; never cached originals.
def shape_wb(*, tail_row=1362, full_catalog=False):
    from openpyxl import load_workbook
    from openpyxl.styles import Font
    book=load_workbook(io.BytesIO(generated_wb()))
    s,t=book['Monthly Prices'],book['Monthly Indices']
    if full_catalog:
        for col in range(7,73):
            s.cell(5,col,f'Extra commodity {col}');s.cell(6,col,'($/kg)')
            for i in range(120):s.cell(i+7,col,100.+i+col)
        for col in range(3,13):
            t.cell(6,col,f'Extra index {col}')
            for i in range(120):t.cell(i+9,col,100.+i+col)
    # Style-only physical cells/rows, including a meaningful last row below1006.
    for row in range(129,tail_row+1):t.cell(row,2).font=Font(bold=True)
    out=io.BytesIO();book.save(out);book.close();return out.getvalue()


def shape_xml(wb, edit):
    import zipfile
    out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(wb)) as original, zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as changed:
        for member in original.infolist():
            raw=original.read(member.filename)
            changed.writestr(member,edit(member.filename,raw))
    return out.getvalue()


OFFICIAL_BIS_HEADER=['DATAFLOW','FREQ','REF_AREA','CURRENCY','COLLECTION','TIME_PERIOD',
                    'OBS_VALUE','UNIT_MULT','DECIMALS','AVAILABILITY','TITLE','OBS_STATUS','OBS_PRE_BREAK','OBS_CONF']

def shape_bis(*, flow='BIS:WS_XRU(1.0)', availability='A', prebreak='', prebreak_month='2016-09', duplicate=False):
    rows=list(csv.DictReader(io.StringIO(generated_bis(duplicate=duplicate).decode())))
    out=io.StringIO();w=csv.DictWriter(out,fieldnames=OFFICIAL_BIS_HEADER);w.writeheader()
    for row in rows:
        affected=row['REF_AREA']=='AA'
        row.update(DATAFLOW=flow,AVAILABILITY=availability if affected else 'A',DECIMALS='2',
                   TITLE='Synthetic currency',OBS_CONF='F',
                   OBS_PRE_BREAK=prebreak if affected and row['TIME_PERIOD']==prebreak_month else '')
        w.writerow(row)
    return out.getvalue().encode()


def test_source_shape_style_tail_preserves_all82_wb_catalog_lines():
    snap=build(shape_wb(full_catalog=True),generated_bis())
    wb=[r for r in snap.records if r['series_kind']=='commodity']
    assert len(wb)==82 and len(snap.records)==88
    assert all(r['required_window_mask']==['valid']*120 for r in wb)
    assert len([r for r in wb if r['locator']['sheet']=='Monthly Indices'])==11
    cotton=next(r for r in wb if r['label']=='Cotton A Index')
    assert snap.values[cotton['series_id']]['2016-09']==102.


def test_source_shape_official14_field_bis_remains_complete_and_public():
    snap=build(generated_wb(),shape_bis())
    aa=next(r for r in snap.records if r['locator'].get('REF_AREA')=='AA')
    assert aa['forecast_eligible'] and aa['required_window_mask']==['valid']*120
    assert snap.values[aa['series_id']]['2016-09']==2.
    assert aa['header_unit_evidence']['source_attributes']=={'DATAFLOW':['BIS:WS_XRU(1.0)'],'AVAILABILITY':['A']}
    assert not any(r['currency']=='USD' and r['series_kind']=='fx' and r['forecast_eligible'] for r in snap.records)
    assert next(r for r in snap.records if r['currency']=='EUR' and r['forecast_eligible'])['locator']['REF_AREA']=='DE'
    assert next(r for r in snap.records if r['currency']=='XDR')['category']=='reserve_basket'


@pytest.mark.parametrize('availability',['B','K','Z','', 'AA'])
def test_source_shape_restricted_unknown_availability_visible_but_held(availability):
    snap=build(generated_wb(),shape_bis(availability=availability))
    aa=next(r for r in snap.records if r['locator'].get('REF_AREA')=='AA')
    assert aa['observation_eligible'] and not aa['forecast_eligible']
    assert aa['eligibility_reason']=='source_availability_not_public'
    assert next(r for r in snap.records if r['currency']=='ZZZ' and r['forecast_eligible'])['locator']['REF_AREA']=='ZZ'
    assert aa['header_unit_evidence']['source_attributes']['AVAILABILITY']==[availability]
    # A regime approval cannot turn restricted observations into public eligibility.
    disposition={aa['series_id']:{'decision':'retain_single_source_regime','evidence_sha256':hashlib.sha256(a.canonical(aa['header_unit_evidence'])).hexdigest(),'disposition_sha256':'9'*64}}
    final=build(generated_wb(),shape_bis(availability=availability),regime_dispositions=disposition)
    assert not next(r for r in final.records if r['series_id']==aa['series_id'])['forecast_eligible']


@pytest.mark.parametrize('flow',['BIS:OTHER(1.0)','', 'BIS:WS_XRU(2.0)'])
def test_source_shape_wrong_dataflow_refused(flow):
    with pytest.raises(ValueError,match='DATAFLOW'):build(generated_wb(),shape_bis(flow=flow))


def test_source_shape_prebreak_digest_month_hold_and_exact_disposition():
    wb,bis=generated_wb(),shape_bis(prebreak='7.2500')
    snap=build(wb,bis);aa=next(r for r in snap.records if r['locator'].get('REF_AREA')=='AA')
    assert not aa['forecast_eligible'] and aa['eligibility_reason']=='currency_regime_disposition_required'
    assert snap.values[aa['series_id']]['2016-09']==2.  # not7.25, no replacement/splice
    assert aa['header_unit_evidence']['prebreak_observations']==[{'month':'2016-09','token_sha256':hashlib.sha256(b'7.2500').hexdigest()}]
    assert 'prebreak_observation_review_required' in aa['semantic_review_flags']
    for changed in (shape_bis(prebreak='7.25'),shape_bis(prebreak='7.2500',prebreak_month='2016-10')):
        other=next(r for r in build(wb,changed).records if r['locator'].get('REF_AREA')=='AA')
        assert other['series_id']!=aa['series_id']
    disposition={aa['series_id']:{'decision':'retain_single_source_regime','evidence_sha256':hashlib.sha256(a.canonical(aa['header_unit_evidence'])).hexdigest(),'disposition_sha256':'9'*64}}
    final=build(wb,bis,regime_dispositions=disposition)
    r=next(r for r in final.records if r['series_id']==aa['series_id'])
    assert r['forecast_eligible'] and final.values[r['series_id']]==snap.values[aa['series_id']]
    disposition[aa['series_id']]['evidence_sha256']='0'*64
    assert not next(r for r in build(wb,bis,regime_dispositions=disposition).records if r['series_id']==aa['series_id'])['forecast_eligible']


@pytest.mark.parametrize('token',['unknown','NaN','Infinity','-1','0','1e9999',' 2 ', 'x'*129])
def test_source_shape_malformed_prebreak_fails_closed(token):
    with pytest.raises(ValueError,match='prebreak|PRE_BREAK|field width'):build(generated_wb(),shape_bis(prebreak=token))


def test_source_shape_unknown_csv_still_refused():
    with pytest.raises(ValueError,match='schema'):build(generated_wb(),shape_bis().replace(b'OBS_PRE_BREAK',b'UNKNOWN_FIELD'))


@pytest.mark.parametrize('cell',[
    b'<c r="B1362"><v>99</v></c>',
    b'<c r="B1362" t="inlineStr"><is><t>hidden</t></is></c>',
    b'<c r="B1362"><f/></c>',
    b'<c r="B1362" t="e"/>',
    b'<c r="B1362" t="s"><v>0</v></c>',
])
def test_source_shape_hidden_tail_values_formulas_errors_refused(cell):
    def edit(name,raw):
        if name=='xl/worksheets/sheet2.xml':
            import re
            raw=re.sub(br'<c r="B1362"[^>]*(?:/>|>.*?</c>)',lambda _:cell,raw)
            raw=raw.replace(b'A1:B1362',b'A1:B128')  # lying dimension cannot hide XML tail
        return raw
    with pytest.raises(ValueError,match='meaningful|tail'):build(shape_xml(shape_wb(),edit),generated_bis())


@pytest.mark.parametrize('fault',['physical','rectangle','duplicate_cell','bad_coordinate','duplicate_row','row_cell_mismatch','DTD','external','malformed'])
def test_source_shape_unsafe_or_oversized_xml_refused(fault):
    wb=shape_wb(tail_row=2049 if fault=='physical' else 1362)
    def edit(name,raw):
        if name=='xl/worksheets/sheet2.xml':
            if fault=='rectangle':raw=raw.replace(b'B1362',b'ZZZ1362')
            elif fault=='duplicate_cell':raw=raw.replace(b'</sheetData>',b'<row r="1400"><c r="B1362"/></row></sheetData>')
            elif fault=='bad_coordinate':raw=raw.replace(b'B1362',b'B01362')
            elif fault=='duplicate_row':raw=raw.replace(b'</sheetData>',b'<row r="1362"/></sheetData>')
            elif fault=='row_cell_mismatch':raw=raw.replace(b'<row r="1362">',b'<row r="1363">')
            elif fault=='DTD':raw=b'<!DOCTYPE worksheet [<!ENTITY unsafe "x">]>'+raw
            elif fault=='malformed':raw=b'<worksheet>'
        if fault=='external' and name=='xl/_rels/workbook.xml.rels':raw=raw.replace(b'</Relationships>',b'<Relationship TargetMode="External" Target="https://invalid/"/></Relationships>')
        return raw
    with pytest.raises((ValueError, __import__('xml.etree.ElementTree',fromlist=['ParseError']).ParseError)):
        build(shape_xml(wb,edit),generated_bis())


def test_source_shape_underreported_dimension_cannot_silently_omit_meaningful_rows():
    # All data is within meaningful1006, but the declared dimension hides it.
    # Refuse the source rather than returning a fabricated incomplete catalog.
    def edit(name,raw):
        if name=='xl/worksheets/sheet1.xml':
            assert b'A5:F126' in raw
            return raw.replace(b'A5:F126',b'A5:F10')
        return raw
    with pytest.raises(ValueError,match='dimension'):
        build(shape_xml(generated_wb(),edit),generated_bis())


@pytest.mark.parametrize('last_row',[1006,2048])
def test_source_shape_generic_physical_boundary_has_no_observation_imputation(last_row):
    snap=build(shape_wb(tail_row=last_row),shape_bis())
    index=next(r for r in snap.records if r['locator'].get('sheet')=='Monthly Indices')
    assert index['required_window_mask']==['valid']*120 and index['source_last_period']=='2026-08'
    assert snap.values[index['series_id']]['2026-08']==319.


def test_source_shape_duplicate_and_prebreak_evidence_cannot_bypass_availability():
    rows=list(csv.DictReader(io.StringIO(shape_bis(duplicate=True).decode())))
    # Only the second copy is restricted and has prebreak evidence.
    rows[1].update(AVAILABILITY='B',OBS_PRE_BREAK='7.25')
    out=io.StringIO();writer=csv.DictWriter(out,fieldnames=OFFICIAL_BIS_HEADER);writer.writeheader();writer.writerows(rows)
    snap=build(generated_wb(),out.getvalue().encode())
    aa=next(r for r in snap.records if r['locator'].get('REF_AREA')=='AA')
    assert aa['duplicate_count']==1 and not aa['forecast_eligible']
    assert aa['header_unit_evidence']['source_attributes']['AVAILABILITY']==['A','B']
    assert aa['header_unit_evidence']['prebreak_observations']==[{'month':'2016-09','token_sha256':hashlib.sha256(b'7.25').hexdigest()}]
    assert '2016-09' not in snap.values[aa['series_id']]


def test_source_shape_optional_official_fields_absent_preserve_exact_compatibility_ids():
    old=build(generated_wb(),generated_bis());new=build(generated_wb(),shape_bis())
    oldfx={r['series_id']:r for r in old.records if r['series_kind']=='fx'}
    newfx={r['series_id']:r for r in new.records if r['series_kind']=='fx'}
    assert oldfx.keys()==newfx.keys()
    for sid in oldfx:
        assert old.values[sid]==new.values[sid]
        assert oldfx[sid]['forecast_eligible']==newfx[sid]['forecast_eligible']


def test_source_shape1362_physical_rows_last_meaningful810_preserves_full_catalog():
    from openpyxl import load_workbook
    book=load_workbook(io.BytesIO(shape_wb(full_catalog=True)));sheet=book['Monthly Indices']
    #802 consecutive generated months1959-11..2026-08; last meaningful row810.
    for i,n in enumerate(range(1959*12+10,2026*12+8)):
        sheet.cell(i+9,1,f'{n//12:04d}M{n%12+1:02d}')
        for col in range(2,13):sheet.cell(i+9,col,500.+i+col-2)
    assert sheet.max_row==1362
    assert max(c.row for row in sheet for c in row if c.value is not None)==810
    out=io.BytesIO();book.save(out);book.close()
    snap=build(out.getvalue(),shape_bis())
    wb=[r for r in snap.records if r['series_kind']=='commodity'];assert len(wb)==82
    index=next(r for r in wb if r['locator']=={'sheet':'Monthly Indices','column':'B','header_rows':[6,7,8]})
    assert index['source_first_period']=='1959-11' and index['source_last_period']=='2026-08'
    assert index['forecast_eligible'] and snap.values[index['series_id']]['2016-09']==1182.
    assert snap.values[index['series_id']]['2026-08']==1301.


def test_source_shape_style_only_xml_indentation_is_not_an_observation():
    def edit(name,raw):
        if name=='xl/worksheets/sheet2.xml':
            import re
            raw,count=re.subn(br'<c r="B1362"[^>]*/>',lambda m:m[0][:-2]+b'>\n  </c>',raw)
            assert count==1
        return raw
    snap=build(shape_xml(shape_wb(),edit),generated_bis())
    index=next(r for r in snap.records if r['locator'].get('sheet')=='Monthly Indices')
    assert index['forecast_eligible'] and index['source_last_period']=='2026-08'


def generated_internal_table_wb():
    """Normal table relationship; generated fixture only, numeric cells unchanged."""
    from openpyxl import load_workbook
    from openpyxl.worksheet.table import Table
    book=load_workbook(io.BytesIO(generated_wb()))
    book['Monthly Prices'].add_table(Table(displayName='InternalDataTable',ref='B5:C7'))
    out=io.BytesIO();book.save(out);book.close();return out.getvalue()


def test_source_shape_v2_internal_table_relationship_is_catalog_compatible():
    import zipfile
    from openpyxl import load_workbook
    wb=generated_internal_table_wb()
    with zipfile.ZipFile(io.BytesIO(wb)) as archive:
        rel=archive.read('xl/worksheets/_rels/sheet1.xml.rels')
        assert b'/xl/tables/table1.xml' in rel and b'TargetMode="External"' not in rel
    # Independent reader witness establishes the generated workbook is usable.
    book=load_workbook(io.BytesIO(wb),read_only=True,data_only=False,keep_links=False)
    try:assert book['Monthly Prices']['B7'].value==102.
    finally:book.close()
    snapshot=build(wb,generated_bis())
    cotton=next(r for r in snapshot.records if r['label']=='Cotton A Index')
    assert cotton['forecast_eligible'] and len(snapshot.records)==12
    assert cotton['required_window_mask']==['valid']*120
    assert snapshot.values[cotton['series_id']]['2016-09']==102.
    # The default/legacy path still accepts ordinary internal relationships.
    a._zip_guard(wb)


@pytest.mark.parametrize('fault',['external','external_mixed_case','DTD','entity','UTF16_DTD','malformed','member_limit'])
def test_source_shape_v2_relationship_security_is_still_enforced(fault):
    from xml.etree.ElementTree import ParseError
    def edit(name,raw):
        if name=='xl/worksheets/_rels/sheet1.xml.rels':
            if fault in ('external','external_mixed_case'):
                mode='External' if fault=='external' else 'eXtErNaL'
                raw=raw.replace(b'<Relationship ',f'<Relationship TargetMode="{mode}" '.encode(),1)
            elif fault=='DTD':raw=b'<!DOCTYPE Relationships [<!ENTITY unsafe "x">]>'+raw
            elif fault=='entity':raw=b'<!ENTITY unsafe "x">'+raw
            elif fault=='UTF16_DTD':raw=('<?xml version="1.0" encoding="utf-16"?><!DOCTYPE Relationships [<!ENTITY unsafe "x">]><Relationships/>').encode('utf-16')
            elif fault=='malformed':raw=b'<Relationships>'
            else:raw=b' '*(a.MAX_MEMBER+1)
        return raw
    wb=shape_xml(generated_internal_table_wb(),edit)
    # All XML/rels retain parsing and security checks under both policies.
    match='external' if fault.startswith('external') else ('DTD/entity' if fault in ('DTD','entity','UTF16_DTD') else ('ZIP member' if fault=='member_limit' else None))
    with pytest.raises((ValueError,ParseError),match=match):build(wb,generated_bis())
    with pytest.raises((ValueError,ParseError),match=match):a._zip_guard(wb)


def source_shape_v3_wb(*, meaningful_tail=False, target=None, fault=None):
    """Generated workbook packaging witnesses; no original source metadata/body."""
    import zipfile
    from openpyxl import load_workbook
    from xml.etree import ElementTree as ET
    raw=generated_wb()
    if meaningful_tail:
        book=load_workbook(io.BytesIO(raw));sheet=book['Monthly Prices']
        for i,n in enumerate(range(1943*12+3,2026*12+8)):
            sheet.cell(i+7,1,f'{n//12:04d}M{n%12+1:02d}')
            for col in range(2,7):sheet.cell(i+7,col,100.+i+col)
        assert sheet.max_row==1007
        out=io.BytesIO();book.save(out);book.close();raw=out.getvalue()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members={m.filename:archive.read(m)for m in archive.infolist()}
    relpath='xl/_rels/workbook.xml.rels';rels=ET.fromstring(members[relpath])
    relationship=next(r for r in rels if r.attrib['Type'].endswith('/worksheet'))
    original='xl/worksheets/sheet1.xml'
    if target is not None:
        # Fixture paths are canonical absolute package paths here; deliberately
        # noncanonical link variants below do not rewrite member coordinates.
        dest=target[1:] if target.startswith('/') else 'xl/'+target
        members[dest]=members.pop(original);relationship.set('Target',target)
        types=ET.fromstring(members['[Content_Types].xml'])
        next(t for t in types if t.get('PartName')=='/'+original).set('PartName','/'+dest)
        members['[Content_Types].xml']=ET.tostring(types)
    dest=(target[1:] if target.startswith('/') else 'xl/'+target) if target else original
    if fault=='missing':members.pop(dest)
    elif fault=='duplicate_id':rels.append(ET.fromstring(ET.tostring(relationship)))
    elif fault=='duplicate_target':
        other=next(r for r in rels if r.get('Type').endswith('/worksheet') and r is not relationship)
        other.set('Target',relationship.get('Target'))
    elif fault in ('dot','parent','double_slash','backslash','percent','fragment','query'):
        variants={'dot':'/xl/worksheets/./sheet1.xml','parent':'/xl/worksheets/nested/../sheet1.xml',
                  'double_slash':'/xl/worksheets//sheet1.xml','backslash':'/xl/worksheets\\sheet1.xml',
                  'percent':'/xl/worksheets/%73heet1.xml','fragment':'/xl/worksheets/sheet1.xml#extra',
                  'query':'/xl/worksheets/sheet1.xml?extra'}
        relationship.set('Target',variants[fault])
    elif fault=='bad_rel_type':relationship.set('Type','urn:unknown:sheet')
    elif fault=='bad_target_mode':relationship.set('TargetMode','Unknown')
    elif fault in ('wrong_content_type','duplicate_override','missing_override','noncanonical_override','alternative_workbook'):
        types=ET.fromstring(members['[Content_Types].xml'])
        override=next(t for t in types if t.get('PartName')=='/'+dest)
        if fault=='wrong_content_type':override.set('ContentType','application/octet-stream')
        elif fault=='duplicate_override':types.append(ET.fromstring(ET.tostring(override)))
        elif fault=='missing_override':types.remove(override)
        elif fault=='noncanonical_override':override.set('PartName','/xl/worksheets/./sheet1.xml')
        else:
            wb=next(t for t in types if t.get('PartName')=='/xl/workbook.xml')
            wb.set('ContentType','application/vnd.ms-excel.sheet.macroEnabled.main+xml')
        members['[Content_Types].xml']=ET.tostring(types)
    elif fault in ('unknown_id','duplicate_sheet_id','duplicate_sheet_name','duplicate_sheets_container','foreign_sheets_container'):
        wb=ET.fromstring(members['xl/workbook.xml']);sheets=wb.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheets')
        if fault=='unknown_id':sheets[0].set('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id','absent')
        elif fault=='duplicate_sheet_id':sheets[1].set('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id',sheets[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'))
        elif fault=='duplicate_sheet_name':sheets[1].set('name',sheets[0].get('name'))
        else:
            group=ET.fromstring(ET.tostring(sheets))
            if fault=='foreign_sheets_container':group.tag='{urn:foreign}sheets'
            wb.append(group)
        members['xl/workbook.xml']=ET.tostring(wb)
    members[relpath]=ET.tostring(rels)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED)as archive:
        for name,body in members.items():archive.writestr(name,body)
    return out.getvalue()


def test_source_shape_v3_nested_meaningful_tail_never_reaches_eligible_catalog():
    direct=source_shape_v3_wb(meaningful_tail=True)
    with pytest.raises(ValueError,match='meaningful tail'):build(direct,generated_bis())
    nested=source_shape_v3_wb(meaningful_tail=True,target='/xl/worksheets/nested/sheet1.xml')
    # V2 accepts this exact body as eligible; V3 must refuse unsupported before
    # the reader consumes an unchecked worksheet (or fully guard its shape).
    with pytest.raises(ValueError,match='worksheet target|meaningful tail'):
        build(nested,generated_bis())


@pytest.mark.parametrize('target',[
    '/xl/worksheets/nested/sheet1.xml','/xl/other/sheet1.xml','/sheet1.xml',
    '/xl/worksheets/sheet1.bin','/xl/worksheets/sheet1.XML',
])
def test_source_shape_v3_unsupported_targets_refused_before_reader(target,monkeypatch):
    raw=source_shape_v3_wb(target=target)
    import openpyxl
    monkeypatch.setattr(openpyxl,'load_workbook',lambda *a,**k:(_ for _ in ()).throw(AssertionError('unchecked worksheet reached reader')))
    with pytest.raises(ValueError,match='worksheet target'):build(raw,generated_bis())


@pytest.mark.parametrize('fault',[
    'missing','duplicate_id','duplicate_target','dot','parent','double_slash','backslash','percent','fragment','query',
    'bad_rel_type','bad_target_mode','wrong_content_type','duplicate_override','missing_override',
    'noncanonical_override','alternative_workbook','unknown_id','duplicate_sheet_id','duplicate_sheet_name',
    'duplicate_sheets_container','foreign_sheets_container',
])
def test_source_shape_v3_invalid_or_ambiguous_links_refused_before_reader(fault,monkeypatch):
    raw=source_shape_v3_wb(fault=fault)
    import openpyxl
    monkeypatch.setattr(openpyxl,'load_workbook',lambda *a,**k:(_ for _ in ()).throw(AssertionError('ambiguous unchecked worksheet reached reader')))
    with pytest.raises(ValueError,match='generic workbook|worksheet target'):build(raw,generated_bis())


@pytest.mark.parametrize('target',['/xl/worksheets/sheet1.xml','worksheets/sheet1.xml'])
def test_source_shape_v3_canonical_absolute_and_relative_targets_preserve_values(target):
    snap=build(source_shape_v3_wb(target=target),generated_bis())
    cotton=next(r for r in snap.records if r['label']=='Cotton A Index')
    assert cotton['forecast_eligible'] and snap.values[cotton['series_id']]['2016-09']==102.
    assert len(snap.records)==12 and cotton['required_window_mask']==['valid']*120



def test_source_shape_v3_nonxml_meaningful_target_rejected_without_body_parser(monkeypatch):
    raw=source_shape_v3_wb(meaningful_tail=True,target='/xl/worksheets/sheet1.bin')
    import openpyxl
    monkeypatch.setattr(openpyxl,'load_workbook',lambda *a,**k:(_ for _ in ()).throw(AssertionError('nonXML worksheet reached reader')))
    with pytest.raises(ValueError,match='worksheet target'):build(raw,generated_bis())


def test_source_shape_v3_nonxml_workbook_part_cannot_select_unchecked_links(monkeypatch):
    import zipfile
    from xml.etree import ElementTree as ET
    with zipfile.ZipFile(io.BytesIO(generated_wb()))as archive:
        members={m.filename:archive.read(m)for m in archive.infolist()}
    members['xl/workbook.bin']=members.pop('xl/workbook.xml')
    members['xl/_rels/workbook.bin.rels']=members.pop('xl/_rels/workbook.xml.rels')
    types=ET.fromstring(members['[Content_Types].xml'])
    next(t for t in types if t.get('PartName')=='/xl/workbook.xml').set('PartName','/xl/workbook.bin')
    members['[Content_Types].xml']=ET.tostring(types)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED)as archive:
        for name,body in members.items():archive.writestr(name,body)
    import openpyxl
    monkeypatch.setattr(openpyxl,'load_workbook',lambda *a,**k:(_ for _ in ()).throw(AssertionError('nonXML workbook reached reader')))
    with pytest.raises(ValueError,match='generic workbook'):build(out.getvalue(),generated_bis())
