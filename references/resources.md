# 模板资源获取

轻量 Skill 包含 100 张结构卡、索引、制作脚本和图标。9 套原始 PPTX（每套包含全部 100 模板）及 100 张预览单独提供；原件字节与校验和不变。

在 Skill 目录运行（Windows 使用 `python`，macOS/Linux 使用 `python3`）：

```sh
python3 scripts/stratx100_resources.py fetch --theme morning-bay-haze
python3 scripts/stratx100_resources.py fetch --preview 055
python3 scripts/stratx100.py inspect 055 --theme morning-bay-haze
```

下载脚本打印实际文件路径，用该路径查看预览。默认缓存为用户目录 `.cache/ppt-plan-beautify/1.0.0`，可通过环境变量 `PPT_TEMPLATE_CACHE` 指定可写目录。制作脚本优先读取本地完整安装资产，其次读取缓存，不会暗中联网。

下载按主题/预览获取，已下载完整且校验通过的文件直接复用。中断保留 `.part`，再次运行按 HTTP Range 续传；服务端不支持 Range 时从头下载。每次请求超时 45 秒，每个入口最多 3 次尝试，完整文件须同时匹配包内固定大小和 SHA-256，成功才原子提交到缓存。不要关闭 TLS 校验，不修改系统代理或 DNS。若系统代理不可用，可显式加 `--direct` 仅让本次下载不使用应用层代理；这不能证明底层路由没有 VPN/TUN。

## 大陆网络与离线使用

下载入口以 `assets/stratx100/resources.json` 为准。资源发布在 [GitHub Release](https://github.com/zyfjacksonchen-source/ppt-plan-beautify/releases/tag/v1.0.0)。GitHub 在大陆不同网络的访问情况可能不同，本包不承诺全国网络稳定可达。网络不通时可使用已有获授权 HTTPS 镜像：

```sh
python3 scripts/stratx100_resources.py fetch --theme morning-bay-haze --base-url https://downloads.example.com
```

`--base-url` 指向存放当前版本文件的目录。镜像必须保持清单中的文件名及原始字节，不能换用不匹配的模板。镜像只能更换来源，不能绕过本地固定校验和。不要猜测尚未发布的镜像地址。

离线包为 `ppt-template-resources-1.0.0.zip`，其下载路径、大小及 SHA-256 记录在清单的 `offline_pack`。使用浏览器/已有下载工具获取后：

```sh
python3 scripts/stratx100_resources.py import /absolute/path/ppt-template-resources-1.0.0.zip
python3 scripts/stratx100_resources.py status
```

导入仅提取清单中精确匹配的 109 个成员，逐文件校验，忽略其他路径；失败文件不覆盖有效缓存。导入成功后可完全离线制作，迁移机器时重新导入即可。模板作者版权标识与原件一并保留，使用范围遵守原模板授权。
