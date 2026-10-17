修复部分旧对话重试时出现 Expected an ID that begins with fc_ 的问题。

兼容层现在会把历史 custom_tool_call 的 ctco_... ID 映射成合法的 Responses fc_... ID，并同步转换对应工具输出的 call_id，保持调用关联。兼容服务会在升级时刷新自身代码，已有供应商和模型配置不变。

Windows 下载 windows.zip，macOS 下载 portable.zip。公开包不包含个人设置、API Key 或服务器数据。
