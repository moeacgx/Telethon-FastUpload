# Telethon-FastUpload 上传测速示例（独立）

这个目录是一个可直接拎走的独立示例：用 **Telethon-FastUpload 多连接并发上传 + 512KB 分片读取** 测试上传带宽。

说明：本示例主要针对“上传跑满带宽”场景效果明显；**下载**建议优先使用 Telethon + `cryptg`，通常更稳定（FastTelethon 的并发下载在部分网络/频道场景可能更容易踩到限制或不稳定）。

## 安装

在本目录安装依赖：

```powershell
python -m pip install -r ./requirements.txt
```

如果你也要做稳定的下载加速（非 Telethon-FastUpload），建议额外安装 `cryptg`：

```powershell
python -m pip install cryptg
```

## 配置

复制并编辑本目录的 `.env`：

```powershell
copy ./.env.example ./.env
```

最少需要：

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_TARGET`（目标频道/群 username 或 `-100...`）

可选：

- `TELEGRAM_SESSION`（默认 `./session.session`）
- `TELEGRAM_PHONE`（session 失效需要重新登录时用）
- 代理：`TELEGRAM_PROXY` 或 `PROXY_*`
- `TELEGRAM_DOWNLOAD_DIR`（默认 `./downloads`，脚本会从这里扫描并上传视频文件）

## 准备文件

- 默认上传目录：`./downloads/`
- 把你要测速的 `mp4/mkv/mov/webm/avi/flv/m4v/ts` 等视频文件放进去即可
- 如果你想换目录：在 `.env` 里设置 `TELEGRAM_DOWNLOAD_DIR=你的目录`

## 运行

```powershell
python ./Telethon_FastUpload_speed.py.py --connections 16 --limit 1 --no-proxy
```

也可以直接不带参数运行，进入交互模式：

```powershell
python ./Telethon_FastUpload_speed.py.py
```

参数含义：

- `--connections 16`：并行连接数（越大越“猛”，但更可能触发限制/收益递减）
- `--limit 1`：只上传前 1 个文件（用于快速测速）
