# 本机任务协议 v1

Python 桌面服务用普通 Python 运行绝对路径 `integrations/job_worker.py`，不用 shell，stdin 写入一行 UTF-8 JSON 后关闭；stdout 逐行读取 JSON。worker 无命令行选项；API key 仅在 stdin 传入，不从父进程环境继承。`python` 字段是所选引擎独立环境的 Python，保留虚拟环境 symlink，不 resolve 为系统解释器。

```json
{"protocol_version":1,"job_id":"job-123","engine":"babeldoc","python":"/absolute/.venvs/babeldoc/bin/python","source":"/absolute/library/paper.pdf","output":"/absolute/jobs/new-job","endpoint":"https://api.deepseek.com","model":"configured-model","api_key":"user-key-only-via-stdin","source_language":"en","target_language":"zh","pages":"1-3","timeout":600,"allow_document_upload":true,"allow_asset_download":true}
```

必选：protocol_version、job_id、engine、python、source、output、endpoint、model、api_key 和两个授权 bool。可选：source_language（en）、target_language（zh）、pages（空字符串代表全部）、timeout（600 秒）。路径必须绝对，output 必须尚不存在。输入最多 1 MiB，不接受重复字段或未知字段。engine 为 `babeldoc` / `pdfmathtranslate`，两者替代使用。版本分别固定 0.6.4 / 1.9.11。

按用户 2026-10-02 的明确授权，桌面端默认策略允许模型 API 外发，以及上游模型/字体下载；任务页不再要求每次额外勾选。可信 caller 按这一配置策略传入两个 flags=true。worker 仍严格要求这两个字段为 JSON bool true，用于拒绝缺少策略授权或格式错误的调用；这不意味着 UI 必须逐次弹窗或勾选。桌面端应显示提供商、endpoint 与发送范围，让默认策略可见。

事件的公共字段为 `protocol_version:1`、`job_id`、`event`、`status`、`progress:null`、`cost:null`、`usage:null`。任务进度与实际费用尚不可观测，始终标为未知。初始事件是 `started/running`，表示参数通过校验并进入执行，不表示远端 API 已启动。结束为 `completed/succeeded` 附 `manifest`，或 `failed/failed`（取消为 `failed/cancelled`）附固定 `error_code/message`。非法输入的 job_id 是 null。成功进程 exit 0，失败 exit 1。manifest 含输出文件名与 SHA-256；文件头校验不证明翻译质量，quality_verified 为 false。后端只在自己创建的 job 输出目录中解析文件名，并再次校验路径和 PDF。

worker 不输出引擎原始日志、密钥、源文件路径或原始异常。后端不得记录 stdin/provider key。worker 通过另一个有界 stdin 请求调用可信 `engine_entry.py`，不再生成含密钥的 TOML/JSON 文件：BabelDOC 从内存解析配置，pdf2zh 在导入高层模块前安装内存配置管理，禁止读取或改写用户的默认配置。两个入口继续调用固定版本上游 CLI 翻译流程，不接受任意上游参数或服务模式。

管道写入由受监督线程执行，不读 stdin 的子进程也不能阻断任务超时。正常结束、失败、超时和取消都先终止受管进程树并回收写线程，再读取产物或清理工作目录；终止未确认则失败并保留目录。Windows 通过 Job 和启动门闩约束 sidecar/引擎；POSIX 协作取消终止引擎进程组。真实上游强杀、旧版本凭据残留恢复、POSIX 拥有者硬退出后的孤儿进程、Windows ACL 仍属发布前待验收项。进程树终止不撤销远端已经接受的请求。

授权允许上游发送模型请求与下载字体/模型。worker 没有新增云服务，也不是网络沙箱；上游真实网络载荷和严格页范围发送仍待验证。不能把部分页范围解释为其他页绝不会传出。桌面 UI 应在运行前明确这一限制。离线资源模式尚未提供，缺少资源下载授权会拒绝运行。
