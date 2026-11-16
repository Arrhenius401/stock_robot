"""行业官方原件来源的离线完整性及冲突回归。"""
import hashlib
import io
import json
from datetime import date

import pandas as pd
import pytest

from radar.stocks.industry_source import (
    _registered_load_industry_source as load_industry_source,
)
from tests.radar.test_stock_market_value_source import inputs

DAY = date(2026, 10, 18)


def seed(tmp_path, conflict=False):
    source = inputs(tmp_path)
    root = tmp_path / 'stock-company-industry'
    root.mkdir()
    def write(name, data, url=None):
        (root / name).write_bytes(data)
        result = {'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        if url:
            result.update(source_url=url, fetched_at=None, time_basis='not_recorded')
        return result
    scope = write('scope.json', source['scope_path'].read_bytes())
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        pd.DataFrame([['说明']]).to_excel(writer,index=False,header=False)
        pd.DataFrame([['','','','','一级','二级','三级',str(810000+n)] for n in range(490)]).to_excel(writer,sheet_name='分类',index=False,header=False)
    taxonomy = write('taxonomy.xlsx',buf.getvalue(),'https://www.swsresearch.com/swindex/pdf/SwClass2021/2014to2021.xlsx')
    rows = [{'股票代码':'600900','计入日期':'2020-01-01','更新日期':'2020-01-01','行业代码':'810000'}]
    if conflict:
        rows.append({**rows[0],'行业代码':'810001'})
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf,index=False,engine='openpyxl')
    history = write('history.xlsx',buf.getvalue(),'https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls')
    manifest = {'version':'stock-company-industry-v1','as_of':DAY.isoformat(),'scope':scope,'history':history,'taxonomy':taxonomy}
    (root/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    return root,manifest


def test_current_historical_and_no_paths(tmp_path):
    seed(tmp_path)
    result=load_industry_source(tmp_path,'600900',DAY)
    assert result['status']=='current'
    assert result['industry']['level1']=='一级' and result['industry']['level2']=='二级'
    assert str(tmp_path) not in json.dumps(result)
    assert load_industry_source(tmp_path,'600900',date(2026,10,19))['status']=='historical'


@pytest.mark.parametrize('damage',['tamper','escape','conflict','scope'])
def test_bad_source_is_unavailable(tmp_path,damage):
    root,manifest=seed(tmp_path,conflict=damage=='conflict')
    if damage=='tamper':
        (root/'history.xlsx').write_text('changed')
    elif damage=='escape':
        manifest['history']['path']='../outside'
    elif damage=='scope':
        manifest['scope']['sha256']='a'*64
    (root/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    assert load_industry_source(tmp_path,'600900',DAY)['status']=='unavailable'


def test_missing_source_creates_nothing(tmp_path):
    assert load_industry_source(tmp_path,'600900',DAY)['status']=='unavailable'
    assert not list(tmp_path.iterdir())
