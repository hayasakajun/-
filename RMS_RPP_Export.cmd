@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing
if not exist "_rms_check.py" goto missing
".venv\Scripts\python.exe" -c "from pathlib import Path; import sys; s=Path(sys.argv[1]).read_text(encoding='utf-8-sig'); Path('_rpp_export.py').write_text(s.split('#'+'--PYTHON--',1)[1],encoding='utf-8')" "%~f0"
if errorlevel 1 goto missing
".venv\Scripts\python.exe" -X utf8 "_rpp_export.py"
pause
exit /b
:missing
echo 请放入原来的RmsLocal文件夹，并先成功运行RMS_Check.cmd。
pause
exit /b 1
#--PYTHON--
from pathlib import Path
from datetime import datetime, timedelta, timezone
import csv
import io
import json
import os
import re
import zipfile
CONTROLS = "\nconst clean = s => (s || '').replace(/\\s/g, '');\nconst visible = e => e.getClientRects().length && !e.disabled;\nif (!/(^|\\.)rakuten\\.co\\.jp$/.test(location.hostname)) return null;\nfunction named(e, text) {\n  const wanted = clean(text);\n  if ([...(e.labels || [])].some(l => clean(l.textContent) === wanted)) return true;\n  let n = e.nextSibling;\n  while (n && n.nodeType === 3 && !clean(n.textContent)) n = n.nextSibling;\n  return n && clean(n.textContent) === wanted;\n}\nconst inputs = [...document.querySelectorAll('input')].filter(visible);\nconst find = text => inputs.filter(e => e.type === 'radio' && named(e, text));\nconst product = find('商品別'), period = find('全期間で表示');\nif (product.length !== 1 || period.length !== 1) return null;\nconst scope = product[0].closest('table') || product[0].closest('form');\nif (!scope || !scope.contains(period[0])) return null;\nconst dates = [...scope.querySelectorAll('input')].filter(e =>\n  visible(e) && ['text','date'].includes(e.type) && /^\\d{4}-\\d{2}-\\d{2}$/.test(e.value));\nconst all = [...scope.querySelectorAll('tr')].filter(tr =>\n  tr.cells.length && clean(tr.cells[0].textContent) === '全てを選択')\n  .flatMap(tr => [...tr.querySelectorAll('input[type=checkbox]')].filter(visible));\nconst action = text => [...document.querySelectorAll('button,a,input[type=button],input[type=submit]')]\n  .filter(e => visible(e) && clean(e.tagName === 'INPUT' ? e.value : e.textContent) === text);\nconst buttons = action('全商品レポートダウンロード'), query = action('この条件で検索');\nif (dates.length !== 2 || all.length !== 1 || buttons.length !== 1 || query.length !== 1) return null;\ndates.sort((a,b) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);\nreturn {product: product[0], period: period[0], dates, all: all[0], button: buttons[0], query: query[0]};\n"

def month_to_yesterday():
    yesterday = datetime.now(timezone(timedelta(hours=9))).date() - timedelta(days=1)
    return (yesterday.replace(day=1).isoformat(), yesterday.isoformat())

def find_context(driver, script=None):
    script = CONTROLS if script is None else script
    from selenium.webdriver.common.by import By
    candidates = []

    def search(handle, frames=()):
        if driver.execute_script(script):
            candidates.append((handle, frames))
        if len(frames) < 2:
            for index, frame in enumerate(driver.find_elements(By.CSS_SELECTOR, 'iframe,frame')):
                driver.switch_to.frame(frame)
                search(handle, frames + (index,))
                driver.switch_to.parent_frame()
    for handle in reversed(driver.window_handles):
        driver.switch_to.window(handle)
        driver.switch_to.default_content()
        search(handle)
    if len(candidates) != 1:
        raise ValueError('未能唯一识别RPP页面，请只保留一个商品别报表页。')
    handle, frames = candidates[0]
    driver.switch_to.window(handle)
    driver.switch_to.default_content()
    for index in frames:
        driver.switch_to.frame(driver.find_elements(By.CSS_SELECTOR, 'iframe,frame')[index])
    controls = driver.execute_script(script)
    if not controls:
        raise ValueError('报表页面已发生变化，请重新运行。')
    return controls

def verify_csv(path, start, end):
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            members = [i for i in archive.infolist() if i.filename.lower().endswith('.csv')]
            if not members or len(members) > 100 or sum((i.file_size for i in members)) > 32 * 1024 * 1024:
                raise ValueError('ZIP内容需要人工检查。')
            payloads = [archive.read(i) for i in members]
    elif path.suffix.lower() == '.csv':
        payloads = [path.read_bytes()]
    else:
        raise ValueError('本版只能核对CSV或包含CSV的ZIP。')
    total = 0
    for data in payloads:
        encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'cp932'
        try:
            text = data.decode('utf-8-sig') if encoding != 'utf-16' else data.decode(encoding)
        except UnicodeError:
            text = data.decode(encoding)
        rows = list(csv.reader(io.StringIO(text)))
        header = next((i for i, row in enumerate(rows) if '商品管理番号' in row and '実績額(合計)' in row), None)
        if header is None:
            raise ValueError('未识别为商品别RPP报表。')
        meta = '\n'.join((','.join(row) for row in rows[:header]))
        match = re.search('(\\d{4}-\\d{2}-\\d{2})\\s*-\\s*(\\d{4}-\\d{2}-\\d{2})', meta)
        if not match or match.groups() != (start, end):
            raise ValueError('下载报表的实际日期与本次目标不一致。')
        if re.search('TOP\\s*\\d+', meta, re.I):
            raise ValueError('下载仍含TOP排名筛选，尚未证实全商品范围。')
        if any((len(row) != len(rows[header]) for row in rows[header + 1:] if any(row))):
            raise ValueError('报表列数不一致，内容需要检查。')
        total += len([row for row in rows[header + 1:] if any(row)])
    return total

def main():
    from rpp_history import baseline_history, download_new_report
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from _rms_check import wait_downloads
    root = Path(__file__).resolve().parent
    local = root / '.local'
    local.mkdir(exist_ok=True)
    folder = root / 'reports' / 'rpp-auto' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    folder.mkdir(parents=True)
    os.environ.update(SE_CACHE_PATH=str(local / 'driver-cache'), SE_AVOID_STATS='true', SE_AVOID_BROWSER_DOWNLOAD='true')
    options = Options()
    options.set_capability('unhandledPromptBehavior', 'ignore')
    for env in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
        candidate = Path(os.environ.get(env, '')) / 'Microsoft/Edge/Application/msedge.exe'
        if candidate.is_file():
            options.binary_location = str(candidate)
            break
    else:
        print('没有找到Edge，请先更新Edge。')
        return
    options.add_argument('--user-data-dir=' + str(local / 'edge-profile'))
    options.add_experimental_option('prefs', {'download.default_directory': str(folder), 'download.prompt_for_download': False, 'credentials_enable_service': False, 'profile.password_manager_enabled': False})
    driver = None
    try:
        driver = webdriver.Edge(options=options)
        driver.get('https://glogin.rms.rakuten.co.jp/')
        input('请登录并进入截图中的RPP报表页，关掉保存密码弹窗，然后按Enter：')
        controls = find_context(driver)
        start, end = month_to_yesterday()
        baseline = baseline_history(driver, find_context)
        controls = find_context(driver)
        print('本次目标：日本时间月初至昨日', start, '至', end)
        for key in ('product', 'period', 'all'):
            if not controls[key].is_selected():
                controls[key].click()
                controls = find_context(driver)
        for index, value in enumerate((start, end)):
            field = controls['dates'][index]
            if field.get_attribute('value') == value:
                continue
            if field.get_attribute('readonly') or field.get_attribute('type') == 'date':
                input('请在网页手动设置目标起止日期，然后按Enter继续：')
                controls = find_context(driver)
                break
            field.clear()
            field.send_keys(value, Keys.TAB)
        controls = find_context(driver)
        if [f.get_attribute('value') for f in controls['dates']] != [start, end]:
            raise ValueError('日期填入后未正确保留，已停止。')
        if not all((controls[key].is_selected() for key in ('product', 'period', 'all'))):
            raise ValueError('商品别、全期间或输出全选状态不正确，已停止。')
        controls['query'].click()

        def ready(_):
            try:
                found = find_context(driver)
                dates = [f.get_attribute('value') for f in found['dates']]
                return found if dates == [start, end] else False
            except Exception:
                return False
        controls = WebDriverWait(driver, 30).until(ready)
        if not all((controls[key].is_selected() for key in ('product', 'period', 'all'))):
            raise ValueError('查询后的筛选状态不正确，已停止。')
        controls['button'].click()
        print('已提交一次全商品报告，接下来检查历史并下载。')
        files = download_new_report(driver, folder, start, end, baseline, wait_downloads, find_context)
        if not files:
            raise ValueError('未收到完整文件；可能在下载历史中生成中，请检查网页。')
        count = sum((verify_csv(path, start, end) for path in files))
        summary = {'stage': 'AUTOMATED_EXPORT_TRIAL', 'start': start, 'end': end, 'rows': count, 'files': [{'format': p.suffix.lower(), 'bytes': p.stat().st_size} for p in files], 'scheduled': False}
        (folder / 'export-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print('已核对日期、商品别字段及无TOP排名标记。记录数：', count)
        print('保存目录：', folder)
        print('尚未设置定时运行，报表仍需检查是否有其他筛选。')
    except ValueError as error:
        print('本次未完成：', error)
    except Exception as error:
        print('本次未完成，错误类型：', type(error).__name__)
    finally:
        if driver:
            input('网页暂时保留，完成检查后按Enter关闭工具浏览器：')
            driver.quit()
if __name__ == '__main__':
    main()
