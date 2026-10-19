
import csv, html, importlib.util, io, json, os, re
import subprocess, sys, time, zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
ROOT = Path(__file__).resolve().parent
BJ = timezone(timedelta(hours=8))
JP = timezone(timedelta(hours=9))
HOME = ROOT / 'reports' / 'free-daily'
def backfill_start(today):
    path = ROOT / '.local' / 'backfill_start.txt'
    if not path.exists():
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(path.read_text(encoding='utf-8-sig').strip())
    except ValueError as error:
        raise ValueError('补跑起始日期格式应为 YYYY-MM-DD') from error
BACKFILL_DAYS = 30
LIMIT = 32 * 1024 * 1024

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)

def read(path):
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {}

def parse(path, target):
    if path.stat().st_size > LIMIT:
        raise ValueError('报表超过32MB')
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            members = z.infolist()
            if len(members) > 100 or sum((x.file_size for x in members)) > LIMIT:
                raise ValueError('ZIP超出大小限制')
            data = [z.read(x) for x in members if x.filename.lower().endswith('.csv') and (not x.is_dir())]
    else:
        data = [path.read_bytes()]
    result, seen = ([], set())
    names = ['クリック数(合計)', '実績額(合計)', '売上金額(合計12時間)', '売上件数(合計12時間)', '売上金額(合計720時間)', '売上件数(合計720時間)']
    if not data:
        raise ValueError('没有CSV数据')
    for payload in data:
        if payload.startswith((b'\xff\xfe', b'\xfe\xff')):
            text = payload.decode('utf-16')
        else:
            try:
                text = payload.decode('utf-8-sig')
            except UnicodeError:
                text = payload.decode('cp932')
        rows = list(csv.reader(io.StringIO(text), strict=True))
        heads = [i for i, row in enumerate(rows) if all((n in row for n in ['商品管理番号'] + names))]
        if len(heads) != 1:
            raise ValueError('无法识别唯一商品报表标题')
        index = heads[0]
        header = rows[index]
        if any((header.count(n) != 1 for n in ['商品管理番号'] + names)):
            raise ValueError('标题重复')
        meta = '\n'.join((','.join(row) for row in rows[:index]))
        periods = re.findall('集計期間[^\\n]*(\\d{4}-\\d{2}-\\d{2})\\s*-\\s*(\\d{4}-\\d{2}-\\d{2})', meta)
        if periods != [(target, target)] or re.search('TOP\\s*\\d+', meta, re.I):
            raise ValueError('文件不是目标日期单日报表，或含TOP筛选；拒绝生成日报')
        for row in rows[index + 1:]:
            if not any((s.strip() for s in row)):
                continue
            if len(row) != len(header):
                raise ValueError('数据列数错误')
            sku = row[header.index('商品管理番号')].strip()
            if not sku or sku in seen:
                raise ValueError('商品编号为空或重复')
            seen.add(sku)
            values = []
            for n in names:
                s = row[header.index(n)].strip()
                if not re.fullmatch('(?:\\d{1,3}(?:,\\d{3})+|\\d+)(?:\\.\\d+)?', s) or len(s) > 40:
                    raise ValueError('指标格式错误')
                values.append(Decimal(s.replace(',', '')))
            if any((values[i] != int(values[i]) for i in (0, 3, 5))):
                raise ValueError('点击或成交件数不是整数')
            result.append([sku] + values)
    return result

def render(rows, target):
    esc = lambda x: html.escape(str(x))
    total = [sum((r[i] for r in rows), Decimal(0)) for i in range(1, 7)]
    clicks, cost, sales12, count12, sales720, count720 = total
    rate = lambda a, b, k=1: f'{a / b * k:.2f}' if b else '—'
    metrics = ''.join((f'<tr><td>{w}小时</td><td>{s}</td><td>{c}</td><td>{rate(s, cost, 100)}%</td><td>{rate(c, clicks, 100)}%</td></tr>' for w, s, c in [(12, sales12, count12), (720, sales720, count720)]))
    details = ''
    for sku, click, cost_, s12, c12, s720, c720 in sorted(rows, key=lambda r: r[2], reverse=True):
        tip = '观察，样本有限；不据此判断盈利'
        if click >= 50 and cost_ >= 1000 and (c720 == 0):
            tip = '优先核查关键词匹配、价格、配送、库存和页面信息；等待归因回补，不直接停投'
        details += f'<tr><td>{esc(sku)}</td><td>{click}</td><td>{cost_}</td><td>{c720}</td><td>{rate(s720, cost_, 100)}%</td><td>{tip}</td></tr>'
    return f'<!doctype html><meta charset="utf-8">\n<title>RPP日报 {target}</title>\n<style>\nbody{{font:16px/1.7 system-ui;max-width:1150px;margin:30px auto;padding:20px}}\ntd,th{{border:1px solid #ccc;padding:8px}}\ntable{{border-collapse:collapse;width:100%}}\n</style>\n<h1>RPP规则日报：{target}（日本报表日期）</h1>\n<p>生成时间（北京时间）：{datetime.now(BJ):%Y-%m-%d %H:%M}<br>\n商品记录：{len(rows)}；点击：{clicks}；\n花费：{cost}日元；平均CPC：{rate(cost, clicks)}日元。</p>\n<table><tr><th>归因口径</th><th>销售额（日元）</th>\n<th>归因成交件数</th><th>ROAS</th><th>CVR</th></tr>{metrics}</table>\n<p>目标日期的720小时归因可在30天窗口内继续变化；\n两种口径不可相加。会补跑缺失日期，但不会刷新已生成的报告、\n不以累计数据相减推算目标日成交。</p>\n<p>排查阈值：点击≥50且花费≥1000日元且720小时归因成交为0。\n这是初始提醒规则，不是行业标准。\n不含成本、自然流量或访客数据，因此不判断利润或全店转化原因，不自动改广告。</p>\n<table><tr><th>商品编号</th><th>点击</th><th>花费（日元）</th>\n<th>720小时成交件数</th><th>720小时ROAS</th><th>规则建议</th></tr>\n{details}</table>\n<p>仅汇总本次导出包含的商品；\n无TOP标记不等于已验证全店商品覆盖。\n零记录表示导出没有商品行。本报告本机计算，无模型API费用。</p>'
HISTORY = "\nif(location.protocol!=='https:' ||\n   !/(^|\\.)rakuten\\.co\\.jp$/.test(location.hostname)) return null;\nconst clean=s=>(s||'').replace(/\\s/g,'');\nconst rows=[];\nfor(const tr of document.querySelectorAll('tr')){\n const c=[...tr.cells];\n if(c.length!==8 || !tr.getClientRects().length)continue;\n const stamp=c[0].textContent.trim().replace(/\\s+/g,' ');\n if(!/^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}$/.test(stamp))continue;\n const a=[...c[2].querySelectorAll(\n   'a,button,input[type=button],input[type=submit]'\n )].filter(e=>e.getClientRects().length&&!e.disabled&&\n   clean(e.value||e.textContent)==='ダウンロード');\n rows.push({\n   stamp,\n   status:clean(c[1].textContent),\n   menu:clean(c[3].textContent),\n   name:clean(c[4].textContent),\n   period:clean(c[5].textContent),\n   start:clean(c[6].textContent),\n   end:clean(c[7].textContent),\n   button:a.length===1?a[0]:null\n });\n}\nreturn rows.length ? {rows} : null;\n"

def choose(rows, target, created_day, normalize):
    matching = []
    for row in rows:
        try:
            start = normalize(row['start'])
            end = normalize(row['end'])
            stamp = datetime.strptime(row['stamp'], '%Y-%m-%d %H:%M:%S')
        except (ValueError, KeyError, TypeError):
            continue
        if row.get('menu') == 'パフォーマンスレポート' and row.get('name') == '全商品レポートダウンロード' and (row.get('period') == '全期間で表示') and (start == target) and (end == target):
            matching.append((stamp, row))
    matching.sort(key=lambda pair: pair[0], reverse=True)
    if len(matching) > 1 and matching[0][0] == matching[1][0]:
        raise ValueError('出现同秒的目标报表，已停止自动选择')
    return matching[0][1] if matching else None

def collect(base, folder, target, state, statefile, setup):

    def set_report_dates():
        log('填写并核对目标单日日期：' + target)
        setter = "\nconst el=arguments[0], value=arguments[1];\nif(el.tagName!=='INPUT'||el.disabled||el.readOnly) return false;\nif(!['text','date'].includes(el.type)) return false;\nconst setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;\nsetter.call(el,value);\nel.dispatchEvent(new Event('input',{bubbles:true}));\nel.dispatchEvent(new Event('change',{bubbles:true}));\nel.dispatchEvent(new Event('blur',{bubbles:true}));\nreturn true;\n"
        for index in (0, 1):
            controls = lookup()
            field = controls['dates'][index]
            if not driver.execute_script(setter, field, target):
                raise ValueError('日期控件只读、禁用或类型不支持；未提交报告')
            time.sleep(0.5)
        stable = 0
        actual = []
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            controls = lookup()
            actual = [f.get_attribute('value') for f in controls['dates']]
            stable = stable + 1 if actual == [target, target] else 0
            if stable >= 5:
                print('起止日期已稳定保留：', target, '至', target, flush=True)
                return
            time.sleep(0.5)
        raise ValueError('日期未稳定保留，目标=' + target + '，实际=' + repr(actual))
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
    from _rms_check import wait_downloads
    from rpp_history import normalize_date, NAV
    local = ROOT / '.local'
    os.environ.update(SE_CACHE_PATH=str(local / 'driver-cache'), SE_AVOID_STATS='true', SE_AVOID_BROWSER_DOWNLOAD='true')
    options = Options()
    options.page_load_strategy = 'eager'
    options.set_capability('unhandledPromptBehavior', 'ignore')
    options.add_argument('--user-data-dir=' + str(local / 'chrome-profile'))
    options.add_experimental_option('prefs', {'download.default_directory': str(folder), 'download.prompt_for_download': False, 'credentials_enable_service': False, 'profile.password_manager_enabled': False})
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(25)

    def log(message):
        print(message, flush=True)
        state['stage'] = message
        save(statefile, state)

    def lookup(script=None):

        def probe(d):
            try:
                return base.find_context(d, script=script)
            except (ValueError, StaleElementReferenceException):
                return False
        return WebDriverWait(driver, 25).until(probe)

    def nav(label):
        from rpp_history import NAV
        log('定位菜单：' + label)
        button = lookup(NAV % json.dumps(label, ensure_ascii=False))['button']
        log('点击菜单：' + label)
        try:
            button.click()
        except TimeoutException:
            print('点击后的页面等待超时，继续核对目标页面；不会重复点击。', flush=True)
        log('菜单点击已执行：' + label)

    def scan():
        log('识别下载历史表')
        try:
            rows = lookup(HISTORY)['rows']
        except TimeoutException:
            driver.switch_to.default_content()
            visible_text = driver.execute_script(
                "return document.body ? document.body.innerText : ''")
            if (not driver.current_url.startswith('https://ad.rms.rakuten.co.jp/rpp/download') or
                    'ダウンロード履歴' not in visible_text or
                    'データがありません' not in visible_text):
                raise
            rows = []
        print('历史表中报告记录数：', len(rows), flush=True)
        for row in rows[:8]:
            print('候选：', row['stamp'], row['status'], row['period'], row['start'], '至', row['end'], row['menu'], row['name'], '可下载=' + str(bool(row['button'])), flush=True)
        return rows
    try:
        log('自动登录RMS')
        from rms_auto_login import ensure_login
        ensure_login(driver, ROOT)
        log('打开RPP报表页')
        try:
            driver.get('https://ad.rms.rakuten.co.jp/rpp/reports')
        except TimeoutException:
            pass
        if 'system_error' in driver.current_url:
            raise ValueError('RMS登录后仍无法进入RPP')
        lookup()
        log('读取下载历史')
        nav('ダウンロード履歴')
        created_day = datetime.now(JP).date().isoformat()
        current = choose(scan(), target, created_day, normalize_date)
        if not current and (not state.get('submitted')):
            nav('パフォーマンスレポート')
            controls = lookup()
            for key in ('product', 'period', 'all'):
                if not controls[key].is_selected():
                    controls[key].click()
                    controls = base.find_context(driver)
            set_report_dates()
            controls = base.find_context(driver)
            if [e.get_attribute('value') for e in controls['dates']] != [target, target]:
                raise ValueError('单日日期未正确设置')
            controls['query'].click()
            time.sleep(3)
            controls = base.find_context(driver)
            if [e.get_attribute('value') for e in controls['dates']] != [target, target] or not all((controls[k].is_selected() for k in ('product', 'period', 'all'))):
                raise ValueError('查询后的日期或选项不正确')
            if not controls['button'].is_enabled():
                raise ValueError('商品别和日期设置后，全商品下载按钮仍不可用；未提交报告')
            state['submitted'] = True
            log('已记录一次提交意图，点击全商品生成')
            try:
                controls['button'].click()
            except TimeoutException:
                pass
            nav('ダウンロード履歴')
        log('等待本次单日报告，最多3分钟；不重复提交')
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            current = choose(scan(), target, created_day, normalize_date)
            if current and current['status'] == '完了' and current['button']:
                log('点击匹配日期的下载按钮')
                try:
                    current['button'].click()
                except TimeoutException:
                    pass
                files = wait_downloads(folder, timeout=90)
                if len(files) != 1:
                    raise ValueError('未收到唯一完整报表，请检查下载情况')
                return files[0]
            print('等待中：' + (current['status'] if current else '尚未找到目标记录'), flush=True)
            time.sleep(10)
            nav('更新')
        raise ValueError('历史报告等待超时；保留提交记录，下次只查历史不重复生成')
    finally:
        try:
            if setup and sys.exc_info()[0] is not None:
                state['failed_stage'] = state.get('stage')
                state['error_type'] = sys.exc_info()[0].__name__
                save(statefile, state)
                save(HOME / 'latest-status.json', state)
                print('出错步骤：', state['failed_stage'], flush=True)
                print('浏览器已保留。请截图当前网页和黑色窗口，不要点击生成报告。', flush=True)
                input('截图后按Enter关闭工具浏览器：')
        finally:
            driver.quit()

def install_task():
    import xml.etree.ElementTree as ET
    sid = subprocess.check_output(['powershell.exe', '-NoProfile', '-Command', '[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value'], text=True).strip()
    esc = lambda x: html.escape(str(x), quote=True)
    boundary = datetime.now(BJ).strftime('%Y-%m-%dT10:00:00+08:00')
    arguments = '-X utf8 "' + str(Path(__file__).resolve()) + '" --auto'
    xml = f'<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n<RegistrationInfo><Description>RmsLocal free RPP daily report</Description></RegistrationInfo>\n<Triggers>\n<CalendarTrigger>\n<Repetition><Interval>PT30M</Interval><Duration>PT14H</Duration><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>\n<StartBoundary>{boundary}</StartBoundary><Enabled>true</Enabled>\n<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n</CalendarTrigger>\n<LogonTrigger><Enabled>true</Enabled><UserId>{esc(sid)}</UserId><Delay>PT1M</Delay></LogonTrigger>\n</Triggers>\n<Principals><Principal id="Author"><UserId>{esc(sid)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n<Settings>\n<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n<StartWhenAvailable>true</StartWhenAvailable>\n<RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>\n<ExecutionTimeLimit>PT12M</ExecutionTimeLimit><Enabled>true</Enabled>\n</Settings>\n<Actions Context="Author"><Exec>\n<Command>{esc(sys.executable)}</Command>\n<Arguments>{esc(arguments)}</Arguments>\n<WorkingDirectory>{esc(ROOT)}</WorkingDirectory>\n</Exec></Actions>\n</Task>'
    ET.fromstring(xml)
    path = HOME / 'task.xml'
    path.write_text(xml, encoding='utf-8')
    literal = str(path).replace("'", "''")
    command = f"Register-ScheduledTask -TaskName 'RmsLocal-RPP-FreeDaily' -Xml ([IO.File]::ReadAllText('{literal}')) -Force -ErrorAction Stop | Out-Null"
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', command], check=True)

def report_exists(state):
    report = state.get('report')
    return bool(state.get('success') and report and Path(report).is_file())

def state_for_target(target):
    current = HOME / (target + '-target-status.json')
    if current.exists():
        return read(current)
    found = []
    for path in HOME.glob('????-??-??-status.json'):
        try:
            item = read(path)
        except (OSError, ValueError):
            continue
        if item.get('target') == target:
            found.append((path.name, item))
    found.sort(reverse=True)
    for _, item in found:
        if report_exists(item):
            return item
    for _, item in found:
        if item.get('submitted'):
            return item
    return found[0][1] if found else {}

def select_target(today, setup):
    if not setup and datetime.now(BJ).hour < 10:
        return None, None
    yesterday = today - timedelta(days=1)
    start = max(backfill_start(today), today - timedelta(days=BACKFILL_DAYS))
    if start > yesterday:
        return None, None
    candidates = [yesterday]
    cursor = start
    while cursor < yesterday:
        candidates.append(cursor)
        cursor += timedelta(days=1)
    for candidate in candidates:
        state = state_for_target(candidate.isoformat())
        if report_exists(state):
            continue
        if not setup and state.get('attempt_day') == today.isoformat() and state.get('attempts', 0) >= 3:
            continue
        return candidate.isoformat(), state
    return None, None

def main():
    import msvcrt
    HOME.mkdir(parents=True, exist_ok=True)
    lock = (HOME / 'run.lock').open('a+b')
    if lock.seek(0, 2) == 0:
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return 0
    setup = '--auto' not in sys.argv
    now = datetime.now(BJ)
    day = now.date().isoformat()
    target, state = select_target(now.date(), setup)
    if target is None:
        if setup:
            print('最近30天内没有需要补跑的日期。', flush=True)
        return 0
    statefile = HOME / (target + '-target-status.json')
    folder = HOME / (day + '-' + now.strftime('%H%M%S%f') + '-' + target)
    folder.mkdir(exist_ok=False)
    state.pop('error', None)
    attempts = state.get('attempts', 0) if state.get('attempt_day') == day else 0
    state.update(success=False, target=target, attempt_day=day,
                 attempts=attempts + 1, stage='开始', report=None)
    save(statefile, state)
    save(HOME / 'latest-status.json', state)
    (HOME / '最新状态.html').write_text('<meta charset="utf-8"><h1>采集中，尚未生成本次日报</h1><p>目标日期：' + target + '</p>', encoding='utf-8')
    log = (folder / 'run.log').open('w', encoding='utf-8', buffering=1)
    if not setup:
        sys.stdout = sys.stderr = log
    try:
        original = (ROOT / 'RMS_RPP_Export.cmd').read_text(encoding='utf-8-sig')
        source = original.split('#' + '--PYTHON--', 1)[1]
        module = ROOT / '_free_rpp_base.py'
        module.write_text(source, encoding='utf-8')
        spec = importlib.util.spec_from_file_location('_free_rpp_base', module)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        old_filter = "visible(e) && clean(e.tagName === 'INPUT' ? e.value : e.textContent) === text"
        new_filter = "e.getClientRects().length && clean(e.tagName === 'INPUT' ? e.value : e.textContent) === text"
        if base.CONTROLS.count(old_filter) != 1:
            raise ValueError('报表控件识别器版本不匹配，已停止')
        base.CONTROLS = base.CONTROLS.replace(old_filter, new_filter)
        path = collect(base, folder, target, state, statefile, setup)
        rows = parse(path, target)
        report = folder / '日报.html'
        report.write_text(render(rows, target), encoding='utf-8')
        state.update(success=True, stage='日报已生成', report=str(report), rows=len(rows))
        save(statefile, state)
        save(HOME / 'latest-status.json', state)
        link = html.escape(report.relative_to(HOME).as_posix(), quote=True)
        (HOME / '最新状态.html').write_text('<meta charset="utf-8"><h1>日报成功</h1><a href="' + link + '">打开本次日报</a>', encoding='utf-8')
        print('日报成功：', report, flush=True)
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        state.update(success=False, error=message)
        save(statefile, state)
        save(HOME / 'latest-status.json', state)
        (HOME / '最新状态.html').write_text('<meta charset="utf-8"><h1>今日采集未完成</h1><p>' + html.escape(state['stage'] + '：' + message) + '</p><p>关闭旧工具Chrome，再双击RMS_FreeDaily.cmd手动试跑。未使用旧数据生成日报。</p>', encoding='utf-8')
        print('未完成：', state['stage'], message, flush=True)
        return 1
    if setup:
        try:
            install_task()
            state['task_installed'] = True
            save(statefile, state)
            save(HOME / 'latest-status.json', state)
            print('已安装北京时间10点任务及登录后补跑。')
            print('优先生成昨天，随后按本机设置补跑近30天内遗漏的日期。')
            print('已生成的报告不重复；每个缺失日期每天最多自动尝试3次。')
        except Exception as error:
            state['task_installed'] = False
            state['task_error'] = type(error).__name__
            save(statefile, state)
            save(HOME / 'latest-status.json', state)
            print('日报成功，但定时任务未安装：', type(error).__name__)
            return 2
    return 0
if __name__ == '__main__':
    sys.exit(main())
