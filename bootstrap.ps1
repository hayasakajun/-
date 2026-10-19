$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$required = @('RMS_FreeDaily.cmd', '_rms_free_daily.py', 'RMS_RPP_Export.cmd', '_rms_check.py', 'rpp_history.py', 'rms_auto_login.py', 'requirements.txt')
foreach ($name in $required) {
    if (-not (Test-Path -LiteralPath $name)) { throw "缺少文件：$name。请重新下载完整仓库。" }
}

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw '请先安装 Python 3.13，并启用 Windows Python Launcher (py)。'
}
& py -3.13 -c 'import sys; assert sys.version_info[:2] == (3, 13)'
if ($LASTEXITCODE -ne 0) { throw '请安装 Python 3.13。' }

if (-not (Test-Path -LiteralPath '.\.venv\Scripts\python.exe')) {
    & py -3.13 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败。' }
}
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '安装 Selenium 失败。' }
& $python -m py_compile '_rms_free_daily.py' '_rms_check.py' 'rpp_history.py' 'rms_auto_login.py'
if ($LASTEXITCODE -ne 0) { throw 'Python 文件语法检查失败。' }

New-Item -ItemType Directory -Path '.\.local' -Force | Out-Null
$loginId = Read-Host '输入你自己的 R-Login ID（不要使用他人的账号）'
$memberId = Read-Host '输入你自己的乐天会员 ID 或邮箱'
if ([string]::IsNullOrWhiteSpace($loginId) -or [string]::IsNullOrWhiteSpace($memberId)) {
    throw '账号不能为空。'
}
Set-Content -LiteralPath '.\.local\rms_user.txt' -Value $loginId.Trim() -Encoding UTF8
Set-Content -LiteralPath '.\.local\rakuten_member.txt' -Value $memberId.Trim() -Encoding UTF8
$loginPassword = Read-Host '输入 R-Login 密码（不会显示）' -AsSecureString
$memberPassword = Read-Host '输入乐天会员密码（不会显示）' -AsSecureString
$loginPassword | ConvertFrom-SecureString | Set-Content -LiteralPath '.\.local\rms_password.dpapi' -Encoding ASCII
$memberPassword | ConvertFrom-SecureString | Set-Content -LiteralPath '.\.local\rakuten_member_password.dpapi' -Encoding ASCII
Remove-Variable loginId,memberId,loginPassword,memberPassword

$defaultStart = (Get-Date).AddDays(-1).ToString('yyyy-MM-dd')
$start = Read-Host "补跑起始日期 YYYY-MM-DD（直接回车用昨天 $defaultStart）"
if ([string]::IsNullOrWhiteSpace($start)) { $start = $defaultStart }
try { $parsed = [datetime]::ParseExact($start, 'yyyy-MM-dd', [cultureinfo]::InvariantCulture) }
catch { throw '日期格式不正确，应为 YYYY-MM-DD。' }
if ($parsed.Date -gt (Get-Date).Date.AddDays(-1)) { throw '补跑起始日期不能晚于昨天。' }
Set-Content -LiteralPath '.\.local\backfill_start.txt' -Value $start -Encoding UTF8

Write-Host '本机安装与凭据录入完成。请关闭旧工具 Chrome，双击 RMS_FreeDaily.cmd 做一次人工监督的首轮测试。'
Write-Host '密码仅写入本机 .local；不要上传 .local、reports 或浏览器配置。'
