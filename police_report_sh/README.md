# 上海交通违法视频举报 API

此目录通过平台 HTTP 接口和页面引用的腾讯 VOD/COS SDK 协议完成认证、视频上传和举报保存，不需要浏览器自动化。

## 一次性准备

`profile.json` 保存默认实名信息，`report.json` 保存默认举报内容。实名信息和实际举报 JSON 均已加入 `.gitignore`；也可以为不同举报另存独立 JSON。

```bash
npm install
python3 api_probe.py
# 已明确同意立即发送本次验证码时，可跳过 YES 提示：
python3 api_probe.py --send-sms
```

认证脚本默认会要求确认发送一条短信；使用 `--send-sms` 时会直接发送一条。之后在终端安全输入短信验证码。成功后会保存 API 会话 Cookie，并记录认证后的页面入口。Cookie 和页面材料均保存在本机忽略目录，权限为 `0600`。

## 提交举报

```bash
npm run submit
# 或：
python3 submit-report.py report.shanghai-dgv400.json
```

脚本会检查实名资料、报告字段、平台下拉选项、10 日受理窗口、视频格式和 100 MB 大小上限。若时长或分辨率超过官网建议，会提示但按原文件上传，不裁剪或转码；平台可能拒收。之后按官网流程请求腾讯 VOD 临时上传授权，上传视频并取得 `fileId`，最后调用一次 `/jb/save` 保存举报。`/jb/save` 结果不明确时不会自动重试，以免重复提交；请先通过官网“查询已上传记录”核实。

地点必须精确对应官网道路联想库中的路段/路口选项。若无精确匹配，脚本会列出查询候选项并停止，不会擅自替换地点，也不会在该阶段上传或提交。

## 使用到的接口

- `POST /jb/sendMsg`：发送短信验证码
- `POST /jb/jy`：验证短信验证码
- `POST /jb/check`：取得举报表单入口
- `GET /jb/S`：取得腾讯 VOD 上传签名
- Tencent VOD `PrepareUploadUGC`、`ApplyUploadUGC`、`CommitUploadUGC`
- `POST /jb/save`：保存举报记录

提交脚本只调用一次举报保存接口。若认证会话过期，重新运行 `python3 api_probe.py` 完成短信验证。
