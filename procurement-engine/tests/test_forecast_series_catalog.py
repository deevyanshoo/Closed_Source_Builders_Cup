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
