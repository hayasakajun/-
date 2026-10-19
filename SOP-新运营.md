# 新运营从零复现 SOP：RMS RPP 商品日报

本 SOP 是给第一次接触项目的运营。完成后，运营自己的 Windows 电脑每天从 RMS 下载 RPP 全商品单日报表，并在本机生成 `日报.html`。**不需要原运营的 GitHub 私密仓库，也不需要复制原运营的密码、浏览器配置或报表。** 本仓库是公开代码，没有店铺数据。

## 0. 准备与授权

1. 使用 Windows 10/11，安装 **Google Chrome** 和 **Python 3.13（包含 Windows `py` 启动器）**。Windows 时间、时区设为北京时间（UTC+8）；电脑保持联网。Python/Chrome 安装请从各自官网完成。
2. 取得**你本人被授权使用**的 RMS R-Login ID/密码、乐天会员 ID/密码，并确认这个账号有目标店铺的 RPP「パフォーマンスレポート」与「ダウンロード履歴」权限。首次在浏览器人工登录一次，确认可进入 RMS 和 RPP。不要使用或索取原运营个人密码。
3. 运行 Windows 脚本时一直使用**同一个 Windows 账户**；本机密码文件使用 DPAPI，换 Windows 账户不能解密。如出现验证码、额外验证或店铺选择，先由运营人工完成，不能假定自动登录支持这些新步骤。
4. 本流程使用你电脑上的 `%LOCALAPPDATA%\RmsLocal`，避免把 `.local` 放在可能同步到云端的桌面文件夹。每位运营各自安装一份。不要把整个文件夹分享出去。

## 1. 下载公开代码（复制到 PowerShell）

在**普通 PowerShell 窗口**中执行，不要切换到另一个管理员账号：

```powershell
$root = Join-Path $env:LOCALAPPDATA 'RmsLocal'
if (Test-Path -LiteralPath $root) { throw "目录已存在：$root。请先确认是否有旧项目，不要覆盖。" }
$zip = Join-Path $env:TEMP 'rakuten-rpp-handoff.zip'
$unpack = Join-Path $env:TEMP ('rakuten-rpp-' + [guid]::NewGuid().ToString('N'))
Invoke-WebRequest 'https://github.com/hayasakajun/-/archive/refs/heads/main.zip' -OutFile $zip
Expand-Archive -LiteralPath $zip -DestinationPath $unpack
$source = Get-ChildItem -LiteralPath $unpack -Directory | Select-Object -First 1
if (-not $source -or -not (Test-Path -LiteralPath (Join-Path $source.FullName 'bootstrap.ps1'))) { throw '下载内容不完整。' }
New-Item -ItemType Directory -Path $root | Out-Null
Copy-Item -Path (Join-Path $source.FullName '*') -Destination $root -Recurse -Force
Set-Location -LiteralPath $root
```

如果公司网络禁止 PowerShell 下载，可在本公开仓库网页选择 **Code → Download ZIP**，解压后把所有项目文件放到 `%LOCALAPPDATA%\RmsLocal`。不要把 ZIP 内多余的顶层文件夹当作 `RmsLocal`。

## 2. 在本机安装依赖并录入自己的账号

在同一个 PowerShell 窗口执行：

```powershell
Set-Location (Join-Path $env:LOCALAPPDATA 'RmsLocal')
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

脚本会安装 `selenium==4.51.0`，检查 Python 文件，询问两组 ID 和密码，并把密码以当前 Windows 用户的 DPAPI 形式写入 `.local`。输入密码时屏幕不显示。补跑起始日期可直接回车选择昨天；要补报更早日期时输入 `YYYY-MM-DD`。程序最多只查最近 30 天，不会无限重做历史。

**不要把账号或密码写进聊天、GitHub、SOP 或截图。** 若系统要求验证码，需人工处理；本工具不绕过验证。

## 3. 首次人工监督测试

1. 关闭上一次测试留下的工具 Chrome。普通 Chrome 可以继续使用。
2. 在 `%LOCALAPPDATA%\RmsLocal` 双击 `RMS_FreeDaily.cmd`，或在 PowerShell 运行：

```powershell
Set-Location (Join-Path $env:LOCALAPPDATA 'RmsLocal')
.\RMS_FreeDaily.cmd
```

3. 等待脚本登录 RMS、进入 RPP、生成并下载目标日全商品报表，再生成 `日报.html`。**不要提前手动改动工具 Chrome 的 RPP 页面，也不要重复点击生成。** 第一次可能需要下载 ChromeDriver。
4. 若黑色窗口显示“日报成功”，运行以下验收命令：

```powershell
Set-Location (Join-Path $env:LOCALAPPDATA 'RmsLocal')
$s = Get-Content '.\reports\free-daily\latest-status.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$s | Select-Object success,target,attempt_day,attempts,stage,rows,task_installed,error,report | Format-List
if ($s.report) { "日报文件存在：$(Test-Path -LiteralPath $s.report)" }
Get-ScheduledTaskInfo -TaskName 'RmsLocal-RPP-FreeDaily' | Select-Object LastRunTime,LastTaskResult,NextRunTime | Format-List
```

通过条件：`success=True`、`target` 是预期日期、`日报文件存在=True`、`task_installed=True`，且打开 `reports\free-daily\最新状态.html` 能看到日报链接。`rows` 是本次文件的商品行数，不应被当作全店所有商品的证明。若 `task_installed=False`，日报成功但定时任务未建成，先排查任务权限。

## 4. 次日自动运行验收

保持电脑开机、联网，使用录入凭据时的 Windows 账户保持登录。任务计划在北京时间每天 **10:00** 启动，之后每 30 分钟检查一次，共 14 小时；登录 Windows 后约 1 分钟也会触发。电脑关机、休眠或退出该账户时，不能保证准时运行。

第二天 10:30 之后，重新执行第 3 节的验收命令。先看 `Get-ScheduledTaskInfo` 的 `LastRunTime` 是否更新，再看状态文件的 `target`、`attempt_day`、`success` 和日报文件是否存在。`LastTaskResult=0` 只代表任务进程结束正常，**不单独证明报表下载成功**。如果昨天的报告早已成功，任务会直接退出，`latest-status.json` 可能不更新。

## 5. 补跑与重试规则

- 每次运行只处理一天：先生成昨天；昨天已成功且文件仍在，则从本机设置的起始日期开始，按日期从早到晚补跑最近 30 天的缺口。
- 每个目标日期有独立状态。自动失败后同一天最多尝试 3 次；手动双击不受这个自动上限限制，所以不要连续反复手动运行。
- 曾提交生成的目标日，重试会先查 RPP 下载历史，避免重复提交。若历史记录已过期但状态仍标记 `submitted=true`，程序可能无法继续生成，此时不要盲目删除状态或重复提交，先人工检查 RPP 历史与报表日期。
- 已生成的日报不会随着 720 小时归因变化自动刷新；本版只补缺失日报，不更新旧日报的指标。

## 6. 失败时怎么处理

失败时不要在网页反复点“生成报告”。若黑色窗口要求截图，先截图当前错误网页和黑色窗口，再按 Enter 关闭工具 Chrome。检查 `reports\free-daily\latest-status.json` 的 `target`、`stage`、`error`，以及本次目录下的 `run.log`。常见原因包括账号过期或要求人工验证、账号没有 RPP 权限、RMS `system_error`、页面改版、下载历史未就绪，以及 Chrome 配置被另一个工具窗口占用。

发给技术协作者前，遮盖截图、日志中的账号、店铺名称、商品编号、订单和本机路径。可先运行下面的脱敏摘要命令；发送前仍需人工检查：

```powershell
Set-Location (Join-Path $env:LOCALAPPDATA 'RmsLocal')
$s = Get-Content '.\reports\free-daily\latest-status.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$s | Select-Object success,target,attempt_day,attempts,stage,submitted,rows,task_installed,error | ConvertTo-Json -Depth 3
```

## 7. 不得上传的文件与账号边界

公开仓库只有通用代码，**下载代码不会登录任何账号**。只有你本机运行 CMD 或定时任务时，脚本才读取你本机 `.local` 中的凭据并尝试登录。不要把 `.local/`（账号、DPAPI 密码、工具 Chrome Cookie/会话）、`reports/`（原始 RPP ZIP/CSV、日报）、`run.log` 或截图原样上传公开仓库。`.gitignore` 不能阻止网页手动上传，也不能抹去已提交到 Git 历史的秘密。

如果误传明文密码或浏览器会话文件，立即更改相应密码、退出相关会话并联系仓库管理员处理历史。每个运营应使用自己的授权账号；离职或权限变动时，管理员应在 RMS 中撤销权限。不要复制原运营的 `.local`、Chrome profile 或整套 Windows 用户目录。
