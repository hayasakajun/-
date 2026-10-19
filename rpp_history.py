
import re
import time
from datetime import date, datetime

COMMON = r"""
const clean = s => (s || '').replace(/\s/g, '');
const visible = e => e.getClientRects().length && !e.disabled;
if (location.protocol !== 'https:' || !/(^|\.)rakuten\.co\.jp$/.test(location.hostname)) return null;
const actions = (scope, name) => [...scope.querySelectorAll('a,button,input[type=button],input[type=submit]')]
  .filter(e => visible(e) && clean(e.tagName === 'INPUT' ? e.value : e.textContent) === name);
"""
NAV = COMMON + "const links = actions(document, %s); return links.length === 1 ? {button:links[0]} : null;"
HISTORY = COMMON + r"""
const expected = ['ダウンロード日時','ステータス','メニュー','ダウンロード名','集計期間','開始','終了'];
const tables = [...document.querySelectorAll('table')].filter(table => {
  const headers = [...table.querySelectorAll('tr')].slice(0,2).flatMap(tr => [...tr.cells].map(c => clean(c.textContent)));
  return visible(table) && expected.every(h => headers.includes(h));
});
if (tables.length !== 1) return null;
const rows = [];
for (const tr of tables[0].querySelectorAll('tr')) {
  const c = [...tr.cells];
  if (!c.length || !/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$/.test(c[0].textContent.trim())) continue;
  if (c.length !== 8) return {error:'历史记录列数不是8列'};
  const buttons = actions(c[2], 'ダウンロード');
  if (buttons.length > 1) return {error:'同一历史记录有多个下载按钮'};
  rows.push({timestamp:c[0].textContent.trim(), status:clean(c[1].textContent), menu:clean(c[3].textContent),
    name:clean(c[4].textContent), period:clean(c[5].textContent), start:clean(c[6].textContent), end:clean(c[7].textContent),
    button:buttons.length === 1 ? buttons[0] : null});
}
return {rows};
"""


def normalize_date(value):
    match = re.fullmatch(
        r'(\d{4})(?:年|[-/])(\d{1,2})(?:月|[-/])(\d{1,2})日?',
        value.strip()
    )
    if not match:
        raise ValueError('历史记录日期格式无法识别')
    return date(*(int(part) for part in match.groups())).isoformat()


def task_key(row):
    datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S')
    return (
        row['timestamp'], row['menu'], row['name'], row['period'],
        normalize_date(row['start']), normalize_date(row['end'])
    )


def pick_current(rows, start, end, baseline):
    matches = [
        row for row in rows
        if row['menu'] == 'パフォーマンスレポート'
        and row['name'] == '全商品レポートダウンロード'
        and row['period'] == '全期間で表示'
        and normalize_date(row['start']) == start
        and normalize_date(row['end']) == end
        and task_key(row) not in baseline
    ]
    if len(matches) > 1:
        raise ValueError('发现多个本次候选任务或重复时间，已停止自动选择')
    return matches[0] if matches else None


def _lookup(driver, lookup, script):
    result = lookup(driver, script=script)
    if result.get('error'):
        raise ValueError(result['error'])
    return result


def _nav(driver, lookup, label):
    import json
    _lookup(
        driver, lookup, NAV % json.dumps(label, ensure_ascii=False)
    )['button'].click()


def _read(driver, lookup, seconds=10):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return _lookup(driver, lookup, HISTORY)['rows']
        except Exception:
            if time.monotonic() >= deadline:
                raise ValueError('未能唯一识别完整下载历史表，请在网页手动处理')
            time.sleep(0.5)


def baseline_history(driver, lookup):
    _nav(driver, lookup, 'ダウンロード履歴')
    try:
        return {
            task_key(row) for row in _read(driver, lookup)
            if row['menu'] == 'パフォーマンスレポート'
            and row['name'] == '全商品レポートダウンロード'
            and row['period'] == '全期間で表示'
        }
    finally:
        _nav(driver, lookup, 'パフォーマンスレポート')
        deadline = time.monotonic() + 30
        while True:
            try:
                lookup(driver)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise ValueError('返回报表后控件未就绪，尚未提交新报告')
                time.sleep(0.5)


def wait_export(folder, wait_downloads):
    while True:
        action = input(
            '请在当前浏览器手动下载本次报告，完成后按Enter重查；Q退出：'
        ).strip().lower()
        if action == 'q':
            return []
        files = wait_downloads(folder, timeout=30)
        if files:
            return files
        print('尚未收到完整文件，浏览器和本次下载目录继续保留。')


def download_new_report(
    driver, folder, start, end, baseline, wait_downloads, lookup
):
    files = wait_downloads(folder, timeout=3)
    if files:
        return files
    try:
        if baseline is None:
            raise ValueError('没有可靠的提交前历史基线，不能自动选择旧任务')
        _nav(driver, lookup, 'ダウンロード履歴')
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            current = pick_current(
                _read(driver, lookup, seconds=3), start, end, baseline
            )
            if current and current['status'] == '完了' and current.get('button'):
                current['button'].click()
                files = wait_downloads(folder, timeout=120)
                if files:
                    return files
                raise ValueError('已点击历史下载一次，但本地没有收到完整文件')
            time.sleep(2)
            _nav(driver, lookup, '更新')
        raise ValueError('本次任务仍未完成，或者未出现唯一匹配的任务')
    except Exception as error:
        print(
            '自动历史下载未完成：',
            str(error) if isinstance(error, ValueError) else type(error).__name__
        )
        print('本次下载目录：', folder)
        return wait_export(folder, wait_downloads)