WebPlayer 手势语音控制网页版 - 使用说明
==========================================

【启动】
双击「启动Web版.bat」——自动打开浏览器并启动本地服务器
（或手动: python server.py，然后访问 http://localhost:8790）

【操作流程】
1. 点「启动」初始化音频
2. 点「开始」播放；六个轨道可单独开关/调音量
3. 点「摄像头」开启手势控制（再点一次关闭）：
   - Man 开: 右手上下=音高(和弦内音), 左手上下=音色明暗
   - Man 关: 右手上下=总音量, 左手上下=总混响
   - 手离开画面 = 参数保持
4. 点「语音」开启语音命令（命令为英文单词）：
   start / pause；pad / pluck / bass / bell / drum / man；no drum / no pad ...

【换电脑移植注意】
1. Python 3.8+，安装依赖: pip install vosk websockets
2. ffmpeg 加入 PATH
3. 语音模型: 下载 Vosk 美式英语小模型 vosk-model-small-en-us-0.15
   (https://alphacephei.com/vosk/models)，解压后修改 server.py 中 VOSK_MODEL
   指向该目录（当前命令词全为英文，用英语模型即可；无模型时仅语音功能不可用）
4. 摄像头手势识别依赖 CDN（MediaPipe），需联网；纯音乐播放无需联网

【公网分享（可选）】
语音识别 WebSocket 已合并到 8790 同端口（路径 /stt-ws），公网只需映射一个端口。
本机测试推荐: cloudflared tunnel --url http://localhost:8790
得到的 https://xxx.trycloudflare.com 即可直接发给朋友（满足摄像头/麦克风的
HTTPS 安全要求）。

【文件结构】
index.html      网页（全部逻辑）
server.py       本地服务器: 静态文件 + 语音识别(同端口 8790, /stt-ws)
Audio\          Pad/Pluck/Bass/Bell 四轨 WAV（16s @ 120BPM 无缝循环）
Drum\drum2.wav  鼓轨循环
