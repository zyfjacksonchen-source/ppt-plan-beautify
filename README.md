# ppt方案美化

![图标](assets/icon.png)

把用户已有的案例/方案文字，按层级、并列、顺序、比较和因果关系结构化，再匹配 100 种原生模板，制作可编辑 PPTX。九套原配色，也支持自定义 HEX 主题色。不研究业务、不代写策略、不补客户事实。

## 下载

- [轻量 Skill ZIP](https://github.com/zyfjacksonchen-source/ppt-plan-beautify/releases/download/v1.0.1/ppt-plan-beautify-1.0.1.zip)：用于 e-Mate SkillHub，约 391 KiB。
- [完整模板资源 ZIP](https://github.com/zyfjacksonchen-source/ppt-plan-beautify/releases/download/v1.0.0/ppt-template-resources-1.0.0.zip)：9 套原生 PPTX 和 100 张预览，约 316 MiB。
- [Release 全部资源与校验和](https://github.com/zyfjacksonchen-source/ppt-plan-beautify/releases/tag/v1.0.0)：也可按主题下载，默认主题约 19 MiB。

机器调用名保留 `xiaohongshu-casecraft`。SkillHub 显示名为 **ppt方案美化**。本仓库仅保存轻量脚本、结构卡、索引和图标，大文件在 Release。

## 使用

需要 Python 3.9 或更高版本，仅使用标准库。Windows 把 `python3` 换成 `python`。

```sh
python3 scripts/stratx100_resources.py fetch --theme morning-bay-haze
python3 scripts/stratx100_resources.py fetch --preview 055
python3 scripts/stratx100.py inspect 055
```

首次下载支持断点续传、有限重试和 SHA-256 校验。默认缓存 `~/.cache/ppt-plan-beautify/1.0.0`，可通过 `PPT_TEMPLATE_CACHE` 自定义。制作步骤与文字映射见 [SKILL.md](SKILL.md)。

## 大陆网络与离线导入

GitHub 在大陆不同网络的访问情况可能不同，不宣称所有运营商稳定可达。不修改系统代理/DNS，不依赖未知第三方加速站。支持已获授权镜像 `--base-url https://host/version-directory`、本次进程直连 `--direct`，或下载完整包后离线导入：

```sh
python3 scripts/stratx100_resources.py import /path/to/ppt-template-resources-1.0.0.zip
python3 scripts/stratx100_resources.py status
```

所有入口使用相同固定校验和。离线导入成功后，制作不需要联网。[详细资源说明](references/resources.md)。

## 模板来源与边界

模板资源来自用户提供的 BerryPPT StratX100 进阶版，保留原件字节、版权标识与来源。公开仓库不改变或重新授予原模板的版权许可；使用与再分发须符合原作者授权。模板里的示例策略和数字仅用于说明版式，不是用户业务事实。

结构/数据自动检查通过不等于逐页视觉验收；字体、固定色图片及 WPS/PowerPoint 渲染仍应按交付页面复验。
