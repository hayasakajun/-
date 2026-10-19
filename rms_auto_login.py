from pathlib import Path
from urllib.parse import urlparse
import subprocess
import time
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException

LOGIN_URL = 'https://glogin.rms.rakuten.co.jp/?sp_id=1'

def visible(driver, selector):
    return [e for e in driver.find_elements(By.CSS_SELECTOR, selector)
            if e.is_displayed() and e.is_enabled()]

def rakuten_page(driver):
    parts = urlparse(driver.current_url)
    host = (parts.hostname or '').lower()
    return parts.scheme == 'https' and any(
        host == domain or host.endswith('.' + domain)
        for domain in ('rakuten.co.jp', 'rakuten.com'))

def secret(root, name):
    path = root / '.local' / name
    if not path.is_file():
        raise ValueError('本机未保存所需的RMS登录密码')
    quoted = str(path).replace("'", "''")
    command = (
        "$s=Get-Content -LiteralPath '" + quoted + "' | Select-Object -First 1 | ConvertTo-SecureString;"
        "$p=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s);"
        "try{[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "[Console]::Write([Runtime.InteropServices.Marshal]::PtrToStringBSTR($p))}"
        "finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p)}"
    )
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
        capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError('无法解密本机密码；请用录入时的Windows账户运行')
    value = result.stdout.decode('utf-8-sig')
    if not value:
        raise ValueError('保存的密码为空')
    return value

def button(driver, labels):
    matches = [
        e for e in visible(driver, 'button,input[type="submit"],input[type="button"],a')
        if any(label in (e.text or e.get_attribute('value') or '') for label in labels)
    ]
    if len(matches) != 1:
        raise ValueError('无法唯一识别登录按钮：' + '/'.join(labels))
    return matches[0]

def wait_page(driver, identify, message, seconds=35):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not rakuten_page(driver):
            raise ValueError('登录页面跳转到非乐天域名，已停止')
        try:
            result = identify()
            if result:
                return result
        except Exception:
            pass
        time.sleep(.5)
    raise ValueError(message)

def ensure_login(driver, root):
    root = Path(root)
    account_file = root / '.local' / 'rms_user.txt'
    member_file = root / '.local' / 'rakuten_member.txt'
    if not account_file.is_file() or not member_file.is_file():
        raise ValueError('缺少本机登录账号或乐天会员账号')
    account = account_file.read_text(encoding='utf-8-sig').strip()
    member = member_file.read_text(encoding='utf-8-sig').strip()
    if not account or not member:
        raise ValueError('本机账号为空')
    first_password = secret(root, 'rms_password.dpapi')
    member_password = secret(root, 'rakuten_member_password.dpapi')
    try:
        driver.get(LOGIN_URL)
    except TimeoutException:
        pass

    def detect():
        host = urlparse(driver.current_url).hostname or ''
        body = driver.find_element(By.TAG_NAME, 'body').text
        passwords = visible(driver, 'input[type="password"]')

        if host == 'glogin.rms.rakuten.co.jp' and len(passwords) == 1:
            accounts = [
                e for e in visible(driver, 'input')
                if 'R-Login ID' in ' '.join(
                    e.get_attribute(key) or ''
                    for key in ('placeholder', 'aria-label'))
            ]
            if len(accounts) != 1:
                accounts = visible(driver, 'input[type="text"],input[type="email"]')
            try:
                first_button = button(driver, ('楽天会員ログインへ',))
            except ValueError:
                first_button = None
            if len(accounts) == 1 and first_button:
                return 'first', (accounts[0], passwords[0], first_button)

        if len(passwords) == 1:
            try:
                return 'member', (
                    passwords[0], button(driver, ('下一步', '次へ', 'Next')))
            except ValueError:
                pass

        if host.endswith('.account.rakuten.com') and not passwords:
            member_inputs = visible(driver, 'input[type="email"],input[type="text"]')
            if len(member_inputs) == 1:
                try:
                    return 'member_id', (member_inputs[0], button(driver, ('下一步', '次へ', 'Next')))
                except ValueError:
                    pass

        if 'R-Login' in body and not passwords:
            try:
                return 'rlogin_complete', button(driver, ('次へ',))
            except ValueError:
                pass

        if not passwords:
            try:
                return 'rms_complete', button(driver, ('RMSを利用します',))
            except ValueError:
                pass

        if (host == 'rms.rakuten.co.jp' or
                host.endswith('.rms.rakuten.co.jp')) and \
                host != 'glogin.rms.rakuten.co.jp' and not passwords:
            return 'done', None
        return None

    stage, elements = wait_page(driver, detect, '无法识别RMS登录页面')

    if stage == 'first':
        elements[0].clear()
        elements[0].send_keys(account)
        elements[1].send_keys(first_password)
        elements[2].click()
        stage, elements = wait_page(
            driver,
            lambda: (result if (result := detect()) and
                     result[0] != 'first' else None),
            'R-Login第一层未通过或未进入下一步')

    if stage == 'member_id':
        elements[0].clear()
        elements[0].send_keys(member)
        elements[1].click()
        stage, elements = wait_page(
            driver,
            lambda: (result if (result := detect()) and
                     result[0] != 'member_id' else None),
            '乐天会员账号未通过或未进入密码页')

    if stage == 'member':
        member_inputs = visible(driver, 'input[type="email"],input[type="text"]')
        if len(member_inputs) > 1:
            raise ValueError('乐天会员账号输入框不唯一，已停止')
        if member_inputs:
            member_inputs[0].clear()
            member_inputs[0].send_keys(member)
        elements[0].send_keys(member_password)
        elements[1].click()
        stage, elements = wait_page(
            driver,
            lambda: (result if (result := detect()) and
                     result[0] != 'member' else None),
            '会员密码未通过或未进入下一步')

    if stage == 'rlogin_complete':
        elements.click()
        stage, elements = wait_page(
            driver,
            lambda: (result if (result := detect()) and
                     result[0] != 'rlogin_complete' else None),
            'R-Login完成页未进入下一步')

    if stage == 'rms_complete':
        elements.click()
        stage, elements = wait_page(
            driver,
            lambda: (result if (result := detect()) and
                     result[0] != 'rms_complete' else None),
            '点击RMS入口后未进入主页')

    if stage != 'done':
        raise ValueError('RMS登录流程未完成')

# RMS_MAINMENU_NOTICE_V1
_login_before_mainmenu_notice = ensure_login

def ensure_login(driver, root):
    _login_before_mainmenu_notice(driver, root)
    from selenium.webdriver.support.ui import WebDriverWait
    driver.switch_to.default_content()
    host = (urlparse(driver.current_url).hostname or '').lower()
    if host != 'mainmenu.rms.rakuten.co.jp':
        return
    try:
        WebDriverWait(driver, 20).until(
            lambda d: d.execute_script('return document.readyState') == 'complete')
    except TimeoutException:
        raise ValueError('RMS确认页加载超时')
    if 'RMSメインメニューへ進む' not in driver.find_element(By.TAG_NAME, 'body').text:
        return
    checks = visible(driver, 'input[type="checkbox"]')
    if len(checks) != 1:
        raise ValueError('无法唯一识别RMS确认框，未进入RPP')
    if not checks[0].is_selected():
        checks[0].click()
    go = button(driver, ('RMSメインメニューへ進む',))
    try:
        go.click()
    except TimeoutException:
        pass
    def entered(d):
        if 'system_error' in d.current_url:
            raise ValueError('进入RMS主菜单后出现系统错误')
        if not rakuten_page(d):
            raise ValueError('RMS主菜单跳转到非乐天域名')
        if d.execute_script('return document.readyState') != 'complete':
            return False
        return 'RMSメインメニューへ進む' not in d.find_element(By.TAG_NAME, 'body').text
    try:
        WebDriverWait(driver, 30).until(entered)
    except TimeoutException:
        raise ValueError('点击后未进入RMS主菜单，已停止打开RPP')
