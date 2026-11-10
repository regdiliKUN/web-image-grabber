# 网页图片全能抓取器

![应用图标](assets/preview.png)

一个 Windows 桌面工具：从网页批量提取**任意格式**的图片，直接写进你指定的本地文件夹。

项目包含两部分：

| 组件 | 位置 | 作用 |
| --- | --- | --- |
| 本地桌面程序 | `desktop/` | 原生窗口 EXE，负责解析网页、下载图片、写入任意本地文件夹 |
| 浏览器扩展 | `browser-extension/` | 抓取「当前已打开的页面」（含登录态、JS 动态渲染、懒加载），一键推送给桌面程序 |

---

## 直接用现成程序（推荐）

到 **Releases** 下载 `WebImageGrabber-win-x64.zip`，解压到任意文件夹，双击 `ImageGrabber.exe` 即可。包体已自带 Python 运行库、Tcl/Tk、图标和扩展，**目标机器不需要安装 Python**。

解压后的目录：

```
ImageGrabber.exe        主程序（双击启动原生窗口）
_internal/              自带运行库
browser-extension/      可选：抓当前浏览器页面的扩展
app.ico                 应用图标
config.json             配置（自动生成/可编辑）
下载图片/                默认输出目录
```

想放桌面快捷方式：右键 `ImageGrabber.exe` →「发送到」→「桌面快捷方式」，再把快捷方式的图标改成 `app.ico`。右键该快捷方式 →「打开文件所在的位置」即可直达包体目录。

---

## 功能

- **不限图片格式**：JPG / PNG / WebP / AVIF / GIF / SVG / BMP / ICO / APNG / JXL / HEIC / TIFF，以及没有扩展名的动态图片、内联 base64 图、blob 图。落盘时按 Content-Type 与文件魔数判定，不靠扩展名白名单。
- **抓得全**：`<img>`、`srcset` / `<picture>`、懒加载 `data-*` 属性、CSS 背景与伪元素、Shadow DOM、页面源码与内联 JSON 里的图片地址、`<a>` 指向的原图链接、网络层所有 `image/*` 响应。
- **保存到任意本地文件夹**：文本框填真实绝对路径，或点「浏览选择…」用系统目录对话框挑；设置会写入 `config.json`。
- **可调项**：最小宽度筛选、并发数、超时、Cookie、忽略证书错误、尝试还原原图（去缩略图尺寸后缀与压缩参数）、是否保留无法识别格式的资源。
- **两路输入**：直接填网址批量抓；或保持程序窗口运行，用扩展把当前网页的图片推过来。
- 内置本地服务 `127.0.0.1:17890`：`/push` 接收扩展推送，`/` 提供等价的网页版操作界面，`/ping`、`/status` 可用于健康检查。

---

## 从源码构建

需要 Windows + **带 tkinter/Tcl 的完整版 Python**（官方 Windows 安装程序，安装时勾选 `tcl/tk`；精简版/embeddable 版没有 tkinter）。程序运行时只用 Python 标准库，第三方库只在构建和辅助脚本时需要。

```bash
pip install -r requirements-build.txt

# 在仓库根目录执行
pyinstaller --noconfirm --noconsole --onedir ^
    --icon=assets/app.ico --name ImageGrabber desktop/launcher.py
```

然后把 `assets/app.ico`、`browser-extension/` 复制到生成的 `dist/ImageGrabber/` 里，即为完整包体。

`desktop/image_grabber.py` 是核心（抓取、格式判定、HTTP 服务、网页界面），`desktop/launcher.py` 是原生窗口入口（tkinter + 启动本地服务）。

---

## 安装浏览器扩展（可选）

需要抓取「当前已打开、需要登录或由 JS 动态渲染」的网页时才需要：

1. Chrome / Edge 打开 `chrome://extensions`（Edge 为 `edge://extensions`）
2. 开启右上角「开发者模式」
3. 点「加载已解压的扩展程序」，选中 `browser-extension` 文件夹
4. 在目标网页点扩展图标 → 扫描 → 勾选图片 →「发送到桌面程序」

桌面程序需保持运行，接收端口为 `127.0.0.1:17890`。需要登录的站点可在程序窗口里填 Cookie。

---

## 使用

1. 双击 `ImageGrabber.exe` 打开原生窗口
2. 在「网页地址」填网址（每行一个，可批量），或留空等待扩展推送
3. 在「保存位置」填或选择目标文件夹
4. 点「开始抓取」，进度与日志在窗口里实时显示

窗口上的「网页版界面」按钮会打开同一套功能的浏览器界面；窗口里的「打开扩展文件夹」按钮附带扩展安装说明。

---

## 目录结构

```
README.md
requirements-build.txt       构建期依赖
assets/app.ico               多尺寸应用图标（16–256）
assets/preview.png           图标预览
browser-extension/           Chrome / Edge MV3 扩展
desktop/image_grabber.py     抓取核心 + 本地 HTTP 服务
desktop/launcher.py          tkinter 原生窗口入口
```

---

## 注意事项

- 请遵守目标网站的 robots 与使用条款，商用前确认图片版权授权。
- 部分站点有防盗链；程序会优先带 Cookie 与 Referer 请求，失败会跳过并记录在日志里。
- 需要登录、JS 渲染、滚动懒加载的页面，建议走浏览器扩展通道；纯 URL 抓取无法执行页面脚本。
- 关闭程序窗口即退出；扩展推送需要程序保持运行。
- Windows 下 `.lnk` 快捷方式请用系统接口（IShellLink）或资源管理器创建，不要手写二进制格式。

---

## 许可证

暂未指定开源许可证，保留所有权利。如需复用或二次分发，请联系仓库所有者。
