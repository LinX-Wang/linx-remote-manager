# 开发与贡献

欢迎通过 [故障反馈 / 功能建议表单](https://github.com/LinX-Wang/linx-remote-manager/issues/new/choose)描述问题，或用 Pull Request 提交改进。表单为中文，也欢迎英文填写。请先阅读 [安全说明](SECURITY.md)，不要提交个人连接、Key、私钥或迁移包；安全漏洞请使用其中的私密报告入口。

## 本地开发

Python 运行时只需要标准库。推荐 Python 3.10+ 与系统 OpenSSH。

```bash
python launch.pyw
python -m unittest discover -s tests -v
python tools/audit_public.py
```

直接运行会读取当前电脑 Cindy 的 SSH 配置。测试不需要真实服务器；先使用隔离的系统账号或临时环境变量测试，避免影响已有个人配置。

## 生成演示截图

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python tools/screenshots.py
```

截图工具在浏览器内拦截所有请求，只使用代码中的虚构数据，不读取本机供应商、服务器或 Key。Windows 若已安装 Edge，可使用 `--channel msedge`。生成的图片位于 `docs/assets`。

## 构建公开包

```bash
python tools/build_release.py --source-only
# Windows：当前 Python 环境需要 PyInstaller
python tools/build_release.py
```

构建器仅收集固定白名单。它不读取 `settings.json`，不复用个人 EXE，不调用个人迁移功能。发行文件与 SHA256SUMS 位于 `dist`。Windows EXE 未签名，macOS 提供共享 Python 源码与启动入口。

GitHub Actions 的 CI 在 Windows、macOS 和 Linux 执行离线单元测试与公开文件检查。Release 工作流可手动生成附件；推送与 VERSION 一致的 `v*` 标签会创建 GitHub Release。不要在 Release 工作流里加入私有配置。

修改发布版本时同步 `VERSION` 和 `CHANGELOG.md`，并更新演示截图。修改 SSH、路由切换或凭据处理时，请覆盖失败回退和任务忙碌情形，不要依赖真实付费 API 测试。
